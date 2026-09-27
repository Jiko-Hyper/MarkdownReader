"""Windows taskbar identity for a Python-hosted desktop application."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import sys
import uuid

APP_ID = 'JikoHyper.MDReader.Desktop'
DEV_ID = 'JikoHyper.MDReader.Development'

class GUID(C.Structure):
    _fields_ = [('data', C.c_ubyte * 16)]
    def __init__(self, value):
        super().__init__()
        self.data[:] = uuid.UUID(value).bytes_le

class PropertyKey(C.Structure):
    _fields_ = [('fmtid', GUID), ('pid', W.DWORD)]

class Value(C.Union):
    _fields_ = [('text', C.c_void_p), ('padding', C.c_ubyte * 16)]

class PropVariant(C.Structure):
    _fields_ = [('vt', W.WORD), ('r1', W.WORD), ('r2', W.WORD), ('r3', W.WORD), ('value', Value)]

IID_STORE = '886d8eeb-8cf2-4446-8d02-cdba1dbdcf99'
FMTID = '9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3'


def identity(root=None):
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    exe = root / 'MDReader.exe'
    if exe.is_file():
        return APP_ID, subprocess.list2cmdline([str(exe)]), str(exe) + ',0'
    return (DEV_ID, subprocess.list2cmdline([sys.executable, str(root / 'main.py')]),
            str(root / 'assets/icon.ico') + ',0')


def set_process_identity():
    if os.name != 'nt':
        return False
    fn = C.WinDLL('shell32').SetCurrentProcessExplicitAppUserModelID
    fn.argtypes, fn.restype = [W.LPCWSTR], C.c_long
    return fn(identity()[0]) >= 0


def _method(store, index, result, *args):
    table = C.cast(store, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result, C.c_void_p, *args)(table[index])


def _check(hr):
    if hr < 0:
        raise OSError('Windows property store failed: 0x%08x' % (hr & 0xffffffff))


def _window_store(hwnd):
    shell = C.WinDLL('shell32')
    fn = shell.SHGetPropertyStoreForWindow
    fn.argtypes = [W.HWND, C.POINTER(GUID), C.POINTER(C.c_void_p)]
    fn.restype = C.c_long
    store = C.c_void_p()
    _check(fn(hwnd, C.byref(GUID(IID_STORE)), C.byref(store)))
    return store


def _write(store, pid, text):
    key = PropertyKey(GUID(FMTID), pid)
    value = PropVariant()
    if text is not None:
        buffer = C.create_unicode_buffer(text)
        value.vt = 31  # VT_LPWSTR; SetValue copies this caller-owned string.
        value.value.text = C.cast(buffer, C.c_void_p).value
    # None leaves VT_EMPTY, releasing the property's Shell-owned resources.
    _check(_method(store, 6, C.c_long, C.POINTER(PropertyKey), C.POINTER(PropVariant))(
        store, C.byref(key), C.byref(value)))


def read_window_properties(hwnd):
    """Read actual Shell values (also used by native regression checks)."""
    store = _window_store(hwnd)
    ole = C.OleDLL('ole32')
    ole.PropVariantClear.argtypes = [C.POINTER(PropVariant)]
    result = {}
    try:
        for pid in (2, 3, 4, 5):
            key, value = PropertyKey(GUID(FMTID), pid), PropVariant()
            try:
                _check(_method(store, 5, C.c_long, C.POINTER(PropertyKey), C.POINTER(PropVariant))(
                    store, C.byref(key), C.byref(value)))
                result[pid] = C.wstring_at(value.value.text) if value.vt == 31 else None
            finally:
                ole.PropVariantClear(C.byref(value))
        return result
    finally:
        _method(store, 2, W.ULONG)(store)


def clear_window(hwnd):
    store = _window_store(hwnd)
    try:
        for pid in (5, 2, 3, 4):
            _write(store, pid, None)
    finally:
        _method(store, 2, W.ULONG)(store)


def configure_window(window, root=None, *, flush=True):
    """Set relaunch properties on the native top-level HWND, before its ID."""
    if os.name != 'nt':
        return False
    if flush:
        window.update_idletasks()
    user = C.WinDLL('user32')
    user.GetAncestor.argtypes, user.GetAncestor.restype = [W.HWND, W.UINT], W.HWND
    hwnd = user.GetAncestor(window.winfo_id(), 2)  # Tk's outer window, not its client HWND
    store = _window_store(hwnd)
    try:
        app_id, command, icon = identity(root)
        # ID is last: Shell must see complete relaunch metadata when grouping.
        for pid, text in ((2, command), (3, icon), (4, 'MDReader'), (5, app_id)):
            _write(store, pid, text)
        if not getattr(window, '_mdreader_taskbar_cleanup', False):
            original_destroy = window.destroy
            def destroy():
                try:
                    clear_window(hwnd)
                except OSError:
                    pass  # Already destroyed; never prevent normal shutdown.
                finally:
                    original_destroy()
            window.destroy = destroy
            window._mdreader_taskbar_cleanup = True
        # Window stores apply SetValue immediately; no file-style Commit needed.
        return hwnd
    finally:
        _method(store, 2, W.ULONG)(store)
