"""Real localhost HTTP + official SDK, simulated upstream only (no plan calls)."""
import asyncio
import importlib.util
import json
import socket
import tempfile
import threading
import time
import unittest

import httpx
import uvicorn

from plan_api.app import create_app
from plan_api.auth import Auth
from plan_api.runtime import Runtime
from test_gateway import MemoryStore


@unittest.skipUnless(importlib.util.find_spec('openai'),'Optional SDK integration test requires openai')
class SDKTests(unittest.TestCase):
    def setUp(self):
        from openai import OpenAI
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
        port=sock.getsockname()[1]
        self.auth=Auth(MemoryStore(self.temp.name),f'http://127.0.0.1:{port}')
        self.auth.accounts={'active':'a','items':{'a':{'access_token':'fake-token','scopes':['chatgpt.tokens.use.direct'],'expires_at':time.time()+3600}}}
        self.seen=[];self.finished=False
        owner=self
        class SlowStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                events=[{'type':'response.created','response':{'id':'r'}},
                    {'type':'response.output_text.delta','delta':'Hi'},
                    {'type':'response.output_text.delta','delta':' there'},
                    {'type':'response.completed','response':{'id':'r','object':'response','status':'completed',
                        'output':[{'id':'msg','type':'message','role':'assistant','status':'completed',
                                   'content':[{'type':'output_text','text':'Hi there','annotations':[]}]}],
                        'usage':{'input_tokens':4,'output_tokens':2,'total_tokens':6}}}]
                for i,event in enumerate(events):
                    if i:await asyncio.sleep(.15)
                    if i==3:owner.finished=True
                    yield ('data: '+json.dumps(event)+'\n\n').encode()
        def upstream(request):
            if request.url.path.endswith('/models'):return httpx.Response(200,json={'models':[{'slug':'m','visibility':'list'}]})
            self.seen.append(json.loads(request.content))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=SlowStream())
        runtime=Runtime(self.auth,lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(upstream),**kw))
        self.server=uvicorn.Server(uvicorn.Config(create_app(port=port,auth=self.auth,runtime=runtime),access_log=False,log_level='error'))
        self.thread=threading.Thread(target=lambda:self.server.run(sockets=[sock]),daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        for _ in range(100):
            if self.server.started:break
            time.sleep(.02)
        self.assertTrue(self.server.started)
        self.client=OpenAI(api_key=self.auth.settings['api_key'],base_url=f'http://127.0.0.1:{port}/v1',
            max_retries=0,http_client=httpx.Client(trust_env=False))
        self.addCleanup(self.client.close)

    def stop(self):
        self.server.should_exit=True;self.thread.join(timeout=5)

    def test_official_sdk_chat_models_and_incremental_stream(self):
        self.assertEqual(self.client.models.list().data[0].id,'m')
        stream=self.client.chat.completions.create(model='m',messages=[{'role':'user','content':'hi'}],stream=True)
        first=next(stream)
        self.assertEqual(first.choices[0].delta.role,'assistant')
        self.assertFalse(self.finished,'First chunk was buffered until completion')
        self.assertEqual(''.join(chunk.choices[0].delta.content or '' for chunk in stream),'Hi there')
        self.assertEqual(len(self.seen),1)

    def test_official_sdk_responses_and_nonstream_chat(self):
        result=self.client.responses.create(model='m',input='hi')
        self.assertEqual(result.output_text,'Hi there')
        result=self.client.chat.completions.create(model='m',messages=[{'role':'user','content':'hi'}])
        self.assertEqual(result.choices[0].message.content,'Hi there')
