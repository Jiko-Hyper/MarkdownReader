import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from mdreader import core, launcher
from tools.ai_client import MDReaderClient


class AiApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-ai-")
        self.addCleanup(self.temp.cleanup)
        self.token = "test-secret-" + "a" * 32
        self.ws, self.httpd, port = core.serve(self.temp.name, ai_token=self.token, ai_writable=True)
        self.thread = core.ServerThread(self.httpd)
        self.thread.start()
        self.addCleanup(self.thread.stop)
        self.base = "http://127.0.0.1:%d" % port
        self.pid = self.ws.create_project("开发项目", with_readme=False)["id"]
        self.pdir = self.ws.require_project(self.pid)
        self.doc = self.ws.create_doc(self.pdir, "设计 文档", content="# 设计\n\n旧段落\n")

    def request(self, path="/api/ai/v1/tools", payload=None, headers=None):
        h = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        h.update(headers or {})
        req = Request(self.base + path, data=None if payload is None else json.dumps(payload).encode(), headers=h)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def call(self, tool_name, **args):
        return self.request("/api/ai/v1/tools/call", {"name": tool_name, "arguments": args})

    def read(self):
        status, result = self.call("read_document", pid=self.pid, doc=self.doc["id"])
        self.assertEqual(status, 200)
        return result["result"]

    def test_discovery_and_read_workflow(self):
        status, discovery = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(len(discovery["tools"]), 7)
        self.assertNotIn(self.token, json.dumps(discovery))
        self.assertEqual(self.call("list_projects")[0], 200)
        status, listing = self.call("list_documents", pid=self.pid)
        self.assertEqual(status, 200)
        self.assertTrue(any(d["id"] == self.doc["id"] for d in listing["result"]["documents"]))
        self.assertTrue(self.call("search_documents", pid=self.pid, query="旧段落")[1]["result"]["hits"])
        self.assertEqual(self.read()["content"], "# 设计\n\n旧段落\n")

    def test_shipped_client_uses_the_real_http_protocol(self):
        client = MDReaderClient(self.base, self.token)
        self.assertEqual(len(client.tools()), 7)
        created = client.call("create_document", pid=self.pid, name="客户端文档", content="示例")
        doc = client.call("read_document", pid=self.pid, doc=created["id"])
        self.assertEqual(doc["content"], "示例")
        with self.assertRaisesRegex(RuntimeError, "HTTP 409"):
            client.call("save_document", pid=self.pid, doc=created["id"], content="冲突", expected="0" * 64)
        with self.assertRaises(ValueError):
            MDReaderClient("https://remote.example", self.token)

    def test_create_save_replace_and_empty_content(self):
        status, created = self.call("create_document", pid=self.pid, name="新文件", content="")
        self.assertEqual(status, 200)
        self.assertEqual(self.ws.read_doc(self.pdir, created["result"]["id"]), "")
        before = self.read()
        status, saved = self.call("replace_text", pid=self.pid, doc=self.doc["id"],
                                 old_text="旧段落", new_text="AI 修改", expected=before["revision"])
        self.assertEqual(status, 200)
        self.assertIn("AI 修改", self.read()["content"])
        status, _ = self.call("save_document", pid=self.pid, doc=self.doc["id"],
                             content="", expected=saved["result"]["revision"])
        self.assertEqual(status, 200)
        self.assertEqual(self.read()["content"], "")

    def test_conflict_and_missing_revision_preserve_content(self):
        before = self.read()
        self.ws.save_doc(self.pdir, self.doc["id"], "用户新内容", before["revision"])
        for tool, extra in [("save_document", {"content": "覆盖"}),
                            ("replace_text", {"old_text": "旧段落", "new_text": "覆盖"})]:
            status, result = self.call(tool, pid=self.pid, doc=self.doc["id"],
                                       expected=before["revision"], **extra)
            self.assertEqual(status, 409)
            self.assertTrue(result["conflict"])
        self.assertEqual(self.call("save_document", pid=self.pid, doc=self.doc["id"], content="覆盖")[0], 400)
        self.assertEqual(self.read()["content"], "用户新内容")

    def test_simultaneous_writers_only_one_commits(self):
        revision = self.read()["revision"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda text: self.call("save_document", pid=self.pid,
                               doc=self.doc["id"], content=text, expected=revision)[0], ["甲", "乙"]))
        self.assertEqual(sorted(results), [200, 409])

    def test_ambiguous_replace_and_invalid_payloads(self):
        self.ws.save_doc(self.pdir, self.doc["id"], "重复 重复", self.read()["revision"])
        revision = self.read()["revision"]
        for old in ("重复", "不存在"):
            self.assertEqual(self.call("replace_text", pid=self.pid, doc=self.doc["id"],
                             expected=revision, old_text=old, new_text="替换")[0], 400)
        for payload in ([], {"name": "list_projects", "arguments": []},
                        {"name": "list_projects", "arguments": {"overwrite": True}},
                        {"name": "read_document", "arguments": {"pid": 123, "doc": "a.md"}}):
            self.assertEqual(self.request("/api/ai/v1/tools/call", payload)[0], 400)
        self.assertEqual(self.read()["content"], "重复 重复")

    def test_auth_origin_and_separate_credentials(self):
        self.assertEqual(self.request(headers={"Authorization": ""})[0], 403)
        self.assertEqual(self.request(headers={"Authorization": "Bearer wrong"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request(headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("/api/state")[0], 403)
        self.assertEqual(self.request(headers={"Authorization": "", "X-MDReader-Session": core.api_of(self.httpd).token})[0], 403)

    def test_disabled_readonly_and_path_escape(self):
        api = core.api_of(self.httpd)
        api.ai.writable = False
        self.assertEqual(len(self.request()[1]["tools"]), 4)
        self.assertEqual(self.call("create_document", pid=self.pid, name="禁止", content="x")[0], 400)
        self.assertEqual(self.call("read_document", pid=self.pid, doc="../../outside.md")[0], 400)
        self.assertEqual(self.call("read_document", pid=self.pid, doc="project.json")[0], 400)
        api.ai = None
        self.assertEqual(self.request()[0], 404)


class AiLauncherTests(unittest.TestCase):
    def test_options_validate_before_starting(self):
        with patch.dict(os.environ, {"MDREADER_AI_TOKEN": ""}), patch.object(launcher, "run") as run:
            self.assertEqual(launcher.main(["--ai-api"]), 2)
            self.assertEqual(launcher.main(["--ai-write"]), 2)
            run.assert_not_called()

    def test_options_pass_credentials_without_printing_them(self):
        token = "b" * 48
        with patch.dict(os.environ, {"MDREADER_AI_TOKEN": token}), patch.object(launcher, "run") as run:
            self.assertEqual(launcher.main(["--ai-api", "--ai-write"]), 0)
            self.assertEqual(run.call_args.kwargs["ai_token"], token)
            self.assertTrue(run.call_args.kwargs["ai_writable"])

    def test_browser_starts_and_stops_server(self):
        with tempfile.TemporaryDirectory(prefix="mdreader-ai-browser-") as root:
            observed = []
            def opened(url):
                with urlopen(url, timeout=3) as response:
                    observed.append(response.status)
            with patch.object(launcher.webbrowser, "open", side_effect=opened), patch.object(launcher, "_idle"):
                launcher.run(workspace=root, open_browser=True)
            self.assertEqual(observed, [200])
