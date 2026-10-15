"""Sidebar history removal must preserve local files and dirty editors."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mdreader import core, winui
from tests import test_native_tabs


class HistoryStorageTests(unittest.TestCase):
    def test_project_records_stay_removed_after_restart_without_deleting_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = core.Workspace(str(Path(tmp, "workspace")))
            external = Path(tmp, "external")
            external.mkdir()
            projects = [ws.create_project("内部"), ws.create_project("外部", parent_dir=str(external))]
            originals = {p: p.read_bytes() for item in projects for p in Path(item["path"]).rglob("*") if p.is_file()}
            ws.forget_project_history([projects[0]["id"]])
            self.assertEqual([p["id"] for p in ws.list_projects()], [projects[1]["id"]])
            ws.forget_project_history(p["id"] for p in projects)
            reloaded = core.Workspace(ws.root)
            self.assertEqual(reloaded.list_projects(), [])
            self.assertEqual(len(reloaded.list_projects(include_hidden=True)), 2)
            for path, data in originals.items():
                self.assertEqual(path.read_bytes(), data)
            self.assertEqual(reloaded.require_project(projects[1]["id"]), projects[1]["path"])


class HistoryUITests(unittest.TestCase):
    setUp = test_native_tabs.NativeTabsTests.setUp
    destroy_window = staticmethod(test_native_tabs.NativeTabsTests.destroy_window)
    edit = test_native_tabs.NativeTabsTests.edit

    def test_folder_cancel_remove_and_reopen(self):
        w = self.win
        folder = Path(self.temp.name, "folder")
        folder.mkdir()
        doc = folder / "keep.md"
        doc.write_text("preserved", encoding="utf-8")
        info = w.ws.folders.add(str(folder))
        w.cur_root = info["id"]
        w.refresh_roots(force=True)
        with patch.object(winui.SavePrompt, "show", return_value=None):
            w.remove_history("folder")
        self.assertEqual(len(w.ws.folders.roots()), 1)
        with patch.object(winui.SavePrompt, "show", return_value=True):
            w.root.deiconify()
            w.root.update()
            w.cmb_root.focus_force()
            w.root.update()
            w.cmb_root.event_generate("<Control-Delete>")
            w.root.update()
        self.assertEqual(w.ws.folders.roots(), [])
        self.assertEqual(w.cmb_root.get(), "")
        self.assertEqual(doc.read_text(encoding="utf-8"), "preserved")
        w.ws.folders.add(str(folder))
        w.refresh_roots(force=True)
        self.assertEqual(len(w.roots), 1)

    def test_clear_projects_preserves_dirty_editor_and_can_save(self):
        w = self.win
        project = w.ws.create_project("保留项目")
        w.cur_pid = project["id"]
        w.refresh_projects()
        self.edit("未保存的正文")
        active = w.active_tab
        doc_path = Path((w.cur_doc or {}).get("_abs") or w.ws.doc_info(project["path"], w.cur_doc["id"])["_abs"])
        with patch.object(winui.SavePrompt, "show", return_value=True):
            w.remove_history("project", all_records=True)
        self.assertEqual(w.projects, [])
        self.assertEqual(w.cmb.get(), "")
        self.assertEqual(w.source, "未保存的正文")
        self.assertTrue(w.dirty)
        self.assertIs(w.active_tab, active)
        self.assertTrue(doc_path.exists())
        w.save_doc()
        self.assertEqual(doc_path.read_text(encoding="utf-8"), "未保存的正文")

    def test_project_delete_key_only_removes_selected_record(self):
        w = self.win
        first = w.ws.create_project("第一项")
        second = w.ws.create_project("第二项")
        w.cur_pid = first["id"]
        w.refresh_projects()
        with patch.object(winui.SavePrompt, "show", return_value=True):
            w.root.deiconify()
            w.root.update()
            w.cmb.focus_force()
            w.root.update()
            w.cmb.event_generate("<Control-Delete>")
            w.root.update()
        self.assertEqual([p["id"] for p in w.projects], [second["id"]])
        self.assertTrue(Path(first["path"]).exists())
        self.assertTrue(Path(second["path"]).exists())

    def test_clear_folders_preserves_open_dirty_document(self):
        w = self.win
        w.open_local_files(self.files[:1])
        self.edit("保留未保存内容")
        for name in ("目录一", "目录二"):
            folder = Path(self.temp.name, name)
            folder.mkdir()
            w.ws.folders.add(str(folder))
        w.refresh_roots(force=True)
        with patch.object(winui.SavePrompt, "show", return_value=True):
            w.remove_history("folder", all_records=True)
        self.assertEqual(w.roots, [])
        self.assertTrue(w.dirty)
        self.assertEqual(w.source, "保留未保存内容")
        self.assertTrue(w.save_doc())
        self.assertEqual(Path(self.files[0]).read_text(encoding="utf-8"), "保留未保存内容")
        for name in ("目录一", "目录二"):
            self.assertTrue(Path(self.temp.name, name).is_dir())

    def test_empty_and_cancelled_project_removal_make_no_changes(self):
        w = self.win
        with patch.object(winui.SavePrompt, "show") as prompt:
            w.remove_history("project")
        prompt.assert_not_called()
        project = w.ws.create_project("取消移除")
        w.refresh_projects()
        with patch.object(winui.SavePrompt, "show", return_value=None):
            w.remove_history("project", all_records=True)
        self.assertEqual([p["id"] for p in w.ws.list_projects()], [project["id"]])


if __name__ == "__main__":
    unittest.main()
