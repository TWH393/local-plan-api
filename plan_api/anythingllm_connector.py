"""AnythingLLM Desktop integration through its official set-provider deep link.
Only reads the app's provider environment; writes encrypted bridge-owned state.
"""
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlencode

from .errors import APIError
from .continue_connector import MODEL_ALIAS

FIELDS={'GenericOpenAiBasePath':'GENERIC_OPEN_AI_BASE_PATH',
        'GenericOpenAiModelPref':'GENERIC_OPEN_AI_MODEL_PREF',
        'GenericOpenAiKey':'GENERIC_OPEN_AI_API_KEY',
        'GenericOpenAiTokenLimit':'GENERIC_OPEN_AI_MODEL_TOKEN_LIMIT',
        'GenericOpenAiMaxTokens':'GENERIC_OPEN_AI_MAX_TOKENS'}

class AnythingConnector:
    def __init__(self,store,origin,folder=None):
        self.store,self.origin=store,origin
        self.folder=Path(folder) if folder else Path(os.environ.get('APPDATA',Path.home()/'AppData/Roaming'))/'anythingllm-desktop/storage'
        self.path=self.folder/'.env';self.lock=threading.Lock()

    def read(self):
        if self.path.is_symlink() or not self.path.is_file():
            raise APIError(409,'anythingllm_missing','请先安装并启动 AnythingLLM 桌面版，完成初始设置。')
        raw=self.path.read_text(encoding='utf-8-sig')
        if len(raw)>2*1024*1024:raise APIError(409,'anythingllm_config','AnythingLLM 配置过大，未读取。')
        env={}
        for line in raw.splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line:continue
            k,v=line.split('=',1);v=v.strip()
            if len(v)>=2 and v[0]==v[-1] and v[0] in ('"',"'"):
                if v[0]=='"':
                    try:v=json.loads(v)
                    except ValueError:v=v[1:-1]
                else:v=v[1:-1]
            env[k.strip()]=v
        return {'provider':env.get('LLM_PROVIDER','default'),
                'settings':{k:env.get(v,'') for k,v in FIELDS.items()}}

    @staticmethod
    def same(current,target):
        return current['provider']==target['provider'] and (target['provider']!='generic-openai' or all(current['settings'].get(k,'')==v for k,v in target['settings'].items()))

    @staticmethod
    def link(config):
        args={'type':'llm','provider':config['provider']}
        if config['provider']=='generic-openai':args.update(config['settings'])
        return 'anythingllm://set-provider?'+urlencode(args)

    def status(self):
        with self.lock:
            state=self.store.load('anythingllm-connection',{})
            result={'installed':self.path.is_file(),'configured':False,'prepared':bool(state),
                    'can_undo':bool(state),'restoring':state.get('phase')=='restoring','restored':False,'error':''}
            if not result['installed']:return result
            try:
                actual=self.read()
                result['configured']=bool(state and self.same(actual,state['entry']) and not result['restoring'])
                result['restored']=bool(result['restoring'] and self.same(actual,state['original']))
                if result['restored']:result['can_undo']=False
                result['manual_bridge']=actual['provider']=='generic-openai' and actual['settings']['GenericOpenAiBasePath'] in (self.origin+'/compat/continue/v1',self.origin+'/compat/anythingllm/v1')
            except (APIError,OSError):result['error']='无法读取 AnythingLLM 配置，请检查应用是否完成初始设置。'
            return result

    def connect(self,key):
        with self.lock:
            current=self.read();old=self.store.load('anythingllm-connection',{})
            if old and old.get('phase')!='restoring' and not self.same(current,old['entry']) and not self.same(current,old['original']):
                raise APIError(409,'anythingllm_changed','AnythingLLM 设置已手动修改，请先恢复原配置或保留当前设置。')
            original=old['original'] if old and old.get('phase')!='restoring' else current
            entry={'provider':'generic-openai','settings':{'GenericOpenAiBasePath':self.origin+'/compat/anythingllm/v1',
                'GenericOpenAiModelPref':MODEL_ALIAS,'GenericOpenAiKey':key,
                'GenericOpenAiTokenLimit':current['settings'].get('GenericOpenAiTokenLimit') or '10000',
                'GenericOpenAiMaxTokens':''}}
            self.store.save('anythingllm-connection',{'original':original,'entry':entry,'phase':'connecting'})
            return self.link(entry)

    def undo(self):
        with self.lock:
            state=self.store.load('anythingllm-connection',{})
            if not state:raise APIError(409,'anythingllm_not_configured','没有可恢复的 AnythingLLM 连接。')
            actual=self.read()
            if not self.same(actual,state['entry']) and not self.same(actual,state['original']):
                raise APIError(409,'anythingllm_changed','AnythingLLM 设置已手动修改，未覆盖当前设置。')
            state['phase']='restoring';self.store.save('anythingllm-connection',state)
            return self.link(state['original'])
