import json
import asyncio
import os
from pathlib import Path
import secrets
import time

from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse,StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.background import BackgroundTask
import httpx

from . import __version__
from .auth import Auth
from .compat import ChatStream,chat_to_responses,response_to_chat,continue_request
from .errors import APIError
from .runtime import Runtime
from .storage import Store
from .continue_connector import ContinueConnector,MODEL_ALIAS,MODEL_NAME
from .startup import Startup
from .anythingllm_connector import AnythingConnector
from .model_follow import identify,follow_latest


def create_app(store=None,port=24664,auth=None,runtime=None,connector=None,anything_connector=None):
    origin=f'http://127.0.0.1:{port}'
    if auth is None:
        store=store or Store(Path(os.environ.get('LOCAL_PLAN_API_DATA_DIR',
            str(Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'LocalPlanAPI'))))
        auth=Auth(store,origin)
    runtime=runtime or Runtime(auth)
    connector=connector or ContinueConnector(auth.store,origin)
    anything_connector=anything_connector or AnythingConnector(auth.store,origin)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    app.state.auth=auth;app.state.runtime=runtime
    sessions={}
    startup=Startup(port)

    @app.get('/admin/startup')
    async def startup_status(request:Request):
        admin(request)
        return startup.status()

    @app.post('/admin/startup')
    async def startup_update(request:Request):
        admin(request);value=await body(request)
        if type(value.get('enabled')) is not bool:
            raise APIError(400,'invalid_request','enabled 必须为布尔值。')
        return startup.set_enabled(value['enabled'])

    @app.exception_handler(APIError)
    async def error_response(request,exc):return JSONResponse(exc.body(),status_code=exc.status)

    @app.exception_handler(httpx.HTTPError)
    async def network_error(request,exc):return JSONResponse(APIError(502,'connection_failed','连接官方服务失败，请稍后重试。').body(),status_code=502)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request,exc):return JSONResponse(APIError(exc.status_code,'http_error',str(exc.detail)).body(),status_code=exc.status_code)

    @app.middleware('http')
    async def guard(request,call_next):
        if (request.headers.get('host')!=f'127.0.0.1:{port}' or
                (request.client and request.client.host!='127.0.0.1') or
                request.headers.get('origin') not in (None,origin)):
            return JSONResponse(APIError(403,'loopback_only','仅允许本机访问。').body(),status_code=403)
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
            'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY'})
        return response

    def admin(request):
        session=request.cookies.get('local_plan_session','')
        if sessions.get(session,0)<=time.time():raise APIError(401,'session_expired','请刷新本地管理页面。')
        return session

    def api_auth(request):
        keys=[auth.settings['api_key']]
        for application in ('continue','anythingllm'):
            if request.url.path.startswith('/compat/'+application+'/v1/'):
                key=auth.settings.get('application_keys',{}).get(application)
                if key:keys.append(key)
        supplied=request.headers.get('authorization','').encode('utf-8')
        if not any(secrets.compare_digest(supplied,('Bearer '+key).encode('utf-8')) for key in keys):
            raise APIError(401,'invalid_api_key','本地 API 密钥无效。')

    async def body(request):
        chunks=[];length=0
        async for chunk in request.stream():
            length+=len(chunk)
            if length>24*1024*1024:raise APIError(413,'payload_too_large','请求超过24MB。')
            chunks.append(chunk)
        data=b''.join(chunks)
        try:value=json.loads(data)
        except (ValueError,UnicodeError) as exc:raise APIError(400,'invalid_json','请求不是有效 JSON。') from exc
        if not isinstance(value,dict):raise APIError(400,'invalid_request','请求必须为 JSON 对象。')
        return value

    @app.get('/',response_class=HTMLResponse)
    async def index(request:Request):
        nonlocal sessions
        sessions={k:v for k,v in sessions.items() if v>time.time()}
        session=request.cookies.get('local_plan_session','')
        if session not in sessions:session=secrets.token_urlsafe(32)
        sessions[session]=time.time()+86400
        response=HTMLResponse((Path(__file__).parent/'dashboard.html').read_text(encoding='utf-8'))
        response.set_cookie('local_plan_session',session,httponly=True,samesite='lax',max_age=86400)
        return response

    @app.get('/health')
    async def health():return {'ok':True,'product':'local-plan-api','version':__version__,'billing':'chatgpt_plan','paid_fallback':False,'pid':os.getpid()}

    @app.get('/admin/status')
    async def status(request:Request):
        admin(request);account=auth.accounts['items'].get(auth.accounts['active'],{})
        return {'version':__version__,'signed_in':bool(account.get('access_token')),
            'plan_enabled':'chatgpt.tokens.use.direct' in account.get('scopes',[]),
            'active':auth.accounts['active'],'accounts':[{'id':k,'email':v.get('email',''),'client':v['client_id'][-8:]}
                for k,v in auth.accounts['items'].items()], 'model':auth.settings['selected_model'],
            'api_base':origin+'/v1','api_key':auth.settings['api_key'],'in_flight':runtime.in_flight,
            'continue_api_base':origin+'/compat/continue/v1',
            'reasoning_effort':auth.settings.get('reasoning_effort',''),
            'auto_follow':auth.settings.get('auto_follow',False),
            'model_update':auth.settings.get('model_update'),
            'usage':runtime.stats()}

    @app.post('/admin/login')
    async def login(request:Request):
        session=admin(request);value=await body(request)
        return {'url':await auth.start(session,new=value.get('new') is True)}

    @app.get('/auth/callback')
    async def callback(request:Request):
        await auth.callback(request.query_params,request.cookies.get('local_plan_session',''))
        runtime.catalog.clear()
        return RedirectResponse('/',303)

    @app.get('/admin/models')
    async def models_admin(request:Request):
        admin(request)
        choices=await runtime.models(force=True)
        return {'models':choices,'model':auth.settings['selected_model'],'model_update':auth.settings.get('model_update')}

    @app.get('/admin/usage')
    async def usage_admin(request:Request):
        admin(request)
        period=request.query_params.get('period','24h')
        try:page=int(request.query_params.get('page','1'))
        except ValueError:raise APIError(400,'invalid_page','页码无效。')
        if period not in ('24h','7d','30d','all') or not 1<=page<=1000000:
            raise APIError(400,'invalid_usage_filter','用量筛选条件无效。')
        return runtime.usage_history(period,page)

    @app.post('/admin/model')
    async def set_model(request:Request):
        admin(request);value=await body(request);model=value.get('model')
        if model not in {m['id'] for m in await runtime.models()}:raise APIError(400,'model_unavailable','请选择账号可用的模型。')
        effort=value.get('reasoning_effort',auth.settings.get('reasoning_effort',''))
        if effort not in ('','low','medium','high'):
            raise APIError(400,'invalid_reasoning_effort','请选择自动、低、中或高思考深度。')
        enabled=value.get('auto_follow',auth.settings.get('auto_follow',False))
        if type(enabled) is not bool:raise APIError(400,'invalid_auto_follow','自动更新开关必须为布尔值。')
        if enabled and not identify(model):raise APIError(400,'unsupported_model_family','该模型命名暂不支持自动跟随，请使用固定型号。')
        if model!=auth.settings['selected_model']:auth.settings.pop('model_update',None)
        auth.settings['auto_follow']=enabled
        auth.settings['reasoning_effort']=effort
        auth.settings['selected_model']=model
        follow_latest(auth.settings,await runtime.models())
        auth.store.save('settings',auth.settings)
        return {'ok':True}

    @app.post('/admin/account')
    async def set_account(request:Request):
        admin(request);value=await body(request)
        if runtime.in_flight:raise APIError(409,'request_active','请求处理中，请结束后再切换账号。')
        if value.get('id') not in auth.accounts['items']:raise APIError(400,'invalid_account','账号不存在。')
        auth.accounts['active']=value['id'];auth.settings['selected_model']=''
        auth.settings['auto_follow']=False;auth.settings.pop('model_update',None)
        auth.store.save('accounts',auth.accounts);auth.store.save('settings',auth.settings);runtime.catalog.clear()
        return {'ok':True}

    @app.post('/admin/disconnect')
    async def disconnect(request:Request):
        admin(request)
        if runtime.in_flight:raise APIError(409,'request_active','请求处理中，请结束后再退出。')
        revoked=await auth.disconnect();runtime.catalog.clear()
        return {'ok':True,'remote_revoked':revoked,'message':'已退出。' if revoked else '本地授权已清除；远程撤销未确认，可在 ChatGPT 设置中断开。'}

    @app.post('/admin/rotate-key')
    async def rotate(request:Request):
        admin(request);auth.settings['api_key']=secrets.token_urlsafe(32);auth.store.save('settings',auth.settings)
        return {'ok':True}

    @app.get('/admin/continue')
    async def continue_status(request:Request):
        admin(request)
        info=await asyncio.to_thread(connector.status)
        account=auth.accounts['items'].get(auth.accounts['active'],{})
        info['bridge_ready']=bool(account.get('access_token') and 'chatgpt.tokens.use.direct' in account.get('scopes',[]))
        info['default_model']=auth.settings['selected_model']
        return info

    @app.post('/admin/continue/connect')
    async def continue_connect(request:Request):
        admin(request)
        if not connector.installed():raise APIError(409,'continue_not_installed','未检测到本机 VS Code 的 Continue 扩展，请先安装 Continue。')
        choices=await runtime.models()
        if not choices:raise APIError(409,'no_models','当前账号没有可用模型。')
        if auth.settings['selected_model'] not in {m['id'] for m in choices}:
            auth.settings['selected_model']=next((m['id'] for m in choices if m['id']=='gpt-6-luna'),choices[0]['id'])
        keys=auth.settings.setdefault('application_keys',{})
        keys.setdefault('continue',secrets.token_urlsafe(32))
        auth.store.save('settings',auth.settings)
        result=await asyncio.to_thread(connector.connect,keys['continue'])
        result['message']='Continue 配置已就绪。请在 Continue 模型列表选择“'+MODEL_NAME+'”。'
        return result

    @app.post('/admin/continue/undo')
    async def continue_undo(request:Request):
        admin(request)
        result=await asyncio.to_thread(connector.undo)
        auth.settings.setdefault('application_keys',{}).pop('continue',None)
        auth.store.save('settings',auth.settings)
        result['message']='已移除本工具添加的连接，并停用该连接专用密钥。'
        return result

    @app.post('/admin/continue/check')
    async def continue_check(request:Request):
        admin(request)
        info=await asyncio.to_thread(connector.status)
        if not info['configured']:raise APIError(409,'continue_not_configured',info['error'] or '请先点击一键连接。')
        entry=auth.store.load('continue-connection',{}).get('entry',{})
        if entry.get('apiKey')!=auth.settings.get('application_keys',{}).get('continue'):
            raise APIError(409,'continue_key_changed','连接专用密钥已失效，请重新连接。')
        choices=await runtime.models()
        if auth.settings['selected_model'] not in {m['id'] for m in choices}:
            raise APIError(409,'model_unavailable','请先在桥里选择当前账号可用的默认模型。')
        return {'ok':True,'model_name':MODEL_NAME,'default_model':auth.settings['selected_model'],
            'inference_tested':False,'message':'本地配置、专用密钥和账号模型目录检查通过；尚未发送模型测试。'}

    @app.get('/admin/anythingllm')
    async def anything_status(request:Request):
        admin(request)
        info=await asyncio.to_thread(anything_connector.status)
        account=auth.accounts['items'].get(auth.accounts['active'],{})
        info['bridge_ready']=bool(account.get('access_token') and 'chatgpt.tokens.use.direct' in account.get('scopes',[]))
        return info

    @app.post('/admin/anythingllm/connect')
    async def anything_connect(request:Request):
        admin(request)
        choices=await runtime.models()
        if not choices:raise APIError(409,'no_models','当前账号没有可用模型。')
        if auth.settings['selected_model'] not in {m['id'] for m in choices}:
            auth.settings['selected_model']=choices[0]['id']
        keys=auth.settings.setdefault('application_keys',{})
        key=keys.get('anythingllm') or secrets.token_urlsafe(32)
        url=await asyncio.to_thread(anything_connector.connect,key)
        keys['anythingllm']=key;auth.store.save('settings',auth.settings)
        return {'url':url,'message':'已准备连接信息。请在 AnythingLLM 中点击 Use／使用确认保存，然后回本页检查连接。'}

    @app.post('/admin/anythingllm/check')
    async def anything_check(request:Request):
        admin(request)
        info=await asyncio.to_thread(anything_connector.status)
        if info['restored']:
            return {'ok':True,'message':'AnythingLLM 原配置已恢复，连接专用密钥已停用。','inference_tested':False}
        if not info['configured']:raise APIError(409,'anythingllm_pending','请先在 AnythingLLM 中确认保存连接设置。')
        entry=auth.store.load('anythingllm-connection',{})['entry']
        if entry['settings']['GenericOpenAiKey']!=auth.settings.get('application_keys',{}).get('anythingllm'):
            raise APIError(409,'anythingllm_key_changed','专用密钥已失效，请重新连接。')
        if auth.settings['selected_model'] not in {m['id'] for m in await runtime.models()}:
            raise APIError(409,'model_unavailable','请先选择当前账号可用模型。')
        return {'ok':True,'inference_tested':False,'message':'AnythingLLM 配置、专用密钥和模型目录检查通过；未发送模型测试。'}

    @app.post('/admin/anythingllm/undo')
    async def anything_undo(request:Request):
        admin(request)
        url=await asyncio.to_thread(anything_connector.undo)
        auth.settings.setdefault('application_keys',{}).pop('anythingllm',None)
        auth.store.save('settings',auth.settings)
        return {'url':url,'message':'专用连接已停用。请在 AnythingLLM 中点击 Use／使用确认恢复原配置；取消不会恢复配置。'}

    @app.post('/admin/stop')
    async def stop(request:Request):
        admin(request)
        if runtime.in_flight:raise APIError(409,'request_active','请求处理中，请结束后再停止服务。')
        hook=getattr(app.state,'shutdown',None)
        if hook is None:raise APIError(409,'external_server','请在启动此服务的终端中停止。')
        return JSONResponse({'ok':True},background=BackgroundTask(hook))

    @app.get('/v1/models')
    @app.get('/compat/continue/v1/models')
    @app.get('/compat/anythingllm/v1/models')
    async def models_api(request:Request):
        api_auth(request)
        entries=[{'id':m['id'],'object':'model','owned_by':'chatgpt-plan'} for m in await runtime.models()]
        if request.url.path.startswith('/compat/') and auth.settings['selected_model']:
            entries.insert(0,{'id':MODEL_ALIAS,'object':'model','owned_by':'local-plan-api'})
        return {'object':'list','data':entries}

    async def inference(request,chat=False,continue_profile=False):
        api_auth(request);value=await body(request)
        ignored=[]
        if continue_profile:value,ignored=continue_request(value)
        response_headers={'X-Local-Plan-Ignored-Parameters':','.join(ignored)} if ignored else {}
        stream=value.get('stream',False)
        if not isinstance(stream,bool):raise APIError(400,'invalid_stream','stream 必须为布尔值。')
        options=value.get('stream_options') or {}
        if not isinstance(options,dict):raise APIError(400,'invalid_stream_options','stream_options 必须为对象。')
        payload=chat_to_responses(value) if chat else value
        if auth.settings.get('auto_follow') and payload.get('model') in (None,'',MODEL_ALIAS):
            await runtime.models()
        payload=runtime.normalize(payload)
        events=runtime.events(payload,request)
        try:
            first=await anext(events)
        except StopAsyncIteration as exc:
            raise APIError(502,'empty_stream','上游没有返回完成结果。') from exc
        if not stream:
            try:
                event=first
                while event.get('type')!='response.completed':event=await anext(events)
                result=event['response']
                return JSONResponse(response_to_chat(result,payload['model']) if chat else result,headers=response_headers)
            except StopAsyncIteration as exc:
                raise APIError(502,'stream_interrupted','上游未完成回复。') from exc
            finally:await events.aclose()
        converter=ChatStream(payload['model'],options.get('include_usage') is True) if chat else None
        async def forward():
            def encode(event):
                if converter:
                    return ''.join('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\n' for chunk in converter.feed(event))
                return 'event: '+event.get('type','message')+'\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n'
            try:
                yield encode(first)
                async for event in events:yield encode(event)
                if chat:yield 'data: [DONE]\n\n'
            except APIError as exc:
                yield ('data: ' if chat else 'event: error\ndata: ')+json.dumps(exc.body(),ensure_ascii=False)+'\n\n'
            finally:await events.aclose()
        return StreamingResponse(forward(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no',**response_headers})

    @app.post('/v1/responses')
    async def responses(request:Request):return await inference(request)

    @app.post('/v1/chat/completions')
    async def completions(request:Request):return await inference(request,True)

    @app.post('/compat/anythingllm/v1/chat/completions')
    @app.post('/compat/continue/v1/chat/completions')
    async def continue_completions(request:Request):return await inference(request,True,True)

    return app
