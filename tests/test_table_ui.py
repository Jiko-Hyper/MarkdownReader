# -*- coding: utf-8 -*-
"""F04 在真实窗口上的回归：工具栏动作与表格对话框。

    python -m unittest tests.test_table_ui

规则本身在 ``tests/test_tables.py`` / ``tests/test_formatting.py`` 里逐条验过；
这里只锁“窗口怎么用这些规则”：

* 一次动作对应**一次撤销**（``edit_separator`` 包住的整段替换）；
* 输入法组合中不往里插内容；
* 表格对话框的结果真的落到缓冲区，看不懂的结构保留源码并给出原因；
* TSV 粘贴要先确认，普通粘贴完全不拦。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from mdreader import tables, winui


class TableUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-tableui-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.path = Path(self.root, "表格笔记.md")
        self.path.write_text("# 表格笔记\n\n正文\n", encoding="utf-8")
        drop = mock.patch.object(winui.MarkdownWindow, "enable_file_drop")
        drop.start()
        self.addCleanup(drop.stop)
        self.win = winui.MarkdownWindow(os.path.join(self.root, "workspace"))
        self.win.root.withdraw()
        self.addCleanup(self.destroy_window, self.win.root)

    @staticmethod
    def destroy_window(root):
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()

    # -- helpers ---------------------------------------------------------
    def open_source(self, text="正文\n"):
        self.win.open_local_files([str(self.path)])
        self.win.toggle_mode()
        self.win._set_widget(text)
        self.win.source = text
        return self.win.editor

    def select(self, editor, first, last, needle_start=None, needle_end=None):
        editor.tag_remove("sel", "1.0", "end")
        editor.mark_set("insert", first)
        editor.tag_add("sel", first, last)

    @contextmanager
    def notice(self):
        seen = []
        with mock.patch.object(winui.MarkdownWindow, "notice",
                               lambda _self, message, error=False: seen.append((message, error))):
            yield seen

    # -- 工具栏 ----------------------------------------------------------
    def test_bold_action_uses_the_shared_rules_and_is_one_undo_step(self):
        editor = self.open_source("这是重点\n")
        self.select(editor, "1.2", "1.4")
        self.assertTrue(self.win.format_selection("bold"))
        self.assertEqual(self.win.get_text(), "这是**重点**\n")
        editor.edit_undo()
        self.assertEqual(self.win.get_text(), "这是重点\n", "工具栏动作必须能一次撤销")

    def test_heading_action_marks_the_whole_line(self):
        editor = self.open_source("标题\n正文\n")
        editor.mark_set("insert", "1.1")
        self.assertTrue(self.win.format_selection("heading", level=2))
        self.assertEqual(self.win.get_text(), "## 标题\n正文\n")
        self.assertTrue(self.win.format_selection("heading", level=2))
        self.assertEqual(self.win.get_text(), "标题\n正文\n")

    def test_formatting_from_preview_switches_to_source_first(self):
        self.open_source("正文\n")
        self.win.toggle_mode()                     # 回到预览
        self.assertEqual(self.win.mode, "preview")
        # 预览里没有可编辑的光标：动作作用在编辑器自己的光标处（这里在文末空行）
        self.assertTrue(self.win.format_selection("bullets"))
        self.assertEqual(self.win.mode, "source")
        self.assertEqual(self.win.get_text(), "正文\n- ")

    def test_composing_ime_blocks_the_format_action(self):
        editor = self.open_source("正文\n")
        self.select(editor, "1.0", "1.2")
        self.win.ime.surface.show("zheng'wen", 9)   # 一次未确认的组合
        with self.notice() as seen:
            self.assertFalse(self.win.format_selection("bold"))
        self.assertEqual(self.win.get_text(), "正文\n", "组合中不得改写缓冲区")
        self.assertTrue(any("输入法" in message for message, _error in seen))
        self.win.ime._focus_out()

    def test_formatting_without_a_document_says_so(self):
        with self.notice() as seen:
            self.assertFalse(self.win.format_selection("bold"))
        self.assertTrue(any("没有打开的文档" in message for message, _error in seen))

    def test_wide_table_wraps_inside_the_window(self):
        """列很多时：每一列都要画出来，并且整张表不能比窗口还宽。

        这里把窗口映射到屏幕外（坐标 4000+）取真实尺寸——未映射的控件量不到宽度，
        只测“标签个数”就测不出“最后几列被挤没了”。
        """
        columns = 12
        header = "| " + " | ".join("列%d" % n for n in range(columns)) + " |"
        sep = "| " + " | ".join(["---"] * columns) + " |"
        row = "| " + " | ".join("比较长的内容%d" % n for n in range(columns)) + " |"
        self.open_source("\n".join([header, sep, row]) + "\n")
        self.win.toggle_mode()
        self.win.root.geometry("900x600+4000+4000")
        self.win.root.deiconify()
        self.addCleanup(self.win.root.withdraw)
        self.win.render()
        self.win.root.update()
        frames = [child for child in self.win.preview.winfo_children()
                  if getattr(child, "_restyle_table", None) is not None]
        self.assertEqual(len(frames), 1)
        labels = frames[0].winfo_children()
        self.assertEqual(len(labels), columns * 2, "表头行与数据行一列都不能少")
        self.assertTrue(all(int(label.cget("wraplength")) > 0 for label in labels),
                        "每列都要能换行，否则会撑破窗口")
        self.assertLessEqual(frames[0].winfo_reqwidth(), self.win.preview.winfo_width() + 8,
                             "宽表不能比阅读区还宽")

    # -- 表格对话框 ------------------------------------------------------
    def test_table_dialog_reads_and_resizes_the_grid(self):
        dialog = winui.TableDialog(self.win.root, self.win.pal, 1, mode="insert",
                                   columns=3, rows=2)
        self.addCleanup(dialog.finish, None)
        self.assertEqual(len(dialog.header_vars), 3)
        self.assertEqual(len(dialog.row_vars), 2)
        dialog.header_vars[0].set("名称")
        dialog.row_vars[0][1].set("甲")
        dialog.align_vars[1].set("居中")
        dialog.columns_var.set(2)
        dialog.rows_var.set(3)
        dialog.on_counts_changed()
        self.assertEqual(len(dialog.header_vars), 2, "改列数后网格跟着变")
        self.assertEqual(len(dialog.row_vars), 3)
        self.assertEqual(dialog.model["header"][0], "名称", "改规模前先收回已填内容")
        self.assertEqual(dialog.model["rows"][0][1], "甲")
        self.assertEqual(dialog.model["aligns"][1], "center")

    def test_table_dialog_refuses_a_multiline_cell(self):
        dialog = winui.TableDialog(self.win.root, self.win.pal, 1, mode="insert",
                                   columns=1, rows=1)
        self.addCleanup(dialog.finish, None)
        dialog.row_vars[0][0].set("第一行\n第二行")
        with mock.patch("tkinter.messagebox.showwarning") as warned:
            dialog.finish(True)
        self.assertTrue(warned.called, "多行单元格要明确提示，而不是写进 Markdown")
        self.assertIsNone(dialog.result)
        self.assertTrue(dialog.window.winfo_exists(), "提示之后对话框还开着")

    def test_insert_table_dialog_writes_the_table_at_the_cursor(self):
        editor = self.open_source("正文\n")
        editor.mark_set("insert", "1.2")
        choice = {"columns": 2, "rows": 1, "has_header": True, "header": ["名称", "数量"],
                  "aligns": ["left", "right"], "fills": [["名称", "数量"], ["甲", "1"]]}
        with mock.patch.object(winui.TableDialog, "show", return_value=choice):
            self.assertTrue(self.win.insert_table_dialog())
        self.assertEqual(self.win.get_text(),
                         "正文\n\n| 名称 | 数量 |\n| --- | ---: |\n| 甲 | 1 |\n")
        editor.edit_undo()
        self.assertEqual(self.win.get_text(), "正文\n", "插入表格也要能一次撤销")

    def test_edit_table_dialog_reports_when_the_cursor_is_not_in_a_table(self):
        self.open_source("正文\n")
        self.win.editor.mark_set("insert", "1.0")
        with self.notice() as seen:
            self.assertFalse(self.win.edit_table_dialog())
        self.assertTrue(any("光标" in message for message, _error in seen))

    def test_edit_table_dialog_keeps_the_source_when_the_structure_is_odd(self):
        odd = "| 名称 | 数量 |\n| --- | --- |\n| 甲 |\n"
        self.open_source(odd)
        self.win.editor.mark_set("insert", "1.2")
        with self.notice() as seen:
            self.assertFalse(self.win.edit_table_dialog())
        self.assertEqual(self.win.get_text(), odd, "看不懂的表格要原样保留")
        self.assertTrue(any("对不上" in message for message, _error in seen))

    def test_edit_table_dialog_replaces_the_whole_table(self):
        self.open_source("| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n")
        self.win.editor.mark_set("insert", "2.2")
        choice = {"columns": 2, "rows": 2, "has_header": True, "header": ["名称", "数量"],
                  "aligns": ["left", "left"], "fills": [["名称", "数量"], ["甲", "1"], ["丙", "3"]]}
        with mock.patch.object(winui.TableDialog, "show", return_value=choice):
            self.assertTrue(self.win.edit_table_dialog())
        self.assertEqual(self.win.get_text(),
                         "| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n| 丙 | 3 |\n")

    # -- TSV 粘贴 --------------------------------------------------------
    def test_paste_as_table_replaces_the_selection_after_confirmation(self):
        self.open_source("开场\n旧内容\n结尾\n")
        self.select(self.win.editor, "2.0", "2.4")
        with mock.patch.object(winui.TablePasteDialog, "show", return_value={"header": True}):
            self.assertTrue(self.win.paste_as_table("名称\t数量\n甲\t1\n"))
        self.assertEqual(self.win.get_text(),
                         "开场\n\n| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n\n结尾\n")

    def test_cancelling_the_paste_preview_changes_nothing(self):
        self.open_source("开场\n结尾\n")
        with mock.patch.object(winui.TablePasteDialog, "show", return_value=None):
            self.assertFalse(self.win.paste_as_table("名称\t数量\n甲\t1\n"))
        self.assertEqual(self.win.get_text(), "开场\n结尾\n")

    def test_single_column_text_is_not_turned_into_a_table(self):
        self.open_source("正文\n")
        with self.notice() as seen:
            self.assertFalse(self.win.paste_as_table("只有一列\n第二行\n"))
        self.assertTrue(any("一列" in message for message, _error in seen))

    def test_normal_clipboard_text_is_pasted_by_tk(self):
        self.open_source("正文\n")
        with mock.patch.object(self.win.root, "clipboard_get", return_value="普通一行"):
            self.assertIsNone(self.win.on_editor_paste())

    def test_tsv_clipboard_is_intercepted_and_confirmed(self):
        self.open_source("正文\n")
        with mock.patch.object(self.win.root, "clipboard_get", return_value="a\tb\n1\t2\n"), \
             mock.patch.object(winui.TablePasteDialog, "show", return_value={"header": True}):
            self.assertEqual(self.win.on_editor_paste(), "break")
        self.assertIn("| a | b |", self.win.get_text())

    def test_clipboard_without_text_is_ignored(self):
        self.open_source("正文\n")
        with mock.patch.object(self.win.root, "clipboard_get", side_effect=Exception("空")):
            self.assertIsNone(self.win.on_editor_paste())


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
