"""Independent, bounded recovery snapshots; never overwrite original documents."""
import hashlib
import json
import os
import time
from pathlib import Path
from .storage import atomic_write

class RecoveryStore:
    def __init__(self, workspace):
        self.root = Path(workspace, '.recovery')

    def key(self, identity):
        return hashlib.sha256(identity.encode('utf-8')).hexdigest()

    def save(self, identity, text, path='', baseline='missing'):
        key = self.key(identity)
        self.root.mkdir(parents=True, exist_ok=True)
        entries = list(self.root.glob('*.json'))
        dest = self.root / (key + '.json')
        record = dict(identity=identity, text=text, path=path, baseline=baseline, time=time.time())
        payload = json.dumps(record, ensure_ascii=False)
        size = len(payload.encode('utf-8'))
        if size > 32*1024*1024 or (not dest.exists() and len(entries) >= 500) or sum(p.stat().st_size for p in entries if p != dest)+size > 256*1024*1024:
            raise ValueError('恢复目录已达容量限制，请先检查并清理旧快照')
        atomic_write(str(dest), payload)
        return key

    def list(self):
        records = []
        for file in self.root.glob('*.json'):
            try:
                if file.stat().st_size > 32*1024*1024: raise ValueError('快照过大')
                data = json.loads(file.read_text(encoding='utf-8'))
                if not isinstance(data.get('text'), str): raise ValueError('快照正文无效')
                records.append(dict(data, key=file.stem))
            except (OSError, ValueError, AttributeError) as exc:
                records.append(dict(key=file.stem, error=str(exc)))
        return records

    def discard(self, identity, saved_text=None):
        file = self.root / (self.key(identity) + '.json')
        if saved_text is not None and file.exists():
            try:
                if json.loads(file.read_text(encoding='utf-8'))['text'] != saved_text: return
            except (OSError, ValueError, KeyError): return
        file.unlink(missing_ok=True)
