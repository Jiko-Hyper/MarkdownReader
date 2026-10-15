"""Apply a verified program-only patch with backups; never touch user data/models."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import zipfile

MANAGED = {'MDReader.exe', '安装.exe', 'main.py', 'components.json', 'VERSION.txt', 'README.md', 'README.en.md',
           'LICENSE', 'CONTRIBUTING.md', '使用说明.md', '使用手册.md', '安装到桌面.ps1', '安装到桌面.cmd',
           '卸载.ps1', '卸载.cmd', '卸载.exe', '启动 MDReader.bat', 'Register-FileAssociation.ps1',
           '更新程序.ps1', '更新程序.cmd', 'Select-Folder.ps1', 'program-files.json'}
PREFIXES = ('mdreader/', 'webui/', 'assets/', 'tools/', 'docs/', 'tests/', 'plugins/', 'setup/', 'legal/')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def apply_update(install, archive):
    from pathlib import PurePosixPath
    def checked_path(name):
        if not name or '\\' in name or ':' in name or PurePosixPath(name).is_absolute() or any(p in ('', '.', '..') for p in name.split('/')):
            raise ValueError('更新路径无效')
    install = Path(install).resolve()
    payload = install/'resources' if (install/'resources/mdreader/core.py').is_file() else install
    if not (install/'MDReader.exe').is_file() or not (payload/'runtime/python.exe').is_file():
        raise ValueError('请选择完整的 MDReader 安装目录')
    current = re.search(r'APP_VERSION = "([^"]+)"', (payload/'mdreader/core.py').read_text('utf-8')).group(1)
    backup = install.parent / (install.name + '.program-backup-' + uuid.uuid4().hex)
    with zipfile.ZipFile(archive) as z, tempfile.TemporaryDirectory(prefix='.mdreader-update-', dir=install.parent) as tmp:
        info = z.getinfo('update-manifest.json')
        if info.file_size > 8*1024*1024:
            raise ValueError('更新清单过大')
        manifest = json.loads(z.read(info))
        if manifest.get('schema') != 1 or manifest.get('base_version') != current:
            raise ValueError('更新包不适用于当前版本，请使用对应更新包或主程序安装包')
        rows = manifest['files']
        names = [i.filename for i in z.infolist()]
        if len(set(n.casefold() for n in names)) != len(names) or set(names) != set(rows) | {'update-manifest.json'}:
            raise ValueError('更新包内容与清单不符')
        if sum(i.file_size for i in z.infolist()) > 512*1024*1024:
            raise ValueError('更新包解压大小超过上限')
        stage = Path(tmp)
        for name, row in rows.items():
            checked_path(name)
            internal_name = name[len('resources/'):] if name.startswith('resources/') else name
            if internal_name not in MANAGED and not internal_name.startswith(PREFIXES):
                raise ValueError('更新包试图改动程序以外的文件')
            destination = install/name
            if destination.is_symlink() or not destination.resolve().is_relative_to(install):
                raise ValueError('更新目标不能越过程序目录')
            if row.get('before') and (not destination.is_file() or sha(destination) != row['before']):
                raise ValueError('程序文件与原版本不一致，请使用完整主程序安装包：' + name)
            if not row.get('before') and destination.exists():
                raise ValueError('新增程序文件已存在，已保留：' + name)
            target = stage/name
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as inp, target.open('xb') as out:
                shutil.copyfileobj(inp, out)
            if sha(target) != row['sha256']:
                raise ValueError('更新文件校验失败：' + name)
        replaced = []
        backup.mkdir()
        try:
            for name in rows:
                target = install/name
                old = backup/name
                if target.exists():
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, old)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage/name, target)
                replaced.append(name)
        except Exception:
            for name in reversed(replaced):
                old = backup/name
                if old.exists():
                    shutil.copy2(old, install/name)
                else:
                    (install/name).unlink(missing_ok=True)
            raise
    return backup


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    print('更新完成，旧程序文件备份在：' + str(apply_update(args.install, args.archive)))
