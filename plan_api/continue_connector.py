"""Round-trip Continue configuration, encrypted backups, surgical undo.

Only the standard local Continue directory is handled. No requests can supply
arbitrary filesystem paths. Status checks never create or modify config files.
"""
import base64
import copy
import hashlib
import io
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap,CommentedSeq
from ruamel.yaml.error import YAMLError

from .errors import APIError

MODEL_ALIAS='local-plan-default'
MODEL_NAME='ChatGPT · Local Plan API'


def digest(value):return hashlib.sha256(value).hexdigest()


class ContinueConnector:
    def __init__(self,store,origin,home=None,config_dir=None):
        self.store,self.origin=store,origin
        self.home=Path(home) if home else Path.home()
        self.folder=Path(config_dir) if config_dir else Path(os.environ.get('CONTINUE_GLOBAL_DIR',self.home/'.continue'))
        self.path=self.folder/'config.yaml';self.lock=threading.Lock()

    def installed(self):
        for name in ('.vscode','.vscode-insiders','.vscode-oss'):
            folder=self.home/name/'extensions'
            if any(p.is_dir() for p in folder.glob('continue.continue-*')):return True
        return False

    def read(self):
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
            raise APIError(409,'continue_config_unsafe','Continue 配置路径不是普通文件，未作修改。')
        raw=self.path.read_bytes() if self.path.exists() else b''
        if len(raw)>2*1024*1024:raise APIError(409,'continue_config_large','Continue 配置超过2MB，未作修改。')
        yaml=YAML();yaml.preserve_quotes=True;yaml.width=4096
        try:doc=yaml.load(raw.decode('utf-8-sig')) if raw.strip() else CommentedMap()
        except (YAMLError,UnicodeError) as exc:
            raise APIError(409,'continue_config_invalid','Continue 现有配置格式有误；未覆盖，请先在 Continue 中修复配置。') from exc
        if doc is None:doc=CommentedMap()
        if not isinstance(doc,dict) or doc.get('schema','v1')!='v1':
            raise APIError(409,'continue_schema_unsupported','当前 Continue 配置格式暂不支持，未作修改。')
        if 'models' in doc and not isinstance(doc['models'],list):
            raise APIError(409,'continue_models_invalid','Continue models 不是列表，未作修改。')
        if getattr(doc.get('models',[]),'anchor',None) and doc['models'].anchor.value:
            raise APIError(409,'continue_models_anchored','Continue 模型列表使用了共享 YAML 锚点，请先手动展开。')
        return raw,doc,yaml

    @staticmethod
    def render(doc,yaml):
        text=io.StringIO();yaml.dump(doc,text)
        return text.getvalue().encode('utf-8')

    @staticmethod
    def matches(item,spec):
        return isinstance(item,dict) and item==spec

    def status(self):
        with self.lock:
            state=self.store.load('continue-connection',{})
            result={'installed':self.installed(),'configured':False,'config_path':str(self.path),
                'model_name':MODEL_NAME,'can_undo':bool(state),'error':'',
                'reload_hint':'首次连接后，在 Continue 模型列表选择“'+MODEL_NAME+'”；未出现时重新加载 VS Code 窗口。'}
            try:
                _,doc,_=self.read()
                result['configured']=bool(state and any(self.matches(m,state.get('entry')) for m in doc.get('models',[])))
                result['model_count']=len(doc.get('models',[]))
                if state and not result['configured']:result['error']='已添加的连接配置被修改或移除，可撤销后重新连接。'
            except (APIError,OSError) as exc:
                result['error']=exc.message if isinstance(exc,APIError) else '无法读取 Continue 配置，请检查文件权限。'
            return result

    def replace(self,raw,new_bytes,existed):
        if self.path.exists()!=existed or (self.path.read_bytes() if existed else b'')!=raw:
            raise APIError(409,'continue_config_changed','Continue 配置刚被其他程序修改，请重新连接。')
        if new_bytes is None:
            self.path.unlink();return
        fd,temp=tempfile.mkstemp(prefix='.local-plan-',suffix='.tmp',dir=self.folder)
        try:
            with os.fdopen(fd,'wb') as f:f.write(new_bytes);f.flush();os.fsync(f.fileno())
            os.replace(temp,self.path)
        finally:
            if Path(temp).exists():Path(temp).unlink()

    def operation(self,callback):
        with self.lock:
            self.folder.mkdir(parents=True,exist_ok=True)
            lockfile=self.folder/'.local-plan-api.lock'
            try:fd=os.open(lockfile,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            except FileExistsError as exc:raise APIError(409,'continue_config_busy','其他连接操作尚未结束，请稍后重试。') from exc
            try:
                os.close(fd)
                return callback()
            except OSError as exc:raise APIError(409,'continue_write_failed','无法保存 Continue 配置；备份已保留，请检查文件权限。') from exc
            finally:lockfile.unlink(missing_ok=True)

    def connect(self,key):
        def apply():
            raw,doc,yaml=self.read();existed=self.path.exists()
            before_state=self.store.load('continue-connection',{})
            models=doc.setdefault('models',CommentedSeq())
            entry={'name':MODEL_NAME,'provider':'openai','model':MODEL_ALIAS,
                'apiBase':self.origin+'/compat/continue/v1','apiKey':key,
                'roles':['chat','edit','apply'],'capabilities':['tool_use','image_input'],
                'useResponsesApi':False}
            named=[i for i,m in enumerate(models) if isinstance(m,dict) and m.get('name')==MODEL_NAME]
            previous=before_state.get('entry',{})
            if not named and previous.get('name')!=MODEL_NAME:
                named=[i for i,m in enumerate(models) if self.matches(m,previous)]
                if not named and previous and any(isinstance(m,dict) and m.get('name')==previous.get('name') for m in models):
                    raise APIError(409,'continue_entry_modified','原连接已被手动修改，未覆盖；请先检查 Continue 配置。')
            if named:
                if len(named)!=1 or not before_state or not self.matches(models[named[0]],before_state.get('entry')):
                    raise APIError(409,'continue_entry_modified','同名模型已存在或被手动修改，未覆盖。请先撤销原连接或重命名该模型。')
                if models[named[0]]==entry:return {'ok':True,'changed':False,'model_name':MODEL_NAME}
                models[named[0]]=entry
            else:models.insert(0,CommentedMap(entry))
            doc.setdefault('name','Local Config');doc.setdefault('version','1.0.0');doc.setdefault('schema','v1')
            updated=self.render(doc,yaml)
            backup='continue-backup-'+time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(4)
            self.store.save(backup,{'existed':existed,'raw':base64.b64encode(raw).decode()})
            state=copy.deepcopy(before_state) if before_state else {
                'original':base64.b64encode(raw).decode(),'original_existed':existed,'restore_exact':True}
            if before_state and digest(raw)!=before_state.get('after_hash'):state['restore_exact']=False
            state.update(entry=entry,after_hash=digest(updated),backup=backup)
            # Persist recovery information before touching the user's file.
            self.store.save('continue-connection',state)
            try:self.replace(raw,updated,existed)
            except BaseException:
                self.store.save('continue-connection',before_state);raise
            return {'ok':True,'changed':True,'model_name':MODEL_NAME,'backup_saved':True}
        return self.operation(apply)

    def undo(self):
        def apply():
            state=self.store.load('continue-connection',{})
            if not state:return {'ok':True,'changed':False}
            raw,doc,yaml=self.read();existed=self.path.exists()
            if state.get('restore_exact') and digest(raw)==state.get('after_hash'):
                updated=base64.b64decode(state['original']) if state['original_existed'] else None
            else:
                models=doc.get('models',[])
                named=[i for i,m in enumerate(models) if isinstance(m,dict) and m.get('name')==state.get('entry',{}).get('name',MODEL_NAME)]
                if named:
                    if len(named)!=1 or not self.matches(models[named[0]],state.get('entry')):
                        raise APIError(409,'continue_entry_modified','连接项已被手动修改，未删除；请先在 Continue 中移除该项。')
                    del models[named[0]];updated=self.render(doc,yaml)
                else:
                    self.store.save('continue-connection',{});return {'ok':True,'changed':False}
            self.replace(raw,updated,existed)
            self.store.save('continue-connection',{})
            return {'ok':True,'changed':True}
        return self.operation(apply)
