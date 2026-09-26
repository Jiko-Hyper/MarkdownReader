# -*- coding: utf-8 -*-
"""F09 链接检查的回归用例。

    python -m unittest tests.test_links

三组：识别（缺文件/越界/绝对路径/锚点/代码里的引用）、替换（只改指定引用、行号正确）、
接口层（授权、缓冲区优先、替换只往文档自己的目录里写）。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mdreader import core, links as LK
from mdreader import render as R

SAMPLE = (
    "# 标题一\n\n## 小节\n\n"
    "![有图](assets/有.png)\n"
    "![缺图](assets/没有.png)\n"
    "![越界](../外面.png)\n"
    "![绝对](C:\\Windows\\win.ini)\n\n"
    "[好链接](别的.md) [坏链接](没有这篇.md) [跳转](#小节) [坏跳转](#不存在)\n\n"
    "```\n![代码里的](不算.png)\n```\n\n"
    "[外站](https://example.com)\n"
)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-links-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "assets").mkdir()
        (self.root / "assets" / "有.png").write_bytes(b"png")
        (self.root / "别的.md").write_text("# 别的\n", encoding="utf-8")

    def identify(self, source):
        path = os.path.abspath(os.path.join(self.root, source))
        if not path.startswith(str(self.root)):
            raise PermissionError(source)
        if not os.path.isfile(path):
            raise FileNotFoundError(source)
        return path

    def report(self, markdown=SAMPLE):
        return LK.check(markdown, identify=self.identify)

    def kinds(self, report):
        return [(item["level"], item["source"]) for item in report["issues"]]

    def test_every_problem_is_named_with_its_line(self):
        report = self.report()
        self.assertEqual(self.kinds(report), [
            ("error", "assets/没有.png"),
            ("error", "../外面.png"),
            ("error", "C:\\Windows\\win.ini"),
            ("error", "没有这篇.md"),
            ("warn", "#不存在"),
        ])
        lines = {item["source"]: item["line"] for item in report["issues"]}
        self.assertEqual(lines["assets/没有.png"], 6)
        self.assertEqual(lines["没有这篇.md"], 10)
        self.assertEqual(report["summary"], "有 4 个必须处理的问题")
        self.assertFalse(report["ok"])

    def test_a_clean_document_reports_nothing(self):
        report = self.report("# 标题\n\n![图](assets/有.png) 与 [链接](别的.md)、[外站](https://a.b)\n")
        self.assertTrue(report["ok"])
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["checked"], 2, "外部网址不计入本地引用")
        self.assertEqual(report["summary"], "没有发现问题")

    def test_references_inside_code_are_not_links(self):
        report = self.report("```\n![图](assets/没有.png)\n```\n\n行内 `[x](没有.md)` 而已\n")
        self.assertEqual(report["issues"], [])

    def test_anchors_match_what_the_renderer_actually_emits(self):
        source = "# 一级\n\n## 中文 标题\n\n## 中文 标题\n\n### Emoji 🎯 也有\n"
        frag, _meta, _engine = R.render_markdown(source)
        emitted = set(__import__("re").findall(r'<h[1-6] id="([^"]+)"', frag))
        self.assertEqual(LK.heading_anchors(source), emitted)

    def test_a_missing_anchor_is_a_warning_with_the_right_anchor(self):
        report = self.report("## 存在的\n\n[ok](#存在的) [bad](#缺的)\n")
        self.assertEqual([item["source"] for item in report["warnings"]], ["#缺的"])
        self.assertEqual(report["errors"], [])

    def test_an_empty_reference_is_an_error(self):
        report = self.report("[空的]() 与 ![图]()\n")
        self.assertEqual(len(report["errors"]), 2)

    def test_reader_failures_are_not_reported_as_missing_files(self):
        """读取器自己出错时要说清是“读不出来”，不能谎报成“文件不存在”。"""
        def broken(_source):
            raise RuntimeError("读取器坏了")

        report = LK.check("![图](assets/有.png)\n", identify=broken)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("读不出", report["errors"][0]["message"])


class ReplaceTests(unittest.TestCase):
    def test_only_the_named_reference_is_replaced(self):
        markdown = "![A](旧的.png)\n\n![B](别的.png)\n\n[链接](旧的.png)\n"
        result = LK.replace_reference(markdown, "旧的.png", "assets/新的.png")
        self.assertTrue(result["ok"])
        self.assertEqual(result["replaced"], 2)
        self.assertIn("![A](assets/新的.png)", result["markdown"])
        self.assertIn("[链接](assets/新的.png)", result["markdown"])
        self.assertIn("![B](别的.png)", result["markdown"])

    def test_code_spans_are_left_alone(self):
        markdown = "![A](旧.png)\n\n`![代码](旧.png)`\n"
        result = LK.replace_reference(markdown, "旧.png", "新.png")
        self.assertEqual(result["replaced"], 1)
        self.assertIn("`![代码](旧.png)`", result["markdown"])

    def test_a_reference_that_is_gone_is_reported(self):
        result = LK.replace_reference("正文\n", "没有.png", "assets/新.png")
        self.assertFalse(result["ok"])
        self.assertIn("已经没有这个引用", result["reason"])

    def test_relative_target_is_written_with_forward_slashes(self):
        document = str(Path(self.__class__.__name__ and tempfile.gettempdir(), "文档", "笔记.md"))
        chosen = str(Path(tempfile.gettempdir(), "文档", "assets", "图.png"))
        self.assertEqual(LK.relative_target(document, chosen), "assets/图.png")


class LinkApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-links-api-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = core.Workspace(str(self.root / "app"))
        self.api = core.Api(self.ws, str(self.root / "webui"))
        self.pid = self.ws.create_project("项目")["id"]
        pdir = self.ws.require_project(self.pid)
        (Path(pdir) / "assets").mkdir(parents=True, exist_ok=True)
        (Path(pdir) / "assets" / "有.png").write_bytes(b"png")
        self.doc = self.ws.create_doc(pdir, "笔记", content=SAMPLE)

    def check(self, **payload):
        payload.setdefault("pid", self.pid)
        payload.setdefault("doc", self.doc["id"])
        return self.api.post("/api/check/links", payload)

    def test_api_reports_the_same_problems(self):
        result = self.check()
        self.assertEqual(result["document"].endswith("笔记.md"), True)
        self.assertEqual([item["source"] for item in result["report"]["errors"]],
                         ["assets/没有.png", "../外面.png", "C:\\Windows\\win.ini",
                          "别的.md", "没有这篇.md"],
                         "项目文档里「别的.md」也不存在，所以同样是错误")

    def test_the_buffer_wins_when_it_differs(self):
        result = self.check(markdown="# 只有正文\n")
        self.assertEqual(result["source"], "buffer")
        self.assertTrue(result["dirty"])
        self.assertEqual(result["report"]["issues"], [])

    def test_checking_does_not_write_anything(self):
        before = sorted(os.listdir(str(self.ws.require_project(self.pid))))
        self.check()
        self.assertEqual(sorted(os.listdir(str(self.ws.require_project(self.pid)))), before)

    def test_another_document_cannot_be_checked_through_this_call(self):
        outside = self.root / "别处.md"
        outside.write_text("# 外面\n", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self.api.post("/api/check/links", {"doc": str(outside)})

    def test_relink_copies_the_file_and_rewrites_only_the_buffer(self):
        chosen = self.root / "新图.png"
        chosen.write_bytes(b"new")
        result = self.api.post("/api/check/links/relink",
                               {"pid": self.pid, "doc": self.doc["id"], "markdown": SAMPLE,
                                "source": "assets/没有.png", "path": str(chosen)})
        self.assertTrue(result["ok"])
        self.assertEqual(result["link"], "assets/新图.png")
        self.assertTrue(Path(result["saved"]).is_file())
        self.assertIn("![缺图](assets/新图.png)", result["markdown"])
        self.assertEqual(result["line"], 6)
        on_disk = self.ws.read_doc(self.ws.require_project(self.pid), self.doc["id"])
        self.assertIn("assets/没有.png", on_disk, "替换只回新正文，磁盘要等用户保存")

    def test_relink_never_overwrites_an_existing_asset(self):
        chosen = self.root / "有.png"
        chosen.write_bytes("另一个同名文件".encode("utf-8"))
        result = self.api.post("/api/check/links/relink",
                               {"pid": self.pid, "doc": self.doc["id"], "markdown": SAMPLE,
                                "source": "assets/没有.png", "path": str(chosen)})
        self.assertTrue(result["ok"])
        self.assertNotEqual(result["link"], "assets/有.png")
        self.assertEqual((Path(self.ws.require_project(self.pid)) / "assets" / "有.png").read_bytes(),
                         b"png", "原有附件不能被覆盖")

    def test_relink_refuses_a_source_that_is_no_longer_there(self):
        chosen = self.root / "新图.png"
        chosen.write_bytes(b"new")
        result = self.api.post("/api/check/links/relink",
                               {"pid": self.pid, "doc": self.doc["id"], "markdown": "# 空\n",
                                "source": "assets/没有.png", "path": str(chosen)})
        self.assertFalse(result["ok"])
        self.assertIn("已经没有这个引用", result["reason"])

    def test_relink_refuses_a_missing_or_oversized_file(self):
        with self.assertRaises(ValueError):
            self.api.post("/api/check/links/relink",
                          {"pid": self.pid, "doc": self.doc["id"], "markdown": SAMPLE,
                           "source": "assets/没有.png", "path": str(self.root / "没有.bin")})
        chosen = self.root / "大图.png"
        chosen.write_bytes(b"png")
        with mock.patch.object(core.os.path, "getsize", return_value=64 * 1024 * 1024):
            with self.assertRaises(ValueError) as raised:
                self.api.post("/api/check/links/relink",
                              {"pid": self.pid, "doc": self.doc["id"], "markdown": SAMPLE,
                               "source": "assets/没有.png", "path": str(chosen)})
        self.assertIn("32 MB", str(raised.exception))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
