# -*- coding: utf-8 -*-
"""Front-end wiring regression tests.

Cheap static checks that catch the mistakes that actually happen when the UI and
the HTTP API drift apart: a button that no longer exists, an element the script
queries but the page never defines, or a request to an endpoint the server does
not serve.

    python -m unittest tests.test_ui_wiring
"""

from __future__ import annotations

import os
import re
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI = os.path.join(HERE, "webui")
CORE = os.path.join(HERE, "mdreader", "core.py")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class UiWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _read(os.path.join(WEBUI, "app.js"))
        cls.html = _read(os.path.join(WEBUI, "index.html"))
        cls.css = _read(os.path.join(WEBUI, "app.css"))
        cls.core = _read(CORE)
        cls.html_ids = set(re.findall(r'id="([^"]+)"', cls.html))

    def test_every_queried_element_exists(self):
        """`$('#x')` must refer to an id that index.html actually defines."""
        queried = set(re.findall(r"""\$\(\s*['"]#([A-Za-z0-9_-]+)['"]""", self.js))
        queried |= set(re.findall(r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]""", self.js))
        # ids the script creates at runtime on its own markup
        dynamic = {"welcomeOpen", "emptyNew", "emptyImport", "mf_",
                   "tblCols", "tblRows", "tblHeader", "pasteHeader"}
        dynamic |= set(re.findall(r'id="([A-Za-z0-9_-]+)"', self.js))
        missing = sorted(queried - self.html_ids - dynamic)
        self.assertEqual(missing, [], "脚本查询了页面上不存在的元素：%s" % missing)

    def test_ids_defined_for_the_script_are_used(self):
        """No dead ids: everything the page defines for the script is queried."""
        queried = set(re.findall(r"""\$\(\s*['"]#([A-Za-z0-9_-]+)['"]""", self.js))
        queried |= set(re.findall(r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]""", self.js))
        # layout-only ids used by CSS or as structural anchors
        css_only = {"app", "sidebar", "main", "topbar", "content", "modeSeg",
                    "viewRender", "viewSource", "recentBlock", "pluginBlock", "formatBar"}
        unused = sorted(self.html_ids - queried - css_only)
        self.assertEqual(unused, [], "页面定义了但脚本从未使用的 id：%s" % unused)

    def test_link_check_ui_is_wired_to_the_server(self):
        """F09：网页端的「检查链接」与服务端接口必须对得上。"""
        self.assertIn("btnLinks", self.html, "页面缺少「检查链接」入口")
        for marker in ("checkDocumentLinks", "linkReportModal", "jumpToSourceLine"):
            self.assertIn(marker, self.js, "界面缺少 %s" % marker)
        for endpoint in ("/api/check/links", "/api/check/links/relink"):
            self.assertIn('"%s"' % endpoint, self.core, "缺少接口声明 %s" % endpoint)
        self.assertIn("'/api/check/links'", self.js, "网页端没有调用链接检查接口")

    def test_table_and_format_ui_is_wired_to_the_server(self):
        """F04：工具栏、表格弹层与核心的两个编辑接口必须对得上。"""
        for element in ("formatBar", "fmtTable", "fmtEditTable", "fmtPasteTable", "fmtHeading"):
            self.assertIn(element, self.html, "页面缺少 %s" % element)
        for action in ("bold", "italic", "bullets", "ordered", "quote", "code", "link"):
            self.assertIn('data-format="%s"' % action, self.html, "工具栏缺少 %s" % action)
        for level in range(1, 7):
            self.assertIn('data-heading="%d"' % level, self.html, "标题菜单缺少 H%d" % level)
        for marker in ("applyFormat", "insertTableDialog", "editTableDialog", "pasteAsTable",
                       "onEditorPaste", "tableModal", "applyEditResult"):
            self.assertIn(marker, self.js, "界面缺少 %s" % marker)
        for endpoint in ("/api/edit/table", "/api/edit/format"):
            self.assertIn('"%s"' % endpoint, self.core, "缺少接口声明 %s" % endpoint)
            self.assertIn("'%s'" % endpoint, self.js, "网页端没有调用 %s" % endpoint)
        self.assertIn(".format-bar", self.css, "样式表缺少格式工具栏样式")
        self.assertIn(".table-grid", self.css, "样式表缺少表格弹层样式")

    def test_plugin_ui_is_wired_to_the_server(self):
        """插件（P01）：页面入口、命令状态与服务端接口必须对得上。"""
        for element in ("btnPlugins", "btnInsertImage", "btnExportPlugin",
                        "pluginPackageFile", "pluginImageFile", "exportFormat"):
            self.assertIn(element, self.html, "页面缺少 %s" % element)
        for marker in ("pluginManager", "refreshPlugins", "pluginTarget",
                       "insertImageWithPlugin", "exportWithPlugin", "pluginListHtml"):
            self.assertIn(marker, self.js, "界面缺少 %s" % marker)
        for endpoint in ("/api/plugins", "/api/plugins/install", "/api/plugins/toggle",
                        "/api/plugins/remove", "/api/plugins/insert", "/api/plugins/export",
                        "/api/plugins/task"):
            self.assertIn('"%s"' % endpoint, self.core, "缺少接口声明 %s" % endpoint)
        self.assertIn(".plugin-row", self.css, "样式表缺少插件行样式")

    def test_image_paste_and_drop_are_wired_to_the_insert_channel(self):
        """F03：网页端粘贴截图 / 拖入图片都与选择文件走同一条插入通道。"""
        for marker in ("insertImageBlob", "clipboardImageFile", "isImageName"):
            self.assertIn(marker, self.js, "界面缺少 %s" % marker)
        self.assertIn("clipboardImageFile(data)", self.js, "粘贴事件没有先认图片")
        self.assertIn("insertImageBlob(image, '已插入截图')", self.js, "粘贴截图没有接进插入通道")
        self.assertIn("droppedFiles.every(", self.js, "拖入图片没有单独的判断")
        self.assertIn("await insertImageBlob(droppedFiles[0]", self.js, "拖入的图片没有接进插入通道")
        self.assertIn("'/api/plugins/insert'", self.js, "图片仍然要交给插件落附件")
        self.assertIn('"/api/plugins/insert"', self.core, "缺少图片插入接口")

    def test_every_api_call_has_a_server_route(self):
        called = set(re.findall(r"""['"](/api/[A-Za-z0-9_/-]+)""", self.js))
        routes = set(re.findall(r"""path == ['"](/api/[A-Za-z0-9_/-]+)['"]""", self.core))
        from mdreader.ai_api import PREFIX
        ai_source = _read(os.path.join(HERE, 'mdreader', 'ai_api.py'))
        routes |= {PREFIX + suffix for suffix in re.findall(r'path != PREFIX \+ "([^"]+)"', ai_source)}
        unknown = sorted(called - routes)
        self.assertEqual(unknown, [], "前端调用了服务端没有的接口：%s" % unknown)

    def test_document_endpoints_are_reachable(self):
        """The endpoints the reader depends on are all declared."""
        for endpoint in ("/api/state", "/api/project", "/api/doc", "/api/loose",
                         "/api/localfile", "/api/loose/open", "/api/loose/save",
                         "/api/loose/saveas", "/api/loose/forget", "/api/loose/to_project",
                         "/api/loose/draft", "/api/dialog/open", "/api/dialog/save"):
            self.assertIn('"%s"' % endpoint, self.core, "缺少接口声明 %s" % endpoint)

    def test_loose_document_ui_is_present(self):
        """临时查看 must stay reachable from the interface."""
        for marker in ("renderLooseDocument", "openLooseFiles", "saveLooseDoc",
                       "looseToProject", "droppedPaths"):
            self.assertIn(marker, self.js, "界面缺少 %s" % marker)
        for element in ("recentList", "btnOpenLocal", "btnNewLoose"):
            self.assertIn(element, self.html, "页面缺少 %s" % element)

    def test_sidebar_lists_are_styled(self):
        for klass in ("recent", "loose-bar", "loose-path", "statline"):
            self.assertIn(".%s" % klass, self.css, "样式表缺少 .%s" % klass)

    def test_page_shares_the_workspace_theme_with_the_desktop(self):
        """网页阅读的底色必须跟随（并被）桌面窗口使用同一个工作区主题。"""
        self.assertIn("loadSharedTheme", self.js, "网页未读取工作区主题")
        self.assertIn("/api/settings", self.js, "网页未与桌面共享主题设置")
        self.assertIn('path == "/api/settings"', self.core, "缺少主题设置接口")


if __name__ == "__main__":
    unittest.main(verbosity=2)
