import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from mdreader import core
from mdreader.media import snapshot_images

ROOT = Path(__file__).resolve().parents[1]


def manifest_id(package):
    """插件 id 从包里的清单读，不在测试里hardcode 版本号。"""
    with zipfile.ZipFile(package) as archive:
        return json.loads(archive.read("manifest.json").decode("utf-8"))["id"]


class OfficialPluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-official-")
        self.addCleanup(self.temp.cleanup)
        self.ws = core.Workspace(str(Path(self.temp.name, "workspace")))
        self.addCleanup(self.ws.plugins.shutdown)
        self.api = core.Api(self.ws, str(ROOT / "webui"))
        for package in sorted((ROOT / "plugins/packages").glob("*.zip")):
            self.ws.plugins.install(str(package))
            self.ws.plugins.enable(manifest_id(package))
        from PIL import Image
        self.picture = Path(self.temp.name, "photo.png")
        Image.new("RGB", (600, 300), "#659ed0").save(self.picture)
        self.doc = Path(self.temp.name, "文档.md")
        self.doc.write_text("# 文档标题\n\n中文 **粗体** and *italic*.\n\n- 第一项\n- 第二项\n\n| 名称 | 数量 |\n|---|---|\n| 测试 | 42 |\n", encoding="utf-8")
        self.info = self.api.loose.open_path(str(self.doc))

    def test_insert_and_export_real_documents_with_embedded_image(self):
        commands = self.ws.plugins.commands()
        image = next(c for c in commands if c["plugin"] == "mdreader.image-insert")
        inserted = self.api.post("/api/plugins/insert", {"command": image["command"], "doc": str(self.doc),
            "revision": self.info["revision"], "path": str(self.picture), "options": {"width": 240}, "wait": 30})
        self.assertIn('width=240', inserted["markdown"])
        markdown = self.doc.read_text(encoding="utf-8") + "\n" + inserted["markdown"]
        for ext in ("docx", "pdf"):
            cmd = next(c for c in commands if c.get("extension") == ext)
            target = Path(self.temp.name, "result." + ext)
            result = self.api.post("/api/plugins/export", {"command": cmd["command"], "doc": str(self.doc),
                "revision": self.info["revision"], "markdown": markdown, "source": "buffer",
                "dest": str(target), "wait": 60})
            self.assertEqual(result["path"], str(target))
            if ext == "docx":
                with zipfile.ZipFile(target) as archive:
                    xml = archive.read("word/document.xml").decode()
                    self.assertIn("文档标题", xml)
                    self.assertIn("w:tbl", xml)
                    self.assertIn("w:drawing", xml)
                    self.assertTrue(any(name.startswith("word/media/") for name in archive.namelist()))
            else:
                self.assertTrue(target.read_bytes().startswith(b"%PDF"))
                self.assertIn(b"/Subtype /Image", target.read_bytes())
        self.assertNotIn('width=240', self.doc.read_text(encoding="utf-8"), "导出不得修改源文件")

    def test_a_wide_image_at_the_end_of_a_chinese_paragraph_still_exports(self):
        """A01 发现：图片片段落在 CJK 折行的行尾时 ReportLab 抛 ``ord("")``，整份导不出来。"""
        markdown = ("# 图文\n\n" + "这是一段中文说明文字。" * 12 + "\n\n"
                    + '![大图](%s "width=700")\n' % self.picture.name)
        commands = self.ws.plugins.commands()
        for ext in ("pdf", "docx"):
            command = next(c for c in commands if c.get("extension") == ext)
            target = Path(self.temp.name, "图文." + ext)
            result = self.api.post("/api/plugins/export", {"command": command["command"],
                "doc": str(self.doc), "revision": self.info["revision"], "markdown": markdown,
                "source": "buffer", "dest": str(target), "wait": 120})
            self.assertEqual(result.get("path"), str(target), result)
            if ext == "pdf":
                self.assertIn(b"/Subtype /Image", target.read_bytes())

    def test_missing_image_and_destination_conflict_keep_existing_output(self):
        commands = self.ws.plugins.commands()
        cmd = next(c for c in commands if c.get("extension") == "pdf")
        target = Path(self.temp.name, "existing.pdf")
        target.write_bytes(b"original")
        result = self.api.post("/api/plugins/export", {"command": cmd["command"], "doc": str(self.doc),
            "dest": str(target), "wait": 30})
        self.assertTrue(result["conflict"])
        self.assertEqual(target.read_bytes(), b"original")
        with self.assertRaises(FileNotFoundError):
            snapshot_images('![missing](missing.png)', str(self.doc), self.temp.name)


class ExportFontFallbackTests(unittest.TestCase):
    """维护者 2026-09-25 反馈：转换后正文里的减号和上下标变成空白。"""

    SYMBOLS = "\u2212\u207b\u2076\u2081\u2082"          # − ⁻ ⁶ ₁ ₂

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-glyphs-")
        self.addCleanup(self.temp.cleanup)
        self.ws = core.Workspace(str(Path(self.temp.name, "workspace")))
        self.addCleanup(self.ws.plugins.shutdown)
        self.api = core.Api(self.ws, str(ROOT / "webui"))
        for package in sorted((ROOT / "plugins/packages").glob("*.zip")):
            self.ws.plugins.install(str(package))
            self.ws.plugins.enable(manifest_id(package))

    def shipped(self, kind):
        """加载插件包里真正发货的那份 exporting.py（不是源码目录里的那一份）。"""
        import importlib.util
        found = list(Path(self.temp.name, "workspace", "plugins", "installed", kind).glob("*/exporting.py"))
        self.assertEqual(len(found), 1, "插件包里应当带一份 exporting.py：%s" % found)
        name = "shipped_exporting_%s" % kind.replace(".", "_")
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(name, found[0])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def reference_cmap(self, path, index=0):
        """独立实现（fontTools）读出来的字形表，用来核对我们自己读的 cmap。"""
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            self.skipTest("这台机器上没有 fontTools，跳过与独立实现的比对")
        font = TTFont(path, fontNumber=index, lazy=False)
        try:
            return set(font.getBestCmap())
        finally:
            font.close()

    def export(self, markdown, ext):
        doc = Path(self.temp.name, "待导出-%s.md" % ext)
        doc.write_text(markdown, encoding="utf-8")
        info = self.api.loose.open_path(str(doc))
        command = next(c for c in self.ws.plugins.commands() if c.get("extension") == ext)
        target = Path(self.temp.name, "结果." + ext)
        result = self.api.post("/api/plugins/export", {"command": command["command"], "doc": str(doc),
            "revision": info["revision"], "markdown": markdown, "dest": str(target), "wait": 90})
        self.assertEqual(result.get("path"), str(target), result)
        return target, result

    def test_shipped_cmap_reader_agrees_with_an_independent_implementation(self):
        """自己读字形表是为了不把控制字符也当成“有字形”，读出来必须和 fontTools 一致。"""
        exporting = self.shipped("mdreader.export-pdf")
        for name, index in (("simsun.ttc", 0), ("msyh.ttc", 0), ("times.ttf", 0)):
            path = os.path.join(exporting.FONT_DIR, name)
            if not os.path.exists(path):
                self.skipTest("这台机器上没有 %s" % name)
            theirs = {code for code in self.reference_cmap(path, index) if code <= 0xFFFF}
            starts, ends = exporting.glyph_spans(path, index)
            mine = set()
            for start, end in zip(starts, ends):
                mine.update(range(start, min(end, 0xFFFF) + 1))
            self.assertEqual(mine - theirs, set(), "%s：自己读出来的字形比 fontTools 多" % name)
            self.assertEqual(theirs - mine, set(), "%s：漏掉了 fontTools 认得的字形" % name)

    def test_cjk_fonts_really_lack_the_minus_and_subscript_glyphs(self):
        """前提核对：这些字符确实不在中文正文/标题字体里，回退不是无缘无故的。"""
        exporting = self.shipped("mdreader.export-pdf")
        for name, missing in (("simsun.ttc", self.SYMBOLS), ("simhei.ttf", self.SYMBOLS + "\u00b2")):
            path = os.path.join(exporting.FONT_DIR, name)
            if not os.path.exists(path):
                self.skipTest("这台机器上没有 %s" % name)
            reference = self.reference_cmap(path)
            for char in missing:
                self.assertNotIn(ord(char), reference, "%s 竟然有 U+%04X" % (name, ord(char)))
                self.assertIsNotNone(exporting._fallback_for(char, False),
                                     "没有回退字体能画 U+%04X" % ord(char))

    def test_pdf_export_draws_symbols_the_chinese_fonts_lack(self):
        markdown = ("# 标题 10\u207b\u2076\n\n正文：R_eq = R_single / (n \u2212 m)，\u03b8\u2082 \u2212 \u03b8\u2081，"
                    "**粗体里的 10\u207b\u2076 与 x\u00b2**。\n\n| 项目 | 值 |\n|---|---|\n| 系数 | \u22122.18 |\n\n"
                    "行内 `变量 \u03b1 10\u207b\u2076`\n\n```text\nvalue = a \u2212 b  # 中文注释 10\u207b\u2076\n```\n")
        target, result = self.export(markdown, "pdf")
        data = target.read_bytes()
        self.assertIn(b"TimesNewRoman", data, "导出稿里没有回退字体的子集")
        try:
            import pypdfium2 as pdfium
        except ImportError:
            self.skipTest("没有 pypdfium2，跳过文本层核对")
        document = pdfium.PdfDocument(str(target))
        try:
            text = "".join(document[index].get_textpage().get_text_range() for index in range(len(document)))
        finally:
            document.close()
        for char in self.SYMBOLS:
            self.assertIn(char, text, "U+%04X 在 PDF 文本层里消失了" % ord(char))
        self.assertIn("R_eq = R_single", text)

    def test_docx_export_switches_only_the_runs_that_need_it(self):
        markdown = ("# 标题\n\n中文正文 10\u207b\u2076 与 R = (n \u2212 m)，\u03b8\u2082 保持中文字体。"
                    "同一段里换行  \n下一行继续 10\u207b\u2076。\n")
        target, result = self.export(markdown, "docx")
        self.assertEqual(result.get("warnings"), [], "换行之类的控制字符不该被当成缺字形")
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        runs = re.findall(r"<w:r>(.*?)</w:r>", xml, re.S)
        switched = [text for run in runs if 'w:ascii="Times New Roman"' in run
                    for text in re.findall(r"<w:t[^>]*>(.*?)</w:t>", run, re.S)]
        self.assertTrue(any("\u207b" in text for text in switched), switched)
        self.assertTrue(any("\u2082" in text for text in switched), switched)
        plain = [text for run in runs if "w:ascii=" not in run
                 for text in re.findall(r"<w:t[^>]*>(.*?)</w:t>", run, re.S)]
        self.assertTrue(any("保持中文字体" in text for text in plain), "中文正文不该被换成西文字体")

    def test_docx_never_leaves_a_character_in_a_font_without_its_glyph(self):
        """Word 那边没法在测试里渲染，就直接检查「每个字符所在的 run 都指定了有它字形的字体」。"""
        markdown = ("# 标题 10\u207b\u2076\n\n**粗体 10\u207b\u2076 与 x\u00b2**，正文 R = (n \u2212 m)，\u03b8\u2082 \u2212 \u03b8\u2081。\n\n"
                    "| 项目 | 值 |\n|---|---|\n| 系数 | \u22122.18\u00b7T_j |\n\n"
                    "行内 `变量 \u03b1 10\u207b\u2076`\n\n```text\nvalue = a \u2212 b  # 中文注释 10\u207b\u2076\n```\n")
        target, _ = self.export(markdown, "docx")
        exporting = self.shipped("mdreader.export-docx")
        files = {entry[0]: (entry[1], entry[2]) for entry in
                 (exporting.BODY_FONT, exporting.CODE_FONT) + exporting.SYMBOL_FONTS}
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        checked, switched = 0, 0
        for run in re.findall(r"<w:r>(.*?)</w:r>", xml, re.S):
            text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", run, re.S))
            if not text:
                continue
            found = re.search(r'w:ascii="([^"]+)"', run)
            family = found.group(1) if found else exporting.BODY_FONT[0]
            if found:
                switched += 1
            self.assertIn(family, files, "run 指定了计划外的字体：%s" % family)
            bold = re.search(r"<w:b(?:\s+w:val=\"(?:1|true)\")?\s*/>", run) is not None
            normal_file, bold_file = files[family]
            cmap = self.reference_cmap(os.path.join(exporting.FONT_DIR, bold_file if bold else normal_file))
            for char in text:
                if ord(char) < 0x20:
                    continue
                self.assertIn(ord(char), cmap, "%s 里没有 U+%04X（字体 %s）" % (text[:12], ord(char), family))
                checked += 1
        self.assertGreater(checked, 20)
        self.assertGreater(switched, 0, "缺字形的字符应当至少切出一个回退 run")

    def test_characters_without_any_glyph_are_reported(self):
        """连回退字体也没有的字符要报出来，不能悄悄留白。"""
        markdown = "# 标题\n\n正文里有 \ue05f 这个任何字体都没有的字符，其他内容照常导出。\n"
        target, result = self.export(markdown, "pdf")
        self.assertTrue(target.is_file())
        self.assertTrue(result.get("warnings"), result)
        self.assertIn("\ue05f", result["warnings"][0])


class FormulaExportTests(unittest.TestCase):
    """F07/F08：公式在导出稿里以图片嵌入，并如实报告“不可编辑/不可检索”。"""

    MARKDOWN = ("# 公式导出\n\n质能关系 $E = mc^{2}$，独立公式：\n\n"
                "$$\n\\frac{a}{b} = \\sqrt[3]{x + 1}\n$$\n\n"
                "画不出来的 $\\foo{x}$ 保持原文。\n")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-formula-export-")
        self.addCleanup(self.temp.cleanup)
        self.ws = core.Workspace(str(Path(self.temp.name, "workspace")))
        self.addCleanup(self.ws.plugins.shutdown)
        self.api = core.Api(self.ws, str(ROOT / "webui"))
        for package in sorted((ROOT / "plugins/packages").glob("*.zip")):
            self.ws.plugins.install(str(package))
            self.ws.plugins.enable(manifest_id(package))

    def export(self, markdown, ext):
        doc = Path(self.temp.name, "公式-%s.md" % ext)
        doc.write_text(markdown, encoding="utf-8")
        info = self.api.loose.open_path(str(doc))
        command = next(c for c in self.ws.plugins.commands() if c.get("extension") == ext)
        target = Path(self.temp.name, "公式结果." + ext)
        result = self.api.post("/api/plugins/export", {"command": command["command"], "doc": str(doc),
            "revision": info["revision"], "markdown": markdown, "source": "buffer",
            "dest": str(target), "wait": 120})
        self.assertEqual(result.get("path"), str(target), result)
        if result.get("needs_confirm"):
            # 缺字体之类的可降级提示要先确认（F06），确认后再导一次
            result = self.api.post("/api/plugins/export", {"command": command["command"],
                "doc": str(doc), "revision": info["revision"], "markdown": markdown,
                "source": "buffer", "dest": str(target), "confirm": True, "wait": 120})
            self.assertEqual(result.get("path"), str(target), result)
        self.assertTrue(target.is_file(), result)
        return target, result

    def pdf_text(self, target):
        try:
            import pypdfium2 as pdfium
        except ImportError:                      # pragma: no cover - 环境缺依赖时跳过
            self.skipTest("没有 pypdfium2，跳过文本层核对")
        document = pdfium.PdfDocument(str(target))
        try:
            return "".join(document[index].get_textpage().get_text_range()
                           for index in range(len(document)))
        finally:
            document.close()

    def test_pdf_embeds_the_formula_as_an_image_and_says_it_is_not_searchable(self):
        target, result = self.export(self.MARKDOWN, "pdf")
        data = target.read_bytes()
        self.assertIn(b"/Subtype /Image", data)
        self.assertTrue(any("公式" in message and "不可检索" in message
                            for message in result["warnings"]), result["warnings"])
        text = self.pdf_text(target)
        self.assertIn("质能关系", text, "正文照常可检索")
        self.assertNotIn("mc", text.replace(" ", ""), "公式在文本层里没有（图片降级）")
        self.assertIn("foo{x}", text, "画不出来的公式保留原始写法，不能被吞掉")

    def test_docx_embeds_the_formula_with_the_tex_as_alternative_text(self):
        target, result = self.export(self.MARKDOWN, "docx")
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
            media = [name for name in archive.namelist() if name.startswith("word/media/")]
        self.assertGreaterEqual(len(media), 2, "两个公式各自嵌一张图：%s" % media)
        self.assertIn('descr="公式：E = mc^{2}"', xml, "原始写法要留在图片的替代文字里")
        self.assertIn("foo{x}", xml, "画不出来的公式保持文本")
        self.assertTrue(any("图片" in message and "不可编辑" in message
                            for message in result["warnings"]), result["warnings"])

    def test_a_document_without_formulas_reports_no_formula_warning(self):
        _target, result = self.export("# 只有正文\n\n没有公式。\n", "pdf")
        self.assertEqual(result["warnings"], [], result["warnings"])

    def test_an_unclosed_dollar_amount_stays_text(self):
        markdown = "# 价格\n\n一共 $5 与 $6 元。\n"
        target, result = self.export(markdown, "docx")
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("$5 与 $6 元", xml)
        self.assertEqual(result["warnings"], [], "金额文本不该被当成公式而报降级")

    def test_a_formula_with_markdown_escapes_still_becomes_an_image(self):
        """A01 发现：``\\,`` 被 CommonMark 反转义成 ``,``，查表落空，公式静默留在正文里。"""
        markdown = "# 细空格\n\n关系式 $\\int_{0}^{1} f(t)\\,dt = 1$ 之后还有文字。\n"
        target, _result = self.export(markdown, "docx")
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("w:drawing", xml, "含 \\, 的公式也要以图片嵌入")
        visible = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
        self.assertNotIn("\\int", visible, "公式已转成图片，正文里不该还留着源码")
        self.assertIn("之后还有文字", visible, "公式前后的正文都要保留")
        pdf_target, _result = self.export(markdown, "pdf")
        self.assertIn(b"/Subtype /Image", pdf_target.read_bytes())
        self.assertNotIn("\\int", self.pdf_text(pdf_target))
        self.assertIn("之后还有文字", self.pdf_text(pdf_target))

    PIECE = ("\\sum_{i=1}^{n} a_{i} x_{i}^{2} + \\sum_{j=1}^{m} b_{j} y_{j}^{2} = "
             "\\int_{0}^{1} f(t)\\,dt + \\frac{\\alpha+\\beta}{\\gamma+\\delta}")

    def wide_markdown(self):
        """一行里放三段求和：渲染出来约 718pt，比 A4 正文宽度（499pt）宽得多。"""
        body = " + ".join([self.PIECE] * 3)
        return "# 宽公式\n\n一行内引用 $%s$ 结束。\n" % body

    def pdf_image_bounds(self, target):
        try:
            import pypdfium2 as pdfium
        except ImportError:                      # pragma: no cover - 环境缺依赖时跳过
            self.skipTest("没有 pypdfium2，跳过页面几何核对")
        document = pdfium.PdfDocument(str(target))
        try:
            size = document[0].get_size()
            boxes = []
            for index in range(len(document)):
                page = document[index]
                boxes += [(index + 1, obj.get_bounds()) for obj in page.get_objects()
                          if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE]
            return size, boxes
        finally:
            document.close()

    def test_a_too_wide_formula_is_scaled_into_the_margins_instead_of_failing(self):
        """A01 发现：过宽公式压出版心，Word 超宽、PDF 直接导不出来（ReportLab 报错）。"""
        markdown = self.wide_markdown()
        target, result = self.export(markdown, "pdf")
        self.assertTrue(any("缩小" in message for message in result["warnings"]),
                        "缩小过宽公式必须给出可见提示：%s" % result["warnings"])
        (page_width, _height), boxes = self.pdf_image_bounds(target)
        self.assertTrue(boxes, "公式应当以图片嵌入")
        for page, (left, _bottom, right, _top) in boxes:
            self.assertLessEqual(right, page_width - 48 + 1,
                                 "第 %d 页公式越出右边距：%r" % (page, (left, right)))
            self.assertGreaterEqual(left, 48 - 1, "第 %d 页公式越出左边距" % page)
        docx_target, docx_result = self.export(markdown, "docx")
        self.assertTrue(any("缩小" in message for message in docx_result["warnings"]),
                        docx_result["warnings"])
        with zipfile.ZipFile(docx_target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        widths = [int(value) / 12700 for value in re.findall(r'<wp:extent cx="(\d+)"', xml)]
        self.assertTrue(widths, "Word 里应当有公式图片")
        for value in widths:
            self.assertLessEqual(value, 6.9 * 72 + 1, "Word 里的公式超出正文宽度：%s" % value)

    def test_the_export_does_not_change_the_source_document(self):
        doc = Path(self.temp.name, "原样.md")
        doc.write_text(self.MARKDOWN, encoding="utf-8")
        self.api.loose.open_path(str(doc))
        command = next(c for c in self.ws.plugins.commands() if c.get("extension") == "pdf")
        self.api.post("/api/plugins/export", {"command": command["command"], "doc": str(doc),
            "markdown": self.MARKDOWN, "source": "buffer",
            "dest": str(Path(self.temp.name, "原样.pdf")), "wait": 120})
        self.assertEqual(doc.read_text(encoding="utf-8"), self.MARKDOWN)


class WideTableExportTests(unittest.TestCase):
    """F04：宽表导出不能裁掉最后几列（换行或压缩列宽，但不能丢内容）。"""

    COLUMNS = 12

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-wide-")
        self.addCleanup(self.temp.cleanup)
        self.ws = core.Workspace(str(Path(self.temp.name, "workspace")))
        self.addCleanup(self.ws.plugins.shutdown)
        self.api = core.Api(self.ws, str(ROOT / "webui"))
        for package in sorted((ROOT / "plugins/packages").glob("*.zip")):
            self.ws.plugins.install(str(package))
            self.ws.plugins.enable(manifest_id(package))

    def wide_markdown(self, fill="内容"):
        header = "| " + " | ".join("列%d" % n for n in range(1, self.COLUMNS + 1)) + " |"
        separator = "| " + " | ".join(["---"] * self.COLUMNS) + " |"
        row = "| " + " | ".join("%s%d" % (fill, n) for n in range(1, self.COLUMNS + 1)) + " |"
        return "# 宽表\n\n%s\n%s\n%s\n" % (header, separator, row)

    def export(self, markdown, ext):
        doc = Path(self.temp.name, "宽表-%s.md" % ext)
        doc.write_text(markdown, encoding="utf-8")
        info = self.api.loose.open_path(str(doc))
        command = next(c for c in self.ws.plugins.commands() if c.get("extension") == ext)
        target = Path(self.temp.name, "宽表结果." + ext)
        result = self.api.post("/api/plugins/export", {"command": command["command"], "doc": str(doc),
            "revision": info["revision"], "markdown": markdown, "dest": str(target), "wait": 90})
        self.assertEqual(result.get("path"), str(target), result)
        return target, result

    def pdf_text(self, target):
        try:
            import pypdfium2 as pdfium
        except ImportError:                      # pragma: no cover - 环境缺依赖时跳过
            self.skipTest("没有 pypdfium2，跳过文本层核对")
        document = pdfium.PdfDocument(str(target))
        try:
            return "".join(document[index].get_textpage().get_text_range()
                           for index in range(len(document)))
        finally:
            document.close()

    def test_pdf_keeps_every_column_of_a_wide_table(self):
        target, _result = self.export(self.wide_markdown(), "pdf")
        # 文本层会按视觉换行切字（单元格里的字被折行），核对前把空白去掉
        text = re.sub(r"\s+", "", self.pdf_text(target))
        for n in range(1, self.COLUMNS + 1):
            self.assertIn("列%d" % n, text, "第 %d 列被裁掉了" % n)
            self.assertIn("内容%d" % n, text, "第 %d 列的数据被裁掉了" % n)

    def test_pdf_wraps_a_long_cell_instead_of_cutting_it(self):
        long_cell = "很长的单元格内容" * 20
        markdown = "# 长单元格\n\n| 甲 | 乙 |\n|---|---|\n| %s | 尾标记 |\n" % long_cell
        target, _result = self.export(markdown, "pdf")
        text = re.sub(r"\s+", "", self.pdf_text(target))
        self.assertIn("尾标记", text)
        self.assertIn(long_cell[-6:], text, "长单元格末尾被截断")

    def test_docx_keeps_every_column_of_a_wide_table(self):
        target, _result = self.export(self.wide_markdown(), "docx")
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        table = re.search(r"<w:tbl>.*?</w:tbl>", xml, re.S)
        self.assertIsNotNone(table, "导出稿里没有 Word 表格")
        rows = re.findall(r"<w:tr\b.*?</w:tr>", table.group(0), re.S)
        self.assertEqual(len(rows), 2, "表头行与数据行都要在")
        for row in rows:
            self.assertEqual(len(re.findall(r"<w:tc>", row)), self.COLUMNS,
                             "一行的单元格数不对，说明有列被丢掉了")
        for n in range(1, self.COLUMNS + 1):
            self.assertIn("列%d" % n, xml)

    def test_extra_cells_beyond_the_header_follow_the_reader_rules(self):
        """多出来的单元格：阅读端与导出端用同一个 GFM 解释，不各说各话。"""
        from mdreader import render
        markdown = "| 甲 | 乙 |\n|---|---|\n| 1 | 2 | 3 |\n"
        fragment, _meta, _engine = render.render_markdown(markdown)
        body_row = fragment.split("</thead>", 1)[-1]
        self.assertEqual(body_row.count("<td>"), 2, "阅读端按表头列数裁掉多余的单元格")
        target, _result = self.export(markdown, "docx")
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        row = re.findall(r"<w:tr\b.*?</w:tr>", xml, re.S)[-1]
        self.assertEqual(len(re.findall(r"<w:tc>", row)), 2, "导出端与阅读端裁列规则不一致")


