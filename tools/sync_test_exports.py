"""Explicitly update only the desktop test workspace's trusted export plugins."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mdreader import core
from mdreader.export_ui import bundled_package
from mdreader.plugins import PluginStore


def sync_exports(workspace):
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise ValueError('测试版工作区不存在，未创建或改动数据')
    (ROOT/'.testtmp').mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='test-sync-', dir=ROOT/'.testtmp'))
    state = workspace/'plugins.json'
    if state.exists():
        shutil.copy2(state, backup/'plugins-before.json')
    store = PluginStore(str(workspace), app_version=core.APP_VERSION)
    try:
        before = {row['id']: row for row in store.list_plugins()}
        for pid in ('mdreader.export-pdf', 'mdreader.export-docx'):
            package = bundled_package(pid)
            with zipfile.ZipFile(package) as archive:
                manifest = json.loads(archive.read('manifest.json'))
            old = before.get(pid, {})
            enabled = bool(old.get('enabled')) if old.get('installed') else True
            if old.get('version') != manifest['version']:
                result = store.install(str(package), enable=enabled)
                activation = result.get('enable') or {}
                if result.get('enable_error') or activation.get('rolled_back'):
                    raise RuntimeError('插件升级未完成：'+pid)
            current = store.entry(pid)
            if not current or current['version'] != manifest['version'] or bool(current['enabled']) != enabled:
                raise RuntimeError('插件版本或启用状态不符合预期：'+pid)
            print(pid, current['version'], 'enabled='+str(current['enabled']), flush=True)
        print('Backup:', backup, flush=True)
    finally:
        store.shutdown()
    return backup


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    sync_exports(parser.parse_args().workspace)
