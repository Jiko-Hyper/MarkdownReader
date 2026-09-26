import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mdreader import core
from mdreader.storage import atomic_write, safe_join


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mdreader-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_failed_replace_preserves_original_and_cleans_temporary(self):
        path = self.root / "中文.md"
        atomic_write(str(path), "原文")
        with patch("mdreader.storage.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                atomic_write(str(path), "新内容")
        self.assertEqual(path.read_text(encoding="utf-8"), "原文")
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_existing_tmp_file_is_not_overwritten(self):
        path = self.root / "doc.md"
        legacy = self.root / "doc.md.tmp"
        legacy.write_text("recovery", encoding="utf-8")
        atomic_write(str(path), "new")
        self.assertEqual(legacy.read_text(encoding="utf-8"), "recovery")
        self.assertEqual(path.read_text(encoding="utf-8"), "new")

    def test_paths_cannot_escape(self):
        for path in ("../outside.md", "..\\outside.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_join(str(self.root), path)
        self.assertEqual(safe_join(str(self.root), "子目录/文件.md"),
                         str(self.root / "子目录" / "文件.md"))

    def test_symlink_escape_is_rejected(self):
        inside = self.root / "inside"
        outside = self.root / "outside"
        inside.mkdir()
        outside.mkdir()
        link = inside / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Windows symlink creation requires developer mode or privilege")
        self.addCleanup(link.unlink)
        with self.assertRaises(ValueError):
            safe_join(str(inside), "link/file.md")

    def test_project_ids_cannot_identify_workspace_root(self):
        ws = core.Workspace(str(self.root))
        for pid in (".", "..", "bad\n"):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                ws.project_dir(pid)

    def test_recycle_failure_preserves_document_and_project(self):
        ws = core.Workspace(str(self.root))
        project = ws.create_project("测试")
        pdir = project["path"]
        doc = ws.create_doc(pdir, "保留", content="原始内容")
        with patch("mdreader.core._send_to_recycle_bin", return_value=False):
            with self.assertRaises(PermissionError):
                ws.delete_doc(pdir, doc["id"])
            with self.assertRaises(PermissionError):
                ws.delete_project(project["id"])
        self.assertEqual(ws.read_doc(pdir, doc["id"]), "原始内容")
        self.assertTrue(os.path.isdir(pdir))
