"""Isolated manual IME check. Never loads the user's workspace or documents.

Run it, then type Chinese with the real input method in the window: the printed
JSON shows the composition string, where the inline preedit is drawn and where
the candidate list was told to go (it must be the preedit bottom + CANDIDATE_GAP).

    python scripts/ime-live-check.py
"""
import ctypes as c
import json
from ctypes import wintypes as w
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mdreader import winui
from mdreader.ime import CANDIDATE_GAP, CFS_CANDIDATEPOS

imm = c.WinDLL("imm32")


class CompositionForm(c.Structure):
    _fields_ = [("style", w.DWORD), ("point", w.POINT), ("area", w.RECT)]


class CandidateForm(c.Structure):
    _fields_ = [("index", w.DWORD), ("style", w.DWORD), ("point", w.POINT), ("area", w.RECT)]


imm.ImmGetContext.argtypes = [w.HWND]
imm.ImmGetContext.restype = w.HANDLE
imm.ImmReleaseContext.argtypes = [w.HWND, w.HANDLE]
imm.ImmGetCompositionWindow.argtypes = [w.HANDLE, c.POINTER(CompositionForm)]
imm.ImmGetCompositionWindow.restype = w.BOOL
imm.ImmGetCandidateWindow.argtypes = [w.HANDLE, w.DWORD, c.POINTER(CandidateForm)]
imm.ImmGetCandidateWindow.restype = w.BOOL


def ime_forms(adapter):
    """Read back the composition/candidate rectangles the IME was given."""
    context = imm.ImmGetContext(adapter._hwnd)
    if not context:
        return None, None
    try:
        composition, candidate = CompositionForm(), CandidateForm()
        imm.ImmGetCompositionWindow(context, c.byref(composition))
        imm.ImmGetCandidateWindow(context, 0, c.byref(candidate))
        return candidate, composition
    finally:
        imm.ImmReleaseContext(adapter._hwnd, context)


def field_state(name, adapter):
    state = dict(name=name, focused=adapter.focused, ok=adapter.ok, error=adapter.error,
                 # `editable` 必须打印：为假时 handle_message 会原样转发
                 # WM_IME_SETCONTEXT，Windows 就会画回自己的白色组合框，而内嵌浮层
                 # 根本不出现——0.2.8 就是这么漏掉过一次。
                 editable=adapter.editable,
                 preedit=adapter.surface.text,
                 drawn=bool(adapter.surface.canvas.winfo_ismapped()),
                 rect=adapter.rect, candidate=None, composition=None)
    if adapter.ok and adapter.rect:
        candidate, composition = ime_forms(adapter)
        if candidate is not None:
            state["candidate"] = dict(
                style=candidate.style, expected_style=CFS_CANDIDATEPOS,
                x=candidate.point.x, y=candidate.point.y,
                below=bool(candidate.point.y >= adapter.rect[1] + adapter.rect[3]),
                gap=CANDIDATE_GAP)
            state["composition"] = dict(style=composition.style, left=composition.area.left,
                                        top=composition.area.top, right=composition.area.right,
                                        bottom=composition.area.bottom)
    return state


with tempfile.TemporaryDirectory(prefix="mdreader-ime-live-") as workspace:
    window = winui.MarkdownWindow(workspace)
    window.new_loose_draft()
    window.set_theme("dark")
    window.root.title("MDReader 输入法验证（独立临时文档）")
    last = None

    # 一进来就检查那个决定性标志位：为假说明打字时会出现系统白框，
    # 不用等到人真的敲键盘才发现。
    if not window.ime.editable:
        print(json.dumps({"warning": "编辑控件不可组合：打字时会出现 Windows 白色组合框",
                          "hint": "显示控件时应由 winui._show() 设置 editable；检查是否有人手工撤销了它"},
                         ensure_ascii=True), flush=True)

    def report():
        global last
        state = dict(hook=window.ime._hwnd, editor=window.text.winfo_id(),
                     native_focus=window.ime._user32.GetFocus(), counts=window.ime.message_counts,
                     text=window.get_text(),
                     fields=[field_state("editor", window.ime),
                             field_state("search", window.ime_search)])
        encoded = json.dumps(state, ensure_ascii=True, sort_keys=True)
        if encoded != last:
            print(encoded, flush=True)
            last = encoded
        window.root.after(300, report)

    # Only the agent-created test document is printed; never user documents.
    window.root.after(300, report)
    window.root.mainloop()
