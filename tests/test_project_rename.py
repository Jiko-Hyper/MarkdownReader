"""Rename project files/folders without losing buffers or saving to old paths."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mdreader import documents
from tests import test_project_delete


class ProjectRenameTests(unittest.TestCase):
    setUp = test_project_delete.ProjectDeleteTests.setUp
    destroy_window = staticmethod(test_project_delete.ProjectDeleteTests.destroy_window)
    prepare = test_project_delete.ProjectDeleteTests.prepare
    select = test_project_delete.ProjectDeleteTests.select
    edit = test_project_delete.ProjectDeleteTests.edit

    def rename(self, relative, title):
        self.select(relative)
        with patch("mdreader.winui.RenamePrompt.show", return_value=title):
            self.assertEqual(self.win.on_project_tree_rename(), "break")

    def test_file_rename_keeps_buffer_editor_and_save_targets_new_path(self):
        w = self.prepare()
        self.edit("# 尚未保存的新内容")
        tab = w.active_tab
        editor = tab["editor"]
        old = self.pdir / "docs/子目录/文档.md"
        original = old.read_bytes()
        self.rename("docs/子目录/文档.md", "新标题")
        new = old.with_name("新标题.md")
        self.assertFalse(old.exists())
        self.assertEqual(new.read_bytes(), original)
        self.assertIs(tab["editor"], editor)
        self.assertTrue(w.dirty)
        self.assertEqual(w.cur_doc["id"], "docs/子目录/新标题.md")
        self.assertEqual(w.ws.recovery.list()[0]["path"], str(new))
        self.assertTrue(w.save_doc())
        self.assertEqual(new.read_text(encoding="utf-8"), "# 尚未保存的新内容")
        self.assertFalse(old.exists())

    def test_folder_rename_retargets_all_open_descendants(self):
        w = self.prepare()
        first = w.active_tab
        w.ws.create_doc(str(self.pdir), "第二份", "docs")
        w.open_doc("docs/第二份.md")
        self.edit("第二份修改")
        self.rename("docs", "资料")
        self.assertTrue((self.pdir / "资料/附件.bin").exists())
        self.assertTrue(first["doc"]["id"].startswith("资料/"))
        self.assertEqual(w.cur_doc["id"], "资料/第二份.md")
        self.assertTrue(w.save_doc())
        self.assertFalse((self.pdir / "docs").exists())
        w.activate_tab(first)
        self.assertEqual(w.cur_doc["id"], "资料/子目录/文档.md")

    def test_cancel_collision_and_invalid_title_keep_original(self):
        w = self.prepare()
        target = self.pdir / "docs/子目录/文档.md"
        sibling = target.with_name("已有.md")
        sibling.write_text("已有内容", encoding="utf-8")
        for title in (None, "", "../越界", "CON", "已有", "结尾."):
            self.rename("docs/子目录/文档.md", title)
            self.assertTrue(target.exists())
            self.assertEqual(w.cur_doc["id"], "docs/子目录/文档.md")
            self.assertEqual(sibling.read_text(encoding="utf-8"), "已有内容")

    def test_rename_does_not_reset_conflict_baseline(self):
        w = self.prepare()
        old_revision = w.cur_doc["revision"]
        target = self.pdir / "docs/子目录/文档.md"
        target.write_text("外部修改", encoding="utf-8")
        self.rename("docs/子目录/文档.md", "新标题.md")
        self.assertEqual(w.cur_doc["revision"], old_revision)
        with self.assertRaises(documents.ConflictError):
            w.ws.save_doc(str(self.pdir), w.cur_doc["id"], "旧缓冲", expected=old_revision)

    def test_double_click_uses_clicked_row_and_folder_text_does_not_create(self):
        w = self.prepare()
        w.root.deiconify()
        w.root.geometry("1200x1000")
        w.root.update()
        self.select("docs")
        row = w.tree.selection()[0]
        w.tree.see(row)
        w.root.update()
        x, y, width, height = w.tree.bbox(row)
        event = SimpleNamespace(num=1, x=x + 45, y=y + height // 2)
        with patch.object(w, "new_untitled_in") as create:
            w.on_plus_click(event)
            create.assert_not_called()
        with patch("mdreader.winui.RenamePrompt.show", return_value="新目录"):
            w.on_project_tree_rename(event)
        self.assertTrue((self.pdir / "新目录").exists())
        self.assertIn("on_project_tree_rename", w.tree.bind("<Double-1>"))
        self.assertIn("on_project_tree_rename", w.tree.bind("<Control-m>"))

    def test_empty_folder_and_case_only_rename(self):
        w = self.prepare()
        self.rename("assets", "ASSETS")
        self.assertIn("ASSETS", [p.name for p in self.pdir.iterdir()])
        self.assertIn("ASSETS", w._project_nodes.values())

    def test_locally_opened_tab_and_recent_path_follow_folder_rename(self):
        w = self.prepare()
        old = self.pdir / "docs/子目录/文档.md"
        w.open_local_files([str(old)])
        self.edit("本地标签修改")
        self.rename("docs", "新目录")
        new = self.pdir / "新目录/子目录/文档.md"
        self.assertEqual(w.cur_loose["path"], str(new))
        self.assertEqual(w.cur_loose["dir"], str(new.parent))
        self.assertTrue(w.save_doc())
        self.assertEqual(new.read_text(encoding="utf-8"), "本地标签修改")
        self.assertFalse(old.exists())
        self.assertIn(str(new), [item["path"] for item in w.loose.list_recent()])

    def test_context_menu_stays_available_and_renames_selected_item(self):
        w = self.prepare()
        w.root.deiconify()
        w.root.geometry("1200x1000")
        w.root.update()
        self.select("docs/子目录/文档.md")
        row = w.tree.selection()[0]
        w.tree.see(row)
        w.root.update()
        x, y, _, height = w.tree.bbox(row)
        with patch("tkinter.Menu.tk_popup"):
            w.on_project_tree_menu(SimpleNamespace(y=y + height // 2, x_root=100, y_root=100))
        menu = w._project_menu
        self.assertTrue(menu.winfo_exists())
        self.assertEqual(menu.entrycget(1, "label"), "更改标题…")
        with patch("mdreader.winui.RenamePrompt.show", return_value="右键标题"):
            menu.invoke(1)
        menu.unpost()
        self.assertTrue((self.pdir / "docs/子目录/右键标题.md").exists())

