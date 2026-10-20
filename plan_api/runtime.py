import asyncio
from contextlib import closing
import copy
import json
import sqlite3
import time

import httpx

from .auth import API
from .errors import APIError
from .continue_connector import MODEL_ALIAS


async def sse_events(response):
    lines=[];size=0
    async for line in response.aiter_lines():
        if line.startswith('data:'):
            value=line[5:].lstrip();size+=len(value)
            if size>8*1024*1024:raise APIError(502,'oversized_event','上游事件过大。')
            lines.append(value)
        elif line=='':
            if lines:
                raw='\n'.join(lines);lines=[];size=0
                if raw!='[DONE]':yield json.loads(raw)
    if lines and '\n'.join(lines)!='[DONE]':yield json.loads('\n'.join(lines))


class Runtime:
    def __init__(self,auth,clients=httpx.AsyncClient):
        self.auth,self.clients=auth,clients
        self.catalog={};self.slots=asyncio.Semaphore(2);self.in_flight=0
        self.database=auth.store.path/'usage.sqlite3'
        with closing(sqlite3.connect(self.database)) as c, c:
            c.execute('CREATE TABLE IF NOT EXISTS requests(at REAL,model TEXT,status TEXT,input_tokens INTEGER,output_tokens INTEGER)')
            columns={r[1] for r in c.execute('PRAGMA table_info(requests)')}
            if 'usage_reported' not in columns:
                c.execute('ALTER TABLE requests ADD COLUMN usage_reported INTEGER')

    def record(self,model,status,usage):
        with closing(sqlite3.connect(self.database)) as c, c:
            c.execute('INSERT INTO requests(at,model,status,input_tokens,output_tokens,usage_reported) VALUES(?,?,?,?,?,?)',
                (time.time(),model,status,usage.get('input_tokens',0),usage.get('output_tokens',0),
                 int('input_tokens' in usage and 'output_tokens' in usage)))

    def usage_history(self,period='24h',page=1):
        cutoff={'24h':86400,'7d':604800,'30d':2592000,'all':None}[period]
        where='at>?' if cutoff else '1=1'
        args=(time.time()-cutoff,) if cutoff else ()
        with closing(sqlite3.connect(self.database)) as c:
            count,completed,inp,out,unknown,legacy=c.execute(
                f"SELECT COUNT(*),SUM(status='completed'),SUM(input_tokens),SUM(output_tokens),"
                f"SUM(usage_reported=0),SUM(usage_reported IS NULL) FROM requests WHERE {where}",args).fetchone()
            pages=max(1,(count+19)//20);page=min(page,pages)
            rows=c.execute(f'SELECT rowid,at,model,status,input_tokens,output_tokens,usage_reported FROM requests WHERE {where} ORDER BY at DESC,rowid DESC LIMIT 20 OFFSET ?',args+((page-1)*20,)).fetchall()
        return {'page':page,'pages':pages,'summary':{'requests':count,'completed':completed or 0,
            'input_tokens':inp or 0,'output_tokens':out or 0,'total_tokens':(inp or 0)+(out or 0),
            'unreported':unknown or 0,'legacy':legacy or 0},'items':[
                dict(zip(('id','at','model','status','input_tokens','output_tokens','usage_reported'),r)) for r in rows]}

    def stats(self):
        with closing(sqlite3.connect(self.database)) as c, c:
            count,success,inp,out=c.execute("SELECT COUNT(*),SUM(status='completed'),SUM(input_tokens),SUM(output_tokens) FROM requests WHERE at>?",(time.time()-86400,)).fetchone()
        return dict(requests=count,completed=success or 0,input_tokens=inp or 0,output_tokens=out or 0)

    async def models(self,identity=None,force=False):
        identity=identity or self.auth.accounts['active']
        token=await self.auth.token(identity)
        hit=self.catalog.get(identity)
        if hit and not force and time.time()-hit[0]<300:return hit[1]
        async with self.clients(timeout=30,follow_redirects=False) as c:
            r=await c.get(API+'/models',headers={'Authorization':'Bearer '+token})
        if r.status_code!=200:raise self.upstream_error(r.status_code,r.json() if r.headers.get('content-type','').startswith('application/json') else {})
        body=r.json()
        if not isinstance(body.get('models'),list):raise APIError(502,'invalid_catalog','上游没有返回套餐模型目录。')
        items=[{'id':m['slug'],'name':m.get('display_name',m['slug'])} for m in body['models']
               if isinstance(m,dict) and m.get('visibility')=='list' and m.get('slug')]
        self.catalog[identity]=(time.time(),items)
        return items

    @staticmethod
    def upstream_error(status,body):
        error=body.get('error') or {}
        if not isinstance(error,dict):error={}
        code=error.get('code') or 'upstream_'+str(status)
        messages={'subscription_sharing_usage_limit_exceeded':'套餐或本应用用量已达上限，请查看 ChatGPT 使用量设置。',
            'subscription_sharing_usage_unavailable':'当前套餐用量暂不可用，请查看 ChatGPT 授权和使用量设置。'}
        return APIError(status,code,messages.get(code,'OpenAI 请求失败（'+str(code)+'），未切换其他账号或付费 API。'))

    def normalize(self,body):
        allowed={'model','input','instructions','reasoning','text','tools','tool_choice','parallel_tool_calls','include','store','stream'}
        if not isinstance(body,dict):raise APIError(400,'invalid_request','请求必须为 JSON 对象。')
        if set(body)-allowed:raise APIError(400,'unsupported_parameter','暂不支持参数：'+', '.join(sorted(set(body)-allowed)))
        if body.get('store') not in (None,False):raise APIError(400,'store_not_supported','套餐请求必须使用 store=false。')
        result=copy.deepcopy(body)
        result['model']=result.get('model') or self.auth.settings['selected_model']
        if result['model']==MODEL_ALIAS:result['model']=self.auth.settings['selected_model']
        effort=self.auth.settings.get('reasoning_effort','')
        if effort and (result.get('reasoning') is None or isinstance(result.get('reasoning'),dict)):
            reasoning=result.get('reasoning') or {}
            if not reasoning.get('effort'):
                result['reasoning']={**reasoning,'effort':effort}
        if not isinstance(result['model'],str) or not result['model']:raise APIError(400,'model_required','请先选择模型或在请求中传入 model。')
        if isinstance(result.get('input'),str):result['input']=[{'role':'user','content':result['input']}]
        if not isinstance(result.get('input'),list) or not result['input']:raise APIError(400,'invalid_input','input 必须为非空文本或消息列表。')
        for message in result['input']:
            if not isinstance(message,dict):raise APIError(400,'invalid_input','input 每项必须为对象。')
            if message.get('role')=='system':message['role']='developer'
        tools=result.get('tools',[])
        if not isinstance(tools,list):raise APIError(400,'invalid_tools','tools 必须为列表。')
        for tool in tools:
            if not isinstance(tool,dict) or tool.get('type')!='function':
                raise APIError(400,'unsupported_tool','首版支持 function 工具；其他托管工具暂未验证。')
        result.update(store=False,stream=True)
        return result

    async def events(self,body,request):
        payload=self.normalize(body);identity=self.auth.accounts['active']
        status='interrupted';tokens={};completed_items={}
        async with self.slots:
            self.in_flight+=1
            try:
                if payload['model'] not in {m['id'] for m in await self.models(identity)}:
                    raise APIError(400,'model_unavailable','该模型不在当前账号的可用列表中，请刷新模型列表。')
                token=await self.auth.token(identity)
                async with self.clients(timeout=httpx.Timeout(240,connect=20),follow_redirects=False) as c:
                    async with c.stream('POST',API+'/responses',headers={'Authorization':'Bearer '+token},json=payload) as r:
                        if r.status_code!=200:
                            await r.aread()
                            try:details=r.json()
                            except ValueError:details={}
                            raise self.upstream_error(r.status_code,details)
                        r.encoding='utf-8'
                        async for event in sse_events(r):
                            if await request.is_disconnected():
                                raise asyncio.CancelledError()
                            kind=event.get('type','')
                            if kind=='response.output_item.done':completed_items[event['output_index']]=event['item']
                            if kind in ('response.failed','response.incomplete','error'):
                                value=event.get('response',event);tokens=value.get('usage') or {}
                                error=value.get('error') or event.get('error') or {'code':kind}
                                code=error.get('code','') if isinstance(error,dict) else ''
                                raise self.upstream_error(429 if code.startswith('subscription_sharing_usage_') else 502,{'error':error})
                            if kind=='response.completed':
                                result=event.get('response',{})
                                if not result.get('output') and completed_items:result['output']=[completed_items[i] for i in sorted(completed_items)]
                                tokens=result.get('usage') or {};status='completed'
                                yield event
                                return
                            yield event
                raise APIError(502,'stream_interrupted','连接在 response.completed 前中断，本次未完成。')
            except APIError as exc:
                status=exc.code;raise
            except asyncio.CancelledError:
                status='cancelled';raise
            except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
                status='upstream_connection_error'
                raise APIError(502,status,'上游连接或响应异常，本次未完成，不会自动重试。') from exc
            finally:
                self.in_flight-=1
                self.record(payload['model'],status,tokens)
