"""No access logs: OAuth codes must not appear in URL logs."""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import urllib.request
import urllib.error

import uvicorn

from plan_api.app import create_app
from plan_api import __version__


def main():
    parser=argparse.ArgumentParser(description='Local Plan API — your ChatGPT account, on your computer')
    parser.add_argument('--port',type=int,default=24664)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--serve',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--foreground',action='store_true',help='Run in the current terminal')
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('port must be 1024–65535')
    data=Path(os.environ.get('LOCAL_PLAN_API_DATA_DIR',
        str(Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'LocalPlanAPI')))
    data.mkdir(parents=True,exist_ok=True)
    if sys.stdout is None:sys.stdout=(data/'runtime.log').open('a',encoding='utf-8')
    if sys.stderr is None:sys.stderr=(data/'error.log').open('a',encoding='utf-8')
    origin=f'http://127.0.0.1:{args.port}'
    if os.name=='nt' and not args.serve and not args.foreground:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def running():
            try:
                with opener.open(origin+'/health',timeout=1) as r:info=json.load(r)
                return bool(info.get('ok') and (info.get('product')=='local-plan-api' or
                    (info.get('billing')=='chatgpt_plan' and info.get('version'))))
            except (OSError,ValueError):return False
        if running():
            with opener.open(origin+'/health',timeout=2) as r:current=json.load(r)
            if current.get('version')!=__version__:
                # Ask the known older bridge to shut down gracefully. Never kill
                # an unknown port owner or interrupt an active model request.
                try:
                    opener.open(origin+'/',timeout=2).close()
                    with opener.open(origin+'/admin/status',timeout=2) as r:state=json.load(r)
                    if state.get('in_flight'):raise RuntimeError('Request active')
                    request=urllib.request.Request(origin+'/admin/stop',data=b'{}',headers={'Content-Type':'application/json'})
                    opener.open(request,timeout=3).close()
                    for _ in range(50):
                        if not running():break
                        time.sleep(.1)
                    if running():raise RuntimeError('Still running')
                except (OSError,ValueError,RuntimeError):
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(None,'旧版本仍在运行或正在处理请求。请在原管理页停止服务后重新打开新版。','Local Plan API',48)
                    return
        if not running():
            command=[sys.executable]
            if not getattr(sys,'frozen',False):command.append(str(Path(__file__).resolve()))
            command+=['--serve','--no-browser','--port',str(args.port)]
            with (data/'runtime.log').open('ab') as out,(data/'error.log').open('ab') as err:
                worker=subprocess.Popen(command,cwd=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent,
                    stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                    creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP)
            for _ in range(100):
                if running():break
                if worker.poll() is not None:break
                time.sleep(.1)
            if not running():
                import ctypes
                ctypes.windll.user32.MessageBoxW(None,'本地桥未能启动，端口可能被其他程序占用。请查看本机 LocalPlanAPI/error.log。','Local Plan API',16)
                return
        if not args.no_browser:webbrowser.open(origin)
        print('Local Plan API is running independently in the background.',flush=True)
        return
    # Bind first, so two launches cannot run competing credential refreshers.
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    if os.name=='nt':sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    try:sock.bind(('127.0.0.1',args.port))
    except OSError:
        print('Port is in use; no second instance started.')
        if not args.no_browser:webbrowser.open(f'http://127.0.0.1:{args.port}')
        return
    sock.listen(128);sock.setblocking(False)
    app=create_app(port=args.port)
    if not args.no_browser:
        def open_browser():
            time.sleep(1);webbrowser.open(f'http://127.0.0.1:{args.port}')
        threading.Thread(target=open_browser,daemon=True).start()
    print(f'Local Plan API http://127.0.0.1:{args.port} (Ctrl+C to stop)',flush=True)
    config=uvicorn.Config(app,access_log=False,log_level='warning')
    server=uvicorn.Server(config)
    app.state.shutdown=lambda:setattr(server,'should_exit',True)
    server.run(sockets=[sock])


if __name__=='__main__':main()
