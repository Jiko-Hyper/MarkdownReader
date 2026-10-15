import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import build_split_release


class CorePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mdreader-core-package-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.output = self.root / 'app', self.root / 'release'
        (self.source / 'mdreader').mkdir(parents=True)
        self.output.mkdir()
        (self.source / 'mdreader/core.py').write_text('APP_VERSION = "0.4.15"\n', encoding='utf-8')
        (self.source / 'MDReader.exe').write_bytes(b'app')
        (self.source / '卸载.exe').write_bytes(b'uninstall')

    def build(self):
        with patch.object(sys, 'argv', ['build_split_release', '--source', str(self.source),
                                       '--out', str(self.output), '--core-only']), contextlib.redirect_stdout(io.StringIO()):
            build_split_release.main()

    def test_core_only_keeps_verified_catalog_without_repacking_models(self):
        components = {name: {'url': 'https://example.test/' + name, 'sha256': name,
                             'files': {'model.bin': name}, 'bytes': 123}
                      for name in ('photo-ocr', 'scan-engine', 'scan-models')}
        (self.output / 'components.json').write_text(json.dumps({'components': components}), encoding='utf-8')
        for name in ('scan-runtime/models-home/model.bin', 'runtime/photo_ocr_runtime/model.bin'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'excluded model payload')
        self.build()
        packages = list(self.output.glob('*.zip'))
        self.assertEqual([path.name for path in packages], ['MarkdownReader-0.4.15-core-win64.zip'])
        with zipfile.ZipFile(packages[0]) as archive:
            self.assertIn('卸载.exe', archive.namelist())
            self.assertFalse(any(name.startswith(('scan-runtime/', 'runtime/photo_ocr_runtime/'))
                                 for name in archive.namelist()))
            catalog = json.loads(archive.read('components.json'))
            self.assertEqual(catalog['components'], components)
            self.assertEqual(catalog['app_version'], '0.4.15')
        self.assertTrue(packages[0].with_suffix('.zip.sha256').is_file())

    def test_core_only_refuses_missing_component_catalog(self):
        with self.assertRaisesRegex(RuntimeError, 'verified components.json'):
            self.build()
        self.assertFalse(list(self.output.glob('*.zip')))

    def test_incremental_archive_includes_its_current_updater_even_when_unchanged(self):
        components = {name: {} for name in ('photo-ocr', 'scan-engine', 'scan-models')}
        (self.output / 'components.json').write_text(json.dumps({'components': components}), encoding='utf-8')
        bootstrap = ('更新程序.cmd', '更新程序.ps1', 'Select-Folder.ps1', 'tools/apply_update.py')
        for name in bootstrap:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('updater fixture', encoding='utf-8')
        base = build_split_release.snapshot(self.source)
        base['version'] = '0.4.14'
        baseline = self.root / 'baseline.json'
        baseline.write_text(json.dumps(base), encoding='utf-8')
        with patch.object(sys, 'argv', ['build_split_release', '--source', str(self.source),
                                       '--out', str(self.output), '--core-only', '--base', str(baseline)]), contextlib.redirect_stdout(io.StringIO()):
            build_split_release.main()
        with zipfile.ZipFile(self.output / 'MarkdownReader-0.4.14-to-0.4.15-update.zip') as archive:
            manifest = json.loads(archive.read('update-manifest.json'))
            for name in bootstrap:
                self.assertIn(name, archive.namelist())
                self.assertEqual(manifest['files'][name]['before'], base['files'][name])
