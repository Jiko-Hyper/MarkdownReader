"""IME protocol and real Tk composition surface, with isolated test input."""
import ctypes as c
import os
import unittest
from ctypes import wintypes as w
from unittest.mock import Mock, patch

from mdreader import ime, winui
from mdreader.display import enable_high_dpi


def ime_forms(adapter, index=0):
    """Read back the composition/candidate rectangles the IME was given."""
    imm = c.WinDLL("imm32")

    class CompositionForm(c.Structure):
        _fields_ = [("style", w.DWORD), ("point", w.POINT), ("area", w.RECT)]

    class CandidateForm(c.Structure):
        _fields_ = [("index", w.DWORD), ("style", w.DWORD),
                    ("point", w.POINT), ("area", w.RECT)]

    imm.ImmGetContext.argtypes = [w.HWND]
    imm.ImmGetContext.restype = w.HANDLE
    imm.ImmReleaseContext.argtypes = [w.HWND, w.HANDLE]
    imm.ImmGetCompositionWindow.argtypes = [w.HANDLE, c.POINTER(CompositionForm)]
    imm.ImmGetCompositionWindow.restype = w.BOOL
    imm.ImmGetCandidateWindow.argtypes = [w.HANDLE, w.DWORD, c.POINTER(CandidateForm)]
    imm.ImmGetCandidateWindow.restype = w.BOOL
    context = imm.ImmGetContext(adapter._hwnd)
    if not context:
        return None, None
    try:
        composition, candidate = CompositionForm(), CandidateForm()
        imm.ImmGetCompositionWindow(context, c.byref(composition))
        imm.ImmGetCandidateWindow(context, index, c.byref(candidate))
        return candidate, composition
    finally:
        imm.ImmReleaseContext(adapter._hwnd, context)


class FakeBridge:
    """Stands in for the native bridge DLL: one composition snapshot at a time.

    The real DLL subclasses the window in C and stores the composition string,
    so nothing re-enters Python from a Win32 callback. Tests drive the same
    ``active``/``read``/``detach`` surface instead of sending window messages.
    """

    def __init__(self, value=None):
        self.value = value
        self.active_flags = []
        self.detached = False

    def active(self, flag):
        self.active_flags.append(bool(flag))

    def read(self):
        return self.value

    def detach(self):
        self.detached = True


def use_bridge(adapter, value=None):
    """Replace an adapter's native bridge with a scripted fake."""
    bridge = FakeBridge(value)
    adapter.hook.native = bridge
    adapter.hook.ok = True
    adapter.ok = True
    return bridge


class ImeProtocolTests(unittest.TestCase):
    """消息协议本身的语义（**不是**运行中程序的证据）。

    `native_bridge.dll` 接管窗口子类后，`InlineIME.handle_message` 已不在生产
    路径上（见其 docstring）。这一组用例保留的是平台无关的协议表述：哪些消息
    原样转发、哪些被吞掉、结果串只转发一次。判断真实程序是否会出现系统白框，
    要看原生桥的 `active`/`read` 接口（见 `tests/test_native_tabs.py` 的
    白框回归用例），不要用这里的结果代替。
    """

    def setUp(self):
        self.adapter = ime.InlineIME.__new__(ime.InlineIME)
        self.adapter.ok = True
        self.adapter.editable = True
        self.adapter.focused = True
        self.adapter.pending = None
        self.adapter._read_preedit = Mock(return_value=("ni'hao", 6))
        self.forward = Mock(return_value=123)

    def send(self, message, flags=0):
        return self.adapter.handle_message(message, 1, flags, self.forward)

    def test_only_default_composition_is_hidden_not_system_candidates(self):
        flags = 0xC000000F
        self.assertEqual(self.send(ime.WM_IME_SETCONTEXT, flags), 123)
        self.forward.assert_called_once_with(flags & ~ime.ISC_SHOWUICOMPOSITIONWINDOW)
        self.assertEqual(self.forward.call_args.args[0] & 15, 15)

    def test_preedit_does_not_write_or_forward_duplicate_text(self):
        self.assertEqual(self.send(ime.WM_IME_STARTCOMPOSITION), 0)
        self.assertEqual(self.send(ime.WM_IME_COMPOSITION, ime.GCS_COMPSTR), 0)
        self.assertEqual(self.adapter.pending, ("ni'hao", 6))
        self.forward.assert_not_called()

    def test_results_are_forwarded_to_tk_exactly_once(self):
        self.adapter.pending = ("ni'hao", 6)
        self.assertEqual(self.send(ime.WM_IME_COMPOSITION, ime.GCS_RESULTSTR), 123)
        self.forward.assert_called_once_with(ime.GCS_RESULTSTR)
        self.assertEqual(self.adapter.pending, ("", 0))

    def test_partial_commit_keeps_following_preedit(self):
        flags = ime.GCS_RESULTSTR | ime.GCS_COMPSTR
        self.send(ime.WM_IME_COMPOSITION, flags)
        self.forward.assert_called_once_with(flags)
        self.assertEqual(self.adapter.pending, ("ni'hao", 6))

    def test_cancel_end_and_focus_loss_clear_the_surface(self):
        for message in (ime.WM_IME_COMPOSITION, ime.WM_IME_ENDCOMPOSITION, ime.WM_KILLFOCUS):
            self.adapter.pending = ("pending", 7)
            self.send(message)
            self.assertEqual(self.adapter.pending, ("", 0))

    def test_preview_and_unavailable_hook_keep_native_handling(self):
        for ok, editable in ((False, True), (True, False)):
            self.adapter.ok, self.adapter.editable = ok, editable
            self.forward.reset_mock()
            self.assertEqual(self.send(ime.WM_IME_COMPOSITION, ime.GCS_RESULTSTR), 123)
            self.forward.assert_called_once_with(ime.GCS_RESULTSTR)

    def test_utf16_cursor_supports_supplementary_characters(self):
        self.assertEqual(ime.character_cursor("a😀中", 3), 2)
        self.assertEqual(ime.character_cursor("a😀中", 4), 3)
        self.assertEqual(ime.character_cursor("abc", -1), 0)


class ImeSurfaceTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        enable_high_dpi()
        self.root = tk.Tk()
        self.root.geometry("600x200+100+100")
        self.editor = tk.Text(self.root, undo=True, bd=0, padx=16,
                              font=(winui.UI_FONT, 12))
        self.editor.pack(fill="both", expand=True)
        self.adapter = ime.InlineIME(self.editor)
        self.adapter.set_editable(True)
        self.editor.insert("1.0", "已有正文")
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self.root.update()
        self.addCleanup(self.root.destroy)

    def test_preedit_matches_every_theme_without_changing_document(self):
        for pal in (winui.DARK, winui.EYE, winui.LIGHT):
            winui.configure_tags(self.editor, pal)
            self.adapter.surface.show("ni'hao你好", 6)
            self.root.update_idletasks()
            canvas = self.adapter.surface.canvas
            self.assertEqual(canvas["background"], pal["bg"])
            self.assertEqual(canvas.itemcget("preedit", "fill"), pal["fg"])
            self.assertEqual(canvas.itemcget("preedit", "font"), self.editor["font"])
            self.assertTrue(canvas.winfo_ismapped())
            self.assertEqual(self.editor.get("1.0", "end-1c"), "已有正文")
            self.assertFalse(self.editor.edit_modified())
            self.assertEqual(self.editor.window_names(), ())
        self.adapter.surface.hide()
        self.assertEqual(self.adapter.surface.canvas.winfo_manager(), "")

    def test_long_composition_keeps_caret_inside_editor(self):
        self.adapter.surface.show("ni'hao" * 150, 900)
        self.root.update_idletasks()
        canvas = self.adapter.surface.canvas
        self.assertLessEqual(canvas.winfo_width(), self.editor.winfo_width())
        self.assertLessEqual(canvas.coords("caret")[0], canvas.winfo_width())
        self.assertEqual(self.editor.get("1.0", "end-1c"), "已有正文")

    @unittest.skipUnless(os.name == "nt", "Windows IMM only")
    def test_native_bridge_composition_is_painted_and_then_cleared(self):
        self.assertTrue(self.adapter.ok, self.adapter.error)
        self.editor.focus_force()
        self.root.update()
        self.assertEqual(self.adapter._hwnd, self.root.winfo_id())
        self.assertNotEqual(self.adapter._hwnd, self.editor.winfo_id())
        bridge = use_bridge(self.adapter, ("ni'hao", 6, 1))
        self.adapter._focus_in()
        self.assertEqual(self.adapter.pending, ("ni'hao", 6))
        self.assertTrue(bridge.active_flags[-1], "组合期间应通知原生层采集")
        # Paint through Tk's own event loop; never re-enter Tk from a callback.
        self.root.after(40, self.root.quit)
        self.root.mainloop()
        self.assertEqual(self.adapter.surface.text, "ni'hao")
        self.assertEqual(self.editor.get("1.0", "end-1c"), "已有正文")
        bridge.value = ("", 0, 12)
        self.adapter.hook.poll()
        self.root.after(40, self.root.quit)
        self.root.mainloop()
        self.assertEqual(self.adapter.surface.text, "")
        self.assertIsNone(self.adapter.error)

    @unittest.skipUnless(os.name == "nt", "Windows IMM only")
    def test_preedit_sits_exactly_on_the_caret(self):
        """place(-in=...) used to push the pinyin one padding step off the line."""
        box = self.adapter.surface.show("ni'hao", 6)
        self.root.update_idletasks()
        canvas = self.adapter.surface.canvas
        x, y, _, _ = box
        self.assertEqual(canvas.winfo_rootx(), self.editor.winfo_rootx() + x)
        self.assertEqual(canvas.winfo_rooty(), self.editor.winfo_rooty() + y)
        self.assertEqual(canvas.winfo_rootx(), self.editor.winfo_rootx() + self.editor.bbox("insert")[0])
        self.assertEqual(canvas.winfo_rooty(), self.editor.winfo_rooty() + self.editor.bbox("insert")[1])

    def test_theme_refresh_zoom_and_preview_keep_preedit_out_of_buffer(self):
        self.adapter.surface.show("zhong'wen", 9)
        winui.configure_tags(self.editor, winui.EYE)
        self.editor.configure(font=(winui.UI_FONT, 16))
        self.adapter.refresh()
        self.assertEqual(self.adapter.surface.canvas["background"], winui.EYE["bg"])
        self.assertEqual(self.adapter.surface.canvas.itemcget("preedit", "font"), self.editor["font"])
        self.adapter.set_editable(False)
        self.assertEqual(self.adapter.surface.text, "")
        self.assertFalse(self.editor.edit_modified())

    @unittest.skipUnless(os.name == "nt", "Windows IMM only")
    def test_candidate_list_is_told_to_stay_below_the_preedit(self):
        """The white candidate box must not land on top of the pinyin."""
        self.adapter._focus_in()
        self.assertTrue(self.adapter.ok, self.adapter.error)
        box = self.adapter.surface.show("ni'hao", 6)
        self.assertIsNotNone(box)
        self.adapter._paint(("ni'hao", 6))
        x, y, width, height = box
        x += self.editor.winfo_rootx() - self.root.winfo_rootx()
        y += self.editor.winfo_rooty() - self.root.winfo_rooty()
        candidate, composition = ime_forms(self.adapter)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.style, ime.CFS_CANDIDATEPOS)
        self.assertEqual((candidate.point.x, candidate.point.y),
                         (x, y + height + ime.CANDIDATE_GAP))
        self.assertGreaterEqual(candidate.point.y, y + height)
        self.assertEqual(composition.style, ime.CFS_RECT)
        self.assertEqual((composition.area.left, composition.area.top,
                          composition.area.right, composition.area.bottom),
                         (x, y, x + max(width, ime.MIN_BOX_WIDTH), y + height))

    @unittest.skipUnless(os.name == "nt", "Windows IMM only")
    def test_every_candidate_list_index_is_placed(self):
        self.adapter._focus_in()
        self.adapter.surface.show("hao", 3)
        self.adapter._paint(("hao", 3))
        for index in range(ime.CANDIDATE_LISTS):
            candidate, _ = ime_forms(self.adapter, index)
            self.assertEqual(candidate.style, ime.CFS_CANDIDATEPOS,
                             "候选列表 %d 未定位" % index)


class ImeEntrySurfaceTests(unittest.TestCase):
    """The search field must get the same inline preedit as the editor."""

    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        enable_high_dpi()
        self.root = tk.Tk()
        self.root.geometry("420x120+120+160")
        self.field = ttk.Entry(self.root)
        self.field.pack(fill="x", padx=10, pady=10)
        self.field.insert(0, "最近打开")
        self.adapter = ime.InlineIME(self.field, palette=winui.DARK)
        self.adapter.set_editable(True)
        self.field.focus_force()
        self.root.update()
        self.addCleanup(self.root.destroy)

    def test_preedit_is_themed_and_never_touches_the_entry_text(self):
        box = self.adapter.surface.show("sou'suo", 4)
        self.root.update_idletasks()
        canvas = self.adapter.surface.canvas
        self.assertEqual(canvas["background"], winui.DARK["bg"])
        self.assertEqual(canvas.itemcget("preedit", "fill"), winui.DARK["fg"])
        self.assertTrue(canvas.winfo_ismapped())
        self.assertEqual(self.field.get(), "最近打开")
        self.assertEqual(canvas.winfo_rootx(), self.field.winfo_rootx() + box[0])
        self.assertEqual(canvas.winfo_rooty(), self.field.winfo_rooty() + box[1])
        self.adapter.surface.hide()
        self.assertEqual(canvas.winfo_manager(), "")

    def test_palette_can_be_read_lazily_so_theme_switches_apply(self):
        self.adapter.surface.show("shu'ru", 5)
        self.assertEqual(self.adapter.surface.canvas["background"], winui.DARK["bg"])
        self.adapter.target.palette = lambda: winui.EYE
        self.adapter.refresh()
        self.assertEqual(self.adapter.surface.canvas["background"], winui.EYE["bg"])

    @unittest.skipUnless(os.name == "nt", "Windows IMM only")
    def test_entry_candidates_are_placed_in_toplevel_coordinates(self):
        self.assertTrue(self.adapter.ok, self.adapter.error)
        self.adapter._focus_in()
        box = self.adapter.surface.show("sou", 3)
        self.assertIsNotNone(box)
        self.adapter._paint(("sou", 3))
        x, y, _, height = box
        x += self.field.winfo_rootx() - self.root.winfo_rootx()
        y += self.field.winfo_rooty() - self.root.winfo_rooty()
        candidate, _ = ime_forms(self.adapter)
        self.assertIsNotNone(candidate)
        self.assertEqual((candidate.point.x, candidate.point.y),
                         (x, y + height + ime.CANDIDATE_GAP))


class ImeCompositionKeepsOutOfTheDocumentTests(unittest.TestCase):
    """E01 验收 4：未确认的组合文字不进正文，也不进撤销记录。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-ime-doc-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name, "笔记.md")
        self.path.write_text("# 原始\n\n原文件内容\n", encoding="utf-8")
        drop = patch.object(winui.MarkdownWindow, "enable_file_drop")
        drop.start()
        self.addCleanup(drop.stop)
        self.win = winui.MarkdownWindow(os.path.join(self.temp.name, "workspace"))
        self.win.root.deiconify()               # 组合浮层需要真实几何
        self.win.root.update()
        self.addCleanup(self.destroy_window, self.win.root)
        self.win.open_local_files([str(self.path)])
        self.win.show_source()
        self.win._set_widget("已有正文\n")      # 清空撤销栈，便于判定组合是否留下记录
        self.editor = self.win.editor
        self.adapter = self.win.ime
        self.editor.focus_force()
        self.win.root.update()
        # 注意：这里**不**调用 set_editable(True)。可编辑性必须由正常的显示流程
        # 给出（见 test_native_tabs 的回归用例）；测试自己设置这个前提，就会
        # 掩盖“生产代码忘了设置”的缺陷——0.2.8 的白框回归正是这样漏掉的。

    @staticmethod
    def destroy_window(root):
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()

    def pump(self, milliseconds=60):
        self.win.root.after(milliseconds, self.win.root.quit)
        self.win.root.mainloop()

    def test_uncommitted_composition_leaves_the_buffer_and_the_undo_stack_alone(self):
        self.assertTrue(self.adapter.editable,
                        "进入源码模式后编辑控件必须已经可组合（0.2.8 白框回归点）")
        bridge = use_bridge(self.adapter, ("ni'hao", 6, 1))
        self.adapter._focus_in()                # 读取组合串，等价于真实按键触发
        self.assertEqual(self.adapter.pending, ("ni'hao", 6))
        self.pump()
        self.assertEqual(self.adapter.surface.text, "ni'hao", "组合文字应显示在内嵌浮层")
        self.assertEqual(self.editor.get("1.0", "end-1c"), "已有正文\n", "未确认文字不得进入正文")
        self.assertFalse(self.editor.edit_modified(), "组合不得把文档标记为已修改")

        bridge.value = ("", 0, 2)               # 组合结束（取消或确认由系统输入法负责）
        self.adapter.hook.poll()
        self.pump()
        self.assertEqual(self.adapter.surface.text, "")
        self.assertIsNone(self.adapter.error)

        # 真正的一次编辑：撤销必须精确回到组合之前的文本，说明组合没有占用撤销记录。
        self.editor.insert("end", "手输\n")
        self.editor.edit_separator()
        self.win.root.update()
        self.editor.edit_undo()
        self.assertEqual(self.editor.get("1.0", "end-1c"), "已有正文\n")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "# 原始\n\n原文件内容\n",
                         "组合期间磁盘内容不得改变")

    def test_switching_tabs_mid_composition_clears_the_old_surface_only(self):
        from pathlib import Path
        second = Path(self.temp.name, "第二份.md")
        second.write_text("# 第二\n\n第二份\n", encoding="utf-8")
        self.assertTrue(self.adapter.editable)
        bridge = use_bridge(self.adapter, ("zhong", 5, 1))
        self.adapter._focus_in()
        self.pump()
        self.assertEqual(self.adapter.surface.text, "zhong")
        first_tab = self.win.active_tab

        self.win.open_local_files([str(second)])
        self.win.show_source()
        self.win._set_widget("第二份正文\n")
        self.assertTrue(self.win.ime.editable, "新标签的编辑控件同样必须已可组合")
        self.assertEqual(self.adapter.surface.text, "", "切走后旧控件上不得残留拼音浮层")
        second_tab = self.win.active_tab

        self.win.activate_tab(first_tab)
        self.assertEqual(self.win.get_text(), "已有正文\n")
        self.assertNotIn("zhong", self.win.get_text(), "组合文字不得被带到正文或另一份文档")
        self.win.activate_tab(second_tab)
        self.assertEqual(self.win.get_text(), "第二份正文\n")
        self.assertIsNone(self.adapter.error)
        self.assertEqual(bridge.detached, False)


class ImeSharedToplevelTests(unittest.TestCase):
    """Editor and search field share the toplevel window that owns IMM."""

    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        enable_high_dpi()
        self.root = tk.Tk()
        self.root.geometry("520x220+140+140")
        self.editor = tk.Text(self.root, undo=True, bd=0, padx=12, height=4,
                              font=(winui.UI_FONT, 12))
        self.editor.pack(fill="both", expand=True)
        self.field = ttk.Entry(self.root)
        self.field.pack(fill="x", padx=10, pady=8)
        self.text_ime = ime.InlineIME(self.editor)
        self.entry_ime = ime.InlineIME(self.field, palette=winui.LIGHT)
        self.text_ime.set_editable(True)
        self.entry_ime.set_editable(True)
        self.root.update()
        self.addCleanup(self.root.destroy)

    def send(self, adapter, flags):
        import ctypes as c
        from ctypes import wintypes as w
        send = c.WinDLL("user32").SendMessageW
        send.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        send.restype = c.c_ssize_t
        return send(adapter._hwnd, ime.WM_IME_COMPOSITION, 0, flags)

    @unittest.skipUnless(os.name == "nt", "Windows IMM only")
    def test_both_fields_install_on_the_same_toplevel_hook(self):
        self.assertTrue(self.text_ime.ok, self.text_ime.error)
        self.assertTrue(self.entry_ime.ok, self.entry_ime.error)
        self.assertEqual(self.text_ime._hwnd, self.entry_ime._hwnd)
        self.assertEqual(self.text_ime._hwnd, self.root.winfo_id())

    @unittest.skipUnless(os.name == "nt", "Windows IMM only")
    def test_only_the_focused_field_keeps_the_composition(self):
        # Drive the focus state the way Tk does, so the test does not depend on
        # which window the desktop happens to activate. Both widgets share one
        # window hook, so patching it here covers the editor and the field.
        bridge = use_bridge(self.text_ime, ("wen", 3, 1))
        self.assertIs(self.entry_ime.hook, self.text_ime.hook)
        self.text_ime._focus_out()
        self.entry_ime._focus_in()
        self.assertEqual(self.entry_ime.pending, ("wen", 3))
        self.assertIsNone(self.text_ime.pending)
        self.assertTrue(bridge.active_flags[-1], "有可编辑控件获得焦点时继续采集")
        self.entry_ime._focus_out()
        self.text_ime._focus_in()
        self.assertEqual(self.text_ime.pending, ("wen", 3))
        self.assertIsNone(self.entry_ime.pending)

    @unittest.skipUnless(os.name == "nt", "Windows IMM only")
    def test_preedit_capture_stops_when_nothing_editable_has_focus(self):
        bridge = use_bridge(self.text_ime, ("wen", 3, 1))
        self.entry_ime._focus_out()
        self.text_ime._focus_out()
        self.text_ime.hook.poll()
        self.assertFalse(bridge.active_flags[-1])
        self.entry_ime._focus_in()
        self.entry_ime.set_editable(False)
        self.entry_ime.hook.poll()
        self.assertFalse(bridge.active_flags[-1])


