import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from mdreader import taskbar
from mdreader.uninstalling import plan_cleanup
from tools.apply_update import apply_update
from tools.build_split_release import write_inventory, snapshot
from tools.migrate_layout import migrate
from tools.package_layout import organize, OUTER_FILES


class CleanLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mdreader-clean-layout-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.program = self.root / '中文 program'
        self.program.mkdir()

    def put(self, name, data=b'program'):
        path = self.program / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def clean_package(self):
        for name in OUTER_FILES:
            self.put(name)
        self.put('mdreader/core.py', b'APP_VERSION = "1.0.0"\n')
        self.put('main.py')
        self.put('runtime/python.exe')
        self.put('tools/apply_update.py')
        self.put('installation-helper.ps1')
        organize(self.program)
        write_inventory(self.program)

    def test_outer_directory_and_inventory_are_clean_without_hiding_attributes(self):
        self.clean_package()
        self.assertEqual({p.name for p in self.program.iterdir()}, OUTER_FILES | {'resources'})
        self.assertTrue((self.program/'resources/main.py').is_file())
        record = json.loads((self.program/'resources/program-files.json').read_text(encoding='utf-8'))
        self.assertIn('resources/main.py', record['files'])
        self.assertIn('resources/program-files.json', record['files'])
        self.assertEqual(snapshot(self.program)['version'], '1.0.0')
        app_id, command, icon = taskbar.identity(self.program/'resources')
        self.assertEqual(app_id, taskbar.APP_ID)
        self.assertIn(str(self.program/'MDReader.exe'), command)
        self.assertEqual(icon, str(self.program/'MDReader.exe')+',0')

    def test_resources_uninstall_keeps_user_files_and_removes_all_api_state(self):
        self.clean_package()
        workspace = self.root/'Config'
        workspace.mkdir()
        (workspace/'ai-providers.json').write_text('{"profiles":{}}')
        (workspace/'ai-connection.json').write_text('{"token":"test"}')
        (workspace/'projects/book').mkdir(parents=True)
        document = workspace/'projects/book/notes.md'
        document.write_text('keep document')
        user = self.put('resources/my-note.json', b'user')
        config = self.put('resources/installation.json')
        plan = plan_cleanup(self.program, workspace)
        selected = set(plan['files'])
        for file in ('ai-providers.json', 'ai-connection.json'):
            self.assertIn(str(workspace/file), selected)
        self.assertIn(str(config), selected)
        self.assertIn(str(self.program/'resources/main.py'), selected)
        self.assertNotIn(str(user), selected)
        self.assertNotIn(str(document), selected)

    def legacy(self):
        self.clean_package()
        owned = ['main.py', 'mdreader/core.py', 'runtime/python.exe',
                 'scan-runtime/models-home/model.bin', 'installation.json']
        for name in owned:
            self.put(name)
        self.put('program-files.json', json.dumps({'schema':1, 'files':owned}).encode())
        self.put('installed-files.json', json.dumps({'schema':1, 'files':owned}).encode())

    def test_upgrade_cleans_owned_legacy_files_and_moves_models_without_user_file_loss(self):
        self.legacy()
        retained = [self.put('mdreader/personal.md', b'notes'),
                    self.put('custom-project/project.json', b'{}'),
                    self.put('custom-project/main.py', b'personal code'),
                    self.put('projects/book/read.md', b'project'),
                    self.put('scan-runtime/models-home/my-note.txt', b'notes')]
        before = {path:path.read_bytes() for path in retained}
        extra = migrate(self.program)
        self.assertEqual(extra, ['resources/scan-runtime/models-home/model.bin'])
        self.assertFalse((self.program/'main.py').exists())
        self.assertFalse((self.program/'program-files.json').exists())
        self.assertEqual((self.program/extra[0]).read_bytes(), b'program')
        self.assertTrue((self.program/'resources/main.py').is_file())
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_bad_legacy_inventory_refuses_before_deletion(self):
        self.legacy()
        self.put('installed-files.json', b'{"schema":1,"files":["../outside"]}')
        with self.assertRaises(ValueError):
            migrate(self.program)
        self.assertTrue((self.program/'main.py').is_file())
        self.assertTrue((self.program/'scan-runtime/models-home/model.bin').is_file())

    def test_future_incremental_update_operates_inside_resources(self):
        self.clean_package()
        core = self.program/'resources/mdreader/core.py'
        before, after = core.read_bytes(), b'APP_VERSION = "1.0.1"\n'
        archive = self.root/'update.zip'
        name = 'resources/mdreader/core.py'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr(name, after)
            package.writestr('update-manifest.json', json.dumps({'schema':1, 'base_version':'1.0.0',
                'files':{name:{'before':hashlib.sha256(before).hexdigest(), 'sha256':hashlib.sha256(after).hexdigest()}}}))
        backup = apply_update(self.program, archive)
        self.assertEqual(core.read_bytes(), after)
        self.assertEqual((backup/name).read_bytes(), before)
        self.assertFalse((self.program/'main.py').exists())

    def test_setup_and_license_files_are_grouped(self):
        for name in OUTER_FILES:
            self.put(name)
        self.put('main.py')
        self.put('mdreader/core.py')
        self.put('Select-Folder.ps1')
        self.put('卸载.ps1')
        self.put('LICENSE')
        organize(self.program)
        self.assertTrue((self.program/'resources/setup/Select-Folder.ps1').is_file())
        self.assertTrue((self.program/'resources/setup/卸载.ps1').is_file())
        self.assertTrue((self.program/'resources/legal/LICENSE').is_file())
        self.assertFalse((self.program/'resources/Select-Folder.ps1').exists())

    def test_upgrade_removes_old_extras_without_retaining_stale_ownership(self):
        self.clean_package()
        old = ['resources/README.md', 'resources/docs/development.md', 'resources/安装到桌面.cmd',
               'resources/tools/smoke_release.py', 'resources/installation.json',
               'resources/scan-runtime/models-home/model.bin']
        for name in old:
            self.put(name)
        note = self.put('resources/docs/my-note.md', b'user note')
        empty = self.program/'resources/my-empty-folder'
        empty.mkdir()
        keep = migrate(self.program, old)
        self.assertEqual(keep, ['resources/scan-runtime/models-home/model.bin'])
        for name in old[:4]:
            self.assertFalse((self.program/name).exists())
        self.assertTrue((self.program/'resources/installation.json').is_file())
        self.assertEqual(note.read_bytes(), b'user note')
        self.assertTrue(empty.is_dir())
