"""Windows-user-bound encryption. No tokens or chat content in logs."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets


class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def protect(value, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('This release requires Windows DPAPI; plaintext storage is not supported.')
    buf = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dest = Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), 'Local Plan API', None, None, None, 1, ctypes.byref(dest))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(dest.pbData, dest.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dest.pbData)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def load(self, name, default):
        path = self.path / (name + '.bin')
        return json.loads(protect(path.read_bytes(), True)) if path.exists() else default

    def save(self, name, value):
        path = self.path / (name + '.bin')
        temp = path.with_suffix('.' + secrets.token_hex(6) + '.tmp')
        temp.write_bytes(protect(json.dumps(value, ensure_ascii=False).encode('utf-8')))
        os.replace(temp, path)
