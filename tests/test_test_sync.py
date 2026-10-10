"""Desktop test synchronization preserves data and existing plugin preferences."""
import tempfile
import unittest
from pathlib import Path
from mdreader import core
from tools.sync_test_exports import ROOT, sync_exports


class TestSyncTests(unittest.TestCase):
    def test_updates_only_export_plugins_and_preserves_disabled_preference(self):
        with tempfile.TemporaryDirectory() as folder:
            ws = core.Workspace(folder)
            self.addCleanup(ws.plugins.shutdown)
            for ext in ('pdf', 'docx'):
                ws.plugins.install(str(ROOT/'plugins/archive/c01-previous'/('mdreader.export-%s-1.0.4.zip'%ext)), enable=True)
            ws.plugins.disable('mdreader.export-pdf')
            ws.plugins.install(str(ROOT/'plugins/packages/mdreader.image-insert-1.0.2.zip'), enable=True)
            image = ws.plugins.entry('mdreader.image-insert')
            note = Path(folder)/'user.md'
            note.write_bytes(b'user-notes-do-not-change')
            original_state = (Path(folder)/'plugins.json').read_bytes()
            backup = sync_exports(folder)
            self.assertEqual((backup/'plugins-before.json').read_bytes(), original_state)
            self.assertEqual(note.read_bytes(), b'user-notes-do-not-change')
            self.assertEqual(ws.plugins.entry('mdreader.image-insert'), image)
            for ext, enabled in [('pdf', False), ('docx', True)]:
                entry = ws.plugins.entry('mdreader.export-'+ext)
                self.assertEqual(entry['version'], '1.0.5')
                self.assertEqual(entry['enabled'], enabled)
                self.assertEqual(entry['previous']['version'], '1.0.4')
            sync_exports(folder)  # Repeating sync never reinstalls the same version.

    def test_missing_workspace_is_not_created(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder)/'missing'
            with self.assertRaises(ValueError):
                sync_exports(missing)
            self.assertFalse(missing.exists())
