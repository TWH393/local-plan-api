import asyncio
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

import httpx
from fastapi.testclient import TestClient

from plan_api.app import create_app
from plan_api.auth import Auth
from plan_api.errors import APIError
from plan_api.runtime import Runtime
from plan_api.storage import Store
from plan_api.continue_connector import ContinueConnector,MODEL_ALIAS,MODEL_NAME


class MemoryStore:
    def __init__(self,path):self.path=Path(path);self.data={}
    def load(self,key,default):return copy.deepcopy(self.data.get(key,default))
    def save(self,key,value):self.data[key]=copy.deepcopy(value)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=MemoryStore(self.temp.name)
        self.auth=Auth(self.store,'http://127.0.0.1:24664')
        self.auth.accounts={'active':'test','items':{'test':{'client_id':'client','subject':'subject',
            'access_token':'fake-oauth-secret','scopes':['chatgpt.tokens.use.direct'],'expires_at':time.time()+3600}}}
        self.seen=[];self.mode='normal'
        def mock(request):
            self.seen.append(request)
            if request.url.path.endswith('/models'):
                return httpx.Response(200,json={'models':[{'slug':'test-model','visibility':'list','display_name':'Test Model'},
                    {'slug':'hidden','visibility':'hidden'}]})
            if self.mode=='http_error':return httpx.Response(429,json={'error':{'code':'subscription_sharing_usage_limit_exceeded'}})
            events=[{'type':'response.created','response':{'id':'r1'}},
                {'type':'response.output_text.delta','delta':'Hello'},
                {'type':'response.output_text.delta','delta':' world'},
                {'type':'response.output_item.done','output_index':0,'item':{'type':'message','content':[{'type':'output_text','text':'Hello world'}]}}]
            if self.mode=='normal':events+=[{'type':'response.completed','response':{'id':'r1','model':'test-model',
                'output':[],'usage':{'input_tokens':10,'output_tokens':2,'total_tokens':12}}}]
            elif self.mode=='late_error':events+=[{'type':'response.failed','response':{'error':{'code':'subscription_sharing_usage_limit_exceeded'}}}]
            return httpx.Response(200,headers={'content-type':'text/event-stream'},
                content=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode())
        factory=lambda **kwargs:httpx.AsyncClient(transport=httpx.MockTransport(mock),**kwargs)
        self.runtime=Runtime(self.auth,factory)
        home=Path(self.temp.name)/'home'
        (home/'.vscode/extensions/continue.continue-2.0').mkdir(parents=True)
        self.connector=ContinueConnector(self.store,'http://127.0.0.1:24664',home=home,config_dir=home/'.continue')
        self.client=TestClient(create_app(auth=self.auth,runtime=self.runtime,connector=self.connector),
            base_url='http://127.0.0.1:24664',client=('127.0.0.1',9999))
        self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer '+self.auth.settings['api_key']}

    def post(self,path,body):return self.client.post(path,headers=self.headers,json=body)

    def test_auth_host_origin_and_admin_guards(self):
        self.assertEqual(self.client.get('/v1/models').status_code,401)
        self.assertEqual(self.client.get('/admin/status').status_code,401)
        self.assertEqual(self.client.get('/health',headers={'Host':'evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/health',headers={'Origin':'https://evil.example'}).status_code,403)
        health=self.client.get('/health').json();self.assertNotIn('api_key',health)
        self.client.get('/')
        status=self.client.get('/admin/status').json()
        self.assertIn('api_key',status);self.assertNotIn('fake-oauth-secret',json.dumps(status))

    def test_models_and_nonstream_responses(self):
        catalog=self.client.get('/v1/models',headers=self.headers).json()
        self.assertEqual([m['id'] for m in catalog['data']],['test-model'])
        r=self.post('/v1/responses',{'model':'test-model','input':'hi'})
        self.assertEqual(r.status_code,200,r.text)

        self.assertEqual(r.json()['output'][0]['content'][0]['text'],'Hello world')
        body=json.loads(self.seen[-1].content)
        self.assertFalse(body['store']);self.assertTrue(body['stream'])
        self.assertEqual(self.runtime.stats()['completed'],1)

    def test_usage_history_protected_paginated_and_unreported(self):
        self.assertEqual(self.client.get('/admin/usage').status_code,401)
        self.client.get('/')
        for _ in range(22):self.runtime.record('test-model','completed',{'input_tokens':10,'output_tokens':2})
        self.runtime.record('test-model','cancelled',{})
        r=self.client.get('/admin/usage?period=all').json()
        self.assertEqual(r['summary']['total_tokens'],264)
        self.assertEqual(r['summary']['requests'],23)
        self.assertEqual(r['summary']['unreported'],1)
        self.assertEqual(len(r['items']),20)
        self.assertEqual(r['items'][0]['usage_reported'],0)
        self.assertEqual(len(self.client.get('/admin/usage?period=all&page=2').json()['items']),3)
        self.assertNotIn('fake-oauth-secret',json.dumps(r))
        for query in ('page=0','page=abc','period=bad'):
            self.assertEqual(self.client.get('/admin/usage?'+query).status_code,400)

    def test_existing_usage_database_migration_preserves_records(self):
        import sqlite3
        other=Path(self.temp.name)/'old';other.mkdir()
        from contextlib import closing
        with closing(sqlite3.connect(other/'usage.sqlite3')) as c, c:
            c.execute('CREATE TABLE requests(at REAL,model TEXT,status TEXT,input_tokens INTEGER,output_tokens INTEGER)')
            c.execute('INSERT INTO requests VALUES(?,?,?,?,?)',(time.time(),'legacy','completed',17,3))
        old_auth=Auth(MemoryStore(other),'http://127.0.0.1:24664')
        runtime=Runtime(old_auth)
        history=runtime.usage_history('all')
        self.assertEqual(history['summary']['total_tokens'],20)
        self.assertEqual(history['summary']['legacy'],1)
        self.assertIsNone(history['items'][0]['usage_reported'])

    def test_reasoning_default_persists_and_reaches_upstream(self):
        self.client.get('/')
        r=self.client.post('/admin/model',json={'model':'test-model','reasoning_effort':'high'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.store.data['settings']['reasoning_effort'],'high')
        self.assertEqual(self.client.get('/admin/status').json()['reasoning_effort'],'high')
        r=self.post('/compat/continue/v1/chat/completions',{'model':MODEL_ALIAS,'messages':[{'role':'user','content':'hi'}]})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(json.loads(self.seen[-1].content)['reasoning']['effort'],'high')
        r=self.post('/v1/responses',{'model':'test-model','input':'hi','reasoning':{'effort':'low','summary':'auto'}})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(json.loads(self.seen[-1].content)['reasoning'],{'effort':'low','summary':'auto'})
        r=self.client.post('/admin/model',json={'model':'test-model','reasoning_effort':'invalid'})
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.auth.settings['reasoning_effort'],'high')
        self.client.post('/admin/model',json={'model':'test-model','reasoning_effort':''})
        payload=self.runtime.normalize({'model':'test-model','input':'hi'})
        self.assertNotIn('reasoning',payload)

    def test_chat_nonstream_and_requested_model_not_overridden(self):
        self.auth.settings['selected_model']='bad-default'
        r=self.post('/v1/chat/completions',{'model':'test-model','messages':[{'role':'user','content':'hi'}]})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['choices'][0]['message']['content'],'Hello world')
        self.assertEqual(r.json()['usage']['prompt_tokens'],10)

    def test_stream_responses_and_chat(self):
        response=self.post('/v1/responses',{'model':'test-model','input':'hi','stream':True})
        self.assertIn('event: response.output_text.delta',response.text)
        response=self.post('/v1/chat/completions',{'model':'test-model','stream':True,
            'stream_options':{'include_usage':True},'messages':[{'role':'user','content':'hi'}]})
        self.assertIn('data: [DONE]',response.text)
        chunks=[json.loads(s[6:]) for s in response.text.splitlines() if s.startswith('data: {')]
        self.assertEqual(''.join(c['choices'][0]['delta'].get('content','') for c in chunks if c['choices']),'Hello world')
        self.assertEqual(chunks[-1]['usage']['total_tokens'],12)

    def test_midstream_limit_is_error_without_success(self):
        self.mode='late_error'
        r=self.post('/v1/chat/completions',{'model':'test-model','stream':True,'messages':[{'role':'user','content':'hi'}]})
        self.assertIn('subscription_sharing_usage_limit_exceeded',r.text)
        self.assertNotIn('[DONE]',r.text)
        self.assertNotIn('"finish_reason": "stop"',r.text)
        self.assertEqual(self.runtime.stats()['completed'],0)

    def test_interrupted_nonstream_is_not_partial_success(self):
        self.mode='interrupted'
        r=self.post('/v1/chat/completions',{'model':'test-model','messages':[{'role':'user','content':'hi'}]})
        self.assertEqual(r.status_code,502)
        self.assertEqual(r.json()['error']['code'],'stream_interrupted')

    def test_unavailable_model_and_unknown_params_before_inference(self):
        r=self.post('/v1/responses',{'model':'missing','input':'hi'})
        self.assertEqual(r.status_code,400)
        r=self.post('/v1/chat/completions',{'model':'test-model','max_tokens':10,'messages':[{'role':'user','content':'hi'}]})
        self.assertEqual(r.status_code,400)
        self.assertFalse(any(r.url.path.endswith('/responses') for r in self.seen))

    def test_http_limit_before_stream_starts(self):
        self.mode='http_error'
        r=self.post('/v1/responses',{'model':'test-model','input':'hi','stream':True})
        self.assertEqual(r.status_code,429)
        self.assertEqual(len([r for r in self.seen if r.url.path.endswith('/responses')]),1)

    def test_missing_plan_scope_never_calls_upstream(self):
        self.auth.accounts['items']['test']['scopes']=[]
        r=self.post('/v1/responses',{'model':'test-model','input':'hi'})
        self.assertEqual(r.status_code,403);self.assertEqual(self.seen,[])

    def test_continue_default_parameters_work_only_on_opt_in_profile(self):
        value={'model':'test-model','messages':[{'role':'user','content':'hi'}],
            'max_tokens':4096,'temperature':0.5,'top_p':1,'stop':[],'prediction':None,
            'frequency_penalty':0,'presence_penalty':0,'stream':True,'stream_options':{'include_usage':True}}
        r=self.post('/v1/chat/completions',value)
        self.assertEqual(r.status_code,400)
        r=self.post('/compat/continue/v1/chat/completions',value)
        self.assertEqual(r.status_code,200,r.text)
        self.assertIn('max_tokens',r.headers['x-local-plan-ignored-parameters'])
        self.assertIn('data: [DONE]',r.text)
        upstream=json.loads(self.seen[-1].content)
        self.assertNotIn('max_tokens',upstream);self.assertNotIn('temperature',upstream)
        self.assertEqual(upstream['model'],'test-model')

    def test_continue_nonempty_stop_still_fails_explicitly(self):
        r=self.post('/compat/continue/v1/chat/completions',{'model':'test-model',
            'messages':[{'role':'user','content':'hi'}],'stop':['stop here']})
        self.assertEqual(r.status_code,400)

    def test_key_rotation_invalidates_old_key(self):
        self.client.get('/')
        self.assertEqual(self.client.post('/admin/rotate-key').status_code,200)
        self.assertEqual(self.client.get('/v1/models',headers=self.headers).status_code,401)

    def test_one_click_connect_default_model_alias_and_scoped_key(self):
        self.client.get('/')
        r=self.client.post('/admin/continue/connect',json={})
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(self.connector.status()['configured'])
        self.assertEqual(self.auth.settings['selected_model'],'test-model')
        check=self.client.post('/admin/continue/check',json={})
        self.assertEqual(check.status_code,200,check.text)
        self.assertFalse(check.json()['inference_tested'])
        self.assertFalse(any(r.url.path.endswith('/responses') for r in self.seen))
        key=self.auth.settings['application_keys']['continue']
        headers={'Authorization':'Bearer '+key}
        self.assertNotEqual(key,self.auth.settings['api_key'])
        self.assertEqual(self.client.get('/v1/models',headers=headers).status_code,401)
        result=self.client.post('/compat/continue/v1/chat/completions',headers=headers,json={
            'model':MODEL_ALIAS,'messages':[{'role':'user','content':'hi'}]})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(json.loads(self.seen[-1].content)['model'],'test-model')
        self.client.post('/admin/continue/undo',json={})
        self.assertFalse(self.connector.status()['configured'])
        self.assertEqual(self.client.get('/compat/continue/v1/models',headers=headers).status_code,401)

    def test_one_click_connect_requires_authorized_account(self):
        self.client.get('/')
        self.auth.accounts['items']['test']['scopes']=[]
        result=self.client.post('/admin/continue/connect',json={})
        self.assertEqual(result.status_code,403)
        self.assertFalse(self.connector.path.exists())

    def test_model_alias_tracks_default_without_rewriting_config(self):
        self.auth.settings['selected_model']='one'
        self.assertEqual(self.runtime.normalize({'model':MODEL_ALIAS,'input':'hi'})['model'],'one')
        self.auth.settings['selected_model']='two'
        self.assertEqual(self.runtime.normalize({'model':MODEL_ALIAS,'input':'hi'})['model'],'two')

    def test_dpapi_disk_roundtrip_and_no_plaintext(self):
        store=Store(Path(self.temp.name)/'disk')
        store.save('test',{'token':'not-for-logs'})
        self.assertEqual(store.load('test',{}),{'token':'not-for-logs'})
        self.assertNotIn(b'not-for-logs',(store.path/'test.bin').read_bytes())


class DisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_request_closes_stream_without_success(self):
        with tempfile.TemporaryDirectory() as temp:
            auth=Auth(MemoryStore(temp),'http://127.0.0.1:24664')
            auth.accounts={'active':'a','items':{'a':{'access_token':'fake','expires_at':time.time()+3600,'scopes':['chatgpt.tokens.use.direct']}}}
            def mock(request):
                if request.url.path.endswith('/models'):return httpx.Response(200,json={'models':[{'slug':'m','visibility':'list'}]})
                return httpx.Response(200,text='data: {"type":"response.created"}\n\n')
            runtime=Runtime(auth,lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(mock),**kw))
            class Request:
                async def is_disconnected(self):return True
            with self.assertRaises(asyncio.CancelledError):
                async for _ in runtime.events({'model':'m','input':'hi'},Request()):pass
            self.assertEqual(runtime.in_flight,0);self.assertEqual(runtime.stats()['completed'],0)
