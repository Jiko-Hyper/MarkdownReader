"""Pinned optional components, stored outside the replaceable program directory."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from .storage import atomic_write

APP = Path(__file__).resolve().parents[1]
NAMES = {'photo-ocr': '照片增强识别', 'scan-engine': '扫描识别引擎', 'scan-models': '扫描识别模型'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_path(name):
    path = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or path.is_absolute() or any(p in ('', '.', '..') for p in name.split('/')):
        raise ValueError('组件中包含无效路径')
    return path


class ComponentStore:
    def __init__(self, workspace, app=None):
        self.app = Path(app) if app else APP
        self.root = Path(workspace) / 'components'
        try:
            self.catalog = json.loads((self.app / 'components.json').read_text('utf-8'))['components']
        except FileNotFoundError:
            self.catalog = {}

    def descriptor(self, name):
        if name not in NAMES or name not in self.catalog:
            raise ValueError('此版本没有提供组件下载信息，请导入对应的官方离线组件包')
        row = self.catalog[name]
        if (len(row.get('sha256', '')) != 64 or any(c not in '0123456789abcdef' for c in row['sha256'])
                or not isinstance(row.get('files'), dict) or not row['files']
                or not isinstance(row.get('bytes'), int) or row['bytes'] <= 0):
            raise ValueError('组件清单无效')
        for path in row['files']:
            checked_path(path)
        return row

    def installed(self, name):
        """Honor the checksum pinned by this app; old component versions remain reusable."""
        if name not in self.catalog:
            return None
        row = self.descriptor(name)
        identity = row.get('content_id', row['sha256'])
        if len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('组件身份无效')
        folder = self.root / identity
        try:
            marker = json.loads((folder / '.installed.json').read_text('utf-8'))
        except (OSError, ValueError):
            return None
        if marker.get('content_id') == identity and all((folder / p).is_file() for p in row['files']):
            return folder
        return None

    def locate(self, name):
        cached = self.installed(name)
        if cached:
            return cached
        if name == 'photo-ocr':
            candidates = [self.app / 'runtime/photo_ocr_runtime', self.app / 'plugins/photo-ocr-dependencies']
            return next((p for p in candidates if (p / 'rapidocr/__init__.py').is_file()), None)
        candidates = [self.app / 'scan-runtime', self.app / 'build/scan-runtime-portable', self.app / 'build/scan-runtime']
        if name == 'scan-engine':
            return next((p for p in candidates if (p / 'python.exe').is_file() or (p / 'Scripts/python.exe').is_file()), None)
        return next((p / 'models-home' for p in candidates if (p / 'models-home/config.yaml').is_file()), None)

    @staticmethod
    def check_cancel(cancel):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('已取消组件安装；已有组件保持可用')

    def install(self, name, archive=None, *, cancel=None, progress=None):
        """Download only on explicit request, verify every file, then publish atomically."""
        import zipfile
        existing = self.locate(name)
        if existing:
            return existing
        row = self.descriptor(name)
        self.root.mkdir(parents=True, exist_ok=True)
        report = progress or (lambda text: None)
        self.check_cancel(cancel)
        if archive is None:
            archive = self.root / (row['sha256'] + '.download')
            url = row.get('url', '')
            if not url.startswith('https://'):
                raise ValueError('组件下载地址必须使用 HTTPS')
            offset = archive.stat().st_size if archive.exists() else 0
            if offset > row['bytes']:
                archive.unlink()
                offset = 0
            if offset < row['bytes']:
                request = urllib.request.Request(url, headers={'Range': 'bytes=%d-' % offset} if offset else {})
                try:
                    response = urllib.request.urlopen(request, timeout=30)
                except OSError as exc:
                    raise ValueError('无法下载组件。可重试，或选择“导入离线组件包”。') from exc
                with response:
                    if not response.geturl().startswith('https://'):
                        raise ValueError('组件下载发生不安全的跳转')
                    if offset and response.status != 206:
                        offset = 0
                    if response.status == 206 and not response.headers.get('Content-Range', '').startswith('bytes %d-' % offset):
                        raise ValueError('服务器返回的续传位置无效')
                    with archive.open('ab' if offset else 'wb') as stream:
                        while True:
                            self.check_cancel(cancel)
                            block = response.read(1024 * 1024)
                            if not block:
                                break
                            offset += len(block)
                            if offset > row['bytes']:
                                raise ValueError('下载内容超过组件清单大小')
                            stream.write(block)
                            report('正在下载%s：%.1f / %.1f MB' % (NAMES[name], offset / 1e6, row['bytes'] / 1e6))
        archive = Path(archive)
        report('正在校验%s…' % NAMES[name])
        self.check_cancel(cancel)
        if archive.stat().st_size != row['bytes'] or digest(archive) != row['sha256']:
            # A rejected downloaded file must not cause an endless failed retry.
            if archive.parent == self.root and archive.suffix == '.download':
                archive.unlink(missing_ok=True)
            raise ValueError('组件校验失败，请重新下载对应版本的官方组件包')
        with tempfile.TemporaryDirectory(prefix='.install-', dir=self.root) as tmp:
            stage = Path(tmp) / 'payload'
            stage.mkdir()
            with zipfile.ZipFile(archive) as zipped:
                entries = [i for i in zipped.infolist() if not i.is_dir()]
                names = [i.filename for i in entries]
                if len(set(n.casefold() for n in names)) != len(names) or set(names) != set(row['files']):
                    raise ValueError('组件内容与清单不符')
                if sum(i.file_size for i in entries) != row['unpacked_bytes']:
                    raise ValueError('组件解压大小无效')
                for info in entries:
                    self.check_cancel(cancel)
                    relative = checked_path(info.filename)
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('组件不允许包含符号链接')
                    target = stage / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    h = hashlib.sha256()
                    report('正在安装%s…' % NAMES[name])
                    with zipped.open(info) as inp, target.open('xb') as out:
                        while True:
                            self.check_cancel(cancel)
                            block = inp.read(1024 * 1024)
                            if not block:
                                break
                            h.update(block)
                            out.write(block)
                    if h.hexdigest() != row['files'][info.filename]:
                        raise ValueError('组件文件校验失败：' + info.filename)
            self.check_cancel(cancel)
            identity = row.get('content_id', row['sha256'])
            if len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
                raise ValueError('组件身份无效')
            atomic_write(str(stage / '.installed.json'), json.dumps({'content_id': identity, 'files': list(row['files'])}))
            destination = self.root / identity
            if destination.exists():
                if self.installed(name):
                    return destination
                raise ValueError('组件目录不完整，请移走损坏目录后重试：' + str(destination))
            stage.rename(destination)
        if archive.parent == self.root and archive.suffix == '.download':
            archive.unlink(missing_ok=True)
        return destination


def missing_for(store, format_name):
    needed = {'scan-md': ('scan-engine', 'scan-models'), 'photo-md': ('photo-ocr',)}.get(format_name, ())
    return [name for name in needed if not store.locate(name)]
