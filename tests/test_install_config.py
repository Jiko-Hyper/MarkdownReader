import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mdreader import core


class InstallConfigTests(unittest.TestCase):
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
