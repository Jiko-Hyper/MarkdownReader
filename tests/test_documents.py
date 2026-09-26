# -*- coding: utf-8 -*-
"""Data-safety regressions for the shared document layer (B01/B02).

These cover what the framework calls high risk: a failed replace must leave the
original intact, an external edit must not be silently overwritten, save-as must
not clobber a file without a backup, and encodings/newlines must survive a save.

    python -m unittest tests.test_documents
"""
from __future__ import annotations

import os
import tempfile
import unittest

from mdreader import documents as D
from mdreader import recovery


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-docs-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name

    def write(self, name, data):
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_plain_utf8_round_trip_keeps_text_and_revision(self):
        path = self.write("a.md", "# 标题\n\n正文\n".encode("utf-8"))
        snap = D.snapshot(path)
        self.assertEqual(snap["text"], "# 标题\n\n正文\n")
        self.assertEqual(snap["encoding"], "utf-8")
        self.assertEqual(snap["newline"], "\n")
        self.assertEqual(snap["revision"], D.revision(path))

    def test_bom_and_crlf_are_detected(self):
        path = self.write("bom.md", "标题\r\n第二行\r\n".encode("utf-8-sig"))
        snap = D.snapshot(path)
        self.assertEqual(snap["encoding"], "utf-8-sig")
        self.assertEqual(snap["newline"], "\r\n")
        self.assertFalse(snap["text"].startswith("\ufeff"))
        self.assertNotIn("\r", snap["text"])

    def test_gb18030_is_read_when_utf8_fails(self):
        path = self.write("gb.md", "中文内容\n".encode("gb18030"))
        snap = D.snapshot(path)
        self.assertEqual(snap["encoding"], "gb18030")
        self.assertEqual(snap["text"], "中文内容\n")

    def test_binary_and_oversized_files_are_refused_not_replaced(self):
        binary = self.write("bin.md", b"# hi\x00\x01\x02")
        with self.assertRaises(ValueError):
            D.snapshot(binary)
        big = self.write("big.md", b"x" * (8 * 1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            D.snapshot(big)


class WriteCheckedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-write-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.conflicts = os.path.join(self.root, "conflicts")
        self.path = os.path.join(self.root, "doc.md")
        with open(self.path, "w", encoding="utf-8", newline="") as handle:
            handle.write("第一版\n")

    def read(self, path=None):
        with open(path or self.path, encoding="utf-8") as handle:
            return handle.read()

    def test_matching_revision_writes_and_returns_the_new_revision(self):
        base = D.revision(self.path)
        revision = D.write_checked(self.path, "第二版\n", base)
        self.assertEqual(self.read(), "第二版\n")
        self.assertEqual(revision, D.revision(self.path))
        self.assertNotEqual(revision, base)

    def test_external_change_blocks_the_save_and_keeps_both_versions(self):
        base = D.revision(self.path)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("外部改的\n")
        with self.assertRaises(D.ConflictError) as caught:
            D.write_checked(self.path, "我编辑的\n", base)
        self.assertEqual(caught.exception.current, D.revision(self.path))
        self.assertEqual(self.read(), "外部改的\n")      # disk untouched
        self.assertNotIn("我编辑的", self.read())

    def test_missing_file_needs_an_explicit_new_target(self):
        base = D.revision(self.path)
        os.remove(self.path)
        with self.assertRaises(D.ConflictError):
            D.write_checked(self.path, "新内容\n", base)
        self.assertFalse(os.path.exists(self.path))

    def test_overwrite_backs_up_the_disk_version_first(self):
        base = D.revision(self.path)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("外部改的\n")
        D.write_checked(self.path, "我的版本\n", base, D.revision(self.path), self.conflicts)
        self.assertEqual(self.read(), "我的版本\n")
        backups = os.listdir(self.conflicts)
        self.assertEqual(len(backups), 1, backups)
        with open(os.path.join(self.conflicts, backups[0]), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "外部改的\n")

    def test_overwrite_requires_a_backup_directory(self):
        base = D.revision(self.path)
        with self.assertRaises(ValueError):
            D.write_checked(self.path, "x\n", base, base, None)

    def test_crlf_file_stays_crlf_after_saving(self):
        crlf = os.path.join(self.root, "crlf.md")
        with open(crlf, "wb") as handle:
            handle.write("一行\r\n二行\r\n".encode("utf-8"))
        D.write_checked(crlf, "一行\n二行\n三行\n", D.revision(crlf))
        with open(crlf, "rb") as handle:
            self.assertEqual(handle.read(), "一行\r\n二行\r\n三行\r\n".encode("utf-8"))

    def test_gb18030_file_is_saved_back_in_its_own_encoding(self):
        gb = os.path.join(self.root, "gb.md")
        with open(gb, "wb") as handle:
            handle.write("中文\n".encode("gb18030"))
        D.write_checked(gb, "中文改\n", D.revision(gb))
        with open(gb, "rb") as handle:
            raw = handle.read()
        self.assertEqual(raw.decode("gb18030"), "中文改\n")

    def test_gb18030_keeps_characters_outside_the_common_chinese_set(self):
        gb = os.path.join(self.root, "gb2.md")
        with open(gb, "wb") as handle:
            handle.write("中文\n".encode("gb18030"))
        D.write_checked(gb, "中文 😀\n", D.revision(gb))
        with open(gb, "rb") as handle:
            raw = handle.read()
        self.assertEqual(raw.decode("gb18030"), "中文 😀\n")


class WriteFailureBranchTests(unittest.TestCase):
    """只读、被占用、空文档：失败时必须保留原文件与缓冲内容。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-writefail-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.path = os.path.join(self.root, "doc.md")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("原始内容\n")

    def read(self):
        with open(self.path, encoding="utf-8") as handle:
            return handle.read()

    @unittest.skipUnless(os.name == "nt", "Windows read-only attribute")
    def test_read_only_target_fails_without_touching_the_file(self):
        import stat
        os.chmod(self.path, stat.S_IREAD)
        self.addCleanup(os.chmod, self.path, stat.S_IWRITE)
        with self.assertRaises(OSError):
            D.write_checked(self.path, "新内容\n", D.revision(self.path))
        self.assertEqual(self.read(), "原始内容\n")
        self.assertEqual(os.listdir(self.root), ["doc.md"], "失败的写入不能留下临时文件")

    @unittest.skipUnless(os.name == "nt", "Windows sharing modes")
    def test_target_held_by_another_process_keeps_the_original(self):
        import ctypes as c
        from ctypes import wintypes as w
        kernel32 = c.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p,
                                         w.DWORD, w.DWORD, w.HANDLE]
        kernel32.CreateFileW.restype = w.HANDLE
        kernel32.CloseHandle.argtypes = [w.HANDLE]
        GENERIC_READ, OPEN_EXISTING, INVALID = 0x80000000, 3, c.c_void_p(-1).value
        handle = kernel32.CreateFileW(self.path, GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
        self.assertNotEqual(handle, INVALID, "无法独占打开测试文件")
        try:
            with self.assertRaises(OSError):
                D.write_checked(self.path, "新内容\n", D.revision(self.path))
        finally:
            kernel32.CloseHandle(handle)
        self.assertEqual(self.read(), "原始内容\n")

    def test_empty_document_can_be_read_and_written(self):
        empty = os.path.join(self.root, "空文档.md")
        open(empty, "w", encoding="utf-8").close()
        snap = D.snapshot(empty)
        self.assertEqual(snap["text"], "")
        self.assertEqual(snap["revision"], D.revision(empty))
        D.write_checked(empty, "第一行\n", snap["revision"])
        with open(empty, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "第一行\n")

    def test_saving_the_same_content_twice_is_stable(self):
        base = D.revision(self.path)
        first = D.write_checked(self.path, "同样内容\n", base)
        second = D.write_checked(self.path, "同样内容\n", first)
        self.assertEqual(first, second, "内容相同时修订号应保持一致")
        self.assertEqual(self.read(), "同样内容\n")


class RecoveryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.store = recovery.RecoveryStore(self.temp.name)

    def test_snapshot_round_trip_is_keyed_by_identity(self):
        key = self.store.save("C:/notes/a.md", "未保存内容", "C:/notes/a.md", "abc")
        self.assertTrue(key)
        records = self.store.list()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["text"], "未保存内容")
        self.assertEqual(records[0]["baseline"], "abc")

    def test_same_identity_overwrites_only_its_own_snapshot(self):
        self.store.save("draft:1", "第一版")
        self.store.save("draft:2", "另一份")
        self.store.save("draft:1", "第二版")
        texts = sorted(record["text"] for record in self.store.list())
        self.assertEqual(texts, ["另一份", "第二版"])

    def test_discard_only_removes_the_saved_version(self):
        self.store.save("draft:1", "已保存的内容")
        self.store.discard("draft:1", "别的内容")        # editor moved on
        self.assertEqual(len(self.store.list()), 1)
        self.store.discard("draft:1", "已保存的内容")
        self.assertEqual(self.store.list(), [])

    def test_corrupt_snapshot_is_reported_and_keeps_the_file(self):
        self.store.save("draft:1", "好内容")
        broken = os.path.join(self.temp.name, ".recovery", "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        records = self.store.list()
        self.assertEqual(len(records), 2)
        self.assertTrue(any("error" in record for record in records))
        self.assertTrue(os.path.exists(broken))
        self.assertEqual(len([r for r in records if r.get("text") == "好内容"]), 1)

    def test_snapshot_limit_is_enforced_without_touching_existing_files(self):
        store = recovery.RecoveryStore(self.temp.name)
        os.makedirs(os.path.join(self.temp.name, ".recovery"), exist_ok=True)
        for index in range(500):
            with open(os.path.join(self.temp.name, ".recovery", "old%d.json" % index),
                      "w", encoding="utf-8") as handle:
                handle.write('{"text": "x"}')
        with self.assertRaises(ValueError):
            store.save("draft:new", "新内容")
        self.assertFalse(os.path.exists(os.path.join(self.temp.name, ".recovery",
                                                    store.key("draft:new") + ".json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
