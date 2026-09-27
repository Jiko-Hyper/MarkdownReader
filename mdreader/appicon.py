# -*- coding: utf-8 -*-
"""应用图标：紫色渐变圆角方块 + 白色 MD。

图标在运行时用 GDI+ 现画（不依赖 Pillow，也不随程序带二进制素材），
任务栏、标题栏和 Alt-Tab 图都用同一张；exe 文件自身的图标由
``assets/icon.ico`` 在打包时写入，两者取色一致（见 ``tools/make_icon.py``）。
绘制失败时安静跳过，绝不影响启动。
"""

from __future__ import annotations

import base64
import ctypes
import struct
import zlib

#: 与产品标识一致的取色：左上一档深青灰，右下一档亮紫。
GRADIENT = ((58, 66, 84), (139, 106, 235))
CORNER_RATIO = 0.235
TEXT_RATIO = 0.42

#: GDI+ 枚举
_SMOOTHING_ANTIALIAS = 4
_TEXT_RENDERING_ANTIALIAS = 4
_FONT_STYLE_BOLD = 1
_UNIT_PIXEL = 2
_PIXEL_FORMAT_ARGB = 0x0026200A
_LOCK_READ = 1

_ARGB = ctypes.c_ulong                          # GDI+ 的 ARGB 是 32 位无符号整数

_cache: "dict[int, bytes]" = {}


def _font_family() -> str:
    """粗体无衬线字体，保证 MD 的字重够。"""
    import os
    for name in ("arialbd.ttf", "segoeuib.ttf", "msyhbd.ttc"):
        if os.path.exists(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)):
            return "Arial"
    return "Arial"


def ensure_tcl_environment() -> bool:
    """让免安装版里的 Tcl/Tk 知道自己的库在哪。

    Python 的 embeddable 包不带 tkinter，Tcl 的库在运行时由 Python 解释器
    按「安装目录 + lib/tcl8.6」去找；免安装版把那两个目录放在 runtime\\tcl\\ 下，
    所以要在这里把 TCL_LIBRARY / TK_LIBRARY 明确指过去。

    只在两个变量都没设、并且确实找到 init.tcl 时才动手，避免盖掉用户环境变量。
    """
    import os
    if os.environ.get("TCL_LIBRARY") and os.environ.get("TK_LIBRARY"):
        return False
    try:
        from .core import app_dir
        root = os.path.join(app_dir(), "runtime", "tcl")
    except Exception:
        return False
    if not os.path.isdir(root):
        return False
    version = "%d.%d" % (8, 6)          # 随包运行时固定为 Tcl/Tk 8.6
    changed = False
    for variable, folder in (("TCL_LIBRARY", "tcl" + version), ("TK_LIBRARY", "tk" + version)):
        path = os.path.join(root, folder)
        if os.path.isfile(os.path.join(path, "init.tcl")) and not os.environ.get(variable):
            os.environ[variable] = path
            changed = True
    return changed


def _png(width: int, height: int, bgra: bytes) -> bytes:
    """把 BGRA 缓冲区编成带 alpha 的 PNG。"""
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)                                   # 每行的过滤器字节
        row = bgra[y * stride:(y + 1) * stride]
        for i in range(0, stride, 4):
            raw += bytes((row[i + 2], row[i + 1], row[i], row[i + 3]))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _draw(size: int) -> bytes:
    """用 GDI+ 画一张 size x size 的图标，返回 PNG 字节。"""
    from ctypes import wintypes

    class StartupInput(ctypes.Structure):
        _fields_ = [("GdiplusVersion", wintypes.UINT), ("DebugEventCallback", ctypes.c_void_p),
                    ("SuppressBackgroundThread", wintypes.BOOL),
                    ("SuppressExternalCodecs", wintypes.BOOL)]

    class BitmapData(ctypes.Structure):
        _fields_ = [("Width", wintypes.UINT), ("Height", wintypes.UINT),
                    ("Stride", ctypes.c_int), ("PixelFormat", ctypes.c_int),
                    ("Scan0", ctypes.c_void_p), ("Reserved", ctypes.c_void_p)]

    gdiplus = ctypes.WinDLL("gdiplus", use_last_error=True)
    gdiplus.GdiplusStartup.argtypes = [ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(StartupInput), ctypes.c_void_p]
    gdiplus.GdipCreateBitmapFromScan0.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                  ctypes.c_int, ctypes.c_void_p,
                                                  ctypes.POINTER(ctypes.c_void_p)]
    gdiplus.GdipGetImageGraphicsContext.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    gdiplus.GdipCreatePath.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    gdiplus.GdipAddPathArc.argtypes = [ctypes.c_void_p] + [ctypes.c_float] * 6
    gdiplus.GdipCreateLineBrush.argtypes = [
        ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), _ARGB, _ARGB, ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p)]
    gdiplus.GdipSetLineSigmaBlend.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float]
    gdiplus.GdipSetLineGammaCorrection.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdiplus.GdipRotateLineTransform.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_int]
    gdiplus.GdipSetPixelOffsetMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdiplus.GdipFillPath.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    gdiplus.GdipCreateFontFamilyFromName.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p,
                                                     ctypes.POINTER(ctypes.c_void_p)]
    gdiplus.GdipCreateFont.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_int,
                                       ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    gdiplus.GdipMeasureString.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_float), ctypes.c_void_p, ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    gdiplus.GdipCreateSolidFill.argtypes = [_ARGB, ctypes.POINTER(ctypes.c_void_p)]
    gdiplus.GdipDrawString.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int,
                                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_float),
                                       ctypes.c_void_p, ctypes.c_void_p]
    gdiplus.GdipBitmapLockBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                           ctypes.c_int, ctypes.POINTER(BitmapData)]
    gdiplus.GdipBitmapUnlockBits.argtypes = [ctypes.c_void_p, ctypes.POINTER(BitmapData)]

    def solid(rgb) -> int:                              # 0xAARRGGBB
        return 0xFF000000 | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]

    token = ctypes.c_void_p()
    startup = StartupInput(1, None, False, False)
    if gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(startup), None) != 0:
        return b""

    image = ctypes.c_void_p()
    graphics = ctypes.c_void_p()
    path = ctypes.c_void_p()
    brush = ctypes.c_void_p()
    family = ctypes.c_void_p()
    font = ctypes.c_void_p()
    white = ctypes.c_void_p()
    locked = BitmapData()
    buffer = b""
    try:
        if gdiplus.GdipCreateBitmapFromScan0(size, size, size * 4, _PIXEL_FORMAT_ARGB, None,
                                             ctypes.byref(image)) != 0:
            return b""
        if gdiplus.GdipGetImageGraphicsContext(image, ctypes.byref(graphics)) != 0:
            return b""
        gdiplus.GdipSetSmoothingMode(graphics, _SMOOTHING_ANTIALIAS)
        gdiplus.GdipSetTextRenderingHint(graphics, _TEXT_RENDERING_ANTIALIAS)
        gdiplus.GdipSetPixelOffsetMode(graphics, 4)      # HighQuality，圆角没有毛边

        radius = size * CORNER_RATIO
        diameter = radius * 2.0
        span = float(size) - 1.0
        gdiplus.GdipCreatePath(0, ctypes.byref(path))
        for x, y, start in ((0.0, 0.0, 180.0), (span - diameter, 0.0, 270.0),
                            (span - diameter, span - diameter, 0.0), (0.0, span - diameter, 90.0)):
            gdiplus.GdipAddPathArc(path, x, y, diameter, diameter, start, 90.0)
        gdiplus.GdipClosePathFigure(path)

        # 对角线性渐变：左上深青灰 -> 右下亮紫，与产品标识一致
        start_pt = (ctypes.c_float * 2)(0.0, 0.0)
        end_pt = (ctypes.c_float * 2)(span, span)
        gdiplus.GdipCreateLineBrush(start_pt, end_pt, solid(GRADIENT[0]), solid(GRADIENT[1]),
                                    0, ctypes.byref(brush))
        gdiplus.GdipSetLineGammaCorrection(brush, 1)
        gdiplus.GdipFillPath(graphics, brush, path)

        gdiplus.GdipCreateFontFamilyFromName(ctypes.c_wchar_p(_font_family()), None,
                                             ctypes.byref(family))
        gdiplus.GdipCreateFont(family, ctypes.c_float(size * TEXT_RATIO), _FONT_STYLE_BOLD,
                               _UNIT_PIXEL, ctypes.byref(font))
        box = (ctypes.c_float * 4)(0.0, 0.0, float(size), float(size))
        bounds = (ctypes.c_float * 4)(0.0, 0.0, 0.0, 0.0)
        gdiplus.GdipMeasureString(graphics, ctypes.c_wchar_p("MD"), -1, font, box, None,
                                  bounds, None, None)
        gdiplus.GdipCreateSolidFill(solid((255, 255, 255)), ctypes.byref(white))
        target = (ctypes.c_float * 4)(
            (size - (bounds[2] - bounds[0])) / 2.0 - bounds[0],
            (size - (bounds[3] - bounds[1])) / 2.0 - bounds[1] - size * 0.01,
            float(size), float(size))
        gdiplus.GdipDrawString(graphics, ctypes.c_wchar_p("MD"), -1, font, target, None, white)

        if gdiplus.GdipBitmapLockBits(image, None, _LOCK_READ, _PIXEL_FORMAT_ARGB,
                                      ctypes.byref(locked)) == 0:
            try:
                buffer = ctypes.string_at(locked.Scan0, size * size * 4)
            finally:
                gdiplus.GdipBitmapUnlockBits(image, ctypes.byref(locked))
    finally:
        for handle, release in ((white, gdiplus.GdipDeleteBrush), (font, gdiplus.GdipDeleteFont),
                                (family, gdiplus.GdipDeleteFontFamily),
                                (brush, gdiplus.GdipDeleteBrush), (path, gdiplus.GdipDeletePath),
                                (graphics, gdiplus.GdipDeleteGraphics),
                                (image, gdiplus.GdipDisposeImage)):
            try:
                if handle:
                    release(handle)
            except Exception:
                pass
        gdiplus.GdiplusShutdown(token)
    return _png(size, size, buffer) if buffer else b""


def render_png(size: int = 64) -> bytes:
    """图标 PNG 字节；非 Windows 或绘制失败时返回 b""。"""
    if size in _cache:
        return _cache[size]
    try:
        data = _draw(size)
    except Exception:
        data = b""
    if data:
        _cache[size] = data
    return data


def apply_window_icon(window, size: int = 64) -> bool:
    """给 Tk 窗口挂上图标；失败安静跳过。"""
    # Do not flush Tk's idle queue while its fonts/layout are still being built.
    # Mapping supplies the real outer HWND after construction, before interaction.
    if not getattr(window, '_mdreader_taskbar_binding', None):
        def on_map(event):
            if event.widget is window:
                try:
                    from .taskbar import configure_window
                    configure_window(window, flush=False)
                except (OSError, AttributeError):
                    pass
        window._mdreader_taskbar_binding = window.bind('<Map>', on_map, add='+')
    try:
        data = render_png(size)
        if not data:
            return False
        import tkinter as tk
        image = tk.PhotoImage(data=base64.b64encode(data).decode("ascii"), master=window)
        window.iconphoto(True, image)
        window._mdreader_icon = image        # 保住引用，否则 Tk 会回收
        return True
    except Exception:
        return False
