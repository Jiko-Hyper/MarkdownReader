# -*- coding: utf-8 -*-
"""
MDReader launcher.

Picks the best desktop window this machine can support:

  1. Tk window + embedded Edge WebView2, when WebView2Loader.dll is available
     (the WebView2 *runtime* ships with Windows, but the loader DLL ships with
     an app -- so we look for it next to us and in the installed runtimes).
  2. Otherwise a self-contained tkinter window with its own Markdown typesetting
     (`mdreader.winui`), which needs nothing beyond the Python standard library.
  3. Plain default browser, if tkinter itself is unavailable.

The HTTP server is always loopback-only (127.0.0.1) and stops with the window.

Command line:
    --workspace DIR, -w DIR   工作区目录
    --port N, -p N            固定端口
    --browser, -b             只用系统浏览器，不开窗口
    --serve                   只启动服务（不打开任何界面）
    --open FILE, -o FILE      启动时载入一个 .md 文件
    --selftest                检查内嵌窗口能否正常启动
    --version / --help
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import webbrowser
from ctypes import wintypes

from . import core

APP_TITLE = "MDReader"


# ==========================================================================
# minimal COM plumbing for WebView2
# ==========================================================================

_HRESULT = ctypes.c_long


def say(message="", *, flush=True):
    """Print that also works when stdout is gone (pythonw.exe sets it to None)."""
    stream = getattr(sys, "stdout", None) or getattr(sys, "stderr", None)
    if stream is None:
        return
    try:
        stream.write(str(message) + "\n")
        if flush:
            stream.flush()
    except Exception:
        pass


def _succeeded(hr) -> bool:
    return hr is not None and hr >= 0


def _webview2_roots() -> list:
    """Candidate folders that may contain WebView2Loader.dll / the runtime."""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    roots = [
        os.path.join(pf86, "Microsoft", "EdgeWebView", "Application"),
        os.path.join(pf, "Microsoft", "EdgeWebView", "Application"),
        os.path.join(pf86, "Microsoft", "Edge", "Application"),
        os.path.join(pf, "Microsoft", "Edge", "Application"),
        os.path.join(local, "Microsoft", "EdgeWebView", "Application"),
        os.path.join(local, "Microsoft", "Edge", "Application"),
    ]
    # also honour the registry, which is authoritative when Edge/WebView2 was
    # installed somewhere unusual
    if os.name == "nt":
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients",
                            r"SOFTWARE\Microsoft\EdgeUpdate\Clients"):
                    try:
                        with winreg.OpenKey(hive, sub) as k:
                            for i in range(winreg.QueryInfoKey(k)[0]):
                                name = winreg.EnumKey(k, i)
                                try:
                                    with winreg.OpenKey(k, name) as ck:
                                        loc = winreg.QueryValueEx(ck, "location")[0]
                                        if isinstance(loc, str) and os.path.isdir(loc):
                                            roots.append(loc)
                                except OSError:
                                    continue
                    except OSError:
                        continue
        except Exception:
            pass
    seen, out = set(), []
    for r in roots:
        if r and r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def _find_webview2_loader() -> str | None:
    """Locate WebView2Loader.dll on this machine.

    It normally ships *with* an application rather than with the WebView2
    runtime, so we look in the places an app or a dev machine would put it.
    """
    arch = "x64" if sys.maxsize > 2 ** 32 else "x86"
    here = os.path.dirname(os.path.abspath(__file__))

    # 1. shipped next to this app (frozen bundles / manual installs)
    local = [
        os.path.join(here, "WebView2Loader.dll"),
        os.path.join(here, arch, "WebView2Loader.dll"),
        os.path.join(os.path.dirname(here), "WebView2Loader.dll"),
        os.path.join(os.path.dirname(here), "runtime", "WebView2Loader.dll"),
        os.path.join(os.path.dirname(here), "runtime", arch, "WebView2Loader.dll"),
        os.path.join(os.path.dirname(here), "lib", "WebView2Loader.dll"),
        os.path.join(os.path.dirname(here), "lib", arch, "WebView2Loader.dll"),
    ]
    for cand in local:
        if os.path.isfile(cand):
            return cand

    # 2. the installed runtimes, in every layout we know about
    for root in _webview2_roots():
        try:
            versions = sorted(os.listdir(root), reverse=True)
        except OSError:
            continue
        for ver in versions:
            base = os.path.join(root, ver)
            if not os.path.isdir(base):
                continue
            for cand in (
                os.path.join(base, "EBWebView", arch, "WebView2Loader.dll"),
                os.path.join(base, "EBWebView", "WebView2Loader.dll"),
                os.path.join(base, arch, "WebView2Loader.dll"),
                os.path.join(base, "WebView2Loader.dll"),
            ):
                if os.path.isfile(cand):
                    return cand
    return None


def webview2_available() -> bool:
    """True when an embedded Edge WebView2 view can be created."""
    try:
        return bool(_find_webview2_loader())
    except Exception:
        return False


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str):
        super().__init__()
        text = text.strip("{}")
        p1, p2, p3, p4, p5 = text.split("-")
        self.Data1 = int(p1, 16)
        self.Data2 = int(p2, 16)
        self.Data3 = int(p3, 16)
        tail = bytes.fromhex(p4 + p5)
        for i, b in enumerate(tail):
            self.Data4[i] = b

    def __repr__(self):
        return "GUID(%08X-%04X-%04X)" % (self.Data1, self.Data2, self.Data3)


IID_IUNKNOWN = "{00000000-0000-0000-C000-000000000046}"
IID_ICoreWebView2 = "{189B8FAA-4E2C-4B6C-9B3B-0E1C1E5A0B8B}"
IID_ENV_COMPLETED = "{4E8A3389-C9D8-4BD2-B6B5-124FEE6CC14D}"
IID_CONTROLLER_COMPLETED = "{6C4819F3-C9B7-4260-8127-C9F5BDE7F68C}"

ENV_OPTIONS_IID = "{AC4B7881-7E9B-4B9B-8A5B-1E3A0C1D6A4B}"

WINFUNCTYPE = ctypes.WINFUNCTYPE


class _EnvOptions(ctypes.Structure):
    """vtable-only struct; layout is documented by the WebView2 SDK."""
    _fields_ = [("lpVtbl", ctypes.c_void_p)]

    def __init__(self):
        super().__init__()
        self.query_interface = _QI_FN(self._query_interface)
        self.add_ref = _AR_FN(self._add_ref)
        self.release = _AR_FN(self._release)
        self.set_user_data_folder = _PWSTR_FN(self._set_user_data_folder)
        self.set_additional_args = _PWSTR_FN(self._set_additional_args)
        self.set_language = _PWSTR_FN(self._set_language)
        self.set_target_compatible = _PWSTR_FN(self._set_target_compatible)
        self.vtbl = _EnvOptionsVtbl(
            ctypes.cast(self.query_interface, ctypes.c_void_p),
            ctypes.cast(self.add_ref, ctypes.c_void_p),
            ctypes.cast(self.release, ctypes.c_void_p),
            ctypes.cast(self.set_user_data_folder, ctypes.c_void_p),
            ctypes.cast(self.set_additional_args, ctypes.c_void_p),
            ctypes.cast(self.set_language, ctypes.c_void_p),
            ctypes.cast(self.set_target_compatible, ctypes.c_void_p),
        )
        self.lpVtbl = ctypes.cast(ctypes.byref(self.vtbl), ctypes.c_void_p)
        self.user_data = None
        self.args = None

    # IUnknown
    def _query_interface(self, this, riid, ppv):
        try:
            iid = ctypes.cast(riid, ctypes.POINTER(_GUID)).contents
            ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(
                ctypes.byref(self), ctypes.c_void_p).value
            return 0
        except Exception:
            return -2147467259

    def _add_ref(self, this):
        return 1

    def _release(self, this):
        return 1

    # ICoreWebView2EnvironmentOptions
    def _set_user_data_folder(self, this, value):
        self.user_data = value
        return 0

    def _set_additional_args(self, this, value):
        self.args = value
        return 0

    def _set_language(self, this, value):
        return 0

    def _set_target_compatible(self, this, value):
        return 0


_QI_FN = WINFUNCTYPE(_HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_AR_FN = WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_PWSTR_FN = WINFUNCTYPE(_HRESULT, ctypes.c_void_p, wintypes.LPCWSTR)


class _EnvOptionsVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", ctypes.c_void_p), ("AddRef", ctypes.c_void_p),
                ("Release", ctypes.c_void_p), ("SetUserDataFolder", ctypes.c_void_p),
                ("SetAdditionalBrowserArguments", ctypes.c_void_p), ("SetLanguage", ctypes.c_void_p),
                ("SetTargetCompatibleBrowserVersion", ctypes.c_void_p)]


class _WebView2:
    """Owns the controller object; vtable calls are hand-rolled."""

    def __init__(self):
        self.controller = None
        self.webview = None

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _vt(ptr, index, restype, *argtypes):
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.cast(vtbl[index], WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes))
        return fn

    def _call(self, ptr, index, restype, *argtypes):
        fn = self._vt(ptr, index, restype, *argtypes)
        return lambda *a: fn(ptr, *a)

    # controller vtable: 3 IUnknown, 4 get_IsVisible, 5 put_IsVisible,
    # 6 get_Bounds, 7 put_Bounds, 8 get_ZoomFactor, 9 put_ZoomFactor,
    # 10 add_ZoomFactorChanged, 11 remove_ZoomFactorChanged, 12 SetBoundsAndZoomFactor,
    # 13 MoveFocus, 14 add_MoveFocusRequested, ... 24 get_CoreWebView2, 25 Close
    def get_bounds(self):
        rect = wintypes.RECT()
        self._call(self.controller, 6, _HRESULT, ctypes.POINTER(wintypes.RECT))(ctypes.byref(rect))
        return rect

    def set_bounds(self, left, top, right, bottom):
        rect = wintypes.RECT(left, top, right, bottom)
        self._call(self.controller, 7, _HRESULT, ctypes.POINTER(wintypes.RECT))(ctypes.byref(rect))

    def set_visible(self, visible: bool):
        self._call(self.controller, 5, _HRESULT, wintypes.BOOL)(1 if visible else 0)

    def get_webview(self):
        out = ctypes.c_void_p()
        hr = self._call(self.controller, 24, _HRESULT, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(out))
        return out.value if _succeeded(hr) else None

    def close(self):
        try:
            self._call(self.controller, 25, _HRESULT)()
        except Exception:
            pass

    # webview vtable: 3 IUnknown, 4 get_Settings, 5 get_Source, 6 Navigate,
    # 7 NavigateToString, 8 add_NavigationStarting, ... 13 get_DocumentTitle,
    # 14 add_ContainsFullScreenElementChanged, ... 34 GoBack, 35 GoForward,
    # ... 38 add_HistoryChanged, ... 45 ExecuteScript, ... 52 OpenDevToolsWindow
    def navigate(self, url: str):
        self._call(self.webview, 6, _HRESULT, wintypes.LPCWSTR)(url)

    def go_back(self):
        self._call(self.webview, 34, _HRESULT)()

    def go_forward(self):
        self._call(self.webview, 35, _HRESULT)()

    def reload(self):
        self._call(self.webview, 11, _HRESULT)()


class _Callback(ctypes.Structure):
    """Generic `ICompletedHandler`-style COM callback."""

    _fields_ = [("lpVtbl", ctypes.c_void_p)]

    def __init__(self, invoke_impl, iids):
        super().__init__()
        self._fn = WINFUNCTYPE(_HRESULT, ctypes.c_void_p, _HRESULT, ctypes.c_void_p)(invoke_impl)
        self._qi = _QI_FN(self._query)
        self._ar = _AR_FN(self._add_ref)
        self._rl = _AR_FN(self._release)
        self._vtbl = _CallbackVtbl(
            ctypes.cast(self._qi, ctypes.c_void_p),
            ctypes.cast(self._ar, ctypes.c_void_p),
            ctypes.cast(self._rl, ctypes.c_void_p),
            ctypes.cast(self._fn, ctypes.c_void_p),
        )
        self.lpVtbl = ctypes.cast(ctypes.byref(self._vtbl), ctypes.c_void_p)
        self._iids = {i.lower().strip("{}") for i in iids}

    def _query(self, this, riid, ppv):
        try:
            iid = ctypes.cast(riid, ctypes.POINTER(_GUID)).contents
            text = "%08x-%04x-%04x-%02x%02x%02x%02x%02x%02x%02x%02x" % (
                iid.Data1, iid.Data2, iid.Data3,
                *[iid.Data4[i] for i in range(8)])
            if text in self._iids or text == IID_IUNKNOWN.strip("{}"):
                ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(
                    ctypes.byref(self), ctypes.c_void_p).value
                return 0
            ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0] = None
            return -2147467262  # E_NOINTERFACE
        except Exception:
            return -2147467259

    def _add_ref(self, this):
        return 1

    def _release(self, this):
        return 0  # owned by Python; keep it alive for the app's lifetime


class _CallbackVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", ctypes.c_void_p), ("AddRef", ctypes.c_void_p),
                ("Release", ctypes.c_void_p), ("Invoke", ctypes.c_void_p)]


# ==========================================================================
# native window
# ==========================================================================

class NativeWindow:
    """Tk shell + WebView2 child window (best effort)."""

    def __init__(self, url: str, title: str = APP_TITLE, selftest: bool = False,
                 workspace: str | None = None):
        from .display import enable_high_dpi
        enable_high_dpi()
        import tkinter as tk

        self.tk = tk
        self.url = url
        self.available = False
        self.error = ""
        self._selftest = selftest
        self.wv = _WebView2()
        self._handlers = []

        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("1260x840")
        self.root.minsize(880, 560)
        try:
            from .appicon import apply_window_icon
            apply_window_icon(self.root)
        except Exception:
            pass

        bar = tk.Frame(self.root, bg="#f6f7f9", height=40)
        bar.pack(side="top", fill="x")
        self._bar = bar

        def mkbtn(text, cmd, width=None):
            b = tk.Button(bar, text=text, command=cmd, relief="flat", bg="#ffffff",
                          activebackground="#eef1f4", bd=1, padx=10, pady=4,
                          cursor="hand2", font=("Microsoft YaHei UI", 9))
            b.pack(side="left", padx=(6, 0), pady=6)
            return b

        self.btn_back = mkbtn("← 后退", self.go_back)
        self.btn_fwd = mkbtn("前进 →", self.go_forward)
        self.btn_reload = mkbtn("⟳ 刷新", self.reload)
        mkbtn("🌐 在浏览器打开", self.open_browser)
        mkbtn("🗀 工作区目录", self.open_workspace)
        self.btn_more = mkbtn("⋯ 更多", self.show_more_menu)

        self.status = tk.Label(bar, text="正在启动…", bg="#f6f7f9", fg="#656d76",
                               font=("Microsoft YaHei UI", 9), anchor="e")
        self.status.pack(side="right", padx=12)

        self.host = tk.Frame(self.root, bg="#ffffff")
        self.host.pack(side="top", fill="both", expand=True)
        self.host.update_idletasks()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Alt-Left>", lambda e: self.go_back())
        self.root.bind("<Alt-Right>", lambda e: self.go_forward())
        self.root.bind("<F5>", lambda e: self.reload())

        self.workspace = workspace or core.default_workspace()

        try:
            self._embed()
            self.available = True
            self.status.config(text="WebView2 已就绪")
        except Exception as exc:  # pragma: no cover - depends on the machine
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self.status.config(text="内嵌视图不可用")
            self._show_fallback()
        self._watch()

    # -- embedding -------------------------------------------------------
    def _embed(self):
        path = _find_webview2_loader()
        if not path:
            raise RuntimeError("未找到 WebView2Loader.dll（系统缺少 WebView2 运行时）")
        self.loader = ctypes.WinDLL(path)
        self.loader.CreateCoreWebView2EnvironmentWithOptions.restype = _HRESULT
        self.loader.CreateCoreWebView2EnvironmentWithOptions.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]

        data_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "MDReader", "WebView2")
        if self.workspace:
            candidate = os.path.join(self.workspace, ".webview2")
            try:
                os.makedirs(candidate, exist_ok=True)
                data_dir = candidate
            except OSError:
                pass
        os.makedirs(data_dir, exist_ok=True)
        options = _EnvOptions()
        options._set_user_data_folder(None, data_dir)

        self._env_handler = _Callback(self._on_env_created, [IID_ENV_COMPLETED])
        hr = self.loader.CreateCoreWebView2EnvironmentWithOptions(
            None, data_dir, ctypes.cast(ctypes.byref(options), ctypes.c_void_p),
            ctypes.cast(ctypes.byref(self._env_handler), ctypes.c_void_p))
        if not _succeeded(hr):
            raise RuntimeError("创建 WebView2 环境失败 (0x%08X)" % (hr & 0xFFFFFFFF))

    def _on_env_created(self, this, hr, env):
        try:
            if not _succeeded(hr):
                raise RuntimeError("WebView2 环境回调失败 (0x%08X)" % (hr & 0xFFFFFFFF))
            if self._selftest:
                say("[selftest] WebView2 environment ready")
            self.env = env
            hwnd = self.host.winfo_id()
            self._ctl_handler = _Callback(self._on_controller_created, [IID_CONTROLLER_COMPLETED])
            # ICoreWebView2Environment vtable:
            #   0-2 IUnknown, 3 CreateCoreWebView2Controller, 4 CreateWebResourceResponse,
            #   5 add_NewBrowserVersionAvailable, 6 CreateCoreWebView2Host,
            #   7 CreateCoreWebView2ControllerAsync (newer runtimes)
            ref = ctypes.cast(env, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            res = None
            for index in (3, 7):
                try:
                    fn = ctypes.cast(ref[index], WINFUNCTYPE(
                        _HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.c_void_p))
                    res = fn(env, hwnd, ctypes.cast(ctypes.byref(self._ctl_handler), ctypes.c_void_p))
                except Exception:
                    res = None
                if _succeeded(res):
                    break
            if not _succeeded(res):
                raise RuntimeError("创建 WebView2 控制器失败 (0x%08X)" % ((res or 0) & 0xFFFFFFFF))
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            try:
                self.root.after(0, self._show_fallback)
            except Exception:
                pass
        return 0

    def _vt_call(self, ptr, index, restype, *argtypes):
        fn = ctypes.cast(
            ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[index],
            WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes))
        return fn

    def _on_controller_created(self, this, hr, controller):
        try:
            if not _succeeded(hr) or not controller:
                raise RuntimeError("WebView2 控制器回调失败 (0x%08X)" % (hr or 0))
            self.wv.controller = controller
            self.wv.webview = self.wv.get_webview()
            if not self.wv.webview:
                raise RuntimeError("无法取得 ICoreWebView2 接口")
            if self._selftest:
                say("[selftest] WebView2 controller ready")
            self._fit()
            self.wv.set_visible(True)
            self.wv.navigate(self.url)
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            try:
                self.root.after(0, self._show_fallback)
            except Exception:
                pass
        return 0

    def _fit(self):
        try:
            w = max(1, self.host.winfo_width())
            h = max(1, self.host.winfo_height())
            self.wv.set_bounds(0, 0, w, h)
        except Exception:
            pass

    def _watch(self):
        def tick():
            if self.available:
                self._fit()
            self.root.after(400, tick)
        self.root.after(400, tick)

    def _show_fallback(self):
        for child in self.host.winfo_children():
            child.destroy()
        frame = self.tk.Frame(self.host, bg="#ffffff")
        frame.pack(fill="both", expand=True)
        xpad = dict(padx=40)
        self.tk.Label(
            frame, text="内嵌浏览器不可用，已切换到系统浏览器模式",
            bg="#ffffff", fg="#1f2328", font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor="w", pady=(36, 4), **xpad)
        self.tk.Label(
            frame, text="原因：" + (self.error or "未知"),
            bg="#ffffff", fg="#cf222e", font=("Microsoft YaHei UI", 9), wraplength=900, justify="left",
        ).pack(anchor="w", pady=4, **xpad)
        self.tk.Label(
            frame, text="界面已经在本机运行，点击下面的按钮在 Edge / Chrome 中打开即可，功能完全一样。",
            bg="#ffffff", fg="#656d76", font=("Microsoft YaHei UI", 10), wraplength=900, justify="left",
        ).pack(anchor="w", pady=4, **xpad)
        self.tk.Button(
            frame, text="🌐  在浏览器中打开 MDReader", command=self.open_browser,
            bg="#0969da", fg="#ffffff", activebackground="#218bff", activeforeground="#ffffff",
            relief="flat", padx=22, pady=10, cursor="hand2",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w", pady=(22, 8), **xpad)
        self.tk.Label(
            frame, text="本机地址：" + self.url,
            bg="#ffffff", fg="#656d76", font=("Consolas", 9),
        ).pack(anchor="w", pady=4, **xpad)

    # -- actions ---------------------------------------------------------
    def go_back(self):
        try:
            if self.available:
                self.wv.go_back()
            else:
                self.open_browser()
        except Exception:
            pass

    def go_forward(self):
        try:
            if self.available:
                self.wv.go_forward()
        except Exception:
            pass

    def reload(self):
        try:
            if self.available:
                self.wv.reload()
        except Exception:
            pass

    def open_browser(self):
        webbrowser.open(self.url)

    def open_workspace(self):
        try:
            os.startfile(self.workspace)  # noqa: S606
        except Exception as exc:
            say("无法打开工作区目录:", exc)

    # -- 更多：以后新增的次要功能放这里 ---------------------------------
    def show_more_menu(self):
        menu = self.tk.Menu(self.root, tearoff=0, bg="#ffffff", fg="#1f2328",
                            activebackground="#eef1f4", activeforeground="#1f2328", bd=0)
        menu.add_command(label="关于 %s" % core.APP_NAME, command=self.show_about)
        menu.add_command(label="打开工作区文件夹", command=self.open_workspace)
        menu.add_command(label="快捷键说明", command=self.show_shortcuts)
        try:
            menu.tk_popup(self.btn_more.winfo_rootx(),
                          self.btn_more.winfo_rooty() + self.btn_more.winfo_height())
        finally:
            menu.grab_release()

    def show_about(self):
        self._info("关于 %s" % core.APP_NAME, "%s %s" % (core.APP_NAME, core.APP_VERSION),
                   ["内嵌 Edge WebView2 的浏览器视图。",
                    "工作区：%s" % self.workspace,
                    "本机地址：%s" % self.url])

    def show_shortcuts(self):
        self._info("快捷键说明", "浏览视图快捷键",
                   [("Alt+←", "后退"), ("Alt+→", "前进"), ("F5", "刷新页面"),
                    ("Ctrl+S", "保存当前文档"), ("Ctrl+B", "折叠 / 展开侧栏"),
                    ("Ctrl+N", "新建文档"), ("Ctrl+E", "阅读 / 源码")])

    def _info(self, title, heading, lines):
        from .winui import InfoDialog, LIGHT
        InfoDialog(self.root, title, heading, lines, LIGHT, 1).show()

    def run(self):
        self.root.mainloop()

    def on_close(self):
        try:
            if self.available:
                self.wv.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


# ==========================================================================
# entry points
# ==========================================================================

def selftest(workspace: str | None = None, timeout: float = 25.0, span: float = 2.5) -> int:
    """Verify that the desktop window can really start on this machine.

    Creates the window the app would actually use, keeps the event loop alive
    for `span` seconds, then exits. Returns 0 on success.
    """
    import tempfile

    ws_root = workspace
    if not ws_root:
        # never touch the user's real workspace from a self test
        ws_root = os.path.join(tempfile.gettempdir(), "mdreader-selftest")
    ws, httpd, port = core.serve(ws_root, port=0)
    url = "http://127.0.0.1:%d/" % port
    thread = core.ServerThread(httpd)
    thread.start()
    say("[selftest] server %s (workspace %s)" % (url, ws.root))
    loader = _find_webview2_loader()
    say("[selftest] WebView2Loader.dll: %s" % (loader or "未找到，将使用内置 tkinter 窗口"))

    use_wv = webview2_available()
    result = {"ok": False}
    win = None
    try:
        if use_wv:
            win = NativeWindow(url, selftest=True, workspace=ws.root)
            result["ok"] = True
            say("[selftest] 窗口类型: Tk + 内嵌 WebView2")
        else:
            from .winui import MarkdownWindow
            win = MarkdownWindow(ws.root, url=url)
            result["ok"] = True
            say("[selftest] 窗口类型: 内置 tkinter 排版窗口")
    except Exception as exc:
        say("[selftest] FAIL: 无法创建窗口: %s: %s" % (type(exc).__name__, exc))
        if win is not None:
            try:
                win.on_close()
            except Exception:
                pass
        thread.stop()
        return 1

    state = {"presented": False}

    def after_start():
        state["presented"] = True
        if hasattr(win, "host"):
            say("[selftest] 视图区域: %dx%d" % (win.host.winfo_width(), win.host.winfo_height()))
        else:
            say("[selftest] 窗口已显示")
        win.root.after(int(span * 1000), finish)

    def finish():
        try:
            if hasattr(win, "on_close"):
                win.on_close()
            else:
                win.root.destroy()
        except Exception:
            pass

    win.root.after(700, after_start)
    try:
        win.root.mainloop()
    except Exception as exc:
        say("[selftest] FAIL: 事件循环异常 %s: %s" % (type(exc).__name__, exc))
        result["ok"] = False
    finally:
        thread.stop()

    if result["ok"] and state["presented"]:
        say("[selftest] OK: 桌面窗口正常启动并已渲染")
        say("[selftest] workspace: %s" % ws.root)
        return 0
    say("[selftest] FAIL: 窗口未能显示")
    return 1


def load_open_files(ws, open_files):
    """Open .md files handed to us by Explorer as *temporary* documents.

    拖到图标上的文件不会被复制进工作区：软件只记住路径，阅读和编辑都作用在
    原文件上。想长期管理时，界面上有「加入项目」。

    Returns (focus_pid, focus_doc, kind) for the last file, or None.
    """
    loose = core.LooseDocs(ws)
    focus = None
    for path in (open_files or []):
        try:
            info = loose.open_path(path)
            focus = (info["id"], info["id"], "loose")
            say("  已临时打开 : %s" % info["path"])
        except Exception as exc:
            say("  跳过 %s（%s）" % (path, exc))
    return focus


def focus_url(base_url: str, focus) -> str:
    if not focus:
        return base_url
    import urllib.parse as _up
    pid, did, kind = focus
    return base_url + "?pid=%s&doc=%s&docKind=%s" % (
        _up.quote(pid), _up.quote(did), _up.quote(kind))


def run(workspace: str | None = None, port: int = 0, open_browser: bool = False,
        use_window: bool = True, open_files=None):
    ws, httpd, real_port = core.serve(workspace, port)
    focus = load_open_files(ws, open_files)
    url = focus_url("http://127.0.0.1:%d/" % real_port, focus)

    say("%s %s 已启动" % (APP_TITLE, core.APP_VERSION))
    say("  工作区 : %s" % ws.root)
    say("  地址   : %s" % url)
    say("  内嵌视图: %s" % ("WebView2" if webview2_available() else "内置 tkinter 窗口（未检测到 WebView2Loader.dll）"))
    
    if open_browser or not use_window:
        core.api_of(httpd).mode = "browser"
        webbrowser.open(url)
        _idle()
        return

    thread = core.ServerThread(httpd)
    thread.start()
    api = core.api_of(httpd)

    try:
        import tkinter  # noqa: F401
    except Exception as exc:
        say("  tkinter 不可用（%s），改用系统浏览器" % exc)
        api.mode = "browser"
        webbrowser.open(url)
        try:
            thread.stop()
        finally:
            _idle()
        return

    if webview2_available():
        try:
            win = NativeWindow(url, workspace=ws.root)
        except Exception as exc:  # tkinter itself failed
            say("  无法创建窗口（%s），改用系统浏览器" % exc)
            api.mode = "browser"
            webbrowser.open(url)
            thread.stop()
            _idle()
            return
        if not win.available:
            # embedded view could not start: keep the window, but drive the
            # browser instead so the user still has a UI.
            say("  内嵌视图启动失败：%s" % win.error)
            api.mode = "browser"
            webbrowser.open(url)
    else:
        try:
            from .winui import MarkdownWindow
        except Exception as exc:
            say("  内置窗口不可用（%s），改用系统浏览器" % exc)
            api.mode = "browser"
            webbrowser.open(url)
            thread.stop()
            _idle()
            return
        win = MarkdownWindow(ws.root, url=url)
        if open_files:
            win.open_local_files(open_files)

    try:
        win.run()
    finally:
        thread.stop()


def _idle():
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # 打包成 exe 之后没有独立解释器，插件工作进程用同一个 exe 重新进入。
    if argv and argv[0] == "--plugin-worker":
        from . import plugin_worker
        return plugin_worker.main()

    workspace = None
    port = 8642
    open_browser = False
    no_window = False
    do_selftest = False
    serve_only = False
    open_files = []

    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--workspace", "-w") and i + 1 < len(argv):
            workspace = argv[i + 1]; i += 2; continue
        if a in ("--port", "-p") and i + 1 < len(argv):
            port = int(argv[i + 1]); i += 2; continue
        if a in ("--open", "-o") and i + 1 < len(argv):
            open_files.append(argv[i + 1]); i += 2; continue
        if a in ("--browser", "-b"):
            open_browser = True; i += 1; continue
        if a == "--no-window":
            no_window = True; i += 1; continue
        if a == "--selftest":
            do_selftest = True; i += 1; continue
        if a == "--serve":
            serve_only = True; i += 1; continue
        if a in ("--help", "-h"):
            say(__doc__)
            return 0
        if a == "--version":
            say("%s %s" % (APP_TITLE, core.APP_VERSION))
            return 0
        # a bare path (drag & drop onto the launcher, or a file association)
        if not a.startswith("-") and os.path.exists(a):
            open_files.append(a)
        i += 1

    if do_selftest:
        return selftest(workspace)
    if no_window:
        open_browser = True
    if serve_only and not open_browser:
        ws, httpd, real_port = core.serve(workspace, port)
        core.api_of(httpd).mode = "browser"
        core.ServerThread(httpd).start()
        focus = load_open_files(ws, open_files)
        say("%s %s 服务已启动" % (APP_TITLE, core.APP_VERSION))
        say("  工作区 : %s" % ws.root)
        say("  地址   : %s" % focus_url("http://127.0.0.1:%d/" % real_port, focus))
        _idle()
        return
    run(workspace=workspace, port=port, open_browser=open_browser, open_files=open_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
