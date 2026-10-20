import asyncio
import json
import tempfile
import time
import unittest
from urllib.parse import parse_qs,urlparse

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt

from plan_api.auth import API,ISSUER,Auth
from plan_api.errors import APIError
from test_gateway import MemoryStore


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        self.public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()));self.public['kid']='test'
        self.nonce='';self.calls=[];self.bad_nonce=False
        def mock(request):
            self.calls.append(request)
            if request.url.path.endswith('openid-configuration'):
                return httpx.Response(200,json={'issuer':ISSUER,'authorization_endpoint':ISSUER+'/authorize',
                    'token_endpoint':ISSUER+'/token','jwks_uri':ISSUER+'/jwks'})
            if request.url.path=='/jwks':return httpx.Response(200,json={'keys':[self.public]})
            data=parse_qs(request.content.decode())
            if data.get('grant_type')==['refresh_token']:
                return httpx.Response(200,json={'access_token':'refreshed','refresh_token':'rotated','expires_in':3600})
            token=jwt.encode({'iss':ISSUER,'aud':'issued-client','sub':'subject','exp':int(time.time())+3600,
                'nonce':'wrong' if self.bad_nonce else self.nonce,'email':'test@example.invalid'},self.key,
                algorithm='RS256',headers={'kid':'test'})
            return httpx.Response(200,json={'id_token':token,'access_token':'fake-access','refresh_token':'fake-refresh',
                'scope':'openid chatgpt.tokens.use.direct offline_access','token_type':'Bearer','expires_in':3600})
        self.auth=Auth(MemoryStore(self.temp.name),'http://127.0.0.1:24664',
            lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(mock),**kw))

    async def begin(self):
        url=await self.auth.start('session');query=parse_qs(urlparse(url).query)
        self.nonce=query['nonce'][0]
        return query

    async def test_pkce_stable_host_and_registered_client(self):
        query=await self.begin()
        self.assertEqual(query['client_id'],['dynamic_agent_client'])
        self.assertEqual(query['resource'],[API]);self.assertEqual(query['code_challenge_method'],['S256'])
        await self.auth.callback({'state':query['state'][0],'code':'fake-code','client_id':'issued-client'},'session')
        account=self.auth.account()
        self.assertEqual(account['client_id'],'issued-client')
        returning=parse_qs(urlparse(await self.auth.start('session')).query)
        self.assertEqual(returning['client_id'],['issued-client'])
        self.assertEqual(returning['ext_agent_host_id'],query['ext_agent_host_id'])
        self.assertNotIn('agent_name_hint',returning)

    async def test_wrong_session_or_state_no_exchange(self):
        query=await self.begin();before=len(self.calls)
        with self.assertRaises(APIError):await self.auth.callback({'state':query['state'][0],'code':'fake','client_id':'issued-client'},'wrong-session')
        self.assertEqual(len(self.calls),before)

    async def test_replayed_callback_rejected(self):
        query=await self.begin();values={'state':query['state'][0],'code':'fake-code','client_id':'issued-client'}
        await self.auth.callback(values,'session')
        with self.assertRaises(APIError):await self.auth.callback(values,'session')

    async def test_invalid_nonce_never_saved(self):
        query=await self.begin();self.bad_nonce=True
        with self.assertRaises(APIError):await self.auth.callback({'state':query['state'][0],'code':'fake','client_id':'issued-client'},'session')
        self.assertEqual(self.auth.accounts['items'],{})

    async def test_missing_issued_client_and_declined_consent(self):
        query=await self.begin();before=len(self.calls)
        with self.assertRaises(APIError):await self.auth.callback({'state':query['state'][0],'code':'fake'},'session')
        query=await self.begin()
        with self.assertRaises(APIError):await self.auth.callback({'state':query['state'][0],'error':'access_denied'},'session')
        self.assertEqual(len(self.calls),before)

    async def test_concurrent_refresh_serialized_and_rotation_persisted(self):
        self.auth.accounts={'active':'one','items':{'one':{'client_id':'issued-client','access_token':'expired',
            'refresh_token':'refresh','scopes':['chatgpt.tokens.use.direct'],'expires_at':0}}}
        first,second=await asyncio.gather(self.auth.token('one'),self.auth.token('one'))
        self.assertEqual((first,second),('refreshed','refreshed'))
        refreshes=[r for r in self.calls if r.url.path=='/token']
        self.assertEqual(len(refreshes),1)
        self.assertEqual(self.auth.store.data['accounts']['items']['one']['refresh_token'],'rotated')

    async def test_untrusted_oidc_host_rejected(self):
        def mock(request):return httpx.Response(200,json={'issuer':ISSUER,'authorization_endpoint':ISSUER+'.evil.example/auth',
            'token_endpoint':ISSUER+'/token','jwks_uri':ISSUER+'/jwks'})
        self.auth.clients=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(mock),**kw)
        with self.assertRaises(APIError):await self.auth.oidc()
