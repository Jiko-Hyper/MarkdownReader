"""Selection navigation, independent reading positions and real key bindings."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mdreader import winui
from mdreader.navigation import (map_selection, map_markdown_selection,
                                 widget_text, widget_index)


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

    def assert_markdown_range(self, source, preview, source_range, preview_range):
        self.assertEqual(map_markdown_selection(source, preview, *preview_range), source_range)
        self.assertEqual(map_markdown_selection(source, preview, *source_range,
                                               from_preview=False), preview_range)

    def test_link_destinations_cannot_steal_repeated_visible_words(self):
        source = '[label](https://example.com/label)\n\nlabel\n'
        preview = 'label\n\nlabel\n\n'
        for source_at, preview_at in [(1, 0), (source.rindex('label'), preview.rindex('label'))]:
            self.assert_markdown_range(source, preview, (source_at, source_at + 5),
                                       (preview_at, preview_at + 5))
        source, preview = '[a](a) [a](a) [a](a)\n', 'a a a\n\n'
        for source_at, preview_at in [(1, 0), (8, 2), (15, 4)]:
            self.assert_markdown_range(source, preview, (source_at, source_at + 1),
                                       (preview_at, preview_at + 1))

    def test_emphasis_inside_repeated_english_word_maps_both_directions(self):
        source, preview = 'read**ing** reading\n', 'reading reading\n\n'
        self.assert_markdown_range(source, preview, (0, 9), (0, 7))
        self.assert_markdown_range(source, preview, (12, 19), (8, 15))
        self.assert_markdown_range(source, preview, (6, 9), (4, 7))

    def test_code_keeps_literal_links_and_emphasis(self):
        source = '`[a](a)` [a](a) a\n'
        preview = ' [a](a)  a a\n\n'
        for source_at, preview_at in [(2, 2), (5, 5), (10, 9), (16, 11)]:
            self.assert_markdown_range(source, preview, (source_at, source_at + 1),
                                       (preview_at, preview_at + 1))
        source = '```md\n[a](a) **a**\n```\n\n[a](a) a\n'
        preview = '[a](a) **a**\n\n\na a\n\n'
        for source_at, preview_at in [(7, 1), (10, 4), (15, 9),
                                      (source.rindex('[a]') + 1, preview.rindex('a a')),
                                      (source.rindex('a'), preview.rindex('a'))]:
            self.assert_markdown_range(source, preview, (source_at, source_at + 1),
                                       (preview_at, preview_at + 1))

    def test_entities_cover_their_complete_source_spelling_and_unicode(self):
        source, preview = '&#x1f600; 😀 &amp; &\n', '😀 😀 & &\n\n'
        self.assert_markdown_range(source, preview, (0, 9), (0, 1))
        self.assert_markdown_range(source, preview, (10, 11), (2, 3))
        self.assert_markdown_range(source, preview, (12, 17), (4, 5))
        self.assert_markdown_range(source, preview, (18, 19), (6, 7))

    def test_objects_do_not_expose_repeated_formula_or_table_content(self):
        source, preview = 'same $same$ same $same$ same\n', 'same ￼ same ￼ same\n\n'
        for source_at, preview_at in [(0, 0), (12, 7), (24, 14)]:
            self.assert_markdown_range(source, preview, (source_at, source_at + 4),
                                       (preview_at, preview_at + 4))
        self.assert_markdown_range(source, preview, (5, 11), (5, 6))
        source = 'same\n\n| same |\n| --- |\n| same |\n\nsame\n'
        preview = 'same\n\n￼\n\nsame\n\n'
        self.assert_markdown_range(source, preview, (source.rindex('same'), source.rindex('same') + 4),
                                   (preview.rindex('same'), preview.rindex('same') + 4))
        source, preview = '![same](same.png) same\n', '🖼 same \n￼\n same\n\n'
        self.assert_markdown_range(source, preview, (source.rindex('same'), source.rindex('same') + 4),
                                   (preview.rindex('same'), preview.rindex('same') + 4))

    def test_long_repetitive_document_uses_visible_occurrence_order(self):
        source, preview = '[a](a) ' * 2500, 'a ' * 2500 + '\n'
        for ordinal in (0, 1250, 2499):
            source_at, preview_at = ordinal * 7 + 1, ordinal * 2
            self.assert_markdown_range(source, preview, (source_at, source_at + 1),
                                       (preview_at, preview_at + 1))

    def test_caret_does_not_select_a_word_and_hidden_markup_stays_local(self):
        source, preview = '[first](https://first) second\n', 'first second\n\n'
        self.assertEqual(map_markdown_selection(source, preview, 8, 8), (25, 25))
        self.assertEqual(map_markdown_selection(source, preview, 25, 25,
                                               from_preview=False), (8, 8))
        first, last = map_markdown_selection(source, preview, 10, 10, from_preview=False)
        self.assertEqual(first, last)
        self.assertLessEqual(first, len('first '))


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
        w.toggle_mode()
        assert_position(source_y)
        w.toggle_mode()
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
        w.text.event_generate('<Control-Shift-E>')
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
        w.text.event_generate('<Control-Shift-E>')
        w.root.update()
        self.assertEqual(w.mode, 'preview')
        self.assertEqual(w.source, before)
        for key, expected in [('<Control-End>', 'end-1c'), ('<Control-Home>', '1.0')]:
            w.text.event_generate(key)
            w.root.update()
            self.assertEqual(w.text.index('insert'), w.text.index(expected))

    def test_uppercase_m_remains_normal_source_input(self):
        w = self.w
        w.show_source()
        w.root.deiconify()
        w.root.update()
        w.editor.focus_force()
        w.editor.tag_remove('sel', '1.0', 'end')
        w.editor.mark_set('insert', 'end-1c')
        before = w.get_text()
        w.editor.event_generate('<KeyPress>', keycode=ord('M'), state=1)
        w.root.update()
        self.assertEqual(w.mode, 'source')
        self.assertEqual(w.get_text(), before + 'M')

    def test_real_accelerator_locates_duplicate_links_and_split_words(self):
        w = self.w
        raw = '[label](https://example.com/label)\n\nlabel\n\nread**ing** reading\n'
        w.show_source(raw)
        w.toggle_mode()
        w.root.deiconify()
        w.root.update()
        for phrase, offset in [('label', raw.rindex('label')), ('reading', raw.index('read'))]:
            with self.subTest(phrase=phrase):
                w.preview.focus_force()
                w.root.update()
                rendered = widget_text(w.preview)
                at = rendered.rindex(phrase) if phrase == 'label' else rendered.index(phrase)
                w.preview.tag_remove('sel', '1.0', 'end')
                w.preview.tag_add('sel', widget_index(w.preview, rendered, at),
                                  widget_index(w.preview, rendered, at + len(phrase)))
                w.preview.event_generate('<KeyPress>', keycode=ord('E'), state=7)
                w.root.update()
                self.assertEqual(w.mode, 'source')
                self.assertEqual(len(widget_text(w.editor, 'sel.first')), offset)
                selected = w.editor.get('sel.first', 'sel.last')
                self.assertEqual(selected.replace('**', ''), phrase)
                w.editor.event_generate('<Control-Shift-E>')
                w.root.update()
                self.assertEqual(w.mode, 'preview')
                self.assertEqual(w.preview.get('sel.first', 'sel.last'), phrase)

    def test_navigation_without_selection_uses_current_position(self):
        w = self.w
        raw = '\n\n'.join('第 %d 段：这是**重点**文字。' % i for i in range(160))
        w.show_source(raw)
        w.root.deiconify()
        w.root.update()
        w.editor.tag_remove('sel', '1.0', 'end')
        w.editor.mark_set('insert', w.editor.search('第 120 段', '1.0'))
        w.switch_at_selection()
        w.root.update()
        self.assertEqual(w.preview.get('insert', 'insert+7c'), '第 120 段')
        self.assertTrue(w.preview.bbox('insert'), '定位结果必须在可见区域内')
        w.preview.yview(w.preview.search('第 80 段', '1.0'))
        w.root.update()
        visible = w.preview.get('@0,0', '@0,0 lineend')
        w.switch_at_selection()
        w.root.update()
        self.assertEqual(w.mode, 'source')
        self.assertIn(visible, w.editor.get('insert linestart', 'insert lineend').replace('**', ''))
        self.assertTrue(w.editor.bbox('insert'))

    def test_composition_does_not_switch_or_lose_selection(self):
        w = self.w
        self.select_last('重点')
        before = w.preview.tag_ranges('sel')
        with patch.object(w, 'composing', return_value=True):
            w.switch_at_selection()
        self.assertEqual(w.mode, 'preview')
        self.assertEqual(w.preview.tag_ranges('sel'), before)

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
