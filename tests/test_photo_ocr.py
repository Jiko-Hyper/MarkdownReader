"""Photo OCR reconstruction, failure handling, and network-free real inference."""
import importlib.util
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"plugins/official/document-convert"))
spec = importlib.util.spec_from_file_location("photo_under_test", ROOT/"plugins/official/document-convert/photo_ocr.py")
photo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(photo)
RUNTIME = Path(sys.executable).parent/"photo_ocr_runtime"
if not RUNTIME.is_dir():
    RUNTIME = ROOT/"plugins/photo-ocr-dependencies"


def row(text, x, y, w=400, h=25, tilt=0):
    return {"text": text, "score": .99,
            "box": [[x,y], [x+w,y+tilt], [x+w,y+tilt+h], [x,y+h]]}


class PhotoLayoutTests(unittest.TestCase):
    def test_wrapped_chinese_list_is_one_item(self):
        output = photo.page_markdown([row("1、身体条件", 60, 0), row("继续说明。", 40, 32), row("2、下一项", 60, 64)])
        self.assertEqual(output, "1. 身体条件继续说明。\n\n2. 下一项\n")

    def test_chinese_numbered_paragraphs_stay_separate(self):
        output = photo.page_markdown([row("一、说明。", 40, 0), row("二、继续。", 40, 32)])
        self.assertEqual(output, "一、说明。\n\n二、继续。\n")

    def test_latin_wrap_keeps_word_boundary(self):
        output = photo.page_markdown([row("This is a", 40, 0), row("document.", 40, 32)])
        self.assertEqual(output, "This is a document.\n")

    def test_tilt_is_not_mistaken_for_large_font(self):
        output = photo.page_markdown([row("Normal text", 40, 0, tilt=20), row("More text", 40, 60, tilt=20)])
        self.assertNotIn("#", output)

    def test_image_html_and_markdown_are_literal(self):
        output = photo.page_markdown([row("<script> ![x](bad) *bold*", 40, 0)])
        self.assertIn(r"\<script\>", output)
        self.assertIn(r"!\[x\]", output)

    def test_decimal_is_not_numbered_list(self):
        self.assertEqual(photo.page_markdown([row("3.14 is pi", 40, 0)]), "3.14 is pi\n")

    def test_empty_page_rejected(self):
        with self.assertRaisesRegex(ValueError, "未识别到文字"):
            photo.page_markdown([])


@unittest.skipUnless(RUNTIME.is_dir(), "Offline photo OCR runtime not installed")
class PhotoEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(RUNTIME))
        cls.network_guard = patch.object(socket.socket, "connect", side_effect=AssertionError("Network is forbidden"))
        cls.network_guard.start()
        cls.addClassCleanup(cls.network_guard.stop)
        cls.engine = photo.load_engine()

    def test_real_recognition_without_network(self):
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        import cv2
        image = Image.new("RGB", (720,160), "white")
        ImageDraw.Draw(image).text((40,40), "Local document 2026", font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",36), fill="black")
        rows = photo.recognize(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR), self.engine, lambda *args: None)
        self.assertEqual("".join(r["text"] for r in rows).replace(" ", ""), "Localdocument2026")

    def test_blank_photo_is_not_a_successful_empty_export(self):
        import numpy as np
        with self.assertRaisesRegex(ValueError, "未识别到文字"):
            photo.recognize(np.full((200,200,3),255,dtype=np.uint8), self.engine, lambda *a: None)

    def test_multi_page_and_oversized_inputs_rejected(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/"pages.tif"
            image = Image.new("RGB", (100,100), "white")
            image.save(target, save_all=True, append_images=[image])
            with self.assertRaisesRegex(ValueError, "单张图片"):
                photo.read_photo(target)
            target = Path(folder)/"large.png"
            Image.new("L", (10001,2)).save(target)
            with self.assertRaisesRegex(ValueError, "图片过大"):
                photo.read_photo(target)

    def test_exif_rotation_is_applied(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/"rotate.jpg"
            image = Image.new("RGB", (60,100), "white")
            exif = Image.Exif()
            exif[274] = 6
            image.save(target, exif=exif)
            self.assertEqual(photo.read_photo(target).shape[:2], (60,100))


if __name__ == "__main__":
    unittest.main()
