"""Surgical AstrBot local configuration edits; no bot restart or model switch."""
import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import threading

from .continue_connector import MODEL_ALIAS
from .errors import APIError

SOURCE='local-plan-api'
MODEL=SOURCE+'/'+MODEL_ALIAS


class AstrBotConnector:
    def __init__(self,store,origin,home=None,root=None):
        self.store,self.origin=store,origin
        self.home=Path(home) if home else Path.home()
        self.root=Path(root) if root else Path(os.environ['ASTRBOT_ROOT']) if os.environ.get('ASTRBOT_ROOT') else None
        self.lock=threading.Lock()

    def instances(self):
        return sorted(p.parent.parent.parent.name for p in (self.home/'.astrbot_launcher/instances').glob('*/core/data/cmd_config.json'))

    def select(self,instance):
        with self.lock:
            if instance not in self.instances():raise APIError(400,'astrbot_location','AstrBot 实例不存在。')
            if self.store.load('astrbot-connection',{}):raise APIError(409,'astrbot_changed','请先撤销当前 AstrBot 连接，再切换实例。')
            self.store.save('astrbot-target',{'instance':instance})
        return {'ok':True}

    def path(self):
        if self.root:return self.root/'data/cmd_config.json'
        instances=self.instances()
        selected=self.store.load('astrbot-target',{}).get('instance')
        if selected in instances:return self.home/'.astrbot_launcher/instances'/selected/'core/data/cmd_config.json'
        candidates=[self.home/'.astrbot_launcher/instances'/i/'core/data/cmd_config.json' for i in instances]
        if len(candidates)!=1:
            raise APIError(409,'astrbot_location','请先安装并启动 AstrBot；多个实例请先在卡片中选择；自定义安装可通过 ASTRBOT_ROOT 指定根目录。')
        return candidates[0]

    def read(self):
        path=self.path()
        if path.is_symlink() or not path.is_file() or path.stat().st_size>8*1024*1024:
            raise APIError(409,'astrbot_config','AstrBot 配置必须为普通文件且小于 8MB。')
        raw=path.read_bytes()
        try:
            doc=json.loads(raw.decode('utf-8-sig'))
            if not isinstance(doc,dict) or any(not isinstance(doc.get(k),list) or any(not isinstance(v,dict) for v in doc[k]) for k in ('provider_sources','provider')):raise ValueError()
        except (ValueError,UnicodeError) as exc:
            raise APIError(409,'astrbot_schema','当前 AstrBot 配置格式暂不支持，未覆盖。') from exc
        return path,raw,doc

    @staticmethod
    def owned(doc):
        entries={}
        for name,id in (('provider_sources',SOURCE),('provider',MODEL)):
            matches=[v for v in doc[name] if v.get('id')==id]
            if len(matches)>1:raise APIError(409,'astrbot_changed','AstrBot 中存在重复的同名连接，未修改。')
            entries[name]=matches[0] if matches else None
        return entries

    def status(self):
        result={'installed':False,'configured':False,'can_undo':False,'needs_restart':False,'error':''}
        with self.lock:
            state=self.store.load('astrbot-connection',{});result['can_undo']=bool(state)
            result['instances']=[] if self.root else self.instances()
            result['instance']=self.store.load('astrbot-target',{}).get('instance','')
            if not result['instance'] and len(result['instances'])==1:result['instance']=result['instances'][0]
            try:
                _,_,doc=self.read();result['installed']=True
                result['configured']=bool(state and self.owned(doc)==state['entries'])
                # Disk checks cannot prove the running process has reloaded.
                result['needs_restart']=result['configured']
            except (APIError,OSError) as exc:
                result['error']=exc.message if isinstance(exc,APIError) else '无法读取 AstrBot 配置。'
        return result

    def entries(self,key):
        return {'provider_sources':{'id':SOURCE,'provider':'openai','type':'openai_chat_completion',
            'provider_type':'chat_completion','enable':True,'key':[key],
            'api_base':self.origin+'/compat/astrbot/v1','timeout':120,'proxy':'','custom_headers':{}},
            'provider':{'id':MODEL,'enable':True,'provider_source_id':SOURCE,'model':MODEL_ALIAS,
            'modalities':['text','image'],'custom_extra_body':{},'max_context_tokens':0}}

    def replace(self,path,raw,doc):
        updated=(json.dumps(doc,ensure_ascii=False,indent=2)+'\n').encode('utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8')
        fd,temp=tempfile.mkstemp(prefix='.local-plan-',suffix='.tmp',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(updated);f.flush();os.fsync(f.fileno())
            if path.is_symlink() or path.read_bytes()!=raw:
                raise APIError(409,'astrbot_changed','AstrBot 配置刚被其他程序修改，请重试。')
            os.replace(temp,path)
        finally:Path(temp).unlink(missing_ok=True)

    def connect(self,key):
        with self.lock:
            path,raw,doc=self.read();current=self.owned(doc);state=self.store.load('astrbot-connection',{})
            entries=self.entries(key)
            if any(v is not None and (not state or v!=state['entries'][n]) for n,v in current.items()):
                raise APIError(409,'astrbot_changed','AstrBot 的同名连接已存在或被手动修改，未覆盖。')
            if current==entries:return {'ok':True,'changed':False,'needs_restart':True}
            if not state:state={'backup':base64.b64encode(raw).decode()}
            state['entries']=entries
            self.store.save('astrbot-connection',state)
            for name,entry in entries.items():
                doc[name]=[v for v in doc[name] if v.get('id')!=entry['id']]+[copy.deepcopy(entry)]
            self.replace(path,raw,doc)
            return {'ok':True,'changed':True,'needs_restart':True}

    def undo(self):
        with self.lock:
            state=self.store.load('astrbot-connection',{})
            if not state:return {'ok':True,'changed':False}
            path,raw,doc=self.read();current=self.owned(doc)
            if any(v is not None and v!=state['entries'][n] for n,v in current.items()):
                raise APIError(409,'astrbot_changed','AstrBot 专用连接已被修改，未删除。')
            if (doc.get('agent_runner',{}).get('config',{}).get('model',{}).get('provider_id')==MODEL or
                    doc.get('provider_settings',{}).get('default_image_caption_provider_id')==MODEL):
                raise APIError(409,'astrbot_in_use','请先在 AstrBot 切换默认模型，再撤销该连接。')
            for name,entry in state['entries'].items():doc[name]=[v for v in doc[name] if v.get('id')!=entry['id']]
            self.replace(path,raw,doc);self.store.save('astrbot-connection',{})
            return {'ok':True,'changed':True,'needs_restart':True}
