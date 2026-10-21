"""Cline Desktop v1 JSON provider/catalog integration; no account tokens exposed."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading

from .continue_connector import MODEL_ALIAS, MODEL_NAME
from .errors import APIError

PROVIDER_ID = 'local-plan-api'
FILES = ('models.json', 'providers.json')


class ClineConnector:
    def __init__(self, store, origin, folder=None):
        self.store, self.origin = store, origin
        self.folder = Path(folder) if folder else Path(os.environ.get(
            'CLINE_DATA_DIR', str(Path(os.environ.get('CLINE_DIR', str(Path.home()/'.cline')))/'data')))/'settings'
        self.lock = threading.Lock()

    def read(self, name):
        path = self.folder/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4*1024*1024:
            raise APIError(409, 'cline_config', '请先启动 Cline 桌面版并完成初始设置；配置文件必须为普通文件。')
        raw = path.read_bytes()
        try:
            doc = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(doc, dict) or doc.get('version') != 1 or not isinstance(doc.get('providers'), dict):
                raise ValueError()
            if any(not isinstance(v, dict) for v in doc['providers'].values()):raise ValueError()
        except (ValueError, UnicodeError) as exc:
            raise APIError(409, 'cline_schema', 'Cline 配置格式暂不支持，未覆盖。') from exc
        return raw, doc

    def status(self):
        result = {'installed':all((self.folder/n).is_file() for n in FILES),
                  'configured':False, 'can_undo':False, 'manual_bridge':False, 'error':''}
        with self.lock:
            state = self.store.load('cline-connection', {})
            result['can_undo'] = bool(state)
            if not result['installed']:return result
            try:
                docs = {n:self.read(n)[1] for n in FILES}
                result['configured'] = bool(state and all(
                    docs[n]['providers'].get(PROVIDER_ID) == state['entries'][n] for n in FILES))
                result['manual_bridge'] = any(
                    v.get('settings', {}).get('baseUrl') in (self.origin+'/v1', self.origin+'/compat/cline/v1')
                    for k,v in docs['providers.json']['providers'].items() if k != PROVIDER_ID)
            except (APIError, OSError) as exc:
                result['error'] = exc.message if isinstance(exc, APIError) else '无法读取 Cline 配置。'
        return result

    def replace(self, name, expected, updated):
        path = self.folder/name
        fd, temp = tempfile.mkstemp(prefix='.local-plan-', suffix='.tmp', dir=self.folder)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(updated);f.flush();os.fsync(f.fileno())
            if path.is_symlink() or path.read_bytes() != expected:
                raise APIError(409, 'cline_changed', 'Cline 配置刚被其他程序修改，请重试。')
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)

    def entries(self, key):
        base = self.origin+'/compat/cline/v1'
        return {
            'providers.json':{'settings':{'provider':PROVIDER_ID, 'apiKey':key,
                'model':MODEL_ALIAS, 'protocol':'openai-chat', 'client':'openai-compatible', 'baseUrl':base},
                'updatedAt':datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),
                'tokenSource':'manual'},
            'models.json':{'provider':{'name':MODEL_NAME, 'baseUrl':base, 'defaultModelId':MODEL_ALIAS,
                'protocol':'openai-chat', 'client':'openai-compatible', 'capabilities':['tools']},
                'models':{MODEL_ALIAS:{'id':MODEL_ALIAS, 'name':MODEL_NAME, 'supportsVision':True}}}}

    def apply(self, entries, removing=False):
        # Compare both owned entries before touching either file. Keep every other
        # provider and top-level field, including OAuth refreshes and user choices.
        snapshots = {n:self.read(n) for n in FILES}
        for n,(_,doc) in snapshots.items():
            existing = doc['providers'].get(PROVIDER_ID)
            if existing is not None and existing != entries[n]:
                raise APIError(409, 'cline_changed', 'Cline 中的同名连接已被手动修改，未覆盖。')
        changes = []
        names = tuple(reversed(FILES)) if removing else FILES
        try:
            for name in names:
                raw, doc = self.read(name)
                existing = doc['providers'].get(PROVIDER_ID)
                if existing is not None and existing != entries[name]:
                    raise APIError(409, 'cline_changed', 'Cline 配置刚被其他程序修改，请重试。')
                if removing:
                    doc['providers'].pop(PROVIDER_ID, None)
                    if name == 'providers.json' and doc.get('lastUsedProvider') == PROVIDER_ID:
                        doc.pop('lastUsedProvider', None)
                else:doc['providers'][PROVIDER_ID] = copy.deepcopy(entries[name])
                new = (json.dumps(doc, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
                if json.loads(raw.decode('utf-8-sig')) != doc:
                    self.replace(name, raw, new);changes.append((name, raw, new))
        except BaseException:
            # Roll back only files still byte-identical to our write. Recovery
            # state remains encrypted so retry/undo can finish a partial write.
            for name, raw, new in reversed(changes):
                try:self.replace(name, new, raw)
                except (OSError, APIError):pass
            raise
        return bool(changes)

    def connect(self, key):
        with self.lock:
            state = self.store.load('cline-connection', {})
            entries = state.get('entries') or self.entries(key)
            if state and entries['providers.json']['settings']['apiKey'] != key:
                raise APIError(409, 'cline_key', 'Cline 专用密钥已变化，请先撤销连接再重新配置。')
            if not state:
                for name in FILES:
                    if PROVIDER_ID in self.read(name)[1]['providers']:
                        raise APIError(409, 'cline_changed', 'Cline 中已存在同名提供商，未覆盖。')
                self.store.save('cline-connection', {'entries':entries})
            changed = self.apply(entries)
            return {'ok':True, 'changed':changed, 'provider_id':PROVIDER_ID, 'model_name':MODEL_NAME}

    def undo(self):
        with self.lock:
            state = self.store.load('cline-connection', {})
            if not state:return {'ok':True, 'changed':False}
            changed = self.apply(state['entries'], removing=True)
            self.store.save('cline-connection', {})
            return {'ok':True, 'changed':changed}
