import json
from pathlib import Path
import tempfile
import unittest

from mdreader.ai_providers import ProviderStore
from mdreader.uninstalling import STATE_FILES, plan_cleanup


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mdreader-retain-user-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.program, self.workspace = self.root / 'Program', self.root / 'Config'
        self.program.mkdir(); self.workspace.mkdir()
        self.put(self.program, 'main.py', 'program')
        self.put(self.program, 'mdreader/core.py', 'program')
        self.inventory(['main.py', 'mdreader/core.py'])

    def put(self, root, name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')
        return path

    def inventory(self, names):
        self.put(self.program, 'program-files.json', json.dumps({'schema': 1, 'files': names}))

    def clean(self):
        plan = plan_cleanup(self.program, self.workspace)
        for name in plan['files']:
            Path(name).unlink()
        for name in plan['directories']:
            path = Path(name)
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        return plan

    def test_clear_every_provider_and_interface_setting_and_require_reconfiguration(self):
        settings = {'selected': 'deepseek', 'profiles': {
            name: {'base_url': 'https://example.test', 'model': 'test', 'secret': 'encrypted-test-only'}
            for name in ('openai', 'anthropic', 'deepseek')}}
        self.put(self.workspace, 'ai-providers.json', json.dumps(settings))
        self.put(self.workspace, 'ai-connection.json', '{"enabled":true,"token":"test-only"}')
        self.assertTrue(all(row['has_key'] for row in ProviderStore(self.workspace).settings()['profiles'].values()))
        self.clean()
        store = ProviderStore(self.workspace)
        self.assertFalse(any(row['has_key'] for row in store.settings()['profiles'].values()))
        for name in ('openai', 'anthropic', 'deepseek'):
            with self.assertRaisesRegex(ValueError, 'API Key'):
                store._profile(name)
        self.assertFalse((self.workspace / 'ai-connection.json').exists())

    def test_projects_attachments_and_unknown_files_in_both_roots_are_byte_identical(self):
        names = ['projects/book/project.json', 'projects/book/docs/chapter.md',
                 'projects/book/assets/photo.bin', 'custom-project/project.json',
                 'custom-project/settings.json', 'personal.md', 'nested/arbitrary.json',
                 'mdreader/my-own-file', 'components/my-own-file.txt', 'project-locations.json']
        protected = {}
        for base in (self.program, self.workspace):
            for name in names:
                path = self.put(base, name, name + '\n用户文件')
                protected[path] = path.read_bytes()
        for name in STATE_FILES:
            self.put(self.workspace, name, 'application state')
        self.clean()
        for path, expected in protected.items():
            self.assertEqual(path.read_bytes(), expected, str(path))
        self.assertFalse((self.program / 'main.py').exists())
        self.assertFalse((self.workspace / 'ai-providers.json').exists())

    def test_known_models_caches_and_plugin_files_removed_but_additions_retained(self):
        identity = 'a' * 64
        self.put(self.program, 'components.json', json.dumps({'components': {
            'test': {'content_id': identity, 'sha256': 'b' * 64, 'files': {'model.bin': 'checksum'}}}}))
        model = self.put(self.workspace, f'components/{identity}/model.bin', 'model')
        user = self.put(self.workspace, f'components/{identity}/user.bin', 'user')
        plugin = self.put(self.workspace, 'plugins/installed/p/1/plugin.py', 'plugin')
        self.put(plugin.parent, '.mdreader-owned-files.json', json.dumps({'schema': 1, 'files': ['plugin.py']}))
        plugin_user = self.put(plugin.parent, 'notes.md', 'user')
        cache = self.put(self.workspace, 'formula-cache/' + 'f' * 64 + '.png', 'cache')
        snapshot = self.put(self.workspace, '.recovery/' + 'c' * 64 + '.json', 'snapshot')
        self.clean()
        for path in (model, plugin, cache, snapshot):
            self.assertFalse(path.exists())
        self.assertEqual(user.read_text(), 'user')
        self.assertEqual(plugin_user.read_text(), 'user')

    def test_inventory_cannot_remove_projects_or_leave_recorded_roots(self):
        self.inventory(['main.py', 'projects/book/project.json'])
        project = self.put(self.program, 'projects/book/project.json', 'project')
        self.clean()
        self.assertEqual(project.read_text(), 'project')

    def test_malformed_inventory_refuses_before_touching_api_or_user_files(self):
        secret = self.put(self.workspace, 'ai-providers.json', 'keep on refusal')
        for bad in ('../outside', 'C:/outside', 'folder/../../outside', '/outside', 'folder\\outside'):
            with self.subTest(path=bad):
                self.inventory([bad])
                with self.assertRaises(ValueError):
                    plan_cleanup(self.program, self.workspace)
                self.assertEqual(secret.read_text(), 'keep on refusal')
