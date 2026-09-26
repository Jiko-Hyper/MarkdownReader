"""Project tree deletion with real widgets and isolated files only."""
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

from mdreader import core, winui
from tests import test_native_tabs


class ProjectDeleteTests(unittest.TestCase):
    # Reuse only the window fixture, not its test methods.
    setUp = test_native_tabs.NativeTabsTests.setUp
    destroy_window = staticmethod(test_native_tabs.NativeTabsTests.destroy_window)
    edit = test_native_tabs.NativeTabsTests.edit

    def prepare(self):
        w = self.win
        pid = w.ws.create_project("删除测试", with_readme=False)["id"]
        self.pdir = Path(w.ws.require_project(pid))
        w.ws.create_doc(str(self.pdir), "文档", "docs/子目录")
        (self.pdir / "docs" / "附件.bin").write_bytes(b"attachment")
        w.cur_pid = pid
        w.refresh_projects()
        return w

    def select(self, relative):
        w = self.win
        row = next(key for key, value in w._project_nodes.items() if value == relative)
        w.tree.selection_set(row)
        w.tree.focus(row)

    def recycle(self, path):
        # Move to a test-owned holding area, never the real Recycle Bin.
        shutil.move(path, str(Path(self.temp.name) / "recycled"))
        return True

    def test_cancel_keeps_local_folder_and_dirty_buffer(self):
        w = self.prepare()
        self.edit("未保存内容")
        self.select("docs")
        with patch.object(winui.SavePrompt, "show", return_value=None), patch.object(core, "_send_to_recycle_bin") as recycle:
            self.assertEqual(w.on_project_tree_delete(), "break")
        recycle.assert_not_called()
        self.assertTrue((self.pdir / "docs").exists())
        self.assertTrue(w.dirty)
        self.assertEqual(w.source, "未保存内容")

    def test_folder_delete_removes_nested_files_and_open_tabs(self):
        w = self.prepare()
        self.edit("未保存内容")
        w.flush_recovery()
        self.select("docs")
        with patch.object(winui.SavePrompt, "show", return_value=True), patch.object(core, "_send_to_recycle_bin", side_effect=self.recycle):
            w.on_project_tree_delete()
        self.assertFalse((self.pdir / "docs").exists())
        self.assertTrue(Path(self.temp.name, "recycled", "附件.bin").exists())
        self.assertEqual(w.tabs, [])
        self.assertEqual(w.ws.recovery.list(), [])
        self.assertNotIn("docs", w._project_nodes.values())

    def test_failure_preserves_dirty_tabs_and_files(self):
        w = self.prepare()
        self.edit("保留缓冲")
        self.select("docs")
        tab = w.active_tab
        with patch.object(winui.SavePrompt, "show", return_value=True), patch.object(core, "_send_to_recycle_bin", return_value=False):
            w.on_project_tree_delete()
        self.assertTrue((self.pdir / "docs").exists())
        self.assertIs(w.active_tab, tab)
        self.assertTrue(w.dirty)

    def test_file_delete_keeps_parent_and_sibling(self):
        w = self.prepare()
        self.select("docs/子目录/文档.md")
        with patch.object(winui.SavePrompt, "show", return_value=True), patch.object(core, "_send_to_recycle_bin", side_effect=self.recycle):
            w.on_project_tree_delete()
        self.assertFalse((self.pdir / "docs/子目录/文档.md").exists())
        self.assertTrue((self.pdir / "docs/子目录").is_dir())
        self.assertTrue((self.pdir / "docs/附件.bin").exists())

    def test_empty_folder_and_path_boundaries(self):
        w = self.prepare()
        for relative in ("", ".", "..", "../outside"):
            with self.assertRaises(ValueError):
                w.ws.delete_project_entry(w.cur_pid, relative)
        self.select("assets")
        with patch.object(winui.SavePrompt, "show", return_value=True), patch.object(core, "_send_to_recycle_bin", side_effect=self.recycle):
            w.on_project_tree_delete()
        self.assertFalse((self.pdir / "assets").exists())

    def test_prompt_is_centered_and_defaults_to_cancel(self):
        w = self.prepare()
        w.root.deiconify()
        w.root.update()
        dialog = winui.SavePrompt(w.root, "文件夹内所有内容也会删除", w.pal, w.ui_scale, deleting=True)
        dialog.window.update_idletasks()
        geometry = dialog.window.geometry()
        size, x, y = geometry.split("+")
        width, height = map(int, size.split("x"))
        self.assertLessEqual(abs(int(x) + width / 2 - (w.root.winfo_rootx() + w.root.winfo_width() / 2)), 1)
        self.assertLessEqual(abs(int(y) + height / 2 - (w.root.winfo_rooty() + w.root.winfo_height() / 2)), 1)
        self.assertEqual(dialog.default_button, "取消")
        self.assertTrue(w.tree.bind("<Delete>"))
        self.assertTrue(w.tree.bind("<Button-3>"))
        dialog.finish(None)
