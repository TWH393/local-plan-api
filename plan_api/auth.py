import asyncio
import base64
import hashlib
import secrets
import time
import uuid
from urllib.parse import urlencode, urlparse

import httpx
import jwt

from .errors import APIError

API = 'https://api.openai.com/v1'
ISSUER = 'https://auth.openai.com'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
APP_NAME = 'Local Plan API'


class Auth:
    def __init__(self, store, origin, client_factory=httpx.AsyncClient):
        self.store, self.origin, self.clients = store, origin, client_factory
        self.settings = store.load('settings', {'host_id': 'urn:uuid:' + str(uuid.uuid4()),
            'api_key': secrets.token_urlsafe(32), 'selected_model': ''})
        self.accounts = store.load('accounts', {'active': '', 'items': {}})
        self.store.save('settings', self.settings)
        self.pending, self.metadata = {}, {}
        self.refresh_lock = asyncio.Lock()

    async def oidc(self):
        if not self.metadata:
            async with self.clients(timeout=25, follow_redirects=False) as c:
                r = await c.get(ISSUER + '/.well-known/openid-configuration')
                r.raise_for_status()
            data = r.json()
            if data.get('issuer') != ISSUER:
                raise APIError(502, 'invalid_issuer', '登录服务身份验证失败。')
            for name in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
                parsed = urlparse(data.get(name, ''))
                if parsed.scheme != 'https' or parsed.netloc != 'auth.openai.com' or parsed.username:
                    raise APIError(502, 'invalid_metadata', '登录服务地址验证失败。')
            self.metadata = data
        return self.metadata

    def account(self, identity=None, require_plan=True):
        account = self.accounts['items'].get(identity or self.accounts['active'])
        if not account or not account.get('access_token'):
            raise APIError(401, 'sign_in_required', '请先在 Local Plan API 页面登录自己的 ChatGPT 账号。')
        if require_plan and 'chatgpt.tokens.use.direct' not in account.get('scopes', []):
            raise APIError(403, 'plan_permission_missing', '当前登录未授权使用 ChatGPT 套餐。')
        return account

    async def start(self, session, new=False):
        meta = await self.oidc()
        account = None if new else self.accounts['items'].get(self.accounts['active'])
        client_id = account['client_id'] if account else 'dynamic_agent_client'
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        self.pending = {k:v for k,v in self.pending.items() if v['expires'] > time.time()}
        self.pending[state] = {'nonce':nonce, 'verifier':verifier, 'client_id':client_id,
            'identity':self.accounts['active'] if account else '', 'session':session, 'expires':time.time()+600}
        args = {'client_id':client_id, 'ext_agent_host_id':self.settings['host_id'],
            'response_type':'code', 'redirect_uri':self.origin+'/auth/callback', 'resource':API,
            'scope':SCOPES, 'state':state, 'nonce':nonce, 'code_challenge_method':'S256',
            'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
        if account and account.get('id_token'):
            args['id_token_hint'] = account['id_token']
        elif not account:
            args['agent_name_hint'] = APP_NAME
        return meta['authorization_endpoint'] + '?' + urlencode(args)

    async def callback(self, query, session):
        tx = self.pending.pop(query.get('state',''), None)
        if not tx or tx['expires'] < time.time() or not secrets.compare_digest(tx['session'], session):
            raise APIError(400, 'invalid_state', '登录请求已过期或校验失败，请重新登录。')
        if query.get('error'):
            raise APIError(400, 'authorization_declined', '未完成授权，请在需要时重新登录。')
        client_id = query.get('client_id') or tx['client_id']
        if (not client_id or client_id == 'dynamic_agent_client' or
                (tx['client_id'] != 'dynamic_agent_client' and tx['client_id'] != client_id)):
            raise APIError(400, 'invalid_client', '授权返回的客户端身份无效。')
        if not query.get('code'):
            raise APIError(400, 'missing_code', '授权没有返回登录码。')
        meta = await self.oidc()
        async with self.clients(timeout=30, follow_redirects=False) as c:
            r = await c.post(meta['token_endpoint'], data={'grant_type':'authorization_code',
                'client_id':client_id,'code':query['code'],'code_verifier':tx['verifier'],
                'redirect_uri':self.origin+'/auth/callback','resource':API})
            if r.status_code != 200:
                raise APIError(401, 'code_exchange_failed', '授权交换失败，请重新登录。')
            value = r.json()
            keys = await c.get(meta['jwks_uri']);keys.raise_for_status()
        try:
            header = jwt.get_unverified_header(value['id_token'])
            if header.get('alg') not in ('RS256','ES256'):
                raise ValueError('Unsupported algorithm')
            key_data = next(k for k in keys.json()['keys'] if k.get('kid') == header.get('kid'))
            claims = jwt.decode(value['id_token'], jwt.PyJWK.from_dict(key_data).key,
                algorithms=[header['alg']], audience=client_id, issuer=ISSUER,
                options={'require':['iss','aud','exp','sub','nonce']})
            if not secrets.compare_digest(claims['nonce'], tx['nonce']):
                raise ValueError('Nonce mismatch')
            if tx['identity'] and self.accounts['items'][tx['identity']]['subject'] != claims['sub']:
                raise ValueError('Account mismatch')
            if not value.get('access_token') or value.get('token_type','').lower() != 'bearer':
                raise ValueError('Missing credential')
        except Exception as exc:
            raise APIError(400, 'invalid_id_token', '登录身份验证失败，未保存凭据。') from exc
        identity = hashlib.sha256((client_id+':'+claims['sub']).encode()).hexdigest()
        self.accounts['items'][identity] = {'client_id':client_id,'subject':claims['sub'],
            'email':claims.get('email',''), 'access_token':value['access_token'],
            'refresh_token':value.get('refresh_token'), 'id_token':value['id_token'],
            'scopes':value.get('scope','').split(), 'expires_at':time.time()+int(value.get('expires_in',3600))}
        self.accounts['active'] = identity
        self.settings['selected_model'] = ''
        self.settings['auto_follow'] = False
        self.settings.pop('model_update',None)
        self.store.save('accounts',self.accounts);self.store.save('settings',self.settings)

    async def token(self, identity):
        async with self.refresh_lock:
            account = self.account(identity)
            if account.get('expires_at',0) > time.time()+90:
                return account['access_token']
            if not account.get('refresh_token'):
                raise APIError(401, 'sign_in_required', '登录已过期，请重新登录。')
            meta = await self.oidc()
            async with self.clients(timeout=30, follow_redirects=False) as c:
                r = await c.post(meta['token_endpoint'],data={'grant_type':'refresh_token',
                    'client_id':account['client_id'],'refresh_token':account['refresh_token'],'resource':API})
            if r.status_code != 200:
                if r.status_code in (400,401,403):
                    for name in ('access_token','refresh_token','id_token'):account.pop(name,None)
                    self.store.save('accounts',self.accounts)
                raise APIError(401 if r.status_code in (400,401,403) else 502,'refresh_failed','登录刷新失败，请稍后重试或重新登录。')
            value=r.json()
            if not value.get('access_token') or not value.get('refresh_token'):
                raise APIError(502,'invalid_refresh','刷新返回不完整，未覆盖原凭据。')
            account.update(access_token=value['access_token'],refresh_token=value['refresh_token'],
                expires_at=time.time()+int(value.get('expires_in',3600)),
                scopes=value.get('scope',' '.join(account['scopes'])).split())
            if value.get('id_token'):account['id_token']=value['id_token']
            self.store.save('accounts',self.accounts)
            self.account(identity)
            return account['access_token']

    async def disconnect(self):
        identity=self.accounts['active'];account=self.accounts['items'].get(identity)
        revoked=False
        if account:
            try:
                meta=await self.oidc();endpoint=meta.get('revocation_endpoint','')
                if endpoint.startswith(ISSUER+'/') and account.get('refresh_token'):
                    async with self.clients(timeout=20,follow_redirects=False) as c:
                        r=await c.post(endpoint,data={'token':account['refresh_token'],
                            'token_type_hint':'refresh_token','client_id':account['client_id']})
                    revoked=r.status_code==200
            except (httpx.HTTPError,APIError):pass
            self.accounts['items'].pop(identity,None)
        self.accounts['active']='';self.settings['selected_model']=''
        self.settings['auto_follow']=False;self.settings.pop('model_update',None)
        self.store.save('accounts',self.accounts);self.store.save('settings',self.settings)
        return revoked
