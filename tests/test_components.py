import io
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from mdreader.components import ComponentStore, checked_path, missing_for
from tools.apply_update import apply_update


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root/'app'
        self.app.mkdir()
        self.workspace = self.root/'workspace'

    def package(self, payload=None, name='photo-ocr'):
        payload = payload or {'rapidocr/__init__.py': b'test engine', 'models/test.bin': b'model'}
        archive = self.root/(name + '.zip')
        with zipfile.ZipFile(archive, 'w') as z:
            for path, data in payload.items():
                z.writestr(path, data)
        row = {'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'bytes': archive.stat().st_size,
               'unpacked_bytes': sum(map(len, payload.values())), 'files': {p: hashlib.sha256(d).hexdigest() for p, d in payload.items()},
               'url': 'https://example.test/component.zip'}
        (self.app/'components.json').write_text(json.dumps({'components': {name: row}}), encoding='utf-8')
        return archive, row

    def test_import_verified_component_reused_after_app_upgrade(self):
        archive, row = self.package()
        store = ComponentStore(self.workspace, self.app)
        installed = store.install('photo-ocr', archive)
        self.assertEqual((installed/'models/test.bin').read_bytes(), b'model')
        self.assertEqual(store.locate('photo-ocr'), installed)
        with patch('urllib.request.urlopen', side_effect=AssertionError('must not download twice')):
            self.assertEqual(ComponentStore(self.workspace, self.app).install('photo-ocr'), installed)
        self.assertEqual(missing_for(store, 'photo-md'), [])

    def test_wrong_archive_and_cancellation_never_publish(self):
        archive, _ = self.package()
        original = archive.read_bytes()
        archive.write_bytes(original[:-1] + b'x')
        store = ComponentStore(self.workspace, self.app)
        with self.assertRaises(ValueError):
            store.install('photo-ocr', archive)
        self.assertIsNone(store.installed('photo-ocr'))
        archive.write_bytes(original)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            store.install('photo-ocr', archive, cancel=cancel)
        self.assertIsNone(store.installed('photo-ocr'))

    def test_all_unsafe_paths_rejected(self):
        for path in ('../escape', '/escape', 'C:/escape', 'a\\b', 'a/../b', 'a//b', 'a/./b'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                checked_path(path)

    def test_duplicate_archive_entries_rejected(self):
        archive, row = self.package({'File': b'one', 'file': b'two'})
        with self.assertRaises(ValueError):
            ComponentStore(self.workspace, self.app).install('photo-ocr', archive)

    def test_bundled_offline_and_development_components_reused(self):
        folder = self.app/'scan-runtime'
        (folder/'models-home').mkdir(parents=True)
        (folder/'python.exe').write_bytes(b'exe')
        (folder/'models-home/config.yaml').write_text('{}')
        store = ComponentStore(self.workspace, self.app)
        self.assertEqual(missing_for(store, 'scan-md'), [])
        self.assertEqual(store.locate('scan-models'), folder/'models-home')

    def test_resume_https_download_and_remove_temporary_archive(self):
        archive, row = self.package()
        store = ComponentStore(self.workspace, self.app)
        store.root.mkdir(parents=True)
        partial = store.root/(row['sha256'] + '.download')
        partial.write_bytes(archive.read_bytes()[:20])
        class Response(io.BytesIO):
            status = 206
            headers = {'Content-Range': 'bytes 20-%d/%d' % (row['bytes']-1, row['bytes'])}
            def geturl(self):
                return 'https://example.test/component.zip'
        with patch('urllib.request.urlopen', return_value=Response(archive.read_bytes()[20:])) as opened:
            result = store.install('photo-ocr')
        self.assertEqual(opened.call_args.args[0].headers['Range'], 'bytes=20-')
        self.assertTrue(result.is_dir())
        self.assertFalse(partial.exists())

    def test_range_ignored_restarts_cleanly(self):
        archive, row = self.package()
        store = ComponentStore(self.workspace, self.app)
        store.root.mkdir(parents=True)
        (store.root/(row['sha256']+'.download')).write_bytes(b'old prefix')
        class Response(io.BytesIO):
            status = 200
            headers = {}
            def geturl(self):
                return 'https://example.test/component.zip'
        with patch('urllib.request.urlopen', return_value=Response(archive.read_bytes())):
            self.assertTrue(store.install('photo-ocr').is_dir())

    def test_different_archive_metadata_keeps_same_content_cache(self):
        archive, row = self.package()
        row['content_id'] = 'a'*64
        (self.app/'components.json').write_text(json.dumps({'components': {'photo-ocr': row}}))
        store = ComponentStore(self.workspace, self.app)
        installed = store.install('photo-ocr', archive)
        row['sha256'] = 'b'*64
        (self.app/'components.json').write_text(json.dumps({'components': {'photo-ocr': row}}))
        self.assertEqual(ComponentStore(self.workspace, self.app).installed('photo-ocr'), installed)


class ProgramUpdateTests(unittest.TestCase):
    setUp = ComponentTests.setUp
    def update_fixture(self, bad=False):
        install = self.root/'installed'
        (install/'mdreader').mkdir(parents=True)
        (install/'runtime/photo_ocr_runtime').mkdir(parents=True)
        (install/'runtime/python.exe').write_bytes(b'python')
        (install/'runtime/photo_ocr_runtime/model.bin').write_bytes(b'keep model')
        (install/'MDReader.exe').write_bytes(b'launcher')
        before = b'APP_VERSION = "1.0.0"\n'
        after = b'APP_VERSION = "1.0.1"\n'
        (install/'mdreader/core.py').write_bytes(before)
        (install/'my-notes.md').write_bytes(b'keep notes')
        rows = {'mdreader/core.py': {'sha256': hashlib.sha256(after).hexdigest(), 'before': hashlib.sha256(before).hexdigest()}}
        archive = self.root/'update.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('mdreader/core.py', b'corrupt' if bad else after)
            z.writestr('update-manifest.json', json.dumps({'schema': 1, 'base_version': '1.0.0', 'version': '1.0.1', 'files': rows}))
        return install, archive, before, after

    def test_update_replaces_only_changed_files_and_backs_up(self):
        install, archive, before, after = self.update_fixture()
        backup = apply_update(install, archive)
        self.assertEqual((install/'mdreader/core.py').read_bytes(), after)
        self.assertEqual((backup/'mdreader/core.py').read_bytes(), before)
        self.assertFalse((backup/'runtime').exists())
        self.assertEqual((install/'my-notes.md').read_bytes(), b'keep notes')
        self.assertEqual((install/'runtime/photo_ocr_runtime/model.bin').read_bytes(), b'keep model')

    def test_update_corruption_and_wrong_base_do_not_write(self):
        install, archive, before, _ = self.update_fixture(bad=True)
        with self.assertRaises(ValueError):
            apply_update(install, archive)
        self.assertEqual((install/'mdreader/core.py').read_bytes(), before)

    def test_modified_source_is_preserved(self):
        install, archive, _, _ = self.update_fixture()
        (install/'mdreader/core.py').write_bytes(b'APP_VERSION = "1.0.0"\n# custom edit')
        with self.assertRaises(ValueError):
            apply_update(install, archive)
        self.assertTrue((install/'mdreader/core.py').read_text().endswith('# custom edit'))

    def test_write_failure_rolls_back_already_replaced_file(self):
        install, archive, before, _ = self.update_fixture()
        with zipfile.ZipFile(archive) as z:
            manifest = json.loads(z.read('update-manifest.json'))
            core_data = z.read('mdreader/core.py')
        manifest['files']['main.py'] = {'sha256': hashlib.sha256(b'new main').hexdigest(), 'before': None}
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('mdreader/core.py', core_data)
            z.writestr('main.py', b'new main')
            z.writestr('update-manifest.json', json.dumps(manifest))
        import os
        original = os.replace
        def replace(source, target):
            if Path(target).name == 'main.py':
                raise PermissionError('simulated locked file')
            return original(source, target)
        with patch('tools.apply_update.os.replace', side_effect=replace), self.assertRaises(PermissionError):
            apply_update(install, archive)
        self.assertEqual((install/'mdreader/core.py').read_bytes(), before)
        self.assertFalse((install/'main.py').exists())


class ComponentProgressUITests(unittest.TestCase):
    from tests.test_native_tabs import NativeTabsTests
    setUp = NativeTabsTests.setUp
    destroy_window = staticmethod(NativeTabsTests.destroy_window)

    def test_nested_component_progress_can_cancel_and_restores_manager_grab(self):
        from mdreader.media_ui import run_job
        w = self.win
        w.root.deiconify()
        w.root.update()
        manager = w._tk.Toplevel(w.root)
        manager.title('Component Manager')
        manager.update()
        manager.grab_set()
        cancel = threading.Event()
        seen = []
        def stop():
            progress = w.root.grab_current()
            seen.append(progress is not manager and progress.title() == '安装识别组件')
            button = next(c for c in progress.winfo_children() if c.winfo_class() == 'Button')
            button.invoke()
        w.root.after(50, stop)
        result = run_job(w, lambda: cancel.wait(2), cancel=cancel, status=lambda: 'Downloading', title='安装识别组件')
        self.assertTrue(result)
        self.assertEqual(seen, [True])
        self.assertIs(w.root.grab_current(), manager)
        manager.destroy()


if __name__ == '__main__':
    unittest.main()
