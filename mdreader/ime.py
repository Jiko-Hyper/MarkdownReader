"""Inline Windows IME preedit, without putting uncommitted text in the document.

Only the composition surface is replaced: a theme-coloured canvas draws the
pinyin at the caret, and the IME is told where that composition really is, so
the candidate list stays *below* the pinyin instead of covering it. Tk still
receives the result string and owns insertion/selection/undo; Windows still owns
the candidate list, its colours and the system IME settings.

See Microsoft WM_IME_SETCONTEXT / WM_IME_COMPOSITION and CANDIDATEFORM
documentation. All rectangles below are in toplevel client pixels: Tk associates
IMM with the toplevel client window (tkWinX.c walks to ``Tk_IsTopLevel``) and
``Tk_SetCaretPos`` reports the caret in that same space.

Two rules keep this safe, and both are enforced by the structure below:

1. A Win32 callback may not call Tcl/Tk and may not call the imm32 setters.
   Both can re-enter code that is already on the stack. The callback only
   records what to draw, and Tk's timer does the drawing and the placing.
2. One window subclass per toplevel, shared by every editable widget in it.
   A subclass per widget would chain Python callbacks on the same HWND, so each
   ``DefSubclassProc`` re-enters Python while the previous callback is still on
   the native stack, which corrupts the interpreter state.
3. One Tk poll loop per toplevel, owned by that shared hook. The window keeps
   one editor widget per open document, so a timer per editor would multiply the
   same 60 Hz poll by the number of open tabs for no extra information: only the
   focused editor can own a composition.
"""
import os

WM_IME_STARTCOMPOSITION = 0x010D
WM_IME_ENDCOMPOSITION = 0x010E
WM_IME_COMPOSITION = 0x010F
WM_IME_SETCONTEXT = 0x0281
WM_KILLFOCUS = 0x0008
WM_NCDESTROY = 0x0082
GCS_COMPSTR = 0x0008
GCS_CURSORPOS = 0x0080
GCS_RESULTSTR = 0x0800
ISC_SHOWUICOMPOSITIONWINDOW = 0x80000000
CFS_RECT = 0x0001
CFS_CANDIDATEPOS = 0x0040
CANDIDATE_LISTS = 4      # CANDIDATEFORM.dwIndex accepts 0..3
CANDIDATE_GAP = 2        # pixels kept between the preedit and the candidate list
MIN_BOX_WIDTH = 2        # a composition that has only just started
HOOK_ATTRIBUTE = "_mdreader_ime_hook"


def character_cursor(text, utf16_offset):
    """IMM cursor offsets count UTF-16 units, not Python code points."""
    return len(text.encode("utf-16-le")[:max(0, utf16_offset) * 2].decode("utf-16-le", errors="ignore"))


def widget_class(widget) -> str:
    try:
        return widget.winfo_class()
    except Exception:
        return ""


def focused_widget(widget):
    """The widget that owns Tk focus, or None.

    ``focus_get()`` raises KeyError for Tk's own auxiliary toplevels (the ttk
    combobox popdown is named ``.popdown`` and is not a child of the root's
    widget tree), and returns None while the whole application is unfocused.
    Neither is an error worth failing the IME hook over.
    """
    try:
        return widget.focus_get()
    except Exception:
        return None


class TextTarget:
    """Caret geometry and chrome of a document editor (``tk.Text``)."""
    name = "text"

    def __init__(self, widget, palette=None):
        self.widget = widget
        self.palette = palette

    def caret_box(self):
        """(x, y, line height) in widget pixels, or None when not visible."""
        try:
            selection = self.widget.tag_ranges("sel")
            box = self.widget.bbox(selection[0] if selection else "insert")
        except Exception:
            return None
        if not box:
            return None
        return int(box[0]), int(box[1]), int(box[3])

    def editable(self):
        try:
            return str(self.widget["state"]) == "normal"
        except Exception:
            return False

    def font(self):
        return self.widget["font"]

    def chrome(self):
        widget = self.widget
        return {"bg": widget["background"], "fg": widget["foreground"],
                "caret": widget["insertbackground"], "inset": int(widget["padx"])}


class EntryTarget:
    """Caret geometry and chrome of a one-line input (``tk.Entry`` / ``ttk.Entry``)."""
    name = "entry"

    def __init__(self, widget, palette=None):
        self.widget = widget
        self.palette = palette

    def palette_values(self):
        palette = self.palette
        if callable(palette):
            try:
                palette = palette()
            except Exception:
                palette = None
        return palette or {}

    def inset(self):
        """Horizontal inset of the text inside the field, for clamping."""
        try:
            from tkinter import ttk
            padding = ttk.Style(self.widget).lookup("TEntry", "padding")
            return max(0, int(str(padding).split()[0]))
        except Exception:
            return 3

    def caret_box(self):
        try:
            box = self.widget.bbox("insert")
        except Exception:
            box = None
        if box:
            try:
                x, y, _, height = (int(value) for value in box)
            except (TypeError, ValueError):
                return self._estimated_box()
            if height > 0 and x >= 0 and y >= 0:
                return x, y, height
        # bbox is empty for an unmapped field or an index scrolled out of view.
        return self._estimated_box()

    def _estimated_box(self):
        import tkinter.font as tkfont
        widget = self.widget
        try:
            font = tkfont.Font(root=widget, font=self.font())
            text = str(widget.get())
            caret = int(widget.index("insert"))
        except Exception:
            return None
        line = font.metrics("linespace")
        height = widget.winfo_height() or line
        return self.inset() + font.measure(text[:caret]), max(0, (height - line) // 2), line

    def editable(self):
        try:
            return str(self.widget["state"]) not in ("disabled", "readonly")
        except Exception:
            return True

    def font(self):
        try:
            return self.widget["font"]
        except Exception:
            return None

    def chrome(self):
        palette = self.palette_values()
        widget = self.widget
        try:
            own_bg = widget["background"]
        except Exception:
            own_bg = ""
        try:
            own_fg = widget["foreground"]
        except Exception:
            own_fg = ""
        bg = palette.get("bg") or own_bg or "#ffffff"
        fg = palette.get("fg") or own_fg or "#000000"
        return {"bg": bg, "fg": fg, "caret": palette.get("accent") or fg, "inset": self.inset()}


def target_for(widget, palette=None):
    """Composition target matching the widget kind."""
    if widget_class(widget) == "Text":
        return TextTarget(widget, palette)
    return EntryTarget(widget, palette)


class CompositionSurface:
    def __init__(self, target):
        import tkinter as tk
        self.target = target
        self.editor = target.widget
        # Sibling, not a document child: preview table cleanup must not delete it.
        self.canvas = tk.Canvas(self.editor.master, bd=0, highlightthickness=0,
                                takefocus=False, cursor="xterm")
        self.text, self.cursor = "", 0
        self.canvas.bind("<Button-1>", lambda _e: self.editor.focus_set())

    def hide(self):
        self.text = ""
        self.canvas.place_forget()

    def show(self, text, cursor):
        import tkinter.font as tkfont
        self.text, self.cursor = text, cursor
        if not text or not self.target.editable():
            self.hide()
            return None
        box = self.target.caret_box()
        if not box:
            self.canvas.place_forget()
            return None
        x, y, line_height = box
        chrome = self.target.chrome()
        font_name = self.target.font()
        font = tkfont.Font(root=self.editor, font=font_name)
        full_width = font.measure(text) + 3
        inset = max(0, int(chrome.get("inset") or 0))
        limit = max(10, self.editor.winfo_width() - 2 * inset)
        width = min(full_width, limit)
        x = max(inset, min(int(x), self.editor.winfo_width() - width - 2))
        # Same font and line box as the text around it, so the preedit does not
        # look like a floating box pasted over the document.
        height = max(line_height, font.metrics("linespace") + 2)
        caret = font.measure(text[:max(0, min(cursor, len(text)))])
        offset = max(0, caret - width + 3)
        self.canvas.configure(bg=chrome["bg"], width=width, height=height)
        self.canvas.delete("all")
        self.canvas.create_text(-offset, 0, text=text, font=font_name,
                                fill=chrome["fg"], anchor="nw", tags="preedit")
        self.canvas.create_line(0, height - 1, width, height - 1,
                                fill=chrome["fg"], dash=(1, 2), tags="underline")
        self.canvas.create_line(caret - offset + 1, 1, caret - offset + 1, height - 2,
                                fill=chrome["caret"], tags="caret")
        # bbox()/dlineinfo() are in the widget's own coordinates, but place(-in=...)
        # puts a child inside the widget's *text area*, i.e. offset by its internal
        # padding (Text padx/pady). Placing in the master with the widget's origin
        # added keeps the preedit on the same baseline and at the same x as the
        # caret instead of one padding-step down and to the right.
        self.canvas.place(x=self.editor.winfo_x() + x, y=self.editor.winfo_y() + y,
                          width=width, height=height)
        # Canvas.lift is an item operation; use the Tk window stacking command.
        self.canvas.tk.call("raise", str(self.canvas))
        return x, y, width, height

    def refresh(self):
        if self.text:
            return self.show(self.text, self.cursor)
        self.canvas.configure(bg=self.target.chrome()["bg"])
        return None


class WindowHook:
    """The one Win32 subclass that serves every inline editor of a window.

    ``register``/``unregister`` are called by :class:`InlineIME`. Messages are
    forwarded to the inline editor that currently owns Tk focus, so the document
    editor and the search field can each draw their own preedit without
    competing for the native hook. The same hook owns the single Tk timer that
    drives every registered editor, because only the focused one can be
    composing.
    """

    def __init__(self, scheduler, interval=16):
        self.scheduler = scheduler
        self.interval = interval
        self.targets = []
        self.message_counts = {}
        self.ok = False
        self.error = None
        self._callback = None
        self._hwnd = None
        self._loop_job = None
        if os.name == "nt":
            try:
                self._install()
            except (AttributeError, OSError, ValueError) as exc:
                self.error = str(exc)

    # -- registry --------------------------------------------------------
    def register(self, target):
        if target not in self.targets:
            self.targets.append(target)
        self._ensure_loop()

    def unregister(self, target):
        if target in self.targets:
            self.targets.remove(target)
        if not self.targets:
            self._cancel_loop()
            self.detach()

    def close(self):
        """Stop the shared timer; safe to call twice and after widget teardown."""
        self._cancel_loop()
        try:
            self.detach()
        except Exception:
            pass

    def detach(self):
        if hasattr(self, "native"):
            self.native.detach()
        self.ok = False

    # -- shared poll loop ------------------------------------------------
    def _ensure_loop(self):
        if self._loop_job is None and self.targets:
            self._start_loop()

    def _cancel_loop(self):
        job, self._loop_job = self._loop_job, None
        if job is not None:
            try:
                self.scheduler.after_cancel(job)
            except Exception:
                pass

    def _start_loop(self):
        try:
            self._loop_job = self.scheduler.after(self.interval, self._loop)
        except Exception:
            self._loop_job = None

    def _loop(self):
        self._loop_job = None
        try:
            self.poll()
            # One frame per editor. Only the owner can hold a composition, so
            # the other editors usually do nothing, but an editor that just
            # received focus has to place its candidate list at once.
            for target in list(self.targets):
                if getattr(target, "ok", False):
                    target._tick()
        except Exception as exc:            # never leave the IME with a dead timer
            self.disable(str(exc))
        if self.targets:
            self._start_loop()

    def poll(self):
        """One frame for every editor: read the composition and reconcile it."""
        if not self.ok:
            for bridge in self._bridges():
                try:
                    bridge.active(False)
                except Exception:
                    pass
            return
        owner = self.owner()
        wanted = bool(owner and owner.ok and owner.editable)
        for bridge in self._bridges():
            try:
                bridge.active(wanted)
            except Exception:
                pass
        if not wanted or owner is None:
            return
        bridge = self._reader(owner)
        if bridge is None:
            return
        value = bridge.read()
        if value is not None:
            text, cursor, count = value
            owner.pending = (text, character_cursor(text, cursor))
            self.message_counts[WM_IME_COMPOSITION] = count

    def _reader(self, owner):
        """The bridge that reports the window's composition.

        The hook owns the real one; an editor may carry its own replacement,
        which is how tests script a composition without a live IME.
        """
        return getattr(owner, "native", None) or getattr(self, "native", None)

    def _bridges(self):
        """Every distinct bridge that has to hear about the active state."""
        found = []
        for bridge in [getattr(self, "native", None)] + \
                [getattr(target, "native", None) for target in self.targets]:
            if bridge is not None and not any(bridge is item for item in found):
                found.append(bridge)
        return found

    def owner(self):
        """The inline editor that currently owns Tk focus."""
        for target in self.targets:
            if target.focused:
                return target
        return None

    def disable(self, error):
        """Shared failure handling; Tk-side cleanup happens on the next timer tick."""
        self.error, self.ok = error, False
        for target in list(self.targets):
            target.error, target.ok = error, False
            target.pending = ("", 0)
        self._cancel_loop()

    def handle_message(self, message, wparam, lparam, forward):
        """Dispatch a window message to the focused editor.

        .. warning::
           **Not on the production path.** Since ``native_bridge.dll`` subclassed
           the toplevel in C, Windows messages are handled there (it strips
           ``ISC_SHOWUICOMPOSITIONWINDOW`` only while the window is *active*, and
           ``active`` is driven by Python's ``native.active(...)`` call from the
           poll loop). Nothing in the app calls this method; keep it only as the
           platform-independent statement of the protocol, and do not treat its
           behaviour as evidence about the running program — use the bridge's
           ``active``/``read`` surface instead.
        """
        if message == WM_KILLFOCUS:
            for target in self.targets:
                target.pending = ("", 0)
            return forward(lparam)
        target = self.owner()
        if target is None:
            return forward(lparam)
        return target.handle_message(message, wparam, lparam, forward)

    # -- native ----------------------------------------------------------
    def _install(self):
        import ctypes as c
        from ctypes import wintypes as w
        self._c = c
        self._imm = c.WinDLL("imm32", use_last_error=True)
        self._comctl = c.WinDLL("comctl32", use_last_error=True)
        self._user32 = c.WinDLL("user32", use_last_error=True)
        self._user32.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        self._user32.SendMessageW.restype = c.c_ssize_t
        self._imm.ImmGetContext.argtypes = [w.HWND]
        self._imm.ImmGetContext.restype = w.HANDLE
        self._imm.ImmReleaseContext.argtypes = [w.HWND, w.HANDLE]
        self._imm.ImmReleaseContext.restype = w.BOOL
        self._imm.ImmGetCompositionStringW.argtypes = [w.HANDLE, w.DWORD, c.c_void_p, w.DWORD]
        self._imm.ImmGetCompositionStringW.restype = w.LONG

        class CompositionForm(c.Structure):
            _fields_ = [("style", w.DWORD), ("point", w.POINT), ("area", w.RECT)]
        self._composition_form = CompositionForm
        self._imm.ImmSetCompositionWindow.argtypes = [w.HANDLE, c.POINTER(CompositionForm)]
        self._imm.ImmSetCompositionWindow.restype = w.BOOL

        class CandidateForm(c.Structure):
            _fields_ = [("index", w.DWORD), ("style", w.DWORD),
                        ("point", w.POINT), ("area", w.RECT)]
        self._candidate_form = CandidateForm
        self._imm.ImmSetCandidateWindow.argtypes = [w.HANDLE, c.POINTER(CandidateForm)]
        self._imm.ImmSetCandidateWindow.restype = w.BOOL
        # Tk_SetCaretPos in tkWinX.c walks to Tk_IsTopLevel and associates
        # IMM with that client HWND. Neither the Text HWND nor its outer
        # Windows decoration wrapper receives normal Tk IME composition.
        self._hwnd = self.scheduler.winfo_id()

        from .native_bridge import NativeBridge
        self.native = NativeBridge(self._hwnd)
        self.ok = True
        return self.native


def hook_for(scheduler):
    """The shared hook of one toplevel window."""
    hook = getattr(scheduler, HOOK_ATTRIBUTE, None)
    if hook is None or not hook.ok:
        hook = WindowHook(scheduler)
        try:
            setattr(scheduler, HOOK_ATTRIBUTE, hook)
        except Exception:
            pass
    return hook


class InlineIME:
    """Inline composition for one editable widget of a window.

    Tk owns native focus/IMM on the toplevel, so the widget itself never sees
    WM_IME_*; the shared :class:`WindowHook` routes messages to whichever
    registered widget currently has Tk focus.
    """
    ok = False
    editable = False
    focused = False
    pending = None
    error = None
    rect = None
    _placing = False
    _applied = None
    _forms_stale = True

    def __init__(self, editor, palette=None):
        self.editor = editor
        self.target = target_for(editor, palette)
        self.surface = CompositionSurface(self.target)
        self.scheduler = editor.winfo_toplevel()
        self.message_counts = {}
        self.hook = None
        if os.name == "nt":
            try:
                self.hook = hook_for(self.scheduler)
                self.hook.register(self)
                self.message_counts = self.hook.message_counts
                for name in ("_c", "_imm", "_comctl", "_user32", "_hwnd",
                             "_candidate_form", "_composition_form"):
                    if hasattr(self.hook, name):
                        setattr(self, name, getattr(self.hook, name))
                self.ok, self.error = self.hook.ok, self.hook.error
            except (AttributeError, OSError, ValueError) as exc:
                self.error = str(exc)
        editor.bind("<Configure>", lambda _e: self.refresh(), add="+")
        editor.bind("<FocusIn>", self._focus_in, add="+")
        editor.bind("<FocusOut>", self._focus_out, add="+")
        editor.bind("<Destroy>", self._destroy, add="+")
        self._focus_changed()

    # -- state -----------------------------------------------------------
    def set_editable(self, editable):
        self.editable = editable
        self.pending = None
        self.reset_placement()
        self.surface.hide()
        self._focus_changed()

    def reset_placement(self):
        self.rect = None
        self._applied = None
        self._forms_stale = True

    def _focus_in(self, _event=None):
        self.focused = True
        self._forms_stale = True
        if self.hook is not None:
            self.hook.poll()

    def _focus_out(self, _event=None):
        self.focused = False
        self.pending = None
        self.reset_placement()
        self.surface.hide()

    def _focus_changed(self, _event=None):
        """Re-read the focus this widget already has, without waiting for events."""
        if focused_widget(self.editor) is self.editor:
            self.focused = True
        else:
            self._focus_out()
        if self.focused and self.ok:
            # Tk focus can move between child controls without a native
            # WM_IME_SETCONTEXT. Reapply the composition flag for the widget
            # that now owns focus.
            self._user32.SendMessageW(self._hwnd, WM_IME_SETCONTEXT, 1, 0xC000000F)

    def close(self):
        """Drop this editor from the window hook and release its preedit surface.

        The composition canvas is a *sibling* of the editor (it has to be, or
        rendering the preview would delete it), so destroying the editor does not
        destroy the canvas: without the explicit cleanup here, closing a tab
        either left a pinyin box floating over the next document or leaked one
        Canvas per closed tab.
        """
        self.ok = False
        self.pending = None
        self.rect = None
        try:
            self.surface.hide()
        except Exception:
            pass
        try:
            self.surface.canvas.destroy()
        except Exception:
            pass
        hook, self.hook = self.hook, None
        if hook is not None:
            try:
                hook.unregister(self)
            except Exception:
                pass

    def _destroy(self, event):
        if event.widget is self.editor:
            self.close()

    # -- Tk side ---------------------------------------------------------
    def _tick(self):
        """One poll frame for this editor, driven by the hook's shared timer."""
        pending, self.pending = self.pending, None
        if pending is not None:
            try:
                self._paint(pending)
            except Exception as exc:
                self._fail(exc)
        else:
            self._track_caret()
            self._sync_forms()

    def _fail(self, exc):
        self.error, self.ok = str(exc), False
        self.reset_placement()
        self.surface.hide()
        try:
            self._user32.SendMessageW(self._hwnd, WM_IME_SETCONTEXT, 1, 0xC000000F)
        except Exception:
            pass

    def _paint(self, pending):
        box = self.surface.show(*pending)
        if box:
            self.rect = self._to_client(box)
        else:
            # Composition finished (or nothing to draw): remember where the
            # caret is, so the next composition starts with a placed candidate
            # list instead of one sitting on top of the pinyin.
            self._track_caret()
        self._sync_forms(force=True)

    def _track_caret(self):
        """Cache the caret rectangle while no composition is being drawn.

        The Win32 callback cannot ask Tk for it, and the IME wants the candidate
        position the moment a composition starts, before the next frame.
        """
        if not self.ok or not self.focused or self.surface.text:
            return
        box = self.target.caret_box()
        if not box:
            self.rect = None
            return
        x, y, line = box
        self.rect = self._to_client((x, y, 0, line))

    def _sync_forms(self, force=False):
        """Apply the composition/candidate rectangles — from Tk's side only.

        The imm32 calls below make the IME send messages back synchronously.
        Doing that inside the Win32 callback would let Tk re-enter from it, so
        the timer is the only place allowed to do it.
        """
        if not self.ok or not self.rect or not self.focused or not self.editable:
            return
        if not force and not self._forms_stale and self.rect == self._applied:
            return
        self._apply_forms()

    def _to_client(self, box):
        x, y, width, height = box
        return (x + self.editor.winfo_rootx() - self.scheduler.winfo_rootx(),
                y + self.editor.winfo_rooty() - self.scheduler.winfo_rooty(),
                width, height)

    def _read_preedit(self):
        context = self._imm.ImmGetContext(self._hwnd)
        if not context:
            return None
        try:
            size = self._imm.ImmGetCompositionStringW(context, GCS_COMPSTR, None, 0)
            if size < 0 or size > 65536:
                return None
            buffer = self._c.create_string_buffer(size + 2)
            actual = self._imm.ImmGetCompositionStringW(context, GCS_COMPSTR, buffer, size)
            if actual < 0:
                return None
            text = buffer.raw[:actual].decode("utf-16-le", errors="replace")
            cursor = self._imm.ImmGetCompositionStringW(context, GCS_CURSORPOS, None, 0)
            return text, character_cursor(text, cursor) if cursor >= 0 else len(text)
        finally:
            self._imm.ImmReleaseContext(self._hwnd, context)

    def _apply_forms(self):
        """Where the composition really is, and where the candidates must go.

        ``ImmSetCandidateWindow`` is the part that stops the candidate list from
        covering the pinyin: CFS_CANDIDATEPOS puts the list just below the
        composition instead of on top of it, and the composition rectangle is
        reported as the preedit box for input methods that place candidates from
        the composition window instead.
        """
        rect = self.rect
        if not self.ok or not rect or self._placing:
            return
        context = self._imm.ImmGetContext(self._hwnd)
        if not context:
            return
        # ImmSet*Window make the IME notify us synchronously; the guard keeps a
        # nested notification from starting a second round.
        self._placing = True
        try:
            x, y, width, height = (int(value) for value in rect)
            width, height = max(MIN_BOX_WIDTH, width), max(1, height)
            form = self._composition_form()
            form.style = CFS_RECT
            form.point.x, form.point.y = x, y + height
            form.area.left, form.area.top = x, y
            form.area.right, form.area.bottom = x + width, y + height
            self._imm.ImmSetCompositionWindow(context, self._c.byref(form))
            for index in range(CANDIDATE_LISTS):
                candidate = self._candidate_form()
                candidate.index = index
                candidate.style = CFS_CANDIDATEPOS
                candidate.point.x, candidate.point.y = x, y + height + CANDIDATE_GAP
                candidate.area.left, candidate.area.top = x, y
                candidate.area.right, candidate.area.bottom = x + width, y + height
                self._imm.ImmSetCandidateWindow(context, self._c.byref(candidate))
        finally:
            self._placing = False
            self._applied, self._forms_stale = rect, False
            self._imm.ImmReleaseContext(self._hwnd, context)

    def refresh(self):
        if self.surface.text:
            self._paint((self.surface.text, self.surface.cursor))
        else:
            self.surface.refresh()
            self._track_caret()

    # -- Win32 side ------------------------------------------------------
    def handle_message(self, message, wparam, lparam, forward):
        """Per-editor message semantics. **Not on the production path.**

        ``native_bridge.dll`` owns the real subclass proc now; see
        :meth:`WindowHook.handle_message`. The equivalence that still matters in
        production is the gate below — ``editable``/``focused`` decide whether
        the window stays "active" for the IME — because that flag drives
        ``native.active(...)`` and therefore whether the C side strips
        ``ISC_SHOWUICOMPOSITIONWINDOW`` (a false flag brings back Windows' own
        white composition box).

        No Tcl/Tk calls here, and no imm32 calls either: both re-enter code that
        is already on the stack. The caller only records what to do; the Tk timer
        paints and places.
        """
        if message in (WM_IME_ENDCOMPOSITION, WM_KILLFOCUS):
            self.pending = ("", 0)
        if not self.ok or not self.editable or not self.focused:
            return forward(lparam)
        if message == WM_IME_SETCONTEXT:
            return forward(lparam & ~ISC_SHOWUICOMPOSITIONWINDOW)
        if message == WM_IME_STARTCOMPOSITION:
            # Ask the timer to place the candidate list right away: it can show
            # up before the first painted frame.
            self._forms_stale = True
            self.pending = ("", 0)
            return 0
        if message == WM_IME_ENDCOMPOSITION:
            return 0
        if message == WM_IME_COMPOSITION:
            if lparam & GCS_RESULTSTR:
                self.pending = ("", 0)
                # Tk is the only writer: never synthesize an extra insertion.
                if lparam & GCS_COMPSTR:
                    pending = self._read_preedit()
                    if pending is not None:
                        self.pending = pending
                return forward(lparam)
            if lparam == 0:
                self.pending = ("", 0)
                return 0
            pending = self._read_preedit()
            if pending is None:
                raise RuntimeError("IME composition unavailable; restoring native input")
            self.pending = pending
            self._forms_stale = True
            return 0
        return forward(lparam)
