import json
import os
import tempfile
import threading
import time
import unittest
import gc
import weakref
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mdreader.ai_providers import ProviderStore, PROVIDERS, ProviderError
from mdreader import core, winui
from mdreader.ai_assistant_ui import AssistantDialog


class FakeProvider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.respond(None)

    def do_POST(self):
        self.respond(json.loads(self.rfile.read(int(self.headers['Content-Length']))))

    def respond(self, body):
        self.server.calls.append((self.path, dict(self.headers), body))
        status = self.server.code
        if status != 200:
            data = {'error': {'message': 'do not echo secret-test-key'}}
        elif getattr(self.server, 'paginated', False) and self.path.startswith('/models'):
            if '?' in self.path:
                data = {'data': [{'id': 'custom/future-model'}], 'has_more': False}
            else:
                data = {'data': [{'id': 'old-model'}], 'has_more': True, 'last_id': 'old-model'}
        elif self.path.endswith('/models'):
            data = {'data': [{'id': 'model-a'}, {'id': 'model-b'}]}
        elif self.path.endswith('/responses'):
            data = {'output': [{'type': 'reasoning'}, {'type': 'message', 'content': [
                {'type': 'output_text', 'text': 'GPT 回复'}, {'type': 'output_text', 'text': '第二段'}]}]}
        elif self.path.endswith('/messages'):
            data = {'content': [{'type': 'thinking', 'thinking': 'hidden'}, {'type': 'text', 'text': 'Claude 回复'}]}
        else:
            data = {'choices': [{'message': {'content': 'DeepSeek 回复'}, 'finish_reason': 'stop'}]}
        if body and '只返回一个 JSON 对象' in json.dumps(body, ensure_ascii=False):
            content = json.dumps({'text': 'DeepSeek 回复', 'document': getattr(self.server, 'document', None)}, ensure_ascii=False)
            if 'choices' in data:
                data['choices'][0]['message']['content'] = content
            elif 'output' in data:
                data['output'] = [{'type': 'message', 'content': [{'type': 'output_text', 'text': content}]}]
            else:
                data['content'] = [{'type': 'text', 'text': content}]
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mdreader-models-')
        self.addCleanup(self.temp.cleanup)
        self.store = ProviderStore(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeProvider)
        self.server.calls, self.server.code = [], 200
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:%s' % self.server.server_port

    def save(self, name='deepseek'):
        return self.store.save({'provider': name, 'api_key': 'secret-test-key', 'base_url': self.base,
                                'model': PROVIDERS[name]['model']})

    def test_keys_are_encrypted_and_never_returned_by_settings(self):
        self.save()
        self.assertNotIn('secret-test-key', Path(self.store.path).read_text(encoding='utf-8'))
        public = self.store.settings()
        self.assertTrue(public['profiles']['deepseek']['has_key'])
        self.assertNotIn('secret-test-key', json.dumps(public))
        restarted = ProviderStore(self.temp.name)
        self.assertEqual(restarted._profile('deepseek')['key'], 'secret-test-key')
        self.store.save({'provider': 'deepseek', 'api_key': ''})
        self.assertEqual(self.store._profile('deepseek')['key'], 'secret-test-key')
        self.store.save({'provider': 'deepseek', 'clear_key': True})
        with self.assertRaisesRegex(ValueError, 'API Key'):
            self.store._profile('deepseek')

    def test_three_protocols_auth_and_response_parsers(self):
        for name, path, answer in [('openai', '/responses', 'GPT 回复\n第二段'),
                                   ('anthropic', '/messages', 'Claude 回复'),
                                   ('deepseek', '/chat/completions', 'DeepSeek 回复')]:
            with self.subTest(provider=name):
                self.save(name)
                if name == 'openai':
                    self.store.save({'provider': name, 'protocol': 'responses'})
                result = self.store.chat({'provider': name, 'messages': [{'role': 'user', 'content': '问题'}]})
                self.assertEqual(result['text'], answer)
                url, headers, body = self.server.calls[-1]
                self.assertEqual(url, path)
                headers = {k.lower(): v for k, v in headers.items()}
                if name == 'anthropic':
                    self.assertEqual(headers['x-api-key'], 'secret-test-key')
                    self.assertEqual(headers['anthropic-version'], '2023-06-01')
                else:
                    self.assertEqual(headers['authorization'], 'Bearer secret-test-key')
                if name == 'openai':
                    self.assertFalse(body['store'])
                self.assertEqual(body['model'], PROVIDERS[name]['model'])

    def test_context_is_opt_in_and_original_history_is_not_mutated(self):
        self.save()
        messages = [{'role': 'user', 'content': '解释'}]
        self.store.chat({'provider': 'deepseek', 'messages': messages})
        self.assertEqual(self.server.calls[-1][2]['messages'][-1]['content'], '解释')
        self.store.chat({'provider': 'deepseek', 'messages': messages, 'context': '未保存的文档'})
        self.assertIn('未保存的文档', self.server.calls[-1][2]['messages'][-1]['content'])
        self.assertEqual(messages[0]['content'], '解释')

    def test_fetch_models_and_test_send_actual_requests(self):
        self.save()
        self.assertEqual(self.store.models('deepseek')['models'], ['model-a', 'model-b'])
        self.store.chat({'provider': 'deepseek', 'context': 'never send me'}, test=True)
        self.assertNotIn('never send me', json.dumps(self.server.calls[-1]))
        self.assertIn('OK', self.server.calls[-1][2]['messages'][-1]['content'])

    def test_edit_proposals_across_protocols_and_invalid_responses(self):
        for name in PROVIDERS:
            self.save(name)
            if name == 'openai':
                self.store.save({'provider': name, 'protocol': 'responses'})
            self.server.document = '原文\n你好'
            result = self.store.chat({'provider': name, 'messages': [{'role': 'user', 'content': '末尾添加你好'}],
                                      'context': '原文', 'edit': True})
            self.assertEqual(result['document'], '原文\n你好')
            self.assertEqual(result['text'], 'DeepSeek 回复')
        payload = {'provider': 'deepseek', 'messages': [{'role': 'user', 'content': '修改'}], 'context': '', 'edit': True}
        for content, reason in [('不是 JSON', 'stop'), ('{"text":"ok","document":"partial"}', 'length'),
                                ('{"text":"ok","document":42}', 'stop')]:
            with patch.object(self.store, '_generate', return_value={'choices': [{'message': {'content': content}, 'finish_reason': reason}]}):
                with self.assertRaisesRegex(ValueError, '未更改文档'):
                    self.store.chat(payload)

    def test_arbitrary_models_and_compatible_protocol_without_vendor_parameters(self):
        self.save('openai')
        for model in ('gpt-older-version', 'o-custom-reasoning', 'vendor/new-model-2099'):
            self.store.save({'provider': 'openai', 'model': model})
            result = self.store.chat({'provider': 'openai', 'messages': [{'role': 'user', 'content': '你好'}]})
            path, headers, body = self.server.calls[-1]
            self.assertEqual(path, '/chat/completions')
            self.assertEqual(body['model'], model)
            self.assertNotIn('max_tokens', body)
            self.assertNotIn('thinking', body)
            self.assertEqual(result['protocol'], 'chat')

    def test_full_model_catalog_pagination_and_cache_survive_switches(self):
        self.save('anthropic')
        self.server.paginated = True
        result = self.store.models('anthropic')
        self.assertEqual(result['models'], ['custom/future-model', 'old-model'])
        self.assertEqual(self.server.calls[-1][0], '/models?after_id=old-model')
        self.store.save({'provider': 'deepseek'})
        self.assertEqual(self.store.settings()['profiles']['anthropic']['models'], result['models'])
        self.store.save({'provider': 'anthropic', 'api_key': 'different-test-key'})
        self.assertEqual(self.store.settings()['profiles']['anthropic']['models'], [])

    def test_model_fetch_does_not_require_a_model_selection(self):
        self.save()
        self.store.save({'provider': 'deepseek', 'model': ''})
        self.assertTrue(self.store.models('deepseek')['models'])
        with self.assertRaisesRegex(ValueError, '选择'):
            self.store.chat({'provider': 'deepseek', 'messages': [{'role': 'user', 'content': 'hi'}]})

    def test_full_endpoint_is_normalized_without_duplicating_paths(self):
        self.store.save({'provider': 'openai', 'base_url': self.base + '/v1/chat/completions',
                         'api_key': 'secret-test-key', 'model': 'arbitrary'})
        profile = self.store.settings()['profiles']['openai']
        self.assertEqual(profile['base_url'], self.base + '/v1')
        self.assertEqual(profile['protocol'], 'chat')
        self.store.chat({'provider': 'openai', 'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertEqual(self.server.calls[-1][0], '/v1/chat/completions')

    def test_automatic_responses_fallback_never_retries_auth_or_quota(self):
        self.store.save({'provider': 'openai', 'api_key': 'secret-test-key'})
        payload = {'provider': 'openai', 'messages': [{'role': 'user', 'content': 'hi'}]}
        chat_reply = {'choices': [{'message': {'content': '兼容成功'}, 'finish_reason': 'stop'}]}
        with patch.object(self.store, '_request', side_effect=[ProviderError('not found', 404), chat_reply]) as request:
            result = self.store.chat(payload)
            self.assertEqual(result['text'], '兼容成功')
            self.assertEqual([call.args[2] for call in request.call_args_list], ['/responses', '/chat/completions'])
        for code in (401, 429, 500):
            with patch.object(self.store, '_request', side_effect=ProviderError('failed', code)) as request:
                with self.assertRaises(ProviderError):
                    self.store.chat(payload)
                self.assertEqual(request.call_count, 1)

    def test_error_messages_do_not_echo_provider_body_or_key(self):
        self.save()
        for code in (401, 403, 404, 429, 500, 302):
            self.server.code = code
            with self.assertRaises(ValueError) as raised:
                self.store.models('deepseek')
            self.assertIn(str(code), str(raised.exception))
            self.assertNotIn('secret-test-key', str(raised.exception))

    def test_changing_destination_requires_reentering_key(self):
        self.save()
        with self.assertRaisesRegex(ValueError, '重新填写'):
            self.store.save({'provider': 'deepseek', 'base_url': 'https://other.example/v1'})
        self.assertEqual(self.store._profile('deepseek')['base_url'], self.base)
        for base in ('http://remote.example', 'https://user:password@api.example', 'https://api.example/?key=x'):
            with self.assertRaises(ValueError):
                self.store.save({'provider': 'deepseek', 'base_url': base, 'api_key': 'new-key'})

    def test_save_failure_and_invalid_input_preserve_settings(self):
        self.save()
        original = Path(self.store.path).read_bytes()
        with patch('mdreader.ai_providers.atomic_write', side_effect=OSError('readonly')):
            with self.assertRaises(OSError):
                self.store.save({'provider': 'deepseek', 'model': 'other'})
        self.assertEqual(original, Path(self.store.path).read_bytes())
        for messages in ([], [{'role': 'system', 'content': 'x'}], [{'role': 'user', 'content': 'x' * 200001}]):
            with self.assertRaises(ValueError):
                self.store.chat({'provider': 'deepseek', 'messages': messages})
        self.assertEqual(self.server.calls, [])

    def test_http_settings_need_ui_session_and_never_expose_key(self):
        ws, server, port = core.serve(self.temp.name)
        thread = core.ServerThread(server)
        thread.start()
        self.addCleanup(thread.stop)
        url = 'http://127.0.0.1:%d/api/assistant/settings' % port
        with self.assertRaises(HTTPError) as raised:
            urlopen(url)
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()
        self.save()
        with urlopen(Request(url, headers={'X-MDReader-Session': core.api_of(server).token})) as response:
            data = response.read().decode()
            self.assertNotIn('secret-test-key', data)
            self.assertNotIn('secret"', data)

    def test_native_assistant_background_request_and_insertion_undo(self):
        ws, server, port = core.serve(self.temp.name)
        self.addCleanup(server.server_close)
        with patch.object(winui.MarkdownWindow, 'enable_file_drop'):
            window = winui.MarkdownWindow(self.temp.name, connection_api=core.api_of(server))
        window.root.withdraw()
        def cleanup():
            for timer in window.root.tk.call('after', 'info'):
                window.root.after_cancel(timer)
            window.root.destroy()
        self.addCleanup(cleanup)
        file = Path(self.temp.name, 'demo.md')
        file.write_text('原始文档', encoding='utf-8')
        window.open_local_files([str(file)])
        window.show_ai_connection()
        dialog = window._assistant_dialog
        dialog.base.set(self.base)
        dialog.key.set('secret-test-key')
        dialog.prompt.insert('1.0', '帮我改进')
        dialog.include_document.set(True)
        start = time.monotonic()
        dialog.send()
        self.assertLess(time.monotonic() - start, 0.5)
        deadline = time.monotonic() + 5
        while dialog.busy and time.monotonic() < deadline:
            window.root.update()
            time.sleep(0.02)
        self.assertFalse(dialog.busy)
        self.assertEqual(dialog.replies['deepseek'], 'DeepSeek 回复')
        self.assertIn('原始文档', self.server.calls[-1][2]['messages'][-1]['content'])
        self.assertEqual(file.read_text(encoding='utf-8'), '原始文档')
        dialog.insert()
        self.assertIn('DeepSeek 回复', window.get_text())
        window.editor.edit_undo()
        self.assertEqual(window.get_text(), '原始文档')
        self.assertEqual(file.read_text(encoding='utf-8'), '原始文档')
        dialog.close()

    def test_native_edit_apply_conflicts_preview_and_single_undo(self):
        ws, server, port = core.serve(self.temp.name)
        self.addCleanup(server.server_close)
        with patch.object(winui.MarkdownWindow, 'enable_file_drop'):
            window = winui.MarkdownWindow(self.temp.name, connection_api=core.api_of(server))
        window.root.withdraw()
        def cleanup():
            for timer in window.root.tk.call('after', 'info'):
                window.root.after_cancel(timer)
            window.root.destroy()
        self.addCleanup(cleanup)
        file = Path(self.temp.name, 'edit.md')
        original = '# 标题\n原始文档'
        file.write_text(original, encoding='utf-8')
        window.open_local_files([str(file)])
        window.show_ai_connection()
        dialog = window._assistant_dialog
        dialog.base.set(self.base)
        dialog.key.set('secret-test-key')
        dialog.include_document.set(True)
        dialog.prompt.insert('1.0', '末尾添加你好')
        self.server.document = original + '\n你好'
        dialog.send()
        deadline = time.monotonic() + 5
        while dialog.busy and time.monotonic() < deadline:
            window.root.update()
            time.sleep(0.02)
        self.assertIsNotNone(dialog.proposals['deepseek'])
        self.assertEqual(window.get_text(), original)
        snapshot, replacement = dialog.proposals['deepseek']
        with self.assertRaisesRegex(ValueError, '切换'):
            dialog.apply_edit({'tab': {}, 'text': original}, replacement)
        with self.assertRaisesRegex(ValueError, '变化'):
            dialog.apply_edit({'tab': window.active_tab, 'text': '过时正文'}, replacement)
        self.assertEqual(window.mode, 'preview')
        dialog.apply_proposal()
        self.assertEqual(window.mode, 'preview')
        self.assertEqual(window.get_text(), replacement)
        self.assertIn('你好', window.preview.get('1.0', 'end'))
        self.assertIsNone(dialog.proposals['deepseek'])
        dialog.apply_proposal()  # repeated click cannot duplicate text
        self.assertEqual(window.get_text(), replacement)
        self.assertEqual(file.read_text(encoding='utf-8'), original)
        window.show_source()
        window.editor.edit_undo()
        window.root.update()
        self.assertEqual(window.get_text(), original)
        dialog.close()

    def test_closing_during_request_releases_all_ui_references_on_main_thread(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        dialog = AssistantDialog(root, self.store, winui.LIGHT)
        dialog.base.set(self.base)
        dialog.key.set('secret-test-key')
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def delayed(*args, **kwargs):
            started.set()
            release.wait(5)
            return {'text': 'OK', 'note': ''}
        with patch.object(self.store, 'chat', side_effect=delayed):
            dialog.test()
            self.assertTrue(started.wait(2))
            dialog.close()
            reference = weakref.ref(dialog)
            del dialog
            gc.collect()
            self.assertIsNone(reference(), 'Network worker must not retain Tk widgets or variables')
            release.set()


if __name__ == '__main__':
    unittest.main()
