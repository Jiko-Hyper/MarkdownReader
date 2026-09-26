# -*- coding: utf-8 -*-
"""F03 的第二、三步：剪贴板截图与拖入图片（两端走同一条插入通道）。

    python -m unittest tests.test_image_insert_ui

两层分开验：

* 剪贴板读取（``mdreader.media``）：``CF_DIB`` 用标准库解成 PNG——没装 Pillow 的
  机器也能贴截图；剪贴板里不是图片时返回空串而不是抛异常；调用方能不能删那份文件
  由 ``owns_clipboard_file`` 回答（用户“复制的图片文件”绝不能删）。
* 窗口接线（``mdreader.winui``）：图片只从**显式**入口进来（菜单 / ``Ctrl+Shift+V`` /
  拖入），插件没启用或文档没保存时只给提示、不改正文；一次只插一张；我们自建的临时
  文件用完就删，用户的原件不动。
"""
from __future__ import annotations

import os
import struct
import tempfile
import types
import unittest
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image, ImageGrab

from mdreader import media, media_ui, winui
from tests import test_native_tabs


def png_bytes(size=(4, 3), color="#3366cc") -> bytes:
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return stream.getvalue()


def dib(width, height, rows, bits=24, top_down=False) -> bytes:
    """造一份 Windows ``CF_DIB``：**没有** 14 字节 BITMAPFILEHEADER。"""
    stride = ((width * bits + 31) // 32) * 4
    header = struct.pack("<IiiHHIIiiII", 40, width, -height if top_down else height,
                         1, bits, 0, stride * height, 0, 0, 0, 0)
    ordered = list(rows) if top_down else list(reversed(rows))
    pixels = bytearray()
    for row in ordered:
        line = bytearray()
        for red, green, blue in row:
            line += bytes((blue, green, red)) + (b"\xff" if bits == 32 else b"")
        pixels += line + b"\x00" * (stride - len(line))
    return header + bytes(pixels)


class ClipboardImageTests(unittest.TestCase):
    """剪贴板 → PNG：标准库也要能干活。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-clip-")
        self.addCleanup(self.temp.cleanup)

    def test_cf_dib_is_decoded_into_a_real_png(self):
        rows = [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 255)]]
        for bits in (24, 32):
            for top_down in (False, True):
                with self.subTest(bits=bits, top_down=top_down):
                    data = media._clipboard_png_from_dib(dib(2, 2, rows, bits, top_down))
                    self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"), "必须是真 PNG")
                    with Image.open(BytesIO(data)) as image:
                        self.assertEqual(image.size, (2, 2))
                        # 第 0 行是图像最上面那一行（自下而上的 DIB 要翻过来）
                        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
                        self.assertEqual(image.getpixel((1, 0)), (0, 255, 0))
                        self.assertEqual(image.getpixel((0, 1)), (0, 0, 255))
                        self.assertEqual(image.getpixel((1, 1)), (255, 255, 255))

    def test_broken_or_unsupported_dib_is_refused_not_guessed(self):
        good = dib(2, 2, [[(1, 2, 3), (4, 5, 6)], [(7, 8, 9), (1, 1, 1)]])
        self.assertIsNone(media._clipboard_png_from_dib(b""), "空数据")
        self.assertIsNone(media._clipboard_png_from_dib(b"\x00" * 8), "连头都不够")
        self.assertIsNone(media._clipboard_png_from_dib(good[:19]), "半个头")
        self.assertIsNone(media._clipboard_png_from_dib(good[:40]), "只有头没有像素")
        # 调色板位图、压缩位图、平面数不对：不猜，直接拒绝
        self.assertIsNone(media._clipboard_png_from_dib(
            dib(2, 2, [[(1, 2, 3), (4, 5, 6)], [(7, 8, 9), (1, 1, 1)]], bits=8)))
        compressed = bytearray(good)
        compressed[16:20] = struct.pack("<I", 1)
        self.assertIsNone(media._clipboard_png_from_dib(bytes(compressed)))
        planes = bytearray(good)
        planes[12:14] = struct.pack("<H", 2)
        self.assertIsNone(media._clipboard_png_from_dib(bytes(planes)))
        # 尺寸离谱（解码炸弹）也拒绝
        bomb = bytearray(good)
        bomb[4:8] = struct.pack("<i", 20000)
        bomb[8:12] = struct.pack("<i", 20000)
        self.assertIsNone(media._clipboard_png_from_dib(bytes(bomb)))

    def test_an_empty_clipboard_is_an_empty_string_not_an_exception(self):
        with mock.patch.object(ImageGrab, "grabclipboard", return_value=None), \
             mock.patch.object(media, "_clipboard_dib", return_value=None):
            self.assertEqual(media.clipboard_image(), "")

    def test_pillow_bitmap_fast_path_writes_into_the_given_folder(self):
        with mock.patch.object(ImageGrab, "grabclipboard",
                               return_value=Image.new("RGB", (3, 2), "#123456")):
            path = media.clipboard_image(directory=self.temp.name)
        self.assertTrue(path.startswith(self.temp.name), path)
        self.assertTrue(media.owns_clipboard_file(path))
        with Image.open(path) as image:
            self.assertEqual(image.size, (3, 2))

    def test_without_pillow_the_dib_fallback_still_produces_a_png(self):
        """没装 Pillow（``from PIL import …`` 失败）时走自写编码器。"""
        stub = types.ModuleType("PIL")
        grab = types.ModuleType("PIL.ImageGrab")

        def boom():
            raise ImportError("没有 Pillow")

        grab.grabclipboard = boom
        stub.ImageGrab = grab
        rows = [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 255)]]
        with mock.patch.dict("sys.modules", {"PIL": stub, "PIL.ImageGrab": grab}), \
             mock.patch.object(media, "_clipboard_dib",
                               return_value=dib(2, 2, rows, bits=24)):
            path = media.clipboard_image(directory=self.temp.name)
        self.assertTrue(path.startswith(self.temp.name), path)
        with open(path, "rb") as stream:
            self.assertTrue(stream.read(8).startswith(b"\x89PNG"))
        with Image.open(path) as image:
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))

    def test_a_copied_image_file_is_returned_and_never_treated_as_ours(self):
        original = Path(self.temp.name, "用户的照片.png")
        original.write_bytes(png_bytes())
        with mock.patch.object(ImageGrab, "grabclipboard", return_value=[str(original)]):
            path = media.clipboard_image()
        self.assertEqual(path, str(original), "复制的图片文件用原件，不另存一份")
        self.assertFalse(media.owns_clipboard_file(path), "用户原件不能被当成临时文件")

    def test_owns_clipboard_file_only_trusts_our_own_temp_files(self):
        target = media._clipboard_target("", ".png")
        try:
            self.assertTrue(media.owns_clipboard_file(target))
            self.assertFalse(media.owns_clipboard_file(""))
            self.assertFalse(media.owns_clipboard_file(str(Path(self.temp.name, "clipart.png"))))
            with mock.patch.object(media.tempfile, "gettempdir",
                                   return_value=str(Path(self.temp.name, "别处"))):
                self.assertFalse(media.owns_clipboard_file(target), "不在临时目录里就不算我们的")
        finally:
            if os.path.exists(target):
                os.remove(target)

    def test_image_extensions_cover_the_declared_formats(self):
        for name in ("a.PNG", "b.jpg", "c.JPEG", "d.gif", "e.webp", "f.bmp", "g.avif"):
            self.assertTrue(media.is_image_path(name), name)
        for name in ("a.md", "b.txt", "c.svg", "没有扩展名", ""):
            self.assertFalse(media.is_image_path(name), name)


class FakeBridge:
    """顶掉真正的插件宿主：只记下调用，返回一份事先写好的结果。"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def post(self, url, payload):
        self.calls.append((url, payload))
        return self.result


class ImageInsertUiTests(unittest.TestCase):
    setUp = test_native_tabs.NativeTabsTests.setUp
    destroy_window = staticmethod(test_native_tabs.NativeTabsTests.destroy_window)

    COMMAND = {"plugin": "mdreader.image-insert", "command": "mdreader.image-insert.insert"}

    @contextmanager
    def notice(self):
        seen = []
        with mock.patch.object(winui.MarkdownWindow, "notice",
                               lambda _self, message, error=False: seen.append((message, error))):
            yield seen

    def open_document(self):
        self.win.open_local_files([self.files[0]])
        return self.win

    def bridge(self, markdown="![截图](assets/clipboard.png)\n"):
        return FakeBridge({"markdown": markdown, "assets": [],
                           "assets_dir": str(Path(self.temp.name, "assets"))})

    # -- 剪贴板截图 -------------------------------------------------------
    def test_clipboard_paste_needs_the_plugin_and_reads_nothing_when_disabled(self):
        self.open_document()
        with mock.patch.object(self.win, "plugin_commands", return_value=[]), \
             mock.patch.object(media, "clipboard_image") as grab, self.notice() as seen:
            self.win.insert_clipboard_image()
        self.assertFalse(grab.called, "插件没启用就不该去读剪贴板")
        self.assertTrue(any("图片插入插件没有启用" in message for message, _e in seen), seen)

    def test_clipboard_paste_needs_a_saved_document(self):
        before = self.win.source
        with mock.patch.object(self.win, "plugin_commands", return_value=[self.COMMAND]), \
             mock.patch.object(media, "clipboard_image") as grab, self.notice() as seen:
            self.win.insert_clipboard_image()
        self.assertFalse(grab.called, "没有可插入的文档时不该去读剪贴板")
        self.assertTrue(any("请先保存当前文档" in message for message, _e in seen), seen)
        self.assertEqual(self.win.source, before)

    def test_an_empty_clipboard_says_so(self):
        self.open_document()
        before = self.win.source
        # 宽度对话框与插件宿主都顶掉：这样即便“空剪贴板”的判断被改坏，
        # 结果也是一条失败断言，而不是弹一个真对话框把测试挂住。
        with mock.patch.object(self.win, "plugin_commands", return_value=[self.COMMAND]), \
             mock.patch.object(media, "clipboard_image", return_value=""), \
             mock.patch.object(media_ui, "choose_width", return_value=720), \
             mock.patch.object(self.win, "plugin_bridge", return_value=self.bridge()), \
             self.notice() as seen:
            self.win.insert_clipboard_image()
        self.assertTrue(any("剪贴板里没有图片" in message for message, _e in seen), seen)
        self.assertEqual(self.win.source, before, "空剪贴板不得改正文")

    def test_clipboard_image_is_inserted_and_our_temp_copy_is_removed(self):
        self.open_document()
        shot = media._clipboard_target("", ".png")
        with open(shot, "wb") as stream:
            stream.write(png_bytes())
        bridge = self.bridge("![截图](assets/clipboard.png)\n")
        with mock.patch.object(self.win, "plugin_commands", return_value=[self.COMMAND]), \
             mock.patch.object(media, "clipboard_image", return_value=shot), \
             mock.patch.object(media_ui, "choose_width", return_value=480), \
             mock.patch.object(self.win, "plugin_bridge", return_value=bridge):
            self.win.insert_clipboard_image()
        url, payload = bridge.calls[0]
        self.assertEqual(url, "/api/plugins/insert")
        self.assertEqual(payload["path"], shot, "插件拿到的是落盘后的文件路径")
        self.assertEqual(payload["options"], {"width": 480})
        self.assertIn("![截图](assets/clipboard.png)", self.win.source)
        self.assertFalse(os.path.exists(shot), "本次新建的临时截图用完要删掉")

    def test_a_copied_image_file_is_left_alone(self):
        self.open_document()
        original = Path(self.temp.name, "用户的照片.png")
        original.write_bytes(png_bytes())
        bridge = self.bridge("![照片](assets/用户的照片.png)\n")
        with mock.patch.object(self.win, "plugin_commands", return_value=[self.COMMAND]), \
             mock.patch.object(media, "clipboard_image", return_value=str(original)), \
             mock.patch.object(media_ui, "choose_width", return_value=720), \
             mock.patch.object(self.win, "plugin_bridge", return_value=bridge):
            self.win.insert_clipboard_image()
        self.assertTrue(original.is_file(), "用户复制的原图一个字节都不能动")
        self.assertEqual(bridge.calls[0][1]["path"], str(original))

    def test_cancelling_the_width_dialog_inserts_nothing(self):
        self.open_document()
        before = self.win.source
        shot = media._clipboard_target("", ".png")
        with open(shot, "wb") as stream:
            stream.write(png_bytes())
        with mock.patch.object(self.win, "plugin_commands", return_value=[self.COMMAND]), \
             mock.patch.object(media, "clipboard_image", return_value=shot), \
             mock.patch.object(media_ui, "choose_width", return_value=None), \
             mock.patch.object(self.win, "plugin_bridge",
                               side_effect=AssertionError("取消后不该调用插件")):
            self.win.insert_clipboard_image()
        self.assertEqual(self.win.source, before)
        self.assertFalse(os.path.exists(shot), "取消也要清掉自己的临时文件")

    # -- 拖入图片 ---------------------------------------------------------
    def test_dropped_image_goes_to_the_insert_channel(self):
        image = str(Path(self.temp.name, "照片.PNG"))
        document = self.files[0]
        folder = Path(self.temp.name, "素材")
        folder.mkdir()
        with mock.patch.object(self.win, "insert_dropped_images") as images, \
             mock.patch.object(self.win, "open_local_files") as opened, self.notice() as seen:
            self.win.on_files_dropped([image, document, str(folder)])
        self.assertEqual(images.call_args[0][0], [image], "图片走插入通道，不当文档打开")
        self.assertEqual(opened.call_args[0][0], [document], ".md 仍然临时打开")
        self.assertTrue(any("文件夹" in message for message, _e in seen), seen)

    def test_a_drop_without_images_is_untouched(self):
        document = self.files[0]
        with mock.patch.object(self.win, "insert_dropped_images") as images, \
             mock.patch.object(self.win, "open_local_files") as opened:
            self.win.on_files_dropped([document])
        self.assertFalse(images.called)
        self.assertEqual(opened.call_args[0][0], [document])

    def test_dropped_image_needs_the_plugin(self):
        self.open_document()
        before = self.win.source
        with mock.patch.object(self.win, "plugin_commands", return_value=[]), self.notice() as seen:
            self.assertFalse(self.win.insert_dropped_images(["a.png"]))
        self.assertTrue(any("图片插入插件" in message for message, _e in seen), seen)
        self.assertEqual(self.win.source, before)

    def test_dropped_image_needs_a_saved_document(self):
        with mock.patch.object(self.win, "plugin_commands", return_value=[self.COMMAND]), \
             self.notice() as seen:
            self.assertFalse(self.win.insert_dropped_images(["a.png"]))
        self.assertTrue(any("请先打开并保存" in message for message, _e in seen), seen)

    def test_only_one_image_per_drop_and_it_says_so(self):
        self.open_document()
        before = self.win.source
        with mock.patch.object(self.win, "plugin_commands", return_value=[self.COMMAND]), \
             mock.patch.object(self.win, "insert_image_file", return_value=True) as insert, \
             self.notice() as seen:
            self.assertTrue(self.win.insert_dropped_images(["a.png", "b.png"]))
        self.assertEqual(insert.call_count, 1, "一次只插一张")
        self.assertTrue(any("其余 1 张" in message for message, _e in seen), seen)
        self.assertEqual(self.win.source, before)

    # -- 入口 -------------------------------------------------------------
    def test_paste_shortcut_is_advertised_and_bound(self):
        table = dict(winui.SHORTCUTS)
        self.assertIn("Ctrl+Shift+V", table)
        self.assertIn("拖入图片", table["拖入窗口"])
        # Tk 里大写 V 与小写 v 是两个 keysym：两个都绑上，系统报哪个都能用
        self.assertTrue(self.win.root.bind("<Control-Shift-V>"), "缺少 Ctrl+Shift+V 绑定")
        self.assertTrue(self.win.root.bind("<Control-Shift-v>"), "缺少 Ctrl+Shift+v 绑定")

    def test_more_menu_exposes_the_clipboard_entry(self):
        menus = []
        real = self.win._tk.Menu

        def factory(*args, **kwargs):
            menu = real(*args, **kwargs)
            menus.append(menu)
            return menu

        with mock.patch.object(self.win._tk, "Menu", factory), \
             mock.patch.object(real, "tk_popup", lambda *a, **k: None):
            self.win.show_more_menu()
        labels = []
        for index in range(menus[0].index("end") or 0):
            if menus[0].type(index) == "separator":      # 分隔线没有 -label 选项
                continue
            labels.append(menus[0].entrycget(index, "label"))
        self.assertTrue(any("粘贴剪贴板图片" in label for label in labels), labels)
        self.assertTrue(any("插入图片" in label for label in labels), labels)


if __name__ == "__main__":
    unittest.main(verbosity=2)
