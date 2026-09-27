"""Delivery checks use installed ZIPs, not direct calls into exporter source."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest

from mdreader import core
from tools.verify_delivery import (FIXTURES, ROOT, install_exports, make_assets,
                                   missing_text, missing_table_headers, page_machine_issues, verify_case)


class DeliveryTests(unittest.TestCase):
    def test_page_report_includes_geometry_failures_on_the_correct_page(self):
        row = {'issues': ['PDF 内容越出页边距'], 'inspection': {
            'content_violations': [{'page': 2}], 'image_text_overlap': [{'page': 3}]}}
        self.assertEqual(page_machine_issues(row, 1), [])
        self.assertEqual(page_machine_issues(row, 2), ['内容越出页边距'])
        self.assertEqual(page_machine_issues(row, 3), ['图片覆盖文字'])

    def test_cancel_real_exports_preserves_existing_target_and_source(self):
        from mdreader.media_ui import export_with_cancel
        with tempfile.TemporaryDirectory(prefix='delivery-cancel-') as temp:
            folder = Path(temp)
            doc = folder / 'source.md'
            doc.write_text('# Cancel\n\nOriginal content.\n', encoding='utf-8')
            original = doc.read_bytes()
            ws = core.Workspace(str(folder / 'workspace'))
            try:
                install_exports(ws)
                api = core.Api(ws, str(ROOT / 'webui'))
                info = api.loose.open_path(str(doc))
                for command in ws.plugins.commands():
                    dest = folder / ('existing.' + command['extension'])
                    dest.write_bytes(b'original-output')
                    cancel = threading.Event()
                    cancel.set()
                    result = export_with_cancel(api, {'command': command['command'],
                        'doc': str(doc), 'revision': info['revision'], 'dest': str(dest),
                        'overwrite': True, 'confirm': True}, cancel)
                    self.assertTrue(result['cancelled'])
                    self.assertEqual(dest.read_bytes(), b'original-output')
                    self.assertEqual(doc.read_bytes(), original)
                # Completion alone must not publish output; cancellation may arrive
                # after the process finishes but before the host commits it.
                command = ws.plugins.commands()[0]
                dest = folder / ('late.' + command['extension'])
                result = api.post('/api/plugins/export', {'command': command['command'],
                    'doc': str(doc), 'dest': str(dest), 'background': True})
                task_id = result['task']['id']
                ws.plugins.tasks.poll(task_id, wait=60)
                self.assertFalse(dest.exists())
                api.post('/api/plugins/task/cancel', {'id': task_id})
                with self.assertRaises(ValueError):
                    api.post('/api/plugins/export', {'task': task_id})
                self.assertFalse(dest.exists())
            finally:
                ws.plugins.shutdown()

    def test_entire_missing_header_is_detected_on_each_table_page(self):
        labels = ['记录号', '数值', '备注']
        self.assertEqual(missing_table_headers(
            ['记录号 数值 备注', '只有数据', '记录号 数值', '记录号 数值 备注'], labels), [2, 3])
        self.assertEqual(missing_table_headers(['只有数据'], labels), [1])
        self.assertEqual(missing_table_headers(['普通正文'], None), [])

    def test_fixed_corpus_with_real_plugins(self):
        with tempfile.TemporaryDirectory(prefix='delivery-regression-') as temp:
            folder = Path(temp)
            cases = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf-8'))['cases']
            self.assertGreaterEqual(len(cases), 20)
            make_assets(folder / 'assets')
            ws = core.Workspace(str(folder / 'workspace'))
            try:
                install_exports(ws)
                api = core.Api(ws, str(ROOT / 'webui'))
                commands = {cmd['extension']: cmd['command'] for cmd in ws.plugins.commands()}
                for case in cases:
                    shutil.copyfile(FIXTURES / (case['id'] + '.md'), folder / (case['id'] + '.md'))
                    for result in verify_case(api, commands, folder, case, render=False):
                        with self.subTest(case=case['id'], format=result['format']):
                            self.assertEqual(result['issues'], [], result)
            finally:
                ws.plugins.shutdown()

    def test_repeated_content_is_not_treated_as_one_occurrence(self):
        self.assertEqual(missing_text(['重复', '重复'], '重复'), ['重复'])
        self.assertEqual(missing_text(['跨行内容'], '跨行\n内容'), [])

    def test_footnotes_do_not_consume_text_or_change_code_and_normal_references(self):
        spec = importlib.util.spec_from_file_location('exporter_under_test',
            ROOT / 'plugins/official/common/exporting.py')
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        source = ('正文[^note] 和 [正常链接][url]。\n\n[^note]: 脚注内容\n\n'
                  '[url]: https://example.com\n\n```text\n[^literal]: code\n```\n')
        blocks = exporter.blocks(source)
        texts = ''.join(token.content for block in blocks for token in block.get('tokens', []))
        self.assertIn('正文[^note]', texts)
        self.assertIn('[^note]: 脚注内容', texts)
        self.assertEqual([block['text'] for block in blocks if block['type'] == 'code'],
                         ['[^literal]: code\n'])
        links = [token.attrGet('href') for block in blocks for token in block.get('tokens', [])
                 if token.type == 'link_open']
        self.assertEqual(links, ['https://example.com'])


if __name__ == '__main__':
    unittest.main()
