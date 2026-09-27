"""A02: real desktop controls and offline official-plugin exports."""
from pathlib import Path
import os
import zipfile
from unittest import mock

from mdreader import core, export_ui
from tests import test_plugins as fixtures


def buttons(widget):
    result = {}
    for child in widget.winfo_children():
        if child.winfo_class() == 'Button':
            result[child.cget('text')] = child
        result.update(buttons(child))
    return result


def labels(widget):
    result = []
    for child in widget.winfo_children():
        if child.winfo_class() == 'Label':
            result.append(child.cget('text'))
        result.append(labels(child))
    return '\n'.join(result)


class ExportEntryTests(fixtures.PluginTestCase):
    window = fixtures.PluginDesktopTests.window
    open_document = fixtures.PluginDesktopTests.open_document

    def test_export_entry_remains_visible_at_minimum_window_width(self):
        win = self.window()
        width, height = win.root.minsize()
        win.root.geometry('%dx%d' % (width, height))
        win.root.deiconify()
        win.root.update()
        self.assertLessEqual(win.btn_export.winfo_rootx()+win.btn_export.winfo_width(),
                             win.root.winfo_rootx()+win.root.winfo_width())
        self.assertLessEqual(win.btn_more.winfo_rootx()+win.btn_more.winfo_width(),
                             win.root.winfo_rootx()+win.root.winfo_width())

    def test_first_use_installs_only_requested_format_and_exports_without_ai(self):
        win = self.window()
        win.ws.plugins.trust = core.PL.TrustRegistry()
        source = self.open_document()
        self.assertIn('导出', win.btn_export.cget('text'))
        dialog = export_ui.setup(win, 'pdf')
        buttons(dialog)['安装并启用'].invoke()
        self.assertIn('已就绪', labels(dialog))
        enabled = [row['id'] for row in win.ws.plugins.list_plugins() if row['enabled']]
        self.assertEqual(enabled, ['mdreader.export-pdf'])
        dialog.destroy()
        target = Path(self.base) / 'result.pdf'
        with mock.patch.object(core, '_dialog_save', return_value=str(target)):
            export_ui.choose_format(win, 'pdf')
        self.assertTrue(target.read_bytes().startswith(b'%PDF'))
        self.assertIn('打开文件', buttons(win.root))
        self.assertIn('打开所在文件夹', buttons(win.root))
        self.assertIn(os.path.basename(source), labels(win.root))

    def test_disabled_plugin_can_be_enabled_without_reinstall(self):
        win = self.window()
        win.ws.plugins.trust = core.PL.TrustRegistry()
        export_ui.prepare_format(win.ws.plugins, 'docx')
        win.ws.plugins.disable('mdreader.export-docx')
        dialog = export_ui.setup(win, 'docx')
        with mock.patch.object(win.ws.plugins, 'install') as install:
            buttons(dialog)['启用插件'].invoke()
        install.assert_not_called()
        self.assertIn('已就绪', labels(dialog))
        self.assertTrue(any(c['extension'] == 'docx' for c in win.plugin_commands()))

    def test_missing_package_explains_recovery_and_does_not_export(self):
        win = self.window()
        with mock.patch.object(export_ui, 'PACKAGES', Path(self.base)/'missing'):
            dialog = export_ui.setup(win, 'pdf')
            buttons(dialog)['安装并启用'].invoke()
        self.assertIn('没有找到对应的随包插件', labels(dialog))
        self.assertIn('插件管理', buttons(dialog))
        self.assertFalse(any(row['enabled'] for row in win.ws.plugins.list_plugins()))

    def test_failed_activation_rollback_is_not_reported_as_ready(self):
        win = self.window()
        with mock.patch.object(win.ws.plugins, 'install'), \
             mock.patch.object(win.ws.plugins, 'enable', return_value={
                 'enabled': False, 'rolled_back': True, 'reason': 'startup failed'}):
            dialog = export_ui.setup(win, 'pdf')
            buttons(dialog)['安装并启用'].invoke()
        self.assertIn('已回退', labels(dialog))
        self.assertNotIn('已就绪', labels(dialog))

    def test_closing_setup_does_not_install_anything(self):
        win = self.window()
        with mock.patch.object(win.ws.plugins, 'install') as install:
            dialog = export_ui.setup(win, 'pdf')
            buttons(dialog)['关闭'].invoke()
        install.assert_not_called()

    def test_export_dialogs_use_all_three_current_themes(self):
        win = self.window()
        for theme in core.THEME_CHOICES:
            win.choose_theme(theme)
            for dialog in (export_ui.setup(win, 'pdf'),
                           export_ui.completed(win, str(Path(self.base)/'result.pdf'), 'sample.md', [])):
                self.assertEqual(dialog.cget('bg'), win.pal['bg'])
                for control in buttons(dialog).values():
                    self.assertEqual(control.cget('bg'), win.pal['button'])
                    self.assertEqual(control.cget('fg'), win.pal['fg'])
                dialog.destroy()

    def test_result_actions_stay_bound_to_export_and_missing_file_is_explained(self):
        win = self.window()
        target = Path(self.base)/'result.pdf'
        target.write_bytes(b'%PDF-test')
        dialog = export_ui.completed(win, str(target), 'original.md', ['公式为图片'])
        self.open_document(name='other.md')
        with mock.patch('os.startfile') as opened, mock.patch.object(win.ws, 'open_in_explorer') as folder:
            buttons(dialog)['打开文件'].invoke()
            buttons(dialog)['打开所在文件夹'].invoke()
            opened.assert_called_once_with(str(target))
            folder.assert_called_once_with(str(target), select=True)
            target.unlink()
            buttons(dialog)['打开文件'].invoke()
            self.assertEqual(opened.call_count, 1)
        self.assertIn('已移动或删除', labels(dialog))

    def test_untrusted_bundled_package_is_not_installed(self):
        win = self.window()
        win.ws.plugins.trust = core.PL.TrustRegistry()
        package = Path(self.base)/'changed.zip'
        package.write_bytes(export_ui.bundled_package('mdreader.export-pdf').read_bytes())
        with zipfile.ZipFile(package, 'a') as archive:
            archive.writestr('extra.txt', 'not the trusted distribution')
        with mock.patch.object(export_ui, 'bundled_package', return_value=package):
            dialog = export_ui.setup(win, 'pdf')
            buttons(dialog)['安装并启用'].invoke()
        self.assertIn('无法启用', labels(dialog))
        self.assertFalse(any(row['installed'] for row in win.ws.plugins.list_plugins()))
