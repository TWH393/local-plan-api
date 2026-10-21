"""DeepSeek Harness desktop profile and credential-reference integration."""
import base64
from contextlib import contextmanager
import copy
import io
import os
from pathlib import Path
import tempfile
import threading

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from .continue_connector import MODEL_ALIAS, MODEL_NAME
from .errors import APIError

ROUTE='local-plan-api'
KEY_REF='LOCAL_PLAN_API_HARNESS_KEY'


class HarnessConnector:
    def __init__(self, store, origin, home=None):
        self.store,self.origin=store,origin
        self.home=Path(home) if home else Path(os.environ.get('DSH_HOME',str(Path.home()/'.dsh')))
        self.profile=self.home/'profiles/desktop'
        self.patch=self.profile/'cordis.patch.yml'
        self.credentials=self.home/'.credentials.yaml'
        self.lock=threading.Lock()

    def read(self,path,default):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise APIError(409,'harness_config','Harness 配置路径不是普通文件，未修改。')
        if path.exists() and path.stat().st_size>4*1024*1024:
            raise APIError(409,'harness_config','Harness 配置过大，未修改。')
        raw=path.read_bytes() if path.exists() else b''
        yaml=YAML();yaml.preserve_quotes=True;yaml.width=4096
        try:doc=yaml.load(raw.decode('utf-8-sig')) if raw.strip() else copy.deepcopy(default)
        except (YAMLError,UnicodeError) as exc:
            raise APIError(409,'harness_config','Harness YAML 配置格式有误，未覆盖。') from exc
        return raw,doc,yaml

    def documents(self):
        patch=self.read(self.patch,CommentedSeq())
        creds=self.read(self.credentials,CommentedMap(version=1))
        if not isinstance(patch[1],list) or any(not isinstance(x,dict) for x in patch[1]):
            raise APIError(409,'harness_schema','Harness profile 配置格式暂不支持。')
        if not isinstance(creds[1],dict) or creds[1].get('version')!=1 or not isinstance(creds[1].get('refs',{}),dict):
            raise APIError(409,'harness_schema','Harness 凭据文件格式暂不支持，未覆盖。')
        rows=[r for r in patch[1] if r.get('id')=='llm-pi-ai']
        if len(rows)>1:raise APIError(409,'harness_schema','Harness 有多条 llm-pi-ai 配置，未修改。')
        row=rows[0] if rows else None
        if row is not None and (not isinstance(row.get('config',{}),dict) or
            not isinstance(row.get('config',{}).get('providers',{}),dict)):
            raise APIError(409,'harness_schema','Harness providers 配置格式暂不支持。')
        return patch,creds,row

    def entry(self):
        return {'displayName':MODEL_NAME,'apiKeyEnv':KEY_REF,'api':'openai-completions',
                'baseURL':self.origin+'/compat/harness/v1',
                'models':[{'id':MODEL_ALIAS,'name':MODEL_NAME,'input':['text','image'],
                           'compat':{'supportsStore':False}}]}

    def status(self):
        result={'installed':(self.profile/'cordis.yml').is_file(),'configured':False,'can_undo':False,'error':''}
        with self.lock:
            state=self.store.load('harness-connection',{});result['can_undo']=bool(state)
            if not result['installed']:return result
            try:
                _,creds,row=self.documents()
                result['configured']=bool(state and row and row.get('config',{}).get('providers',{}).get(ROUTE)==state['entry']
                                          and creds[1].get('refs',{}).get(KEY_REF)==state['key'])
            except (APIError,OSError) as exc:
                result['error']=exc.message if isinstance(exc,APIError) else '无法读取 Harness 配置。'
        return result

    @contextmanager
    def writers(self):
        # Coordinate with Harness' own package/config and credential writers.
        held=[]
        try:
            for target in (self.profile/'package.json',self.credentials):
                lock=Path(str(target)+'.lock')
                try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
                except FileExistsError as exc:raise APIError(409,'harness_busy','Harness 正在保存配置，请稍后重试。') from exc
                held.append(lock)
                with os.fdopen(fd,'w') as f:f.write(str(os.getpid())+'\n')
            yield
        finally:
            for lock in reversed(held):lock.unlink(missing_ok=True)

    def replace(self,path,expected,value,yaml):
        out=io.StringIO();yaml.dump(value,out);updated=out.getvalue().encode('utf-8')
        fd,temp=tempfile.mkstemp(prefix='.local-plan-',suffix='.tmp',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(updated);f.flush();os.fsync(f.fileno())
            if path.is_symlink() or (path.read_bytes() if path.exists() else b'')!=expected:
                raise APIError(409,'harness_changed','Harness 配置刚发生变化，请重试。')
            os.replace(temp,path)
        finally:Path(temp).unlink(missing_ok=True)

    def connect(self,key):
        with self.lock:
            if not (self.profile/'cordis.yml').is_file():
                raise APIError(409,'harness_missing','请先安装并启动 DeepSeek Harness 桌面版。')
            with self.writers():
                patch,creds,row=self.documents();state=self.store.load('harness-connection',{})
                entry=self.entry();current=row.get('config',{}).get('providers',{}).get(ROUTE) if row else None
                oldkey=creds[1].get('refs',{}).get(KEY_REF)
                if current is not None and (not state or current!=state['entry']):
                    raise APIError(409,'harness_changed','Harness 中的同名提供商已存在或被修改，未覆盖。')
                if oldkey is not None and (not state or oldkey!=state['key']):
                    raise APIError(409,'harness_changed','Harness 中的专用密钥已被修改，未覆盖。')
                if current==entry and oldkey==key:return {'ok':True,'changed':False}
                if not state:
                    state={'entry':entry,'key':key,'created_row':row is None,
                           'backup_patch':base64.b64encode(patch[0]).decode(),
                           'backup_credentials':base64.b64encode(creds[0]).decode()}
                state.update(entry=entry,key=key)
                self.store.save('harness-connection',state)
                # Credentials first; if profile write fails, recovery state lets
                # the next connect/undo safely finish without touching other keys.
                creds[1].setdefault('refs',CommentedMap())[KEY_REF]=key
                self.replace(self.credentials,creds[0],creds[1],creds[2])
                if row is None:
                    row=CommentedMap(id='llm-pi-ai',config=CommentedMap());patch[1].append(row)
                row.setdefault('config',CommentedMap()).setdefault('providers',CommentedMap())[ROUTE]=entry
                self.replace(self.patch,patch[0],patch[1],patch[2])
                return {'ok':True,'changed':True}

    def undo(self):
        with self.lock:
            state=self.store.load('harness-connection',{})
            if not state:return {'ok':True,'changed':False}
            with self.writers():
                patch,creds,row=self.documents()
                current=row.get('config',{}).get('providers',{}).get(ROUTE) if row else None
                key=creds[1].get('refs',{}).get(KEY_REF)
                if current not in (None,state['entry']) or key not in (None,state['key']):
                    raise APIError(409,'harness_changed','Harness 专用连接已被手动修改，未删除。')
                if row and current is not None:
                    providers=row['config']['providers'];providers.pop(ROUTE,None)
                    if not providers:row['config'].pop('providers',None)
                    if state['created_row'] and not row['config'] and set(row)=={'id','config'}:patch[1].remove(row)
                    self.replace(self.patch,patch[0],patch[1],patch[2])
                if key is not None:
                    creds[1]['refs'].pop(KEY_REF,None)
                    if not creds[1]['refs']:creds[1].pop('refs',None)
                    self.replace(self.credentials,creds[0],creds[1],creds[2])
                self.store.save('harness-connection',{})
                return {'ok':True,'changed':True}
