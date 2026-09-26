"""Versioned file I/O shared by desktop and HTTP. No UI dependencies."""
import hashlib
import os
import threading
import time
import uuid
from pathlib import Path
from .storage import atomic_write

_write_lock = threading.RLock()

class ConflictError(Exception):
    def __init__(self, path, current):
        self.path, self.current = path, current
        super().__init__('磁盘文件已被修改或删除，已阻止覆盖。请重新加载、另存为或明确覆盖。')

def snapshot(path):
    data = Path(path).read_bytes()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('文档超过 8 MB')
    enc = 'utf-8'
    if data.startswith(b'\xef\xbb\xbf'): enc = 'utf-8-sig'
    elif data.startswith(b'\xff\xfe'): enc = 'utf-16-le'
    elif data.startswith(b'\xfe\xff'): enc = 'utf-16-be'
    try:
        text = data.decode(enc)
        if enc.startswith('utf-16'): text = text.removeprefix('\ufeff')
    except UnicodeDecodeError:
        if enc != 'utf-8':
            raise ValueError('带 BOM 的文档编码损坏，已拒绝读取')
        enc = 'gb18030'
        try: text = data.decode(enc)
        except UnicodeDecodeError as exc:
            raise ValueError('无法可靠识别文档编码；请先在其他编辑器转换为 UTF-8') from exc
    if '\x00' in text:
        raise ValueError('文档含二进制数据，已拒绝以文本覆盖')
    newline = '\r\n' if '\r\n' in text else ('\r' if '\r' in text else '\n')
    return dict(text=text.replace('\r\n', '\n').replace('\r', '\n'),
                revision=hashlib.sha256(data).hexdigest(), encoding=enc, newline=newline, data=data)

def revision(path):
    try: return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError: return 'missing'

def write_checked(path, text, expected, overwrite=None, backup_dir=None):
    """Optimistic concurrency. External writers can still race the final replace."""
    path = os.path.realpath(os.path.abspath(path))
    with _write_lock:
        current = revision(path)
        if current != (overwrite if overwrite is not None else expected):
            raise ConflictError(path, current)
        fmt = snapshot(path) if current != 'missing' else dict(encoding='utf-8', newline='\n')
        normalized = text.replace('\r\n', '\n').replace('\r', '\n')
        try:
            data = normalized.replace('\n', fmt['newline']).encode(fmt['encoding'])
            if fmt['encoding'] == 'utf-16-le': data = b'\xff\xfe' + data
            elif fmt['encoding'] == 'utf-16-be': data = b'\xfe\xff' + data
        except UnicodeEncodeError as exc:
            raise ValueError('当前编码不能保存这些字符，请另存为新的 UTF-8 文件') from exc
        if overwrite is not None and current != 'missing':
            if not backup_dir: raise ValueError('覆盖前必须提供冲突备份目录')
            backup = Path(backup_dir, '%d-%s-%s' % (time.time_ns(), uuid.uuid4().hex[:8], Path(path).name))
            atomic_write(str(backup), fmt['data'])
            if revision(path) != current: raise ConflictError(path, revision(path))
        atomic_write(path, data)
        return hashlib.sha256(data).hexdigest()
