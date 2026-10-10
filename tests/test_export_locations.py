"""C02: locate the inspected snapshot without editing or reloading the document."""
import hashlib
from pathlib import Path
from mdreader import core, exporting as EX, winui
from tests import test_plugins as fixtures
from tests.test_export_entry import buttons
import unittest
from unittest import mock


class LocationTests(unittest.TestCase):
    def report(self, text):
        def missing(source):
            raise FileNotFoundError(source)
        return EX.preflight(EX.snapshot(identity=__file__, markdown=text, revision='r',
            source=EX.SOURCE_BUFFER), resource_reader=missing, fonts=lambda _n: True)

    def test_repeated_images_each_have_their_own_line_and_code_is_ignored(self):
        report = self.report('```\n![a](missing.png)\n```\n![a](missing.png)\n\n![b](missing.png)\n')
        issue = report['errors'][0]
        self.assertEqual([p['line'] for p in issue['locations']], [4, 6])
        self.assertIn('路径', issue['advice'])
        self.assertTrue(report['blocked'])

    def test_formula_and_wide_table_locations_use_the_original_snapshot(self):
        table = '|'+ '|'.join('c%d'%n for n in range(13))+'|\n|'+ '|'.join(['---']*13)+'|\n'
        report = self.report('title\n\n$\\foo{x}$\n\n'+table+'\n```\n'+table+'```\n')
        issues = {p['kind']: p for p in report['warnings']}
        self.assertEqual(issues['formula']['locations'][0]['line'], 3)
        self.assertEqual([p['line'] for p in issues['wide_table']['locations']], [5])
        self.assertFalse(report['blocked'])

    def test_remote_images_and_dead_links_have_locations(self):
        report = self.report('![r](https://example.com/a.png)\n\n[local](missing.md)\n')
        issues = {p['kind']: p for p in report['warnings']}
        self.assertEqual(issues['remote_image']['locations'][0]['line'], 1)
        self.assertEqual(issues['dead_link']['locations'][0]['line'], 3)


class DesktopLocationTests(fixtures.PluginTestCase):
    window = fixtures.PluginDesktopTests.window
    open_document = fixtures.PluginDesktopTests.open_document
    enable = fixtures.PluginDesktopTests.enable

    def test_export_flow_locates_missing_image_without_creating_target(self):
        win = self.window()
        self.enable(fixtures.SAMPLE_A)
        self.open_document(text='# title\n\n![a](missing.png)\n')
        target = Path(self.base)/'never-created.txt'
        def locate(dialog):
            buttons(dialog.window)['定位所选问题'].invoke()
            return False
        with mock.patch.object(core, '_dialog_save', return_value=str(target)), \
             mock.patch.object(winui.ExportReportDialog, 'show', autospec=True, side_effect=locate):
            win.plugin_export_document(win.plugin_commands(core.PL.CAP_EXPORT)[0])
        win.root.update()
        self.assertFalse(target.exists())
        self.assertEqual(win.mode, 'source')
        self.assertEqual(win.editor.index('insert'), '3.0')
        self.assertIn('missing.png', win.get_text())

    def test_dialog_locate_stops_export_and_uses_current_theme(self):
        win = self.window()
        self.open_document()
        report = EX.preflight(EX.snapshot(identity=str(Path(self.base)/'sample.md'),
            markdown='![a](missing.png)\n', revision='r', source=EX.SOURCE_BUFFER),
            resource_reader=lambda _s: None, fonts=lambda _n: True)
        for theme in core.THEME_CHOICES:
            win.choose_theme(theme)
            selected = []
            dialog = winui.ExportReportDialog(win.root, win.pal, report=report, on_locate=selected.append)
            self.assertEqual(dialog.window.cget('bg'), win.pal['bg'])
            self.assertNotIn('仍然导出（按提示）', buttons(dialog.window))
            self.assertIn('建议', dialog.report_box.get('1.0', 'end'))
            buttons(dialog.window)['定位所选问题'].invoke()
            self.assertFalse(dialog.result)
            self.assertEqual(selected[0]['line'], 1)

    def inspected(self, win):
        win.stash_tab()
        return {'document': win.cur_loose['path'],
                'source_hash': hashlib.sha256(win.source.encode('utf-8')).hexdigest()}

    def test_locating_selects_source_without_changing_text_or_disk(self):
        win = self.window()
        path = self.open_document()
        report = self.inspected(win)
        before = Path(path).read_bytes()
        text = win.source
        win._locate_export_issue({'line': 2}, report)
        self.assertEqual(win.mode, 'source')
        self.assertEqual(win.editor.index('insert'), '2.0')
        self.assertEqual(win.get_text(), text)
        self.assertEqual(Path(path).read_bytes(), before)

    def test_changed_buffer_and_switched_document_are_not_located(self):
        win = self.window()
        self.open_document()
        report = self.inspected(win)
        win.show_source()
        win.editor.insert('end', '\nunsaved\n')
        win._locate_export_issue({'line': 1}, report)
        self.assertIn('unsaved', win.get_text())
        self.assertIn('版本不同', win.lbl_status.cget('text'))
        self.open_document(name='other.md')
        win._locate_export_issue({'line': 1}, report)
        self.assertIn('版本不同', win.lbl_status.cget('text'))
