# -*- coding: utf-8 -*-
"""原生端的保存安全、恢复、查找与目录（B02/B03/B06）在真实窗口上的回归。

`document_ui.py` 是桌面窗口里最容易丢内容的一段，之前只有业务层有测试，
这里补上窗口级的用例：冲突对话框的每个分支、恢复快照的写入与丢弃、
另存为的重指向、文档内查找与标题导航、阅读位置的保存与恢复。

    python -m unittest tests.test_document_ui
"""
from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from mdreader import core, winui


def buttons(dialog):
    """Every tk.Button inside a dialog, keyed by its label."""
    found = {}
    stack = [dialog]
    while stack:
        widget = stack.pop()
        for child in widget.winfo_children():
            if child.winfo_class() == "Button":
                found[str(child.cget("text"))] = child
            stack.append(child)
    return found


class DocumentUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-docui-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.path = Path(self.root, "笔记.md")
        self.path.write_text("# 原始\n\n原文件内容\n", encoding="utf-8")
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
    def open_document(self):
        self.win.open_local_files([str(self.path)])
        self.assertEqual(self.win.mode, "preview")
        return self.win.active_tab

    def edit(self, text):
        if self.win.mode != "source":
            self.win.toggle_mode()
        self.win._set_widget(text)
        self.win.source = text
        self.win.set_dirty(True)

    def conflict(self):
        """Make the disk version differ from the baseline the tab holds."""
        self.path.write_text("# 外部改的\n\n磁盘新内容\n", encoding="utf-8")
        return core.D.ConflictError(str(self.path), core.D.revision(self.path))

    # -- B03 恢复快照 ----------------------------------------------------
    def test_flush_recovery_writes_only_dirty_tabs(self):
        self.open_document()
        self.win.flush_recovery()
        self.assertEqual(self.win.ws.recovery.list(), [], "未修改时不应产生快照")

        self.edit("未保存的编辑内容")
        self.win.flush_recovery()
        records = self.win.ws.recovery.list()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["text"], "未保存的编辑内容")
        self.assertEqual(records[0]["path"], str(self.path))
        self.assertIsNone(self.win._recovery_job)

    def test_recovery_snapshot_is_dropped_after_a_successful_save(self):
        self.open_document()
        self.edit("先存快照")
        self.win.flush_recovery()
        self.assertEqual(len(self.win.ws.recovery.list()), 1)
        self.assertTrue(self.win.save_doc())
        self.assertEqual(self.win.ws.recovery.list(), [], "保存成功后应清理对应快照")
        self.assertIn("先存快照", self.path.read_text(encoding="utf-8"))

    def test_recovery_snapshot_survives_a_failed_save(self):
        import stat
        self.open_document()
        self.edit("救命的编辑")
        self.win.flush_recovery()
        self.assertEqual(len(self.win.ws.recovery.list()), 1)
        os.chmod(self.path, stat.S_IREAD)
        self.addCleanup(os.chmod, self.path, stat.S_IWRITE)
        self.assertFalse(self.win.save_doc(), "只读文件保存应当失败")
        self.assertEqual(len(self.win.ws.recovery.list()), 1, "保存失败不能丢弃快照")
        self.assertEqual(self.win.get_text(), "救命的编辑", "失败后编辑内容必须保留")

    def test_recovery_offer_is_silent_without_snapshots(self):
        with mock.patch.object(self.win, "show_recovery") as shown:
            self.win.offer_recovery()
        shown.assert_not_called()

    def test_recovery_offer_waits_while_a_plugin_job_is_running(self):
        """插件任务借用事件循环时不能弹模态恢复框——会把任务和用户一起卡住。"""
        self.open_document()
        self.edit("未保存的编辑内容")
        self.win.flush_recovery()
        self.assertEqual(len(self.win.ws.recovery.list()), 1)
        self.win._plugin_busy = True
        try:
            with mock.patch.object(self.win, "show_recovery") as shown, \
                 mock.patch.object(self.win.root, "after") as reschedule:
                self.win.offer_recovery()
        finally:
            self.win._plugin_busy = False
        shown.assert_not_called()
        self.assertTrue(reschedule.called, "忙的时候要把这次询问往后挪，而不是丢掉")
        with mock.patch.object(self.win, "show_recovery") as shown:
            self.win.offer_recovery()
        self.assertTrue(shown.called, "空闲下来之后照常询问")

    def test_abandoning_an_edit_clears_its_snapshot(self):
        """明确放弃编辑时清理快照，避免下次重复恢复（5.3）。"""
        tab = self.open_document()
        self.edit("要被放弃的内容")
        self.win.flush_recovery()
        self.assertEqual(len(self.win.ws.recovery.list()), 1)
        self.win.set_dirty(False)
        self.win.close_tab(tab)
        self.assertEqual(self.win.ws.recovery.list(), [], "放弃编辑后不应留下快照")

    def test_snapshot_from_a_killed_session_restores_as_unsaved(self):
        """异常结束留下的快照：可以恢复，但绝不覆盖原文件。"""
        self.open_document()
        self.win.ws.recovery.save(str(self.path), "崩溃前的文字", str(self.path), "missing")
        original = self.path.read_text(encoding="utf-8")

        with mock.patch("tkinter.messagebox.askyesno", return_value=False):
            self.win.show_recovery()
        self.assertEqual(self.path.read_text(encoding="utf-8"), original, "拒绝恢复不得改动磁盘")
        self.assertNotEqual(self.win.get_text(), "崩溃前的文字")

        with mock.patch("tkinter.messagebox.askyesno", return_value=True):
            self.win.show_recovery()
        self.assertEqual(self.win.mode, "source")
        self.assertEqual(self.win.get_text(), "崩溃前的文字")
        self.assertTrue(self.win.dirty, "恢复出来的内容必须标成未保存")
        self.assertEqual(self.path.read_text(encoding="utf-8"), original, "恢复不得覆盖原文件")

    def test_recovery_offer_fires_when_a_snapshot_exists(self):
        self.open_document()
        self.win.ws.recovery.save(str(self.path), "上一次没保存的内容", str(self.path), "missing")
        with mock.patch.object(self.win, "show_recovery") as shown:
            self.win.offer_recovery()
        shown.assert_called_once_with()
        self.assertEqual(len(self.win.ws.recovery.list()), 1)

    # -- B02 冲突分支 ----------------------------------------------------
    def test_conflict_cancel_keeps_both_versions(self):
        self.open_document()
        self.edit("我改的内容")
        with self.conflict_save("取消，保留编辑") as result:
            self.assertFalse(result())

    def test_conflict_overwrite_backs_up_the_disk_version(self):
        self.open_document()
        self.edit("我改的内容")
        with self.conflict_save("备份后覆盖磁盘版本") as result:
            self.assertTrue(result())
        self.assertIn("我改的内容", self.path.read_text(encoding="utf-8"))
        conflicts = os.listdir(os.path.join(self.win.ws.root, ".recovery", "conflicts"))
        self.assertEqual(len(conflicts), 1, conflicts)
        with open(os.path.join(self.win.ws.root, ".recovery", "conflicts", conflicts[0]),
                  encoding="utf-8") as handle:
            self.assertIn("磁盘新内容", handle.read(), "覆盖前的磁盘版本必须留有备份")
        self.assertFalse(self.win.dirty)

    @contextmanager
    def conflict_save(self, label):
        """Run one save that hits a conflict, pressing ``label`` in the dialog.

        A watchdog closes any dialog that is still open, so a missing button can
        never leave the suite waiting on a modal window.
        """
        conflict = self.conflict()
        error = []

        def press():
            try:
                self.press_conflict(label)
            except Exception as exc:                # pragma: no cover - diagnostic
                error.append(str(exc))

        def watchdog():
            for child in list(self.win.root.winfo_children()):
                if child.winfo_class() == "Toplevel":
                    child.destroy()

        self.win.root.after(60, press)
        self.win.root.after(4000, watchdog)
        with mock.patch.object(core.Workspace, "save_doc", side_effect=conflict):
            yield lambda: self.win.save_doc()
        self.assertEqual(error, [], "冲突对话框交互失败")

    def press_conflict(self, label):
        for child in self.win.root.winfo_children():
            if child.winfo_class() == "Toplevel" and "冲突" in str(child.title()):
                target = buttons(child).get(label)
                if target is None:
                    child.destroy()
                    raise AssertionError("冲突对话框缺少按钮：%s（现有 %s）"
                                         % (label, sorted(buttons(child))))
                target.invoke()
                return
        raise AssertionError("冲突对话框没有出现")

    # -- 另存为 ----------------------------------------------------------
    def test_save_as_writes_the_new_file_and_repoints_the_tab(self):
        self.open_document()
        self.edit("另存的内容")
        target = os.path.join(self.root, "另存.md")
        with mock.patch.object(self.win, "_ask_save_path", return_value=target):
            self.assertTrue(self.win.save_as())
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "另存的内容")
        self.assertFalse(self.win.dirty)
        self.assertEqual(self.win.cur_loose["path"], target)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "# 原始\n\n原文件内容\n",
                         "另存为不能改动原文件")

    def test_save_as_cancel_keeps_the_buffer(self):
        self.open_document()
        self.edit("还没想好")
        with mock.patch.object(self.win, "_ask_save_path", return_value=""):
            self.assertFalse(self.win.save_as())
        self.assertEqual(self.win.get_text(), "还没想好")
        self.assertTrue(self.win.dirty)

    # -- 撤销与重做（5.1：与保存、切换标签、主题切换的配合）-----------------
    def append_line(self, line):
        self.win.text.insert("end", line)
        self.win.text.edit_separator()

    def undo_redo_works(self):
        """一次真实往返：撤销应改掉内容，重做应完全还原。"""
        before = self.win.get_text()
        try:
            self.win.text.edit_undo()
        except Exception:
            return False
        changed = self.win.get_text()
        try:
            self.win.text.edit_redo()
        except Exception:
            return False
        return changed != before and self.win.get_text() == before

    def test_undo_survives_saving_and_switching_theme(self):
        self.open_document()
        self.edit("原始内容\n")
        self.append_line("追加的一行\n")
        self.assertTrue(self.undo_redo_works(), "前提：刚输入完应当可以撤销")

        self.win.source = self.win.get_text()
        self.win.set_dirty(True)
        self.assertTrue(self.win.save_doc())
        self.assertTrue(self.undo_redo_works(), "保存不应清空撤销历史")

        for theme in ("dark", "eye", "light"):
            self.win.set_theme(theme)
            self.win.root.update_idletasks()
            self.assertTrue(self.undo_redo_works(), "%s 主题切换不应清空撤销历史" % theme)

    def test_undo_redo_round_trip_keeps_the_buffer_stable(self):
        self.open_document()
        self.edit("第一行\n")
        self.append_line("第二行\n")
        self.append_line("第三行\n")
        before = self.win.get_text()
        self.win.text.edit_undo()
        self.win.text.edit_undo()
        self.assertEqual(self.win.get_text(), "第一行\n")
        self.win.text.edit_redo()
        self.win.text.edit_redo()
        self.assertEqual(self.win.get_text(), before)

    def test_undo_survives_view_switches_because_each_document_keeps_its_editor(self):
        """E01：源码 → 预览 → 源码后，撤销仍精确还原刚才的输入。"""
        self.open_document()
        self.edit("原始内容\n")
        self.append_line("切换视图前输入的一行\n")
        self.assertTrue(self.undo_redo_works())
        editor = self.win.editor
        self.win.toggle_mode()          # 源码 -> 预览
        self.assertEqual(self.win.text, self.win.preview, "预览应使用只读预览控件")
        self.win.toggle_mode()          # 预览 -> 源码
        self.assertIs(self.win.editor, editor, "回到源码应复用同一个编辑控件")
        self.assertEqual(self.win.get_text(), "原始内容\n切换视图前输入的一行\n")
        self.assertTrue(self.undo_redo_works(), "视图切换后仍应可以撤销和重做")
        self.win.text.edit_undo()
        self.assertEqual(self.win.get_text(), "原始内容\n")

    def test_undo_survives_tab_switches_in_both_directions(self):
        """E01：A、B 各自的编辑历史互不干扰，来回切换仍能撤销。"""
        second = Path(self.root, "第二份.md")
        second.write_text("# 第二份\n\n另一份内容\n", encoding="utf-8")
        self.open_document()
        self.edit("A 的原始\n")
        self.append_line("A 追加\n")
        first = self.win.active_tab
        first_editor = self.win.editor
        self.win.open_local_files([str(second)])
        self.win.show_source("B 的原始\n")
        self.win.editor.insert("end", "B 追加\n")
        self.win.editor.edit_separator()
        second_tab = self.win.active_tab
        self.assertIsNot(self.win.editor, first_editor)

        self.win.activate_tab(first)
        self.assertIs(self.win.editor, first_editor)
        self.assertEqual(self.win.get_text(), "A 的原始\nA 追加\n")
        self.win.text.edit_undo()
        self.assertEqual(self.win.get_text(), "A 的原始\n", "A 的撤销只影响 A")
        self.assertNotIn("B 追加", self.win.get_text())
        self.win.text.edit_redo()
        self.assertEqual(self.win.get_text(), "A 的原始\nA 追加\n")

        self.win.activate_tab(second_tab)
        self.assertEqual(self.win.get_text(), "B 的原始\nB 追加\n", "B 的内容不被 A 的撤销改动")
        self.win.text.edit_undo()
        self.assertEqual(self.win.get_text(), "B 的原始\n")
        self.win.text.edit_redo()
        self.assertEqual(self.win.get_text(), "B 的原始\nB 追加\n")

        self.win.activate_tab(first)
        self.assertEqual(self.win.get_text(), "A 的原始\nA 追加\n")
        self.assertNotIn("B 的", self.win.get_text())

    def test_tab_switch_restores_the_cursor_and_selection_per_document(self):
        second = Path(self.root, "第三份.md")
        second.write_text("# 第三份\n\n第三份内容\n", encoding="utf-8")
        self.open_document()
        self.edit("第一行\n第二行\n")
        self.win.text.mark_set("insert", "2.2")
        self.win.text.tag_add("sel", "2.0", "2.3")
        first = self.win.active_tab
        self.win.open_local_files([str(second)])
        self.win.toggle_mode()
        self.win.text.mark_set("insert", "1.1")
        self.win.activate_tab(first)
        self.assertEqual(self.win.text.index("insert"), "2.2", "回到 A 应恢复光标")
        self.assertEqual(tuple(map(str, self.win.text.tag_ranges("sel"))), ("2.0", "2.3"),
                         "回到 A 应恢复选区")

    def test_unsaved_marker_returns_when_redo_leaves_the_saved_baseline(self):
        """E01 验收 3：撤销回保存基线去掉未保存标记，重做后标记恢复。"""
        self.open_document()
        self.edit("已保存内容\n")
        self.win.save_doc()
        self.assertFalse(self.win.dirty)
        self.win.text.insert("end", "新输入\n")
        self.win.text.edit_separator()
        self.win.root.update()
        self.assertTrue(self.win.dirty, "输入后应标记未保存")
        self.win.text.edit_undo()
        self.win.root.update()
        self.assertFalse(self.win.dirty, "撤销回保存基线后不应再标记未保存")
        self.win.text.edit_redo()
        self.win.root.update()
        self.assertTrue(self.win.dirty, "重做离开基线后应重新标记未保存")

    def test_closing_a_tab_releases_its_editor_and_composition_adapter(self):
        """E01 验收 6：关闭标签回收控件与输入法注册项。"""
        self.open_document()
        self.edit("待关闭的编辑")
        editor = self.win.editor
        adapter = self.win.ime
        self.assertIsNotNone(adapter)
        self.assertIn(editor, self.win._editors)
        with mock.patch.object(self.win, "ask_save_changes", return_value=False):
            self.win.close_active_tab()
        self.assertFalse(editor.winfo_exists(), "关闭标签后编辑控件应被销毁")
        self.assertNotIn(editor, self.win._editors)
        self.assertNotIn(adapter, self.win._imes)
        self.assertTrue(self.win.preview.winfo_exists(), "预览控件不受标签关闭影响")

    def test_switching_documents_does_not_leak_undo_into_the_other_file(self):
        """切到另一份文档时，撤销不能把上一份的内容写进来。"""
        second = Path(self.root, "第二份.md")
        second.write_text("# 第二份\n\n另一份内容\n", encoding="utf-8")
        self.open_document()
        self.edit("第一份的编辑\n")
        self.append_line("第一份追加\n")
        self.win.open_local_files([str(second)])
        self.assertEqual(self.win.mode, "preview", "打开文档应先进入阅读视图")
        self.win.toggle_mode()
        self.assertEqual(self.win.get_text(), "# 第二份\n\n另一份内容\n")
        self.assertFalse(self.undo_redo_works(), "不应能撤销出上一份文档的内容")
        self.assertNotIn("第一份", self.win.get_text())

    def test_save_as_keeps_the_tab_history_and_repoints_the_tab(self):
        """E01 实现范围：另存为成功后延续当前标签的历史，不重建编辑控件。"""
        self.open_document()
        self.edit("原始内容\n")
        self.append_line("另存前输入\n")
        self.assertTrue(self.undo_redo_works())
        editor = self.win.editor
        target = Path(self.root, "另存 目标.md")
        with mock.patch.object(self.win, "_ask_save_path", return_value=str(target)):
            self.assertTrue(self.win.save_as())
        self.assertEqual(target.read_text(encoding="utf-8"), "原始内容\n另存前输入\n")
        self.assertFalse(self.win.dirty, "另存成功后应清掉未保存标记")
        self.assertIs(self.win.editor, editor, "另存为不应重建编辑控件")
        self.assertTrue(self.undo_redo_works(), "另存为成功后必须延续撤销历史")
        self.win.text.edit_undo()
        self.assertEqual(self.win.get_text(), "原始内容\n")

        tabs_before = len(self.win.tabs)
        self.win.open_local_files([str(target)])
        self.assertEqual(len(self.win.tabs), tabs_before, "标签身份已指向新路径，不应再开一个标签")
        self.assertIs(self.win.editor, editor)

    def test_undo_and_redo_survive_failed_and_cancelled_saves(self):
        """E01 验收 5：保存失败、冲突取消、另存取消后仍能撤销重做，磁盘未被误写。"""
        self.open_document()
        self.edit("原始内容\n")
        self.append_line("追加的一行\n")
        after_typing = self.win.get_text()
        original_disk = self.path.read_text(encoding="utf-8")

        with mock.patch.object(self.win.loose, "save", side_effect=OSError("磁盘已满")):
            self.assertFalse(self.win.save_doc(), "写入失败时保存必须返回失败")
        self.assertEqual(self.path.read_text(encoding="utf-8"), original_disk, "失败不得改写磁盘")
        self.assertTrue(self.undo_redo_works(), "保存失败后应仍可撤销重做")

        self.win.source = self.win.get_text()
        self.win.set_dirty(True)
        with self.conflict_save("取消，保留编辑") as result:
            self.assertFalse(result())
        self.assertEqual(self.path.read_text(encoding="utf-8"), "# 外部改的\n\n磁盘新内容\n",
                         "冲突取消不得写回磁盘")
        self.assertTrue(self.undo_redo_works(), "冲突取消后应仍可撤销重做")

        with mock.patch.object(self.win, "_ask_save_path", return_value=""):
            self.assertFalse(self.win.save_as(), "另存取消应返回失败")
        self.assertTrue(self.undo_redo_works(), "另存取消后应仍可撤销重做")
        self.assertEqual(self.win.get_text(), after_typing)
        self.assertEqual(self.win.active_tab.get("source"), after_typing)

    # -- B06 查找与目录 --------------------------------------------------
    def test_find_in_document_highlights_and_reports_misses(self):
        self.open_document()
        self.edit("# 一\n\n关键词甲 与 关键词甲\n")
        text = self.win.get_text()
        self.win.set_dirty(False)                    # 查找不得让文档变“未保存”
        self.win.find_in_document()
        dialog = self.toplevel("文档内查找")
        self.addCleanup(dialog.destroy)
        finder = self.win._find_dialog
        finder.entry.insert(0, "关键词甲")
        finder.refresh()
        self.assertTrue(self.win.text.tag_ranges("find_hit"), "命中处没有高亮")
        self.assertIn("共 2 个匹配", finder.status.cget("text"))
        buttons(dialog)["next"].invoke()
        self.assertTrue(self.win.text.tag_ranges("find_hit"))
        finder.query.set("根本没有的词")
        finder.refresh()
        self.assertIn("无匹配结果", finder.status.cget("text"))
        self.assertFalse(self.win.dirty, "查找不得改动正文或未保存状态")
        self.assertEqual(self.win.get_text(), text)

    def test_outline_lists_source_headings_and_skips_code_fences(self):
        self.open_document()
        self.edit("# 一级\n\n```\n# 代码里的井号\n```\n\n## 二级\n")
        self.win.set_dirty(False)
        self.win.show_outline()
        dialog = self.toplevel("标题导航")
        self.addCleanup(dialog.destroy)
        boxes = [w for w in dialog.winfo_children() if w.winfo_class() == "Listbox"]
        self.assertTrue(boxes, "目录列表没有创建")
        items = [boxes[0].get(index) for index in range(boxes[0].size())]
        self.assertEqual(len(items), 2, items)
        self.assertIn("一级", items[0])
        self.assertTrue(any("二级" in item for item in items))
        self.assertFalse(any("代码里的井号" in item for item in items), "代码围栏里的井号不是标题")
        boxes[0].selection_set(1)
        boxes[0].event_generate("<<ListboxSelect>>")
        self.win.root.update()
        self.assertFalse(self.win.dirty)

    def toplevel(self, title_fragment):
        for child in self.win.root.winfo_children():
            if child.winfo_class() == "Toplevel" and title_fragment in str(child.title()):
                return child
        self.fail("没有找到窗口：%s" % title_fragment)

    # -- 阅读位置 --------------------------------------------------------
    def long_document(self):
        lines = "\n".join("第 %d 行" % index for index in range(500))
        self.path.write_text("# 长文\n\n" + lines + "\n", encoding="utf-8")
        return self.path

    def test_reading_position_round_trip(self):
        self.long_document()
        self.open_document()
        self.win.text.yview_moveto(0.5)
        self.win.root.update_idletasks()
        self.win.save_reading_positions()
        moved = self.win.text.yview()[0]
        self.assertGreater(moved, 0.0)
        self.win.text.yview_moveto(0.0)
        self.win.restore_reading_position()
        self.assertAlmostEqual(self.win.text.yview()[0], moved, places=2)

    def test_reading_position_survives_a_restart(self):
        self.long_document()
        self.open_document()
        self.win.text.yview_moveto(0.6)
        self.win.root.update_idletasks()
        self.win.save_reading_positions()
        expected = self.win.text.yview()[0]

        reopened = winui.MarkdownWindow(self.win.ws.root)
        try:
            reopened.root.withdraw()
            reopened.open_local_files([str(self.path)])
            reopened.root.update_idletasks()
            self.assertAlmostEqual(reopened.text.yview()[0], expected, places=2)
        finally:
            self.destroy_window(reopened.root)


class LinkCheckUiTests(unittest.TestCase):
    """F09：桌面端的「检查当前文档链接」——报告、定位、重新选择文件。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-linkui-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = Path(self.root, "笔记.md")
        self.path.write_text("# 标题\n\n![缺图](assets/没有.png)\n\n[坏链接](没有.md)\n",
                             encoding="utf-8")
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

    def open_document(self):
        self.win.open_local_files([str(self.path)])
        self.win.show_source()
        return self.win.active_tab

    def test_the_report_lists_problems_and_locates_the_line(self):
        self.open_document()
        captured = {}

        def answer(_self):
            captured["issues"] = _self.issues
            return {"action": "locate", "index": 0}

        with mock.patch.object(winui.LinkReportDialog, "show", autospec=True, side_effect=answer):
            self.assertTrue(self.win.check_document_links())
        self.assertEqual([item["source"] for item in captured["issues"]],
                         ["assets/没有.png", "没有.md"])
        selected = self.win.editor.get("sel.first", "sel.last")
        self.assertEqual(selected, "![缺图](assets/没有.png)", "定位应当选中出问题的那一行")
        self.assertIn("第 3 行", self.win.lbl_status.cget("text"))

    def test_a_clean_document_says_so_without_opening_a_dialog(self):
        self.path.write_text("# 只有正文\n\n没有引用。\n", encoding="utf-8")
        self.open_document()
        with mock.patch.object(winui.LinkReportDialog, "show",
                               autospec=True, return_value={"action": None}) as shown:
            self.assertTrue(self.win.check_document_links())
        self.assertFalse(shown.called)
        self.assertIn("没有发现问题", self.win.lbl_status.cget("text"))

    def test_an_unexpected_answer_ends_the_check_instead_of_looping(self):
        """对话框返回一个不认识的答复时也必须收尾（否则会一遍遍重问）。"""
        self.open_document()
        with mock.patch.object(winui.LinkReportDialog, "show",
                               autospec=True, return_value={"action": "whatever"}):
            self.assertFalse(self.win.check_document_links())

    def test_relink_copies_the_chosen_file_and_updates_the_buffer(self):
        self.open_document()
        chosen = Path(self.root, "新图.png")
        chosen.write_bytes(b"png")
        replies = [{"action": "choose", "index": 0}, {"action": None}]

        def answer(_self):
            return replies.pop(0)

        with mock.patch.object(winui.LinkReportDialog, "show", autospec=True, side_effect=answer), \
             mock.patch.object(core, "_dialog_images", return_value=[str(chosen)]) as picker:
            self.win.check_document_links()
        self.assertTrue(picker.called)
        self.assertIn("![缺图](assets/新图.png)", self.win.get_text())
        self.assertTrue(Path(self.root, "assets", "新图.png").is_file())
        self.assertTrue(self.win.dirty, "改的是缓冲区，要标成未保存")
        self.assertIn("assets/没有.png", self.path.read_text(encoding="utf-8"),
                      "磁盘上的旧引用要等用户保存才变")
        self.win.text.edit_undo()
        self.assertIn("assets/没有.png", self.win.get_text(), "一次替换要能一次撤销")

    def test_cancelling_the_report_changes_nothing(self):
        self.open_document()
        before = self.win.get_text()
        with mock.patch.object(winui.LinkReportDialog, "show",
                               autospec=True, return_value={"action": None}):
            self.assertFalse(self.win.check_document_links())
        self.assertEqual(self.win.get_text(), before)

    def test_an_unsaved_draft_is_refused_early(self):
        self.win.new_loose_draft()
        with mock.patch.object(winui.LinkReportDialog, "show") as shown:
            self.assertFalse(self.win.check_document_links())
        self.assertFalse(shown.called)
        self.assertIn("请先保存", self.win.lbl_status.cget("text"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
