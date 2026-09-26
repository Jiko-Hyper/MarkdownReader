import json
import os
import tempfile
import unittest
from urllib.request import Request, urlopen

from mdreader import core


class RecoveryApiTests(unittest.TestCase):
    """快照由两个入口共用：网页写的快照桌面也能恢复，反之亦然。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-recovery-api-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.ws = core.Workspace(self.root)
        self.api = core.Api(self.ws, os.path.join(self.root, "webui"))

    def test_snapshot_round_trip_through_the_api(self):
        saved = self.api.post("/api/recovery/save", {"identity": "C:/notes/一.md",
                                                     "text": "未保存的内容",
                                                     "path": "C:/notes/一.md",
                                                     "baseline": "abc"})
        self.assertTrue(saved["ok"])
        listing = self.api.get("/api/recovery", {})
        self.assertEqual(len(listing["records"]), 1)
        self.assertNotIn("text", listing["records"][0], "列表不应传输正文")
        one = self.api.get("/api/recovery", {"key": saved["key"]})
        self.assertEqual(one["records"][0]["text"], "未保存的内容")
        self.assertEqual(one["records"][0]["baseline"], "abc")

    def test_discard_only_drops_the_saved_version(self):
        self.api.post("/api/recovery/save", {"identity": "draft:1", "text": "已保存内容"})
        self.api.post("/api/recovery/discard", {"identity": "draft:1", "saved_text": "别的内容"})
        self.assertEqual(len(self.api.get("/api/recovery", {})["records"]), 1)
        self.api.post("/api/recovery/discard", {"identity": "draft:1", "saved_text": "已保存内容"})
        self.assertEqual(self.api.get("/api/recovery", {})["records"], [])

    def test_snapshot_needs_an_identity(self):
        with self.assertRaises(ValueError):
            self.api.post("/api/recovery/save", {"text": "没有身份"})

    def test_unknown_snapshot_key_is_reported(self):
        with self.assertRaises(KeyError):
            self.api.get("/api/recovery", {"key": "deadbeef"})

    def test_document_payload_carries_the_shared_identity(self):
        pid = self.ws.create_project("项目")["id"]
        pdir = self.ws.require_project(pid)
        doc = self.ws.create_doc(pdir, "第一篇", content="# 一\n")
        payload = self.api.get("/api/doc", {"pid": pid, "doc": doc["id"]})
        self.assertEqual(payload["identity"], os.path.join(pdir, "第一篇.md"))
        self.assertTrue(payload["info"]["revision"])


class EditApiTests(unittest.TestCase):
    """F04：表格与格式接口。规则在核心，桌面端 import 同一批函数，网页端走这两个接口。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-edit-api-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.ws = core.Workspace(self.root)
        self.api = core.Api(self.ws, os.path.join(self.root, "webui"))

    def test_format_endpoint_returns_the_new_text_and_selection(self):
        result = self.api.post("/api/edit/format", {"text": "重点", "start": 0, "end": 2,
                                                    "action": "bold"})
        self.assertEqual(result["text"], "**重点**")
        self.assertEqual((result["start"], result["end"]), (2, 4))

    def test_format_endpoint_reports_an_unknown_action(self):
        with self.assertRaises(ValueError):
            self.api.post("/api/edit/format", {"text": "x", "action": "rainbow"})

    def test_table_read_then_edit_round_trip(self):
        text = "| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n"
        read = self.api.post("/api/edit/table", {"text": text, "op": "read", "offset": 4})
        self.assertEqual(read["table"]["header"], ["名称", "数量"])
        edited = self.api.post("/api/edit/table", {"text": text, "op": "insert_row", "offset": 4,
                                                   "args": {"index": 1, "values": ["乙", "2"]}})
        self.assertIn("| 乙 | 2 |", edited["text"])

    def test_a_structure_we_cannot_read_is_refused_with_a_reason(self):
        text = "| 名称 | 数量 |\n| --- | --- |\n| 甲 |\n"
        with self.assertRaises(ValueError) as raised:
            self.api.post("/api/edit/table", {"text": text, "op": "delete_row", "offset": 4,
                                              "args": {"index": 0}})
        self.assertIn("对不上", str(raised.exception))

    def test_cursor_outside_a_table_is_reported(self):
        with self.assertRaises(ValueError):
            self.api.post("/api/edit/table", {"text": "正文\n", "op": "read", "offset": 0})

    def test_parse_op_only_previews_and_keeps_the_warnings(self):
        parsed = self.api.post("/api/edit/table", {"text": "", "op": "parse",
                                                   "payload": "a\tb\tc\n1\t2\n"})
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["rows"], [["a", "b", "c"], ["1", "2", ""]])
        self.assertTrue(parsed["warnings"])

    def test_paste_op_replaces_the_selection(self):
        text = "开场\n旧内容\n结尾\n"
        result = self.api.post("/api/edit/table", {
            "text": text, "op": "paste", "payload": "名称\t数量\n甲\t1\n",
            "start": text.index("旧内容"), "end": text.index("结尾"), "header": True})
        self.assertEqual(result["text"],
                         "开场\n\n| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n\n结尾\n")

    def test_missing_text_and_oversized_text_are_both_refused(self):
        with self.assertRaises(ValueError):
            self.api.post("/api/edit/format", {"start": 0, "end": 0, "action": "bold"})
        with self.assertRaises(ValueError):
            self.api.post("/api/edit/format", {"text": "x" * (core.Api.EDIT_MAX_CHARS + 1),
                                               "start": 0, "end": 0, "action": "bold"})

    def test_editing_never_touches_the_workspace(self):
        before = sorted(os.listdir(self.root))
        self.api.post("/api/edit/format", {"text": "重点", "start": 0, "end": 2, "action": "bold"})
        self.api.post("/api/edit/table", {"text": "| a |\n| --- |\n| 1 |\n", "op": "insert_row",
                                          "offset": 0, "args": {"index": 0, "values": ["2"]}})
        self.assertEqual(sorted(os.listdir(self.root)), before, "这两个接口不写磁盘")


class FormulaApiTests(unittest.TestCase):
    """F05：公式图片接口与工作区缓存。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-formula-api-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.ws = core.Workspace(self.root)
        self.api = core.Api(self.ws, os.path.join(self.root, "webui"))

    def test_endpoint_returns_a_png_and_reuses_the_cache(self):
        first = self.api.get("/api/formula", {"tex": r"\frac{a}{b}", "size": "17",
                                              "theme": "light"})
        self.assertEqual(first.status, 200)
        self.assertEqual(first.ctype, "image/png")
        self.assertTrue(first.body.startswith(b"\x89PNG"))
        cache = core.formula_cache_dir(self.root)
        files = os.listdir(cache)
        self.assertEqual(len(files), 1, files)
        again = self.api.get("/api/formula", {"tex": r"\frac{a}{b}", "size": "17",
                                              "theme": "light"})
        self.assertEqual(again.body, first.body)
        self.assertEqual(len(os.listdir(cache)), 1, "同一个表达式不该重复生成")

    def test_different_theme_or_size_gets_its_own_picture(self):
        light = self.api.get("/api/formula", {"tex": "x", "size": "17", "theme": "light"})
        dark = self.api.get("/api/formula", {"tex": "x", "size": "17", "theme": "dark"})
        bigger = self.api.get("/api/formula", {"tex": "x", "size": "34", "theme": "light"})
        self.assertNotEqual(light.body, dark.body)
        self.assertNotEqual(light.body, bigger.body)
        self.assertEqual(len(os.listdir(core.formula_cache_dir(self.root))), 3)

    def test_endpoint_reports_a_bad_expression_and_missing_text(self):
        with self.assertRaises(ValueError):
            self.api.get("/api/formula", {"tex": r"\foo{x}"})
        with self.assertRaises(ValueError):
            self.api.get("/api/formula", {})
        with self.assertRaises(ValueError):
            self.api.get("/api/formula", {"tex": "x", "size": "很大"})

    def test_rendered_document_points_at_the_endpoint(self):
        pid = self.ws.create_project("公式")["id"]
        pdir = self.ws.require_project(pid)
        doc = self.ws.create_doc(pdir, "公式", content="# 标题\n\n质能 $E = mc^{2}$。\n")
        result = self.ws.render_doc(pdir, doc["id"], pid=pid)
        self.assertIn("/api/formula?", result["html"])
        self.assertIn("formula-img", result["html"])
        self.assertIn("E = mc^{2}", result["html"], "替代文字要保留源码")
        self.assertEqual(result["raw"].count("$"), 2, "渲染不得改动正文")

    def test_export_inlines_the_pictures(self):
        pid = self.ws.create_project("公式")["id"]
        pdir = self.ws.require_project(pid)
        doc = self.ws.create_doc(pdir, "公式", content="$$\nE = mc^{2}\n$$\n")
        dest = os.path.join(self.temp.name, "导出.html")
        self.ws.export_doc_html(pdir, doc["id"], dest, pid=pid)
        with open(dest, encoding="utf-8") as handle:
            page = handle.read()
        self.assertIn("data:image/png;base64,", page)
        self.assertNotIn("/api/formula?", page, "导出的 HTML 不能依赖本地服务")

    def test_a_broken_formula_does_not_break_the_whole_document(self):
        pid = self.ws.create_project("公式")["id"]
        pdir = self.ws.require_project(pid)
        doc = self.ws.create_doc(pdir, "公式", content="开头 $\\foo{x}$ 结尾，还有 $y$。\n")
        result = self.ws.render_doc(pdir, doc["id"], pid=pid)
        self.assertIn("formula-error", result["html"])
        self.assertIn("$\\foo{x}$", result["html"])
        self.assertIn("结尾", result["html"])
        self.assertEqual(result["html"].count("<img"), 1)


class ServerTests(unittest.TestCase):
    def test_simultaneous_servers_keep_their_own_workspace(self):
        with tempfile.TemporaryDirectory(prefix="mdreader-servers-") as root:
            servers = []
            try:
                for name in ("first", "second"):
                    ws, httpd, port = core.serve(os.path.join(root, name))
                    ws.create_project(name)
                    thread = core.ServerThread(httpd)
                    thread.start()
                    servers.append((ws, httpd, thread, port, name))
                for ws, httpd, thread, port, name in servers:
                    # The local service refuses requests without the session
                    # token it handed to the page it served.
                    token = core.api_of(httpd).token
                    request = Request("http://127.0.0.1:%d/api/state" % port,
                                      headers={"X-MDReader-Session": token})
                    with urlopen(request, timeout=5) as response:
                        state = json.load(response)
                    self.assertEqual(state["workspace"], ws.root)
                    self.assertEqual([p["name"] for p in state["projects"]], [name])
            finally:
                for _, _, thread, _, _ in servers:
                    thread.stop()
                    thread.join(timeout=5)
