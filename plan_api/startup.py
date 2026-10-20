"""Per-user Windows sign-in startup; never enabled implicitly."""
import os
from pathlib import Path
import subprocess
import sys

from .errors import APIError

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'LocalPlanAPI'


def startup_command(port):
    executable = Path(sys.executable)
    if getattr(sys, 'frozen', False):
        args = [str(executable)]
    else:
        windowless = executable.with_name('pythonw.exe')
        args = [str(windowless if windowless.exists() else executable),
                str(Path(__file__).resolve().parent.parent / 'run.py')]
    return subprocess.list2cmdline(args + ['--no-browser', '--port', str(port)])


class Startup:
    def __init__(self, port):
        self.port = port

    def status(self):
        if os.name != 'nt':
            return {'supported': False, 'enabled': False, 'needs_update': False}
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                command, _ = winreg.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            command = ''
        return {'supported': True, 'enabled': bool(command),
                'needs_update': bool(command and command != startup_command(self.port))}

    def set_enabled(self, enabled):
        if os.name != 'nt':
            raise APIError(400, 'startup_unsupported', '开机自启动目前仅支持 Windows。')
        import winreg
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, startup_command(self.port))
                else:
                    try:
                        winreg.DeleteValue(key, VALUE_NAME)
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            raise APIError(500, 'startup_failed', '无法更新开机自启动设置，请检查系统权限。') from exc
        return self.status()
