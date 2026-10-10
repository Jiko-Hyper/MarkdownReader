"""OCR failure handling and Markdown reconstruction, in addition to the 20-image corpus."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mdreader import core
from mdreader.conversion import convert_file

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/dependencies"))
spec = importlib.util.spec_from_file_location("ocr_under_test", ROOT / "plugins/official/document-convert/image_ocr.py")
ocr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ocr)


def line(text, y=0, height=30):
    return {"text": text, "words": [{"text": text, "x": 0, "y": y, "width": 200, "height": height}]}


class MarkdownReconstructionTests(unittest.TestCase):
    def test_literal_markup_is_not_an_active_image_or_html(self):
        text = ocr.markdown_from_lines([line('<script>alert(1)</script> ![x](https://example.com/x) *bold*')])
        from markdown_it import MarkdownIt
        rendered = MarkdownIt().render(text)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<em>", rendered)

    def test_decimal_is_not_reinterpreted_as_a_list(self):
        self.assertEqual(ocr.markdown_from_lines([line("3.14 is pi")]), "3.14 is pi\n")

    def test_cjk_spaces_and_full_width_list_marker(self):
        self.assertEqual(ocr.markdown_from_lines([line("2 ． 打 开 文 档")]), "2. 打开文档\n")

    def test_bullet_heading_and_paragraph_gap(self):
        result = ocr.markdown_from_lines([line("Title", 0, 60), line("• First", 80), line("• Second", 125), line("Paragraph", 220)])
        self.assertTrue(result.startswith("## Title\n\n- First"))
        self.assertIn("- Second\n\nParagraph", result)

    def test_empty_lines_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "未识别到文字"):
            ocr.markdown_from_lines([])

    def test_timeout_has_actionable_message(self):
        with tempfile.TemporaryDirectory() as folder:
            conversion = SimpleNamespace(source=Path(folder)/"source.png", task=SimpleNamespace(
                path=lambda name: str(Path(folder)/name), progress=lambda *a: None))
            with patch.object(ocr, "prepare_image"), patch.object(ocr.subprocess, "run", side_effect=subprocess.TimeoutExpired("ocr", 90)):
                with self.assertRaisesRegex(ValueError, "超时"):
                    ocr.convert(conversion)


@unittest.skipUnless(os.name == "nt", "Windows OCR integration")
class ImageFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.ws = core.Workspace(str(cls.folder / "workspace"))
        cls.ws.plugins.install(str(ROOT / "plugins/packages/mdreader.document-convert-1.4.0.zip"))
        cls.ws.plugins.enable("mdreader.document-convert")
        cls.command = next(c["command"] for c in cls.ws.plugins.commands() if c["format"] == "image-md")

    @classmethod
    def tearDownClass(cls):
        cls.ws.plugins.shutdown()
        cls.temp.cleanup()

    def assert_rejected(self, source, message):
        output = source.with_suffix(".md")
        original = source.read_bytes()
        with self.assertRaisesRegex(Exception, message):
            convert_file(self.ws.plugins, self.command, str(source), str(output))
        self.assertFalse(output.exists())
        self.assertEqual(source.read_bytes(), original)

    def test_blank_image(self):
        from PIL import Image
        source = self.folder / "blank.png"
        Image.new("RGB", (200, 200), "white").save(source)
        self.assert_rejected(source, "未识别到文字")

    def test_corrupt_image(self):
        source = self.folder / "corrupt.png"
        source.write_bytes(b"not an image")
        self.assert_rejected(source, "无法读取")

    def test_multiple_pages_are_not_silently_dropped(self):
        from PIL import Image
        source = self.folder / "multipage.tiff"
        image = Image.new("RGB", (100, 100), "white")
        image.save(source, save_all=True, append_images=[image])
        self.assert_rejected(source, "多页")

    def test_large_dimensions(self):
        from PIL import Image
        source = self.folder / "oversized.png"
        Image.new("L", (10001, 2), 255).save(source)
        self.assert_rejected(source, "图片过大")

    def test_wrong_extension(self):
        source = self.folder / "file.svg"
        source.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.assert_rejected(source, "此命令只支持")


if __name__ == "__main__":
    unittest.main()
