"""Local image references shared by native preview and export snapshots."""
import os
import re
import struct
import tempfile
import zlib
from urllib.parse import unquote, urlsplit
from .storage import is_within

IMAGE = re.compile(r'!\[([^\]]*)\]\((<[^>]+>|[^\s)]+)(?:\s+"([^"]*)")?\)')

#: 认得的图片扩展名（拖入与剪贴板都按这个判断）。
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif")


def is_image_path(path: str) -> bool:
    return os.path.splitext(str(path))[1].lower() in IMAGE_EXTS


# --------------------------------------------------------------------------
# 剪贴板里的图片（只在用户显式操作时读，绝不后台监视）
# --------------------------------------------------------------------------

def _clipboard_png_from_dib(dib: bytes):
    """把 Windows 剪贴板里的 ``CF_DIB`` 转成 PNG 字节；认不出来时返回 ``None``。

    只用标准库：自己解析 ``BITMAPINFOHEADER`` 与像素，再用 zlib 编 PNG。这样没装
    Pillow 的机器也能粘贴截图（Pillow 只是更省事的**可选**快路）。
    """
    if len(dib) < 40:
        return None
    size, width, height, planes, bits, compression = struct.unpack("<IiiHHI", dib[:20])
    if size < 40 or planes != 1 or compression != 0 or bits not in (24, 32) or width <= 0:
        return None
    top_down = height < 0
    height = abs(height)
    if width * height > 40_000_000:
        return None
    palette = 0                                          # 24/32 位没有调色板
    stride = ((width * bits + 31) // 32) * 4
    # CF_DIB 是**没有** 14 字节 BITMAPFILEHEADER 的 DIB：像素紧跟在信息头（+调色板）后面。
    start = size + palette
    if len(dib) < start + stride * height:
        return None
    raw = bytearray()
    for row in range(height):
        source = height - 1 - row if not top_down else row
        line = dib[start + source * stride:start + source * stride + width * (bits // 8)]
        raw.append(0)                                   # filter: none
        for x in range(width):
            b, g, r = line[x * (bits // 8)], line[x * (bits // 8) + 1], line[x * (bits // 8) + 2]
            if bits == 32:
                alpha = line[x * 4 + 3]
                if alpha == 0:                          # 有些程序不填 alpha，当成不透明
                    alpha = 255
                r = r * alpha // 255
                g = g * alpha // 255
                b = b * alpha // 255
            raw += bytes((r, g, b))
    chunks = [b"\x89PNG\r\n\x1a\n"]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    chunks.append(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
    chunks.append(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
    chunks.append(chunk(b"IEND", b""))
    return b"".join(chunks)


def _clipboard_dib():
    """读剪贴板里的 ``CF_DIB``（没有就返回 ``None``）。只在被显式调用时打开剪贴板。"""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(8)             # CF_DIB
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            size = kernel32.GlobalSize(handle)
            return ctypes.string_at(pointer, size)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_image(directory: str = "") -> str:
    """把剪贴板里的图片落成 PNG 文件并返回路径；没有图片时返回空串。

    **只在用户显式操作时调用**（菜单项 / `Ctrl+Shift+V`）——程序不监视剪贴板。
    优先用 Pillow（能处理更多格式与“复制的图片文件”），没有 Pillow 时退到
    ``CF_DIB`` + 自写 PNG 编码。

    返回的路径有两种来源：我们自己新建的临时文件（``owns_clipboard_file`` 为真），
    或用户在资源管理器里“复制”的那张**原文件**——后者绝不能删。调用方想清理临时
    文件前先问 ``owns_clipboard_file``。``directory`` 只在自建临时文件时使用。
    """
    try:                                                # 可选快路：Pillow
        from PIL import Image, ImageGrab
        grabbed = ImageGrab.grabclipboard()
        if isinstance(grabbed, list) and grabbed:
            first = str(grabbed[0])
            if is_image_path(first) and os.path.isfile(first):
                return first                            # 用户复制的原件：不复制、不删除
        if grabbed is not None and hasattr(grabbed, "save"):
            target = _clipboard_target(directory, ".png")
            grabbed.convert("RGB").save(target, "PNG")
            return target
        return ""
    except ImportError:
        pass
    except Exception:
        return ""                                       # 剪贴板里不是图片
    png = _clipboard_png_from_dib(_clipboard_dib() or b"")
    if not png:
        return ""
    target = _clipboard_target(directory, ".png")
    with open(target, "wb") as stream:
        stream.write(png)
    return target


def owns_clipboard_file(path: str) -> bool:
    """``clipboard_image`` 给的路径是不是我们自建的临时文件（可以安全删掉）。"""
    if not path:
        return False
    try:
        root = os.path.realpath(tempfile.gettempdir())
        return (os.path.basename(path).startswith("clipboard-")
                and is_within(root, os.path.realpath(path)))
    except Exception:
        return False


def _clipboard_target(directory: str, extension: str) -> str:
    folder = directory or tempfile.gettempdir()
    os.makedirs(folder, exist_ok=True)
    handle, path = tempfile.mkstemp(prefix="clipboard-", suffix=extension, dir=folder)
    os.close(handle)
    return path



def image_matches(markdown):
    excluded = [(m.start(), m.end()) for m in re.finditer(
        r'(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[^\n]*(?:\n|$)|(`+)[^`\n]*?\2', markdown)]
    return [match for match in IMAGE.finditer(markdown)
            if not any(start <= match.start() < end for start, end in excluded)]


def width_of(title, default=720):
    match = re.search(r'(?:^|\s)width=(\d+)', title or '')
    return max(16, min(4096, int(match.group(1)))) if match else default


def local_image(source, document, root):
    source = source.strip('<>')
    parsed = urlsplit(source)
    if parsed.scheme or parsed.netloc or source.startswith(('\\', '/')):
        raise ValueError('仅支持文档目录内的本地图片：' + source)
    path = os.path.realpath(os.path.join(os.path.dirname(document), unquote(parsed.path)))
    if not is_within(root, path):
        raise ValueError('图片路径超出文档授权目录：' + source)
    if not os.path.isfile(path):
        raise FileNotFoundError('图片不存在：' + source)
    return path


def snapshot_images(markdown, document, root):
    files, resources = [], {}
    total = 0
    for match in image_matches(markdown):
        source = match.group(2).strip('<>')
        if source in resources:
            continue
        path = local_image(source, document, root)
        size = os.path.getsize(path)
        total += size
        if total > 48 * 1024 * 1024:
            raise ValueError('图片总大小超过 48 MB，请减少图片后导出')
        name = 'resource-%04d%s' % (len(files), os.path.splitext(path)[1].lower())
        with open(path, 'rb') as stream:
            files.append({'name': name, 'data': stream.read()})
        resources[source] = {'name': name}
    return files, resources


def insert_preview_image(widget, source, match):
    """Decode once per rendering; font zoom reuses the embedded image widget."""
    try:
        from PIL import Image, ImageTk, ImageOps
        import tkinter as tk
        document = getattr(widget, '_image_document', '')
        root = getattr(widget, '_image_root', '')
        if not document or not root:
            return False
        path = local_image(source, document, root)
        with Image.open(path) as original:
            picture = ImageOps.exif_transpose(original)
            desired = width_of(match.group(3) if match else '', min(picture.width, 720))
            available = max(100, widget.winfo_width() - 70)
            width = min(desired, available)
            height = max(1, round(picture.height * width / picture.width))
            picture = picture.resize((width, height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(picture, master=widget)
        label = tk.Label(widget, image=photo, bg=widget.cget('bg'), bd=0, cursor='hand2')
        label.image = photo
        label.bind('<MouseWheel>', lambda event: widget.event_generate('<MouseWheel>', delta=event.delta) or 'break')
        label.bind('<Control-MouseWheel>', lambda event: widget.event_generate('<Control-MouseWheel>', delta=event.delta) or 'break')
        edit = getattr(widget, '_image_edit', None)
        if edit and match:
            label.bind('<Double-1>', lambda _e: edit(match.start()))
        widget.insert('end', '\n')
        widget.window_create('end', window=label, padx=4, pady=8)
        widget.insert('end', '\n')
        return True
    except (ImportError, OSError, ValueError):
        return False
