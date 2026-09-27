"""Selection navigation, independent reading positions and real key bindings."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mdreader import winui
from mdreader.navigation import map_selection, widget_text, widget_index


class MappingTests(unittest.TestCase):
    def test_repeated_words_follow_document_order(self):
        source = '# first\n\n重复的**重点**文字\n\n# second\n\n重复的**重点**文字\n'
        preview = 'first\n重复的重点文字\nsecond\n重复的重点文字\n'
        start = preview.rindex('重点')
        a, b = map_selection(preview, source, start, start+2)
        self.assertEqual((a, b), (source.rindex('重点'), source.rindex('重点')+2))
        a, b = map_selection(source, preview, source.rindex('重点'), source.rindex('重点')+2)
        self.assertEqual(preview[a:b], '重点')
        self.assertEqual(a, start)

    def test_empty_and_markup_only(self):
        self.assertEqual(map_selection('', '', 0, 0), (0, 0))
        a, b = map_selection('**重点**', '重点', 0, 6)
        self.assertEqual((a, b), (0, 2))

    def test_long_document_keeps_common_chinese_words(self):
        source = ''.join('## section %d\n这是**重点**内容\n\n' % i for i in range(500))
        preview = ''.join('section %d\n这是重点内容\n\n' % i for i in range(500))
        start = preview.rindex('重点')
        self.assertEqual(map_selection(preview, source, start, start+2),
                         (source.rindex('重点'), source.rindex('重点')+2))

    def test_inline_markup_inside_english_word(self):
        source, preview = 'a read**ing** example', 'a reading example'
        a, b = map_selection(preview, source, 2, 9)
        self.assertEqual(source[a:b].replace('**', ''), 'reading')


class NavigationUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mdreader-nav-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name, '导航.md')
        self.path.write_text('# 第一部分\n\n这是**重点**内容\n\n# 第二部分\n\n这是**重点**内容\n', encoding='utf-8')
        drop = patch.object(winui.MarkdownWindow, 'enable_file_drop')
        drop.start()
        self.addCleanup(drop.stop)
        self.w = winui.MarkdownWindow(str(Path(self.temp.name, 'workspace')))
        self.w.root.withdraw()
        self.addCleanup(self.close)
        self.w.open_local_files([str(self.path)])

    def close(self):
        for timer in self.w.root.tk.call('after', 'info'):
            self.w.root.after_cancel(timer)
        self.w.root.destroy()

    def select_last(self, word):
        w = self.w.text
        hit = w.search(word, 'end', backwards=True)
        self.assertTrue(hit)
        w.tag_remove('sel', '1.0', 'end')
        w.tag_add('sel', hit, '%s+%dc' % (hit, len(word)))
        return hit

    def test_bidirectional_selection_and_unsaved_changes(self):
        self.select_last('重点')
        self.w.switch_at_selection()
        self.assertEqual(self.w.mode, 'source')
        self.assertEqual(self.w.text.index('insert'), '7.4')
        self.assertEqual(self.w.text.get('sel.first', 'sel.last'), '重点')
        self.w.text.insert('end', '\n新增**词语**\n')
        self.w.text.edit_separator()
        self.select_last('词语')
        self.w.switch_at_selection()
        self.assertEqual(self.w.mode, 'preview')
        self.assertEqual(self.w.text.get('sel.first', 'sel.last'), '词语')
        self.w.toggle_mode()
        self.w.text.edit_undo()
        self.assertNotIn('新增', self.w.get_text())

    def test_modes_and_tabs_remember_separate_positions(self):
        w = self.w
        def position():
            return w.text.index('@0,0'), w.text.dlineinfo('@0,0')[1]

        def assert_position(expected):
            w.root.update()
            actual = position()
            self.assertEqual(actual[0], expected[0])
            self.assertLessEqual(abs(actual[1] - expected[1]), 1)

        w.root.deiconify()
        w.root.update()
        w.show_source('\n\n'.join('第 %d 行内容' % i for i in range(300)))
        w.source = w.get_text()
        w.text.yview_moveto(.7)
        w.root.update_idletasks()
        source_y = position()
        w.toggle_mode()
        w.root.update()
        w.text.yview_moveto(.3)
        w.root.update()
        preview_y = position()
        w.switch_at_selection()
        assert_position(source_y)
        w.switch_at_selection()
        w.root.update()
        assert_position(preview_y)
        first = w.active_tab
        other = Path(self.temp.name, 'other.md')
        other.write_text('another document', encoding='utf-8')
        w.open_local_files([str(other)])
        w.activate_tab(first)
        assert_position(preview_y)
        w.toggle_mode()
        assert_position(source_y)
        w.save_reading_positions()
        w.active_tab['views'] = {}
        w.text.yview_moveto(0)
        w.restore_reading_position()
        assert_position(source_y)
        w.toggle_mode()
        assert_position(preview_y)

    def test_actual_keys_do_not_insert_shortcut_text(self):
        w = self.w
        w.root.deiconify()
        w.root.update()
        self.select_last('重点')
        w.text.focus_force()
        w.root.update()
        w.text.event_generate('<Shift-M>')
        w.root.update()
        self.assertEqual(w.mode, 'source')
        before = w.get_text()
        self.assertFalse(w.text.bind('<Control-m>'))
        with patch.object(w, 'format_selection') as formula:
            w.text.event_generate('<Control-Shift-M>')
            w.root.update()
            formula.assert_called_once_with('formula_inline')
        self.assertEqual(w.mode, 'source')
        for key, expected in [('<Control-End>', 'end-1c'), ('<Control-Home>', '1.0')]:
            w.text.event_generate(key)
            w.root.update()
            self.assertEqual(w.text.index('insert'), w.text.index(expected))
        w.text.event_generate('<Shift-M>')
        w.root.update()
        self.assertEqual(w.mode, 'preview')
        self.assertEqual(w.source, before)
        for key, expected in [('<Control-End>', 'end-1c'), ('<Control-Home>', '1.0')]:
            w.text.event_generate(key)
            w.root.update()
            self.assertEqual(w.text.index('insert'), w.text.index(expected))

    def test_embedded_objects_and_unicode_offsets(self):
        w = self.w.preview
        w.configure(state='normal')
        w.delete('1.0', 'end')
        w.insert('end', '😀前')
        label = self.w._tk.Label(w, text='object')
        w.window_create('end', window=label)
        w.insert('end', '后重点')
        text = widget_text(w)
        self.assertEqual(text, '😀前\ufffc后重点')
        index = widget_index(w, text, text.index('重点'))
        self.assertEqual(w.get(index, index+'+2c'), '重点')
