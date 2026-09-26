import json
import os
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mdreader import core
from mdreader.ai_connection import configure, settings_path, status
from mdreader.ai_connection_ui import AiConnectionDialog
from mdreader.winui import LIGHT


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mdreader-ai-connect-')
        self.addCleanup(self.temp.cleanup)
        self.ws, self.httpd, port = core.serve(self.temp.name)
        self.api = core.api_of(self.httpd)
        self.thread = core.ServerThread(self.httpd)
        self.thread.start()
        self.addCleanup(self.thread.stop)
        self.base = 'http://127.0.0.1:%d' % port

    def request(self, path, token=None, payload=None, session=False):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if session:
            headers['X-MDReader-Session'] = self.api.token
        req = Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers=headers)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_enable_from_ui_and_restore_without_environment_or_shell(self):
        self.assertFalse(status(self.api)['enabled'])
        with patch('subprocess.Popen', side_effect=AssertionError('must not invoke shell')):
            code, configured = self.request('/api/ai-connection', payload={'enabled': True}, session=True)
            self.assertEqual(code, 200)
            self.assertFalse(configured['writable'])
            self.assertTrue(configured['token'])
            self.assertIn(configured['address'], configured['instructions'])
            self.assertEqual(self.request('/api/ai/v1/tools', configured['token'])[0], 200)
            restored = core.Api(self.ws, self.api.webui)
            self.assertEqual(restored.ai.token, configured['token'])
            self.assertFalse(restored.ai.writable)

    def test_management_is_not_available_with_ai_token(self):
        token = configure(self.api, {'enabled': True})['token']
        self.assertEqual(self.request('/api/ai-connection', token)[0], 403)
        self.assertEqual(self.request('/api/ai-connection', token, {'writable': True})[0], 403)
        self.assertFalse(self.api.ai.writable)

    def test_permission_reset_disable_and_restart(self):
        first = configure(self.api, {'enabled': True})
        changed = configure(self.api, {'writable': True})
        self.assertEqual(first['token'], changed['token'])
        self.assertEqual(len(self.request('/api/ai/v1/tools', first['token'])[1]['tools']), 7)
        reset = configure(self.api, {'reset_key': True})
        self.assertNotEqual(first['token'], reset['token'])
        self.assertEqual(self.request('/api/ai/v1/tools', first['token'])[0], 403)
        self.assertEqual(self.request('/api/ai/v1/tools', reset['token'])[0], 200)
        self.assertTrue(status(self.api)['last_access'])
        configure(self.api, {'enabled': False})
        self.assertEqual(self.request('/api/ai/v1/tools', reset['token'])[0], 404)
        self.assertEqual(status(self.api)['token'], '')
        self.assertIsNone(core.Api(self.ws, self.api.webui).ai)

    def test_save_failure_preserves_live_connection_and_saved_key(self):
        old = configure(self.api, {'enabled': True})
        with patch('mdreader.ai_connection.atomic_write', side_effect=OSError('磁盘只读')):
            with self.assertRaises(OSError):
                configure(self.api, {'reset_key': True})
        self.assertEqual(self.api.ai.token, old['token'])
        self.assertEqual(core.Api(self.ws, self.api.webui).ai.token, old['token'])

    def test_bad_settings_fail_closed_and_can_be_repaired(self):
        with open(settings_path(self.api), 'w', encoding='utf-8') as stream:
            stream.write('{broken')
        fresh = core.Api(self.ws, self.api.webui)
        self.assertIsNone(fresh.ai)
        self.assertTrue(status(fresh)['error'])
        configure(fresh, {'enabled': True})
        self.assertFalse(status(fresh)['error'])
        for bad in ({'enabled': 'false'}, {'token': 'injected'}, []):
            with self.assertRaises(ValueError):
                configure(fresh, bad)

    def test_visual_controls_copy_test_and_close_without_shell(self):
        root = tk.Tk()
        root.geometry('800x650+30+30')
        root.update()
        self.addCleanup(root.destroy)
        dialog = AiConnectionDialog(root, self.api, LIGHT)
        self.addCleanup(lambda: dialog.close() if dialog.window.winfo_exists() else None)
        with patch('subprocess.Popen', side_effect=AssertionError('must not invoke shell')):
            self.assertEqual(str(dialog.copy_button['state']), 'disabled')
            dialog.toggle.invoke()
            self.assertTrue(self.api.ai)
            self.assertFalse(self.api.ai.writable)
            dialog.write_toggle.invoke()
            self.assertTrue(self.api.ai.writable)
            with patch.object(dialog.window, 'clipboard_clear'), patch.object(dialog.window, 'clipboard_append') as copied:
                dialog.copy_button.invoke()
                self.assertIn(self.api.ai.token, copied.call_args.args[0])
            dialog.test_button.invoke()
            self.assertIn('检测通过', dialog.message['text'])
            old_token = self.api.ai.token
            dialog.reset_button.invoke()
            self.assertNotEqual(old_token, self.api.ai.token)
            dialog.toggle.invoke()
            self.assertIsNone(self.api.ai)
            self.assertEqual(dialog.key.get(), '')
            timer = dialog.timer
            dialog.close()
            self.assertNotIn(timer, root.tk.call('after', 'info'))


if __name__ == '__main__':
    unittest.main()
