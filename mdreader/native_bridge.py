"""ctypes calls into a native bridge; never installs a Python callback."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path

_library = None

def library():
    global _library
    if _library is None:
        dll = c.WinDLL(str(Path(__file__).with_suffix('.dll')))
        signatures = {
            'md_attach': ([w.HWND], c.c_int), 'md_detach': ([w.HWND], None),
            'md_active': ([w.HWND, c.c_int], None),
            'md_read': ([w.HWND, w.LPWSTR, c.c_int, c.POINTER(c.c_int), c.POINTER(w.ULONG)], w.ULONG),
            'md_drop_enable': ([w.HWND], None), 'md_drop_count': ([w.HWND], c.c_int),
            'md_drop_read': ([w.HWND, c.c_int, w.LPWSTR, c.c_int], None),
            'md_drop_clear': ([w.HWND], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(dll, name); fn.argtypes, fn.restype = args, result
        _library = dll  # retained for the process lifetime, including WM_NCDESTROY
    return _library

class NativeBridge:
    def __init__(self, hwnd):
        self.hwnd, self.dll = hwnd, library()
        if not self.dll.md_attach(hwnd):
            raise OSError('无法安装原生窗口消息桥')
        self.revision = 0

    def active(self, active):
        self.dll.md_active(self.hwnd, bool(active))

    def read(self):
        buf, cursor, messages = c.create_unicode_buffer(32768), c.c_int(), w.ULONG()
        revision = self.dll.md_read(self.hwnd, buf, len(buf), c.byref(cursor), c.byref(messages))
        if revision == self.revision:
            return None
        self.revision = revision
        return buf.value, cursor.value, messages.value

    def drops(self):
        count = self.dll.md_drop_count(self.hwnd)
        paths = []
        for index in range(min(count, 512)):
            buf = c.create_unicode_buffer(32768)
            self.dll.md_drop_read(self.hwnd, index, buf, len(buf)); paths.append(buf.value)
        if count: self.dll.md_drop_clear(self.hwnd)
        return paths

    def detach(self):
        self.dll.md_detach(self.hwnd)
