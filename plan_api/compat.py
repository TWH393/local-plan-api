"""Protocol conversion only. Never executes model-requested tools."""
import copy
import json
import time
import uuid

from .errors import APIError


def invalid(message):
    raise APIError(400,'invalid_request',message)


def continue_request(body):
    """Explicit opt-in profile: Continue adds these options unconditionally.

    The plan does not implement sampling/output caps. Report ignored names in
    a response header; never relax the normal, strict API endpoints.
    """
    result=copy.deepcopy(body);ignored=[]
    for key in ('temperature','top_p','frequency_penalty','presence_penalty','max_tokens','max_completion_tokens'):
        if key in result:
            result.pop(key);ignored.append(key)
    for key in ('stop','prediction'):
        if key in result and result[key] in (None,[],{}):
            result.pop(key)
    return result,ignored


def content(parts, assistant=False):
    if isinstance(parts,str):return parts
    if parts is None:return ''
    if not isinstance(parts,list):invalid('消息 content 必须为文本或内容列表。')
    output=[]
    for part in parts:
        if not isinstance(part,dict):invalid('消息内容项无效。')
        if part.get('type')=='text':
            if not isinstance(part.get('text'),str):invalid('文本内容无效。')
            output.append({'type':'output_text' if assistant else 'input_text','text':part['text']})
        elif part.get('type')=='image_url' and not assistant:
            image=part.get('image_url',{})
            if not isinstance(image,dict) or not isinstance(image.get('url'),str):invalid('图片 URL 无效。')
            obj={'type':'input_image','image_url':image['url']}
            if image.get('detail'):obj['detail']=image['detail']
            output.append(obj)
        else:invalid('暂不支持该消息类型：'+str(part.get('type')))
    return output


def chat_to_responses(body):
    if not isinstance(body,dict):invalid('请求必须为 JSON 对象。')
    allowed={'model','messages','stream','stream_options','tools','tool_choice','parallel_tool_calls',
             'reasoning_effort','response_format','n'}
    extra=set(body)-allowed
    if extra:invalid('套餐兼容入口暂不支持参数：'+', '.join(sorted(extra)))
    if body.get('n',1)!=1:invalid('仅支持 n=1。')
    messages=body.get('messages')
    if not isinstance(messages,list) or not messages:invalid('messages 必须为非空消息列表。')
    result={'model':body.get('model',''),'input':[]}
    for message in messages:
        if not isinstance(message,dict):invalid('消息必须为对象。')
        role=message.get('role')
        if role=='tool':
            if not isinstance(message.get('tool_call_id'),str) or not message['tool_call_id']:invalid('工具结果缺少 tool_call_id。')
            text=message.get('content','')
            if not isinstance(text,str):invalid('工具结果 content 必须为文本。')
            result['input'].append({'type':'function_call_output','call_id':message['tool_call_id'],'output':text})
        elif role in ('system','developer','user','assistant'):
            converted=content(message.get('content'),role=='assistant')
            if converted or not message.get('tool_calls'):
                result['input'].append({'role':'developer' if role=='system' else role,'content':converted})
            if message.get('tool_calls'):
                if role!='assistant' or not isinstance(message['tool_calls'],list):invalid('tool_calls 只能出现在 assistant 消息中。')
                for call in message['tool_calls']:
                    if not isinstance(call,dict) or call.get('type')!='function':invalid('仅支持 function 工具调用。')
                    fn=call.get('function',{})
                    if not isinstance(fn,dict):invalid('工具 function 必须为对象。')
                    if not call.get('id') or not fn.get('name') or not isinstance(fn.get('arguments'),str):invalid('工具调用缺少 id、name 或字符串 arguments。')
                    result['input'].append({'type':'function_call','call_id':call['id'],'name':fn['name'],'arguments':fn['arguments']})
        else:invalid('不支持的消息角色：'+str(role))
    if 'tools' in body:
        if not isinstance(body['tools'],list):invalid('tools 必须为列表。')
        result['tools']=[]
        for tool in body['tools']:
            if not isinstance(tool,dict) or tool.get('type')!='function' or not isinstance(tool.get('function'),dict):invalid('Chat Completions 入口仅支持 function 工具定义。')
            fn=tool['function']
            if not isinstance(fn.get('name'),str) or not fn['name']:invalid('工具缺少名称。')
            result['tools'].append({'type':'function',**copy.deepcopy(fn)})
    if 'tool_choice' in body:
        choice=body['tool_choice']
        if isinstance(choice,dict):
            if choice.get('type')!='function' or not choice.get('function',{}).get('name'):invalid('无效 tool_choice。')
            choice={'type':'function','name':choice['function']['name']}
        elif choice not in ('auto','none','required'):invalid('无效 tool_choice。')
        result['tool_choice']=choice
    if 'parallel_tool_calls' in body:result['parallel_tool_calls']=body['parallel_tool_calls']
    if body.get('reasoning_effort'):result['reasoning']={'effort':body['reasoning_effort']}
    if 'response_format' in body:
        fmt=body['response_format']
        if not isinstance(fmt,dict):invalid('response_format 必须为对象。')
        if fmt.get('type')=='json_schema':
            spec=fmt.get('json_schema',{})
            result['text']={'format':{'type':'json_schema',**copy.deepcopy(spec)}}
        elif fmt.get('type') in ('text','json_object'):result['text']={'format':copy.deepcopy(fmt)}
        else:invalid('不支持该 response_format。')
    return result


def usage(value):
    value=value or {}
    result={'prompt_tokens':value.get('input_tokens',0),'completion_tokens':value.get('output_tokens',0),
            'total_tokens':value.get('total_tokens',value.get('input_tokens',0)+value.get('output_tokens',0))}
    if value.get('input_tokens_details'):result['prompt_tokens_details']=value['input_tokens_details']
    if value.get('output_tokens_details'):result['completion_tokens_details']=value['output_tokens_details']
    return result


def response_to_chat(response, model):
    text=[];calls=[];refusals=[]
    for item in response.get('output',[]):
        if item.get('type')=='message':
            for part in item.get('content',[]):
                if part.get('type')=='output_text':text.append(part.get('text',''))
                elif part.get('type')=='refusal':refusals.append(part.get('refusal',''))
        elif item.get('type')=='function_call':
            calls.append({'id':item['call_id'],'type':'function','function':{
                'name':item['name'],'arguments':item.get('arguments','')}})
        elif item.get('type') not in ('reasoning',):
            raise APIError(502,'unsupported_output','上游返回了 Chat Completions 无法表示的结果，请使用 Responses 接口。')
    message={'role':'assistant','content':''.join(text) or None}
    if calls:message['tool_calls']=calls
    if refusals:message['refusal']=''.join(refusals)
    return {'id':'chatcmpl-'+response.get('id',uuid.uuid4().hex),'object':'chat.completion',
        'created':int(response.get('created_at',time.time())),'model':response.get('model',model),
        'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if calls else 'stop'}],
        'usage':usage(response.get('usage'))}


class ChatStream:
    def __init__(self,model,include_usage=False):
        self.model=model;self.include_usage=include_usage
        self.ident='chatcmpl-'+uuid.uuid4().hex;self.created=int(time.time())
        self.started=False;self.calls={};self.text='';self.refusal=''

    def chunk(self,delta,finish=None):
        return {'id':self.ident,'object':'chat.completion.chunk','created':self.created,'model':self.model,
                'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}

    def feed(self,event):
        kind=event.get('type','');chunks=[]
        if not self.started:
            response=event.get('response',{})
            if response.get('id'):self.ident='chatcmpl-'+response['id']
            self.started=True;chunks.append(self.chunk({'role':'assistant','content':''}))
        if kind=='response.output_text.delta':
            delta=event.get('delta','');self.text+=delta;chunks.append(self.chunk({'content':delta}))
        elif kind=='response.refusal.delta':
            delta=event.get('delta','');self.refusal+=delta;chunks.append(self.chunk({'refusal':delta}))
        elif kind=='response.output_item.added' and event.get('item',{}).get('type')=='function_call':
            item=event['item'];key=event['output_index']
            self.calls[key]={'index':len(self.calls),'arguments':'','call_id':item['call_id'],'name':item['name']}
            chunks.append(self.chunk({'tool_calls':[{'index':self.calls[key]['index'],'id':item['call_id'],
                'type':'function','function':{'name':item['name'],'arguments':''}}]}))
        elif kind=='response.function_call_arguments.delta':
            key=event.get('output_index')
            if key not in self.calls:raise APIError(502,'invalid_tool_stream','工具参数流缺少调用身份。')
            call=self.calls[key];delta=event.get('delta','');call['arguments']+=delta
            chunks.append(self.chunk({'tool_calls':[{'index':call['index'],'function':{'arguments':delta}}]}))
        elif kind=='response.completed':
            final=response_to_chat(event['response'],self.model)
            message=final['choices'][0]['message']
            # Some servers only return final content rather than incremental deltas.
            if not self.text and message.get('content'):chunks.append(self.chunk({'content':message['content']}))
            if not self.refusal and message.get('refusal'):chunks.append(self.chunk({'refusal':message['refusal']}))
            for call in message.get('tool_calls',[]):
                prior=next((p for p in self.calls.values() if p['call_id']==call['id']),None)
                if prior:
                    args=call['function']['arguments']
                    if not args.startswith(prior['arguments']):raise APIError(502,'invalid_tool_stream','最终工具参数与流不一致。')
                    rest=args[len(prior['arguments']):]
                    if rest:chunks.append(self.chunk({'tool_calls':[{'index':prior['index'],'function':{'arguments':rest}}]}))
                else:
                    index=len(self.calls);self.calls['final-'+call['id']]={'index':index,'call_id':call['id']}
                    chunks.append(self.chunk({'tool_calls':[{'index':index,**call}]}))
            chunks.append(self.chunk({},final['choices'][0]['finish_reason']))
            if self.include_usage:
                item=self.chunk({});item['choices']=[];item['usage']=final['usage'];chunks.append(item)
        return chunks
