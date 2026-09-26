"""Real-window regression checks for the modeless document search."""
import unittest
from unittest.mock import patch

from tests import test_document_ui


class FindUiTests(unittest.TestCase):
    def setUp(self):
        recovery = patch.object(test_document_ui.winui.MarkdownWindow, 'offer_recovery')
        recovery.start()
        self.addCleanup(recovery.stop)
        fixture = test_document_ui.DocumentUiTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.w = fixture.win
        self.addCleanup(lambda: self.w._find_dialog.close()
                        if getattr(self.w, '_find_dialog', None) else None)
        fixture.open_document()
        fixture.edit('目标 一\n\n中间目标\n\n末尾目标\n')
        self.w.root.deiconify()
        self.w.root.update()

    def open_find(self, needle='目标'):
        self.w.find_in_document()
        finder = self.w._find_dialog
        finder.query.set(needle)
        finder.refresh()
        return finder

    def test_all_matches_and_four_button_order_and_wrap(self):
        f = self.open_find()
        self.assertEqual(list(f.buttons), ['goto the First', 'back', 'next', 'goto the Last'])
        self.assertEqual(len(f.matches), 3)
        self.assertEqual(len(self.w.text.tag_ranges('find_hit')), 6)
        self.assertIn('共 3 个匹配', f.status.cget('text'))
        self.assertEqual(f.current, 0)
        for action, expected in [('back', 2), ('next', 0), ('next', 1), ('next', 2),
                                 ('next', 0), ('goto the Last', 2), ('goto the First', 0)]:
            f.buttons[action].invoke()
            self.assertEqual(f.current, expected)
            self.assertEqual(self.w.text.index('insert'), f.matches[expected][0])
            self.assertEqual(tuple(map(str, self.w.text.tag_ranges('find_current'))), f.matches[expected])
            self.assertEqual(len(self.w.text.tag_ranges('find_hit')), 6)

    def test_typing_automatically_searches_and_returns_to_first(self):
        f = self.open_find()
        f.go('last')
        f.entry.delete(0, 'end')
        f.entry.insert(0, '中间')
        done = self.w._tk.BooleanVar(self.w.root)
        self.w.root.after(180, lambda: done.set(True))
        self.w.root.wait_variable(done)
        self.assertEqual(f.current, 0)
        self.assertEqual(len(f.matches), 1)
        self.assertEqual(self.w.text.get(*f.matches[0]), '中间')

    def test_zero_empty_literal_emoji_and_case_insensitive_queries(self):
        self.fixture.edit('😀 X.y x.y xxx\n😀')
        f = self.open_find('x.y')
        self.assertEqual(len(f.matches), 2)
        f.query.set('😀'); f.refresh()
        self.assertEqual(len(f.matches), 2)
        self.assertTrue(all(self.w.text.get(*pair) == '😀' for pair in f.matches))
        for query in ('not found', ''):
            f.query.set(query); f.refresh()
            self.assertEqual(f.matches, [])
            self.assertIn('0', f.status.cget('text'))
            self.assertFalse(self.w.text.tag_ranges('find_hit'))
            self.assertTrue(all(str(b.cget('state')) == 'disabled' for b in f.buttons.values()))

    def test_preview_search_does_not_edit_or_dirty_document(self):
        self.w.toggle_mode()
        self.w.set_dirty(False)
        before = self.w.source
        f = self.open_find()
        self.assertEqual(len(f.matches), 3)
        self.assertEqual(str(self.w.text.cget('state')), 'disabled')
        f.go('next'); f.close()
        self.assertFalse(self.w.dirty)
        self.assertEqual(self.w.source, before)
        self.assertFalse(self.w.text.tag_ranges('find_hit'))
        self.assertFalse(self.w.text.tag_ranges('find_current'))

    def test_centered_fixed_size_and_theme_colors(self):
        for theme in ('dark', 'light', 'eye'):
            self.w.set_theme(theme, persist=False)
            f = self.open_find()
            self.w.root.update_idletasks()
            self.assertEqual(tuple(map(int, f.window.resizable())), (0, 0))
            self.assertFalse(f.window.overrideredirect(), 'native title bar provides dragging and close')
            self.assertEqual(f.window.cget('bg'), self.w.pal['bg'])
            self.assertEqual(f.entry.cget('fg'), self.w.pal['fg'])
            for button in f.buttons.values():
                for state in ('fg', 'activeforeground', 'disabledforeground'):
                    self.assertEqual(button.cget(state), self.w.pal['head'])
            self.assertEqual(self.w.text.tag_cget('find_hit', 'background'), self.w.pal['sel'])
            parent_center = self.w.root.winfo_rootx() + self.w.root.winfo_width()/2
            center = f.window.winfo_rootx() + f.window.winfo_width()/2
            self.assertLess(abs(parent_center-center), 25)
            f.close()

    def test_outside_click_closes_and_preserves_existing_bindings(self):
        previous = self.w.root.bind('<ButtonPress-1>')
        f = self.open_find()
        self.w.text.event_generate('<ButtonPress-1>', x=30, y=30)
        self.assertTrue(f.closed)
        self.assertIsNone(self.w._find_dialog)
        self.assertEqual(self.w.root.bind('<ButtonPress-1>').strip(), previous.strip())
        self.assertFalse(self.w.text.tag_ranges('find_hit'))

    def test_close_button_cancels_timer_and_menu_reuses_window(self):
        f = self.open_find()
        self.w.find_in_document()
        self.assertIs(self.w._find_dialog, f)
        f.query.set('pending')
        pending = f.pending
        self.w.root.tk.call(f.window.protocol('WM_DELETE_WINDOW'))
        self.assertTrue(f.closed)
        self.assertNotIn(pending, self.w.root.tk.call('after', 'info'))
        self.assertIsNone(self.w._find_dialog)

    def test_switching_view_closes_search_without_stale_tags(self):
        f = self.open_find()
        editor = self.w.text
        self.w.toggle_mode()
        self.assertTrue(f.closed)
        self.assertFalse(editor.tag_ranges('find_hit'))

    def test_real_ctrl_f_opens_and_escape_closes(self):
        self.w.text.focus_force()
        self.w.root.update()
        self.w.text.event_generate('<Control-f>')
        self.w.root.update()
        f = self.w._find_dialog
        self.assertIsNotNone(f)
        f.entry.event_generate('<Escape>')
        self.w.root.update()
        self.assertIsNone(self.w._find_dialog)

    def test_ctrl_f_first_press_opens_from_each_main_focus_and_second_closes(self):
        self.w.toggle_mode()
        for target in (self.w.root, self.w.preview, self.w.recent_search, self.w.tree):
            with self.subTest(widget=str(target)):
                target.focus_force()
                self.w.root.update()
                target.event_generate('<Control-f>')
                self.w.root.update_idletasks()
                finder = self.w._find_dialog
                self.assertIsNotNone(finder)
                finder.query.set('目标'); finder.refresh()
                finder.entry.event_generate('<Control-f>')
                self.assertIsNone(self.w._find_dialog)
                self.assertTrue(finder.closed)
                self.assertFalse(self.w.text.tag_ranges('find_hit'))
        self.w.toggle_mode()
        original = self.w.get_text()
        self.w.root.update_idletasks()
        for _ in range(3):
            self.w.text.event_generate('<Control-f>')
            self.w.root.update_idletasks()
            finder = self.w._find_dialog
            self.assertIsNotNone(finder)
            finder.entry.event_generate('<Control-f>')
            self.assertIsNone(self.w._find_dialog)
        self.assertEqual(self.w.get_text(), original)

    def test_ctrl_f_opens_before_any_document_is_loaded(self):
        self.w.set_dirty(False)
        self.w.close_active_tab()
        self.assertIsNone(self.w.active_tab)
        self.w.root.focus_force()
        self.w.root.update()
        self.w.root.event_generate('<Control-f>')
        finder = self.w._find_dialog
        self.assertIsNotNone(finder)
        self.assertIn('请先打开', finder.status.cget('text'))
        finder.query.set('文档'); finder.refresh()
        self.assertEqual(finder.matches, [], 'empty-state instructions are not a document')
        finder.entry.event_generate('<Control-f>')
        self.assertIsNone(self.w._find_dialog)

    def test_search_entry_uses_inline_composition_in_every_theme(self):
        for theme in ('light', 'dark', 'eye'):
            self.w.set_theme(theme, persist=False)
            f = self.open_find()
            f.entry.focus_force()
            ready = self.w._tk.BooleanVar(self.w.root)
            self.w.root.after(40, lambda: ready.set(True))
            self.w.root.wait_variable(ready)
            self.assertTrue(f.ime.editable)
            self.assertTrue(f.ime.ok, f.ime.error)
            self.assertIs(f.ime.scheduler, f.window)
            before, source = f.query.get(), self.w.get_text()
            box = f.ime.surface.show("sou'suo", 4)
            self.w.root.update_idletasks()
            canvas = f.ime.surface.canvas
            self.assertIsNotNone(box)
            self.assertTrue(canvas.winfo_ismapped())
            self.assertEqual(canvas.winfo_rootx(), f.entry.winfo_rootx() + box[0])
            self.assertEqual(canvas.winfo_rooty(), f.entry.winfo_rooty() + box[1])
            self.assertGreaterEqual(canvas.winfo_rootx(), f.entry.winfo_rootx())
            self.assertGreaterEqual(canvas.winfo_rooty(), f.entry.winfo_rooty())
            self.assertLessEqual(canvas.winfo_rootx() + canvas.winfo_width(),
                                 f.entry.winfo_rootx() + f.entry.winfo_width())
            self.assertLessEqual(canvas.winfo_rooty() + canvas.winfo_height(),
                                 f.entry.winfo_rooty() + f.entry.winfo_height())
            self.assertEqual(canvas.cget('bg'), f.entry.cget('bg'))
            self.assertEqual(canvas.itemcget('preedit', 'fill'), f.entry.cget('fg'))
            self.assertEqual(canvas.itemcget('preedit', 'font'), f.entry.cget('font'))
            self.assertEqual(f.query.get(), before, 'uncommitted pinyin is not a search query')
            self.assertEqual(self.w.get_text(), source)
            f.close()
            self.assertIsNone(f.ime.hook)
            self.assertFalse(canvas.winfo_exists())

    def test_search_only_counts_current_document(self):
        f = self.open_find()
        self.assertEqual(len(f.matches), 3)
        first = self.w.active_tab
        editor = self.w.text
        other = self.fixture.path.with_name('另一个文档.md')
        other.write_text('目标只在这一处', encoding='utf-8')
        self.w.open_local_files([str(other)])
        self.assertTrue(f.closed)
        self.assertFalse(editor.tag_ranges('find_hit'))
        second = self.open_find()
        self.assertEqual(len(second.matches), 1)
        self.w.activate_tab(first)
        self.assertTrue(second.closed)
        self.assertEqual(len(self.open_find().matches), 3)
