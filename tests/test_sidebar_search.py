from pathlib import Path
import unittest
from unittest.mock import patch

from mdreader import winui
from tests import test_native_tabs


class SidebarSearchTests(unittest.TestCase):
    setUp = test_native_tabs.NativeTabsTests.setUp
    destroy_window = staticmethod(test_native_tabs.NativeTabsTests.destroy_window)

    def show(self, widget):
        self.win.root.deiconify()
        self.win.root.update()
        widget.focus_force()
        self.win.root.update()

    def type_query(self, widget, text):
        self.show(widget)
        widget.delete(0, 'end')
        widget.insert(0, text)
        self.win.root.update()

    def projects(self):
        w = self.win
        self.first = w.ws.create_project('Alpha 资料')
        self.second = w.ws.create_project('第二项目 Beta')
        w.cur_pid = self.first['id']
        w.refresh_projects()

    def folders(self):
        self.locations = []
        for parent in ('left', 'right'):
            folder = Path(self.temp.name, parent, '相同目录')
            folder.mkdir(parents=True)
            (folder / 'read.md').write_text(parent, encoding='utf-8')
            self.locations.append(self.win.ws.folders.add(str(folder)))
        self.win.cur_root = self.locations[0]['id']
        self.win.refresh_roots(force=True)

    def test_project_search_filters_without_switching_until_enter(self):
        self.projects()
        w = self.win
        expected = next(index for index, item in enumerate(w.projects) if item['id'] == self.second['id'])
        self.type_query(w.cmb, 'bEtA')
        self.assertEqual(w.cur_pid, self.first['id'])
        self.assertEqual(w.cmb._visible_indices, [expected])
        self.assertTrue(w.cmb._popup.winfo_viewable())
        self.assertIs(w.root.focus_get(), w.cmb, '搜索结果不得抢走打字焦点')
        w.cmb.event_generate('<Return>')
        w.root.update()
        self.assertEqual(w.cur_pid, self.second['id'])
        self.assertEqual(w.cmb.current(), expected)
        self.assertFalse(w.cmb._popup.winfo_viewable())

    def test_same_name_folder_can_be_found_by_path_and_clicked(self):
        self.folders()
        w = self.win
        expected = next(index for index, item in enumerate(w.roots) if item['id'] == self.locations[1]['id'])
        self.type_query(w.cmb_root, 'right')
        self.assertEqual(w.cmb_root._visible_indices, [expected])
        self.assertIn('right', w.cmb_root._list.get(0))
        w.cmb_root._list.event_generate('<ButtonPress-1>', x=5, y=5)
        w.root.update()
        self.assertEqual(w.cur_root, self.locations[1]['id'])
        self.assertEqual(w.cmb_root.current(), expected)
        self.assertTrue(w.root_tree.exists('f:read.md'))

    def test_refresh_does_not_erase_query_or_select_a_filtered_index(self):
        self.projects()
        self.folders()
        w = self.win
        self.type_query(w.cmb_root, 'right')
        before = w.cur_root
        w.refresh_roots()
        self.assertEqual(w.cmb_root.get(), 'right')
        self.assertEqual(w.cur_root, before)
        self.type_query(w.cmb, 'Beta')
        w.refresh_projects(history_only=True)
        self.assertEqual(w.cmb.get(), 'Beta')
        self.assertEqual(w.cur_pid, self.first['id'])

    def test_no_results_escape_clear_and_arrow_selection(self):
        self.projects()
        w = self.win
        self.type_query(w.cmb, '没有任何匹配项')
        self.assertEqual(w.cmb._visible_indices, [])
        self.assertEqual(w.cmb._list.get(0), '没有匹配项')
        w.cmb.event_generate('<Return>')
        self.assertEqual(w.cur_pid, self.first['id'])
        w.cmb.event_generate('<Escape>')
        self.assertEqual(w.cmb.get(), 'Alpha 资料 (1)')
        self.assertFalse(w.cmb._popup.winfo_viewable())
        self.type_query(w.cmb, '')
        self.assertEqual(len(w.cmb._visible_indices), 2)
        expected = w.projects[w.cmb._visible_indices[1]]['id']
        w.cmb.event_generate('<Down>')
        w.cmb.event_generate('<Return>')
        w.root.update()
        self.assertEqual(w.cur_pid, expected)

    def test_delete_is_text_editing_and_typing_does_not_rename_history(self):
        self.projects()
        self.folders()
        w = self.win
        for combo, items in ((w.cmb, w.ws.list_projects), (w.cmb_root, w.ws.folders.roots)):
            original = items()
            self.type_query(combo, 'abcdef')
            combo.icursor(0)
            combo.selection_clear()
            with patch.object(winui.SavePrompt, 'show') as prompt:
                combo.event_generate('<Delete>')
                w.root.update()
            prompt.assert_not_called()
            self.assertEqual(combo.get(), 'bcdef')
            self.assertEqual(items(), original)

    def test_three_themes_cover_typing_results_and_chinese_preedit(self):
        self.projects()
        self.folders()
        w = self.win
        for theme in ('dark', 'light', 'eye'):
            w.set_theme(theme, persist=False)
            for combo, adapter in ((w.cmb, w.ime_project_search), (w.cmb_root, w.ime_folder_search)):
                self.type_query(combo, '相' if combo is w.cmb_root else '项目')
                self.assertEqual(str(combo.cget('state')), 'normal')
                self.assertEqual(w.style.lookup('TCombobox', 'fieldbackground', ('focus',)), w.pal['bg'])
                self.assertEqual(combo._list.cget('background'), w.pal['bg'])
                self.assertEqual(combo._list.cget('selectbackground'), w.pal['sel'])
                self.assertTrue(adapter.editable)
                original = combo.get()
                adapter.surface.show('xiang', 3)
                w.root.update_idletasks()
                self.assertEqual(adapter.surface.canvas.cget('background'), w.pal['bg'])
                self.assertEqual(combo.get(), original, '输入法组合不能成为已提交搜索文字')
                adapter.surface.hide()
