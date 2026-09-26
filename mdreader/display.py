"""Windows display setup, called before the first Tk window is created."""
import os


def style_titlebar(root, dark, background, foreground):
    """Best-effort app titlebar colours; older Windows keeps its native chrome."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    try:
        root.update_idletasks()
        parent = ctypes.windll.user32.GetParent
        parent.argtypes = [wintypes.HWND]
        parent.restype = wintypes.HWND
        hwnd = parent(root.winfo_id()) or root.winfo_id()
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        set_attribute.restype = ctypes.c_long
        value = ctypes.c_int(bool(dark))
        set_attribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        for attribute, colour in ((35, background), (36, foreground)):
            rgb = colour.lstrip("#")
            value = wintypes.DWORD(int(rgb[0:2], 16) | int(rgb[2:4], 16) << 8 | int(rgb[4:6], 16) << 16)
            set_attribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


def enable_high_dpi():
    if os.name != "nt":
        return False
    import ctypes
    try:
        fn = ctypes.windll.user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = ctypes.c_bool
        if fn(ctypes.c_void_p(-4)):
            return True
    except (AttributeError, OSError):
        pass
    try:
        # E_ACCESSDENIED means a host has already selected an awareness mode.
        return ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0
    except (AttributeError, OSError):
        return bool(ctypes.windll.user32.SetProcessDPIAware())
