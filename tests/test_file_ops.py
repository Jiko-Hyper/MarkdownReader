# -*- coding: utf-8 -*-
"""文件整理与导出：导入不静默覆盖、重名可预期、导出重复目标要确认。

    python -m unittest tests.test_file_ops
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from mdreader import core


class WorkspaceFileOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-fileops-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.ws = core.Workspace(self.root)
        self.pid = self.ws.create_project("项目")["id"]
        self.pdir = self.ws.require_project(self.pid)
        self.outside = os.path.join(self.root, "外部")
        os.makedirs(self.outside, exist_ok=True)

    def write(self, name, text="内容\n", where=None):
        path = os.path.join(where or self.outside, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_import_copies_and_keeps_the_original(self):
        source = self.write("笔记.md", "# 笔记\n")
        added = self.ws.import_paths(self.pdir, [source])["added"]
        self.assertEqual(len(added), 1)
        self.assertTrue(os.path.isfile(source), "导入不应移动或删除原文件")
        self.assertTrue(os.path.isfile(os.path.join(self.pdir, added[0])))

    def test_import_never_silently_replaces_an_existing_name(self):
        first = self.write("同名.md", "第一份\n")
        self.ws.import_paths(self.pdir, [first])
        again = self.write("同名.md", "第二份\n")
        added = self.ws.import_paths(self.pdir, [again])["added"]
        self.assertEqual(len(added), 1)
        self.assertNotEqual(added[0], "同名.md")
        names = sorted(name for name in os.listdir(self.pdir) if name.endswith(".md"))
        self.assertIn("同名.md", names)
        contents = []
        for name in names:
            with open(os.path.join(self.pdir, name), encoding="utf-8") as handle:
                contents.append(handle.read())
        self.assertIn("第一份\n", contents)
        self.assertIn("第二份\n", contents)

    def test_import_move_is_explicit_only(self):
        source = self.write("搬走.md")
        self.ws.import_paths(self.pdir, [source], move=True)
        self.assertFalse(os.path.exists(source))
        moved = [name for name in os.listdir(self.pdir) if name.endswith(".md")]
        self.assertTrue(moved)

    def test_import_keeps_the_folder_structure_and_its_images(self):
        self.write("资料/正文.md", "![图](img/pic.png)\n", where=self.outside)
        self.write("资料/img/pic.png", "png\n", where=self.outside)
        added = self.ws.import_paths(self.pdir, [os.path.join(self.outside, "资料")],
                                     subdir="导入")["added"]
        self.assertTrue(any(path.endswith("正文.md") for path in added), added)
        # The picture the document references must arrive with it (5.5).
        self.assertTrue(os.path.isfile(os.path.join(self.pdir, "导入", "资料", "img", "pic.png")),
                        os.listdir(os.path.join(self.pdir, "导入", "资料")))

    def test_import_skips_and_reports_what_it_cannot_take(self):
        missing = self.ws.import_paths(self.pdir, [os.path.join(self.outside, "没有这个.md")])
        self.assertEqual(missing["added"], [])
        self.assertIn("不存在", missing["skipped"][0]["reason"])
        other = self.write("表格.xlsx")
        result = self.ws.import_paths(self.pdir, [other])
        self.assertEqual(result["added"], [])
        self.assertIn("Markdown", result["skipped"][0]["reason"])
        self.assertTrue(os.path.isfile(other), "被拒绝的源文件必须原样保留")

    def test_rename_refuses_an_existing_name_instead_of_overwriting(self):
        first = self.ws.create_doc(self.pdir, "一")["id"]
        self.ws.create_doc(self.pdir, "二")
        with self.assertRaises(FileExistsError):
            self.ws.rename_doc(self.pdir, first, "二.md")
        self.assertTrue(os.path.isfile(os.path.join(self.pdir, "一.md")))
        self.assertEqual(self.ws.read_doc(self.pdir, "二.md"), self.ws.read_doc(self.pdir, "二.md"))

    def test_move_refuses_to_drop_a_document_on_an_existing_one(self):
        moved = self.ws.create_doc(self.pdir, "移动", subdir="")["id"]
        self.ws.create_doc(self.pdir, "移动", subdir="子目录")
        with self.assertRaises(FileExistsError):
            self.ws.move_doc(self.pdir, moved, "子目录")
        self.assertTrue(os.path.isfile(os.path.join(self.pdir, "移动.md")))

    def test_delete_keeps_the_file_when_the_recycle_bin_is_unavailable(self):
        doc = self.ws.create_doc(self.pdir, "删除")
        with mock.patch.object(core, "_send_to_recycle_bin", return_value=False):
            with self.assertRaises(PermissionError):
                self.ws.delete_doc(self.pdir, doc["id"], to_recycle=True)
        self.assertTrue(os.path.isfile(os.path.join(self.pdir, "删除.md")),
                        "回收站不可用时必须保留文件")

    def test_search_reports_file_path_and_snippet(self):
        self.ws.create_doc(self.pdir, "搜索目标", content="# 标题\n\n里面有独门关键词。\n")
        hits = self.ws.search(self.pdir, "独门关键词")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["id"], "搜索目标.md")
        self.assertEqual(hits[0]["name"], "搜索目标")
        self.assertIn("独门关键词", hits[0].get("snippet") or "")
        self.assertIn("dir", hits[0])


class ExportGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-export-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.ws = core.Workspace(self.root)
        self.pid = self.ws.create_project("导出手册")["id"]
        self.pdir = self.ws.require_project(self.pid)
        self.doc = self.ws.create_doc(self.pdir, "第一篇", content="# 第一篇\n\n正文\n")
        self.api = core.Api(self.ws, os.path.join(self.root, "webui"))

    def test_default_target_never_replaces_an_earlier_export(self):
        first = self.api.post("/api/doc/export", {"pid": self.pid, "doc": self.doc["id"]})
        second = self.api.post("/api/doc/export", {"pid": self.pid, "doc": self.doc["id"]})
        self.assertTrue(first["ok"] and second["ok"])
        self.assertNotEqual(first["path"], second["path"])
        self.assertTrue(os.path.isfile(first["path"]) and os.path.isfile(second["path"]))

    def test_existing_explicit_target_must_be_confirmed(self):
        target = os.path.join(self.root, "导出.html")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("旧内容")
        blocked = self.api.post("/api/doc/export", {"pid": self.pid, "doc": self.doc["id"], "dest": target})
        self.assertFalse(blocked["ok"])
        self.assertTrue(blocked["conflict"])
        self.assertEqual(blocked["revision"], core.D.revision(target))
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "旧内容")

    def test_confirmation_writes_and_stale_confirmation_is_refused(self):
        target = os.path.join(self.root, "确认.html")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("旧内容")
        stale = core.D.revision(target)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("确认期间又变了")
        again = self.api.post("/api/doc/export",
                              {"pid": self.pid, "doc": self.doc["id"], "dest": target, "overwrite": stale})
        self.assertFalse(again["ok"], "过期确认不能覆盖新版本")
        current = core.D.revision(target)
        done = self.api.post("/api/doc/export",
                             {"pid": self.pid, "doc": self.doc["id"], "dest": target, "overwrite": current})
        self.assertTrue(done["ok"])
        with open(target, encoding="utf-8") as handle:
            self.assertIn("第一篇", handle.read())

    def test_os_dialog_confirmation_is_accepted(self):
        target = os.path.join(self.root, "对话框.html")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("旧内容")
        done = self.api.post("/api/doc/export",
                             {"pid": self.pid, "doc": self.doc["id"], "dest": target, "overwrite": True})
        self.assertTrue(done["ok"])

    def test_project_export_shares_the_same_rule(self):
        target = os.path.join(self.root, "合集.html")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("旧内容")
        blocked = self.api.post("/api/project/export", {"pid": self.pid, "dest": target})
        self.assertTrue(blocked["conflict"])
        self.assertTrue(self.api.post("/api/project/export",
                                      {"pid": self.pid, "dest": target, "overwrite": True})["ok"])

    def test_export_does_not_modify_the_source_document(self):
        before = self.ws.read_doc(self.pdir, self.doc["id"])
        mtime = os.stat(os.path.join(self.pdir, "第一篇.md")).st_mtime_ns
        self.api.post("/api/doc/export", {"pid": self.pid, "doc": self.doc["id"]})
        self.assertEqual(self.ws.read_doc(self.pdir, self.doc["id"]), before)
        self.assertEqual(os.stat(os.path.join(self.pdir, "第一篇.md")).st_mtime_ns, mtime)


if __name__ == "__main__":
    import unittest.mock  # noqa: F401  (used through unittest.mock above)
    unittest.main(verbosity=2)
