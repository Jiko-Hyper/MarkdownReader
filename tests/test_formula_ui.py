# -*- coding: utf-8 -*-
"""F05 在真实窗口上的回归：预览把公式画成图片嵌进去。

    python -m unittest tests.test_formula_ui

规则与渲染本身在 `tests/test_formula.py` 里逐条验过；这里只锁窗口这一层：

* 行内与独立块公式都**真的变成图片**（不是替代文字，也不是原始 `$…$`）；
* 画不出来的公式把**源码留在正文里**，不吞掉内容；
* 行内代码、转义美元符号、金额文本保持原样；
* 换主题/缩放时图片跟着重画（缓存按字号与主题分开）。
"""
from __future__ import annotations

import os
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest import mock

from mdreader import winui


SOURCE = (
    "# 公式\n\n"
    "行内 $E = mc^{2}$ 与代码 `$不是公式$`，还有转义 \\$5 与金额 $5 与 $6 元。\n\n"
    "$$\n\\frac{a}{b} = \\sqrt[3]{x + 1}\n$$\n"
)


class FormulaPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-formula-ui-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "公式笔记.md"
        self.path.write_text(SOURCE, encoding="utf-8")
        drop = mock.patch.object(winui.MarkdownWindow, "enable_file_drop")
        drop.start()
        self.addCleanup(drop.stop)
        self.win = winui.MarkdownWindow(str(self.root / "workspace"))
        self.win.root.withdraw()
        self.addCleanup(self.destroy_window, self.win.root)

    @staticmethod
    def destroy_window(root):
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()

    def show(self, text=None):
        if text is not None:
            self.path.write_text(text, encoding="utf-8")
        self.win.open_local_files([str(self.path)])
        self.win.root.update()
        return self.win.preview

    def formulas(self):
        return [child for child in self.win.preview.winfo_children()
                if hasattr(child, "_formula_tex")]

    def cache_files(self):
        directory = Path(self.root, "workspace", "formula-cache")
        return sorted(os.listdir(directory)) if directory.is_dir() else []

    # -- 正常路径 --------------------------------------------------------
    def test_toolbar_inserted_formulas_preview_without_saving_the_file(self):
        self.show("公式草稿\n")
        self.win.toggle_mode()
        editor = self.win.editor
        editor.mark_set("insert", "end-1c")
        self.assertTrue(self.win.format_selection("formula_inline"))
        self.assertIn("$x$", editor.get("1.0", "end-1c"))
        editor.tag_remove("sel", "1.0", "end")
        editor.mark_set("insert", "end-1c")
        self.assertTrue(self.win.format_selection("formula_block"))
        self.win.toggle_mode()
        self.win.root.update()
        self.assertEqual([child._formula_display for child in self.formulas()], [False, True])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "公式草稿\n")

    def test_inline_and_display_formulas_become_images(self):
        self.show()
        found = self.formulas()
        self.assertEqual([child._formula_tex for child in found],
                         ["E = mc^{2}", "\\frac{a}{b} = \\sqrt[3]{x + 1}"])
        self.assertEqual([child._formula_display for child in found], [False, True])
        for child in found:
            self.assertTrue(str(child.cget("image")), "公式必须真的嵌成图片")
            self.assertGreater(int(child.image.width()), 8)
            self.assertGreater(int(child.image.height()), 8)
        self.assertEqual(len(self.cache_files()), 2, "两张图各自缓存一份")

    def test_text_around_the_formula_is_untouched(self):
        preview = self.show()
        text = preview.get("1.0", "end-1c")
        self.assertIn("行内", text)
        self.assertIn("$不是公式$", text, "行内代码里的美元符号不该变成公式")
        self.assertIn("$5 与 $6 元", text, "金额文本不该变成公式")

    def test_theme_change_repaints_the_picture(self):
        self.show()
        before = self.cache_files()
        self.win.set_theme("dark")
        self.win.render()
        self.win.root.update()
        self.assertEqual(len(self.formulas()), 2, "换主题后公式图片还在")
        after = self.cache_files()
        self.assertGreater(len(after), len(before), "暗色主题应当另有一份缓存")

    def test_zoom_keeps_the_pictures_and_reloads_them(self):
        self.show()
        first = self.formulas()[0]
        before_width = int(first.image.width())
        self.win.base_size = self.win.base_size + 6
        self.win._style_widget(self.win.preview, force=True)
        self.win.root.update()
        self.win._restyle_tables()
        self.win.root.update()
        found = self.formulas()
        self.assertEqual(len(found), 2)
        self.assertGreater(int(found[0].image.width()), before_width,
                           "放大字号后公式图片也要跟着变大")

    def drain_formulas(self):
        refresh = self.win.preview._formula_refresh
        deadline = time.perf_counter() + 5
        while refresh.remaining and time.perf_counter() < deadline:
            self.win.root.update()
            time.sleep(.002)
        self.assertFalse(refresh.remaining, 'Formula updates did not finish')

    def test_many_formulas_defer_rendering_and_follow_latest_zoom(self):
        self.show(''.join('正文 $x^{%d}$\n\n' % n for n in range(30)))
        first = self.formulas()[0]
        old_width = first.image.width()
        from mdreader import formula
        with mock.patch.object(formula, 'cached', wraps=formula.cached) as cached:
            self.win.base_size += 2
            self.win._apply_zoom()
            self.assertEqual(cached.call_count, 0, 'Zoom must not redraw the whole document synchronously')
            self.assertTrue(self.win.preview._formula_refresh.remaining)
            self.win.base_size += 2
            self.win._apply_zoom()
            self.drain_formulas()
        self.assertGreater(first.image.width(), old_width)
        self.assertEqual(self.win.preview._formula_refresh.style[1], self.win.base_size)
        self.assertEqual(len(self.formulas()), 30)

    def test_duplicate_formulas_share_decoded_images_and_zoom_back_reuses_them(self):
        self.show('正文 $x^{2}$\n\n' * 20)
        original = self.formulas()[0].image
        self.assertTrue(all(label.image is original for label in self.formulas()))
        self.win.base_size += 1
        self.win._apply_zoom()
        self.drain_formulas()
        from mdreader import formula
        with mock.patch.object(formula, 'cached', side_effect=AssertionError('Decoded image cache missed')):
            self.win.base_size -= 1
            self.win._apply_zoom()
            self.drain_formulas()
        self.assertTrue(all(label.image is original for label in self.formulas()))

    def test_switching_document_cancels_pending_formula_updates(self):
        self.show(''.join('正文 $x^{%d}$\n\n' % n for n in range(20)))
        self.win.base_size += 1
        self.win._apply_zoom()
        previous = self.win.preview._formula_refresh
        self.assertTrue(previous.remaining)
        self.win.source = '# 新文档\n\n保留新正文'
        self.win.render()
        self.win.root.update()
        self.assertIsNone(previous.job)
        self.assertFalse(previous.remaining)
        self.assertIn('保留新正文', self.win.preview.get('1.0', 'end-1c'))

    def test_zoom_with_memory_only_formula_cache_still_resizes(self):
        self.show('正文 $x^{2}$\n')
        label = self.formulas()[0]
        before = label.image.width()
        self.win.preview._formula_cache = ''
        self.win.base_size += 3
        self.win._apply_zoom()
        self.assertGreater(label.image.width(), before)

    def test_slow_formula_worker_does_not_block_scrolling_or_ui_timers(self):
        self.show(''.join('正文 $x^{%d}$\n\n' % n for n in range(20)))
        started, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        worker_threads = []
        prepare = winui._prepare_formula
        def blocked(*args):
            worker_threads.append(threading.get_ident())
            started.set()
            release.wait(2)
            return prepare(*args)
        with mock.patch.object(winui, '_prepare_formula', blocked):
            try:
                self.win.base_size += 1
                self.win._apply_zoom()
                deadline = time.perf_counter() + 2
                while not started.is_set() and time.perf_counter() < deadline:
                    self.win.root.update()
                    time.sleep(.002)
                self.assertTrue(started.is_set())
                heartbeat = []
                self.win.root.after(0, lambda: heartbeat.append(True))
                before = self.win.preview.index('@0,0')
                self.win.preview.yview_scroll(1, 'pages')
                self.win.root.update()
                self.assertTrue(heartbeat, 'UI timer must run while formula rendering is blocked')
                self.assertNotEqual(before, self.win.preview.index('@0,0'))
                self.assertTrue(self.win.preview._formula_refresh.remaining)
            finally:
                release.set()
            self.drain_formulas()
        self.assertTrue(worker_threads)
        self.assertNotIn(main_thread, worker_threads)

    # -- 失败与边界 ------------------------------------------------------
    def test_a_formula_that_cannot_be_drawn_keeps_its_source(self):
        preview = self.show("正文 $\\foo{x}$ 结尾。\n")
        self.assertEqual(self.formulas(), [], "画不出来就不要嵌图")
        text = preview.get("1.0", "end-1c")
        self.assertIn("\\foo{x}", text, "错误公式必须留下源码")
        self.assertIn("结尾", text)

    def test_a_good_formula_next_to_a_bad_one_still_renders(self):
        self.show("好的 $x^{2}$ 与坏的 $\\foo{y}$ 混在一起。\n")
        self.assertEqual(len(self.formulas()), 1)
        self.assertIn("\\foo{y}", self.win.preview.get("1.0", "end-1c"))

    def test_unclosed_display_formula_does_not_eat_the_document(self):
        preview = self.show("开头\n\n$$\n\\frac{a}{b}\n\n结尾一句\n")
        text = preview.get("1.0", "end-1c")
        self.assertIn("结尾一句", text, "没有闭合的 $$ 不能把后面的正文吞掉")

    def test_a_document_without_formulas_has_no_cache_at_all(self):
        self.show("# 只有正文\n\n没有公式。\n")
        self.assertEqual(self.formulas(), [])
        self.assertEqual(self.cache_files(), [], "没有公式就不该建缓存目录")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
