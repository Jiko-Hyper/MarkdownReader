import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mdreader import core


class InstallConfigTests(unittest.TestCase):
    def test_default_app_uses_current_packaged_exe_and_quotes_paths(self):
        with tempfile.TemporaryDirectory(prefix="reader's ") as temp:
            program = Path(temp)
            payload = program/'resources'
            (payload/'setup').mkdir(parents=True)
            (payload/'setup/Register-FileAssociation.ps1').write_text('# fixture')
            (program/'MDReader.exe').write_bytes(b'fixture')
            with patch.object(core.os, 'name', 'nt'), patch.object(core, 'app_dir', return_value=str(payload)), patch.object(core, '_run_ps') as run:
                core.open_default_app_settings()
                expected_script = str(payload/'setup/Register-FileAssociation.ps1').replace("'", "''")
                expected_exe = str(program/'MDReader.exe').replace("'", "''")
                run.assert_called_once_with("& '%s' -Exe '%s' -OpenDefaultSettings" % (expected_script, expected_exe), check=True)

    def test_default_app_rejects_incomplete_or_source_only_package(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(core.os, 'name', 'nt'), patch.object(core, 'app_dir', return_value=temp), patch.object(core, '_run_ps') as run:
            with self.assertRaisesRegex(RuntimeError, '完整安装包'):
                core.open_default_app_settings()
            run.assert_not_called()

    def test_workspace_selection(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(core, 'app_dir', return_value=temp), patch.dict(os.environ, {}, clear=True):
            config = Path(temp) / 'installation.json'
            self.assertEqual(core.default_workspace(), os.path.join(os.path.expanduser('~'), 'MDReader'))
            workspace = str(Path(temp) / '中文 data')
            config.write_text(json.dumps({'workspace': workspace}), encoding='utf-8-sig')
            self.assertEqual(core.default_workspace(), workspace)
            with patch.dict(os.environ, {'MDREADER_HOME': temp}):
                self.assertEqual(core.default_workspace(), temp)
            config.write_text('{"workspace": "relative"}', encoding='utf-8')
            with self.assertRaises(ValueError):
                core.default_workspace()
            config.write_text('broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                core.default_workspace()
