import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mdreader import core, winui
from tests import test_project_delete


class ProjectLocationsTests(unittest.TestCase):
    def test_external_project_survives_restart_and_keeps_operations_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp, "chosen")
            parent.mkdir()
            ws = core.Workspace(str(Path(tmp, "workspace")))
            project = ws.create_project("项目", parent_dir=str(parent))
            self.assertEqual(Path(project["path"]).parent, parent)
            self.assertFalse(Path(ws.projects_dir, project["id"]).exists())
            reloaded = core.Workspace(ws.root)
            self.assertEqual(reloaded.require_project(project["id"]), project["path"])
            self.assertIn(project["id"], [p["id"] for p in reloaded.list_projects()])
            doc = reloaded.create_doc(project["path"], "本地文档")
            result = reloaded.rename_project_entry(project["id"], doc["id"], "改名")
            self.assertTrue(Path(result["path"]).exists())
            with self.assertRaises(ValueError):
                reloaded.rename_project_entry(project["id"], "../outside", "禁止")
            with patch.object(core, "_send_to_recycle_bin", return_value=False):
                with self.assertRaises(PermissionError):
                    reloaded.delete_project(project["id"])
            self.assertIn(project["id"], reloaded.external_projects())
            reloaded.delete_project(project["id"], to_recycle=False)
            self.assertTrue(parent.exists())
            self.assertNotIn(project["id"], reloaded.external_projects())

    def test_duplicate_names_do_not_overwrite_existing_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = core.Workspace(str(Path(tmp, "workspace")))
            parent = Path(tmp, "chosen")
            parent.mkdir()
            (parent / "项目").mkdir()
            sentinel = parent / "项目/keep.txt"
            sentinel.write_text("preserved")
            first = ws.create_project("项目", parent_dir=str(parent))
            second = ws.create_project("项目")
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(sentinel.read_text(), "preserved")
            with self.assertRaises(ValueError):
                ws.create_project("bad", parent_dir=str(parent / "missing"))


class ProjectLocationUITests(unittest.TestCase):
    setUp = test_project_delete.ProjectDeleteTests.setUp
    destroy_window = staticmethod(test_project_delete.ProjectDeleteTests.destroy_window)
    prepare = test_project_delete.ProjectDeleteTests.prepare
    select = test_project_delete.ProjectDeleteTests.select

    def test_new_project_uses_chosen_location_and_cancel_creates_nothing(self):
        w = self.win
        parent = Path(self.temp.name, "chosen")
        parent.mkdir()
        def choose(dialog):
            dialog.location.set(str(parent))
            dialog.finish("所选位置")
            return dialog.result
        with patch.object(winui.RenamePrompt, "show", choose):
            w.new_project()
        self.assertEqual(Path(w.ws.require_project(w.cur_pid)).parent, parent)
        count = len(w.ws.list_projects())
        with patch.object(winui.RenamePrompt, "show", return_value=None):
            w.new_project()
        self.assertEqual(len(w.ws.list_projects()), count)

    def test_path_selection_and_shortcut_scope(self):
        w = self.prepare()
        for relative in ("docs", "docs/子目录/文档.md"):
            self.select(relative)
            with patch.object(winui, "PathDialog") as prompt:
                self.assertEqual(w.on_project_tree_path(), "break")
                self.assertEqual(prompt.call_args.args[1], str(self.pdir / relative))
                self.assertEqual(prompt.call_args.args[2], w.pal)
        self.assertIn("on_project_tree_path", w.tree.bind("<Control-y>"))
        self.assertNotIn("on_project_tree_path", w.root.bind("<Control-y>"))
        listed = dict(winui.SHORTCUTS)
        for key in ("Delete", "Ctrl+M", "Ctrl+Y", "双击标题", "Enter"):
            self.assertIn(key, listed)

    def test_address_dialog_center_and_theme(self):
        w = self.win
        w.root.deiconify()
        w.root.geometry("1200x900+100+100")
        w.root.update()
        for palette in winui.THEMES.values():
            dialog = winui.PathDialog(w.root, self.temp.name, palette)
            dialog.window.deiconify()
            dialog.window.update()
            self.assertEqual(dialog.entry.get(), self.temp.name)
            self.assertEqual(dialog.entry.cget("readonlybackground"), palette["tree_bg"])
            self.assertEqual(dialog.window.cget("bg"), palette["bg"])
            size, x, y = dialog.window.geometry().split("+")
            width, height = map(int, size.split("x"))
            self.assertLessEqual(abs(int(x) + width / 2 - (w.root.winfo_rootx() + w.root.winfo_width() / 2)), 1)
            self.assertLessEqual(abs(int(y) + height / 2 - (w.root.winfo_rooty() + w.root.winfo_height() / 2)), 1)
            dialog.finish()
