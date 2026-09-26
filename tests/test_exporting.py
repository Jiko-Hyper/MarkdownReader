# -*- coding: utf-8 -*-
"""F06 统一导出服务的回归用例。

    python -m unittest tests.test_exporting

三组：

* **预检**：缺图/越界图是严重问题，公式、过宽表格、死链、字体、大文档是可降级提示，
  空文档直接拒绝；一切都只算不写。
* **快照与来源**：未保存时必须由用户选「当前编辑内容」还是「磁盘版本」，默认前者；
  已经保存过的文档不问。
* **目标**：不能导出到源文档本身；目标已存在要确认（确认后目标被换过要重新确认）；
  失败或取消保留原目标。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mdreader import core, exporting as EX
from mdreader import documents as D


class SourceChoiceTests(unittest.TestCase):
    def test_a_saved_document_is_exported_from_disk_without_asking(self):
        result = EX.choose_source(has_buffer=False, dirty=False)
        self.assertEqual(result["source"], EX.SOURCE_DISK)
        self.assertFalse(result["needs_choice"])

    def test_unsaved_edits_make_the_user_choose_and_default_to_the_buffer(self):
        result = EX.choose_source(has_buffer=True, dirty=True)
        self.assertEqual(result["source"], EX.SOURCE_BUFFER)
        self.assertTrue(result["needs_choice"], "有未保存修改时必须先问")

    def test_an_explicit_answer_wins_and_stops_the_question(self):
        for asked in (EX.SOURCE_BUFFER, EX.SOURCE_DISK):
            with self.subTest(asked=asked):
                result = EX.choose_source(has_buffer=True, dirty=True, asked=asked)
                self.assertEqual(result["source"], asked)
                self.assertFalse(result["needs_choice"], "用户已经选过了就不再问")

    def test_an_unmodified_buffer_needs_no_question(self):
        result = EX.choose_source(has_buffer=True, dirty=False)
        self.assertFalse(result["needs_choice"])


class DetectTests(unittest.TestCase):
    def test_counts_what_the_document_actually_contains(self):
        text = ("# 标题\n\n![图](assets/图.png)\n\n公式 $x^{2}$\n\n"
                "| a | b |\n|---|---|\n| 1 | 2 |\n\n[本地](别的.md) 与 [外站](https://example.com)\n"
                "```\n![代码里的图](不算.png)\n$不算公式$\n```\n")
        found = EX.detect_document(text)
        self.assertEqual(found["images"], ["assets/图.png"], "代码块里的图片引用不算")
        self.assertEqual([entry["tex"] for entry in found["formulas"]], ["x^{2}"])
        self.assertEqual(found["tables"], 1)
        self.assertEqual(found["table_columns"], [(2, 1)])
        self.assertEqual(found["links"], ["别的.md", "https://example.com"])
        self.assertEqual(found["external_links"], 1)
        self.assertEqual(found["headings"], 1)

    def test_inline_code_does_not_look_like_a_link(self):
        found = EX.detect_document("行内代码 `[看起来像链接](其实不是.md)` 而已\n")
        self.assertEqual(found["links"], [])


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-preflight-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.doc = self.root / "笔记.md"
        self.doc.write_text("# 笔记\n", encoding="utf-8")
        (self.root / "assets").mkdir(exist_ok=True)
        (self.root / "assets" / "有.png").write_bytes(b"png")

    def plan(self, markdown, *, fonts=None):
        snapshot = EX.snapshot(identity=str(self.doc), markdown=markdown, revision="rev",
                               source=EX.SOURCE_DISK, root=str(self.root))

        def reader(source):
            path = os.path.join(self.root, source)
            if not os.path.isfile(path):
                raise FileNotFoundError(source)
            return path

        return EX.preflight(snapshot, resource_reader=reader,
                            fonts=fonts or (lambda _name: True))

    def test_a_missing_image_blocks_the_export(self):
        report = self.plan("![图](assets/没有.png)\n")
        self.assertTrue(report["blocked"])
        self.assertIn("找不到", report["errors"][0]["message"])

    def test_an_image_outside_the_root_blocks_the_export(self):
        snapshot = EX.snapshot(identity=str(self.doc), markdown="![图](别的/图.png)\n",
                               revision="rev", source=EX.SOURCE_DISK, root=str(self.root))

        def reader(_source):
            raise PermissionError("越界")

        report = EX.preflight(snapshot, resource_reader=reader, fonts=lambda _n: True)
        self.assertTrue(report["blocked"])
        self.assertIn("授权", report["errors"][0]["message"])

    def test_an_image_that_exists_is_not_reported(self):
        report = self.plan("![图](assets/有.png)\n")
        self.assertFalse(report["blocked"])
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["stats"]["images"], 1)

    def test_degradable_items_are_warnings_not_errors(self):
        report = self.plan("公式 $\\foo{x}$\n\n"
                           "| a | b | c | d | e | f | g | h | i | j | k | l | m |\n"
                           "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
                           "| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |\n\n"
                           "[死链](没有.md)\n")
        kinds = {item["kind"] for item in report["warnings"]}
        self.assertEqual(kinds, {"formula", "wide_table", "dead_link"})
        self.assertFalse(report["blocked"], "这些都能降级，不该阻止导出")
        self.assertIn("提示", report["summary"])

    def test_an_empty_document_is_refused(self):
        report = self.plan("   \n\n")
        self.assertTrue(report["blocked"])
        self.assertEqual(report["errors"][0]["kind"], "empty")

    def test_missing_fonts_are_reported_as_a_swap(self):
        report = self.plan("正文\n", fonts=lambda _name: False)
        self.assertIn("font", {item["kind"] for item in report["warnings"]})
        self.assertIn("替代", report["warnings"][0]["message"])

    def test_a_clean_document_reports_nothing(self):
        report = self.plan("# 标题\n\n普通正文，$E = mc^{2}$，![图](assets/有.png)\n")
        self.assertTrue(report["ok"])
        self.assertEqual(report["items"], [])
        self.assertEqual(report["summary"], "没有发现问题")

    def test_preflight_never_writes_anything(self):
        before = sorted(os.listdir(self.root))
        self.plan("![图](assets/没有.png)\n公式 $\\foo{x}$\n")
        self.assertEqual(sorted(os.listdir(self.root)), before)


class PageOptionTests(unittest.TestCase):
    def test_defaults_are_a4_light_and_independent_of_the_reading_theme(self):
        options = EX.page_options()
        self.assertEqual(options["size"], "A4")
        self.assertEqual(options["theme"], "print")
        self.assertNotIn("dark", options.values())

    def test_callers_may_override_single_entries(self):
        options = EX.page_options({"body_pt": 12, "size": "A4"})
        self.assertEqual(options["body_pt"], 12)
        self.assertEqual(options["margin_mm"], EX.PAGE_DEFAULTS["margin_mm"])


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-target-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.doc = self.root / "笔记.md"
        self.doc.write_text("# 笔记\n", encoding="utf-8")

    def test_the_source_document_is_never_a_valid_target(self):
        self.assertTrue(EX.same_file(str(self.doc), str(self.doc)))
        self.assertTrue(EX.same_file(str(self.doc), str(self.root / "." / "笔记.md")))
        self.assertFalse(EX.same_file(str(self.doc), str(self.root / "别的.md")))

    def test_an_existing_target_needs_confirmation(self):
        missing = self.root / "还没有.pdf"
        target = self.root / "导出.pdf"
        self.assertIsNone(EX.target_conflict(str(missing), None), "不存在的目标没有冲突")
        target.write_bytes(b"old")
        conflict = EX.target_conflict(str(target), None)
        self.assertTrue(conflict and conflict["conflict"])
        self.assertEqual(conflict["revision"], D.revision(str(target)))
        self.assertIsNone(EX.target_conflict(str(target), True), "确认过就不再问")
        self.assertIsNone(EX.target_conflict(str(target), D.revision(str(target))),
                          "带着用户看到的修订号确认也可以")


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-resources-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "assets").mkdir()
        (self.root / "assets" / "一.png").write_bytes(b"\x89PNG-one")
        (self.root / "assets" / "二.jpg").write_bytes(b"\xff\xd8-two")

    def test_resources_are_collected_once_each(self):
        def identify(source):
            return str(self.root / source)

        files, manifest = EX.resources("![一](assets/一.png)\n\n![再引用](assets/一.png)\n"
                                       "![二](assets/二.jpg)\n",
                                       identify=identify, opener=lambda path: Path(path).read_bytes())
        self.assertEqual(len(files), 2, "同一张图只内嵌一次")
        self.assertEqual(len(manifest), 2)
        self.assertTrue(all(item["name"].startswith("resource-") for item in files))

    def test_a_missing_resource_stops_the_snapshot_instead_of_exporting_without_it(self):
        with self.assertRaises(EX.ExportError):
            EX.resources("![缺](assets/没有.png)\n", identify=lambda source: str(self.root / source),
                         opener=lambda path: Path(path).read_bytes())

    def test_remote_images_are_not_fetched(self):
        files, manifest = EX.resources("![远程](https://example.com/a.png)\n",
                                       identify=lambda source: (_ for _ in ()).throw(
                                           AssertionError("不能去抓远程图片")),
                                       opener=lambda path: b"")
        self.assertEqual((files, manifest), ([], {}))


class ExportApiTests(unittest.TestCase):
    """接口层：/api/export/check 只算不写，/api/plugins/export 拦在预检上。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-export-api-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = core.Workspace(str(self.root / "app"))
        self.api = core.Api(self.ws, str(self.root / "webui"))
        pid = self.ws.create_project("项目")["id"]
        pdir = self.ws.require_project(pid)
        self.pid = pid
        self.doc = self.ws.create_doc(pdir, "笔记", content="# 笔记\n\n正文\n")

    def check(self, **payload):
        payload.setdefault("pid", self.pid)
        payload.setdefault("doc", self.doc["id"])
        return self.api.post("/api/export/check", payload)

    def test_check_reports_the_page_template_and_the_source(self):
        plan = self.check()
        self.assertEqual(plan["page"]["size"], "A4")
        self.assertIn(plan["source"], (EX.SOURCE_BUFFER, EX.SOURCE_DISK))
        self.assertTrue(plan["preflight"]["ok"])
        self.assertEqual(plan["preflight"]["summary"], "没有发现问题")

    def test_check_does_not_write_or_run_a_task(self):
        before = sorted(os.listdir(str(self.root / "app")))
        self.check()
        self.assertEqual(sorted(os.listdir(str(self.root / "app"))), before)

    def test_unsaved_edits_are_reported_before_exporting(self):
        plan = self.check(markdown="# 改过\n")
        self.assertTrue(plan["needs_source"])
        self.assertEqual(plan["source"], EX.SOURCE_BUFFER)
        self.assertTrue(plan["dirty"])

    def test_check_finds_a_missing_image_and_blocks(self):
        pdir = self.ws.require_project(self.pid)
        doc = self.ws.create_doc(pdir, "缺图", content="# 缺图\n\n![图](assets/没有.png)\n")
        plan = self.check(doc=doc["id"])
        self.assertTrue(plan["preflight"]["blocked"])
        self.assertIn("找不到", plan["preflight"]["errors"][0]["message"])

    def test_an_image_outside_the_document_folder_is_refused(self):
        """越出授权目录的图片一律拒绝——预检也不能变成“任意路径探测”。"""
        outside = self.root / "别处"
        outside.mkdir(exist_ok=True)
        (outside / "图.png").write_bytes(b"png")
        pdir = self.ws.require_project(self.pid)
        doc = self.ws.create_doc(pdir, "越界", content="# 越界\n\n![图](../别处/图.png)\n")
        plan = self.check(doc=doc["id"])
        self.assertTrue(plan["preflight"]["blocked"])
        kinds = {item["kind"] for item in plan["preflight"]["errors"]}
        self.assertIn("outside_image", kinds)

    def test_check_works_without_any_export_plugin(self):
        """还没装插件时也要能告诉用户“这篇文档导出会有什么问题”。"""
        plan = self.check()
        self.assertEqual(plan["command"], "")
        self.assertIsInstance(plan["preflight"], dict)

    def test_exporting_without_a_plugin_command_is_refused(self):
        with self.assertRaises(Exception):
            self.api.post("/api/plugins/export", {"pid": self.pid, "doc": self.doc["id"]})


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
