"""Local Docker-only Open WebUI connection management with encrypted backups."""
import copy
import json
import os
import subprocess
import threading
import time

from .errors import APIError

WORKER = r'''
import json,sys,sqlite3
from pathlib import Path
p=Path('/app/backend/data/webui.db')
if not p.is_file():raise RuntimeError('Unsupported data location')
a=json.load(sys.stdin)
c=sqlite3.connect('file:'+str(p)+'?mode=rw',uri=True,timeout=10)
try:
 if a['op']=='write':c.execute('BEGIN IMMEDIATE')
 row=c.execute('SELECT id,data FROM config ORDER BY id DESC LIMIT 1').fetchone()
 if not row:raise RuntimeError('Complete initial setup first')
 data=json.loads(row[1]);current=data.get('openai',{})
 if a['op']=='write':
  if current!=a['expected']:raise RuntimeError('Configuration changed concurrently')
  data['openai']=a['replacement']
  c.execute('UPDATE config SET data=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(json.dumps(data),row[0]));c.commit()
 print(json.dumps(current if a['op']=='read' else {'ok':True}))
finally:c.close()
'''


def configured_section(current,url,key):
    result=copy.deepcopy(current)
    urls=result.setdefault('api_base_urls',[]);keys=result.setdefault('api_keys',[])
    configs=result.setdefault('api_configs',{})
    if not isinstance(urls,list) or not isinstance(keys,list) or not isinstance(configs,dict):
        raise APIError(409,'openwebui_schema','当前 Open WebUI 配置格式暂不支持自动修改。')
    matches=[i for i,u in enumerate(urls) if u==url]
    if len(matches)>1:raise APIError(409,'openwebui_duplicate','存在多条同名连接，请先在 Open WebUI 中整理。')
    i=matches[0] if matches else len(urls)
    if not matches:urls.append(url)
    while len(keys)<len(urls):keys.append('')
    keys[i]=key
    configs[str(i)]={'enable':True,'prefix_id':'','model_ids':[],'tags':[]}
    result['enable']=True
    return result


class OpenWebUIConnector:
    def __init__(self,store,port=24664,runner=subprocess.run):
        self.store,self.port,self.runner=store,port,runner
        self.lock=threading.Lock()
        self.url=f'http://host.docker.internal:{port}/compat/openwebui/v1'

    def run(self,args,payload=None,timeout=20):
        try:
            r=self.runner(['docker',*args],input=payload,text=True,capture_output=True,timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except (OSError,subprocess.SubprocessError) as exc:
            raise APIError(409,'openwebui_docker','无法操作 Docker，请确认 Docker Desktop 已启动。') from exc
        if r.returncode:raise APIError(409,'openwebui_docker','无法操作 Open WebUI 容器，请确认容器名为 open-webui，且使用本地 SQLite 数据目录。')
        return r.stdout

    def inspect(self):
        try:
            info=json.loads(self.run(['inspect','open-webui']))[0]
            image=info['Config']['Image']
            if 'open-webui' not in image.lower():raise ValueError('Unexpected image')
            return {'installed':True,'running':bool(info['State']['Running'])}
        except (APIError,ValueError,KeyError,IndexError):return {'installed':False,'running':False}

    def worker(self,payload):
        raw=self.run(['exec','-i','open-webui','python','-c',WORKER],json.dumps(payload),timeout=20)
        try:return json.loads(raw)
        except ValueError as exc:raise APIError(409,'openwebui_config','读取 Open WebUI 配置失败。') from exc

    def status(self):
        info=self.inspect();state=self.store.load('openwebui-connection',{})
        info.update(configured=False,can_undo=bool(state),needs_restart=bool(state.get('pending_restart')))
        if info['running']:
            try:
                current=self.worker({'op':'read'})
                urls=current.get('api_base_urls',[]);keys=current.get('api_keys',[])
                target=state.get('after',{})
                if state:
                    expected_keys=target.get('api_keys',[]);expected_urls=target.get('api_base_urls',[])
                    i=expected_urls.index(self.url)
                    info['configured']=any(u==self.url and j<len(keys) and keys[j]==expected_keys[i] and current.get('api_configs',{}).get(str(j))==target.get('api_configs',{}).get(str(i)) for j,u in enumerate(urls))
            except (APIError,ValueError,IndexError,TypeError):pass
        return info

    def restart(self):
        self.run(['restart','--time','10','open-webui'],timeout=35)
        check="import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2).close()"
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            try:
                self.run(['exec','open-webui','python','-c',check],timeout=5)
                return True
            except APIError:time.sleep(1)
        return False

    def connect(self,key):
        if not self.lock.acquire(blocking=False):raise APIError(409,'openwebui_busy','Open WebUI 配置正在处理中。')
        try:
            if not self.inspect()['running']:raise APIError(409,'openwebui_docker','请先启动名为 open-webui 的 Docker 容器。')
            current=self.worker({'op':'read'});state=self.store.load('openwebui-connection',{})
            missing=self.url not in current.get('api_base_urls',[])
            if state and current not in (state['after'],state['original']) and not missing:
                raise APIError(409,'openwebui_changed','Open WebUI 连接配置已被手动修改，未覆盖。可保留手动设置或仅停用专用密钥。')
            after=configured_section(current,self.url,key)
            # An explicit reconnect may re-add a removed connection, preserving
            # the current list as the new undo snapshot. Never overwrite an
            # existing target whose URL/key/options were changed manually.
            original=state['original'] if state and not missing else current
            if after==current and state and not state.get('pending_restart'):
                return {'ok':True,'changed':False,'ready':True}
            recovery={'original':original,'after':after,'pending_restart':True}
            self.store.save('openwebui-connection',recovery)
            if current!=after:
                try:self.worker({'op':'write','expected':current,'replacement':after})
                except BaseException:
                    self.store.save('openwebui-connection',state);raise
            ready=self.restart()
            if ready and self.worker({'op':'read'})!=after:
                raise APIError(409,'openwebui_changed','Open WebUI 启动后连接配置发生变化，未确认成功。请刷新其设置页后重试。')
            recovery['pending_restart']=not ready;self.store.save('openwebui-connection',recovery)
            return {'ok':True,'changed':current!=after,'ready':ready}
        finally:self.lock.release()

    def undo(self):
        if not self.lock.acquire(blocking=False):raise APIError(409,'openwebui_busy','Open WebUI 配置正在处理中。')
        try:
            state=self.store.load('openwebui-connection',{})
            if not state:return {'ok':True,'changed':False,'ready':True}
            current=self.worker({'op':'read'})
            if current!=state['after'] and not (state.get('pending_undo') and current==state['original']):
                raise APIError(409,'openwebui_changed','Open WebUI 连接配置已被手动修改，未覆盖。可仅停用专用密钥。')
            state['pending_undo']=True;self.store.save('openwebui-connection',state)
            if current!=state['original']:
                self.worker({'op':'write','expected':current,'replacement':state['original']})
            ready=self.restart()
            self.store.save('openwebui-connection',{})
            return {'ok':True,'changed':True,'ready':ready}
        finally:self.lock.release()
