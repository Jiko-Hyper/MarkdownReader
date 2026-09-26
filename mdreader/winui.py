# -*- coding: utf-8 -*-
"""Pure-tkinter Markdown preview + navigation window for MDReader.

Standard library only (tkinter, os, sys, re, html, webbrowser, unicodedata,
datetime).  Two layers:

* ``parse_markdown`` turns Markdown into a flat list of ``(text, tags)``
  segments and never touches tkinter, so it is unit-testable head-less.
* :class:`MarkdownWindow` renders those segments into a ``tk.Text`` widget and
  adds project / document navigation, editing, saving and theming.

The document parser is line based and deliberately small: it covers what a
local Markdown reader needs (headings, lists, quotes, code, tables, front
matter, a useful inline subset).  Links open in the default browser on click.

``python -m mdreader.winui`` runs a head-less parser self-check;
``python -m mdreader.winui --gui [--workspace DIR]`` opens the window.
"""

from __future__ import annotations

import datetime
import html as _html
import json
import os
import re
import sys
import unicodedata

from . import core
from . import formatting as FM
from . import media as MD
from . import tables as TB
from .document_ui import DocumentActions

UI_FONT = "Microsoft YaHei UI"
UI_FONT_FALLBACK = "Segoe UI"
MONO_FONT = "Consolas"
MONO_FONT_FALLBACK = "Courier New"
UI_SIZE = 10          # chrome font size
PREVIEW_SIZE = 12     # base size of the rendered document
TAB_STEP = 20         # marker -> item text distance in the list tags

GUTTER = "\u2502 "    # blockquote gutter
CHECK_OFF = "\u2610"
CHECK_ON = "\u2611"
RAY = "\u2500"        # horizontal rule / table separator
BULLETS = ("\u2022", "\u25e6", "\u25aa")
MASK_L, MASK_R = "\ue000", "\ue001"   # placeholders used while parsing

LIGHT = {
    "bg": "#ffffff", "fg": "#1f2328", "muted": "#59636e", "faint": "#8c959f",
    "sel": "#b6dcff", "code_bg": "#f2f4f7", "code_fg": "#1f2328",
    "quote_bg": "#f7f8fa", "quote_fg": "#57606a", "accent": "#0969da",
    "rule": "#d0d7de", "chip_bg": "#fff3cd", "side": "#f6f8fa",
    "tree_bg": "#f6f8fa", "head": "#0a3069",
    "button": "#e9eef3", "hover": "#dce5ee", "thumb": "#bac5d0",
}
DARK = {
    "bg": "#22262d", "fg": "#dce1e8", "muted": "#a8b1be", "faint": "#8994a3",
    "sel": "#3b5068", "code_bg": "#2b313a", "code_fg": "#dce1e8",
    "quote_bg": "#29313b", "quote_fg": "#b9c4d1", "accent": "#9bbde3",
    "rule": "#3b434f", "chip_bg": "#514833", "side": "#292e36",
    "tree_bg": "#292e36", "head": "#e3eaf2",
    "button": "#343c47", "hover": "#414d5d", "thumb": "#576374",
}
EYE = {
    "bg": "#f3efdf", "fg": "#394538", "muted": "#5d6a56", "faint": "#74806b",
    "sel": "#d1dec1", "code_bg": "#e7e7d6", "code_fg": "#394538",
    "quote_bg": "#e8ecdc", "quote_fg": "#53634d", "accent": "#4c6b43",
    "rule": "#cbd0b9", "chip_bg": "#e5dbad", "side": "#e6e9d8",
    "tree_bg": "#e6e9d8", "head": "#344c2f",
    "button": "#d9e0cb", "hover": "#c9d5b9", "thumb": "#a6b398",
}
THEMES = {"light": LIGHT, "dark": DARK, "eye": EYE}
THEME_LABELS = {"light": "明亮", "dark": "夜间", "eye": "护眼"}


def read_ui_preferences(workspace):
    """Workspace preferences; the theme is shared with the browser view."""
    return core.read_ui_settings(workspace)


def _text_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(_s(text).encode("utf-8")).hexdigest()


def tab_needs_save(tab):
    loose = tab.get("loose") or {}
    return bool(tab.get("dirty") or (loose and not loose.get("path")))


def tab_title(tab):
    info = tab.get("loose") or tab.get("doc") or {}
    title = "Untitled" if tab.get("loose") and not info.get("path") else (
        info.get("file") or info.get("name") or "Untitled")
    if len(title) > 25:
        title = title[:22] + "…"
    return ("#" if tab_needs_save(tab) else "") + title


class SavePrompt:
    """Themed, centred save/discard/cancel prompt owned by the app window."""

    def __init__(self, parent, title, pal, scale=1, *, deleting=False):
        import tkinter as tk
        self.result = None
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("确认删除" if deleting else "是否保存")
        self.default_button = "取消" if deleting else "是"
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.configure(background=pal["bg"])
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.finish(None))
        self.window.bind("<Escape>", lambda _e: self.finish(None))
        body = tk.Frame(self.window, bg=pal["bg"], padx=round(26 * scale), pady=round(22 * scale))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="确认删除本地内容？" if deleting else "是否保存文档？", bg=pal["bg"], fg=pal["fg"],
                 font=(UI_FONT, 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text=title if deleting else "「%s」尚未保存。\n选择“否”将放弃未保存的内容。" % title,
                 bg=pal["bg"], fg=pal["muted"], justify="left", anchor="w",
                 wraplength=round(380 * scale), pady=round(16 * scale)).pack(fill="x")
        actions = tk.Frame(body, bg=pal["bg"])
        actions.pack(fill="x")
        self.buttons = {}
        choices = (("取消", None), ("删除并移入回收站", True)) if deleting else (("取消", None), ("否", False), ("是", True))
        for label, answer in choices:
            primary = answer is True
            button = tk.Button(actions, text=label, width=16 if deleting and primary else 7, relief="flat", bd=0, pady=7,
                               bg=pal["accent"] if primary else pal["button"],
                               fg=pal["bg"] if primary else pal["fg"],
                               activebackground=pal["accent"] if primary else pal["hover"],
                               activeforeground=pal["bg"] if primary else pal["fg"],
                               highlightbackground=pal["bg"], highlightcolor=pal["accent"],
                               command=lambda value=answer: self.finish(value))
            button.pack(side="right", padx=(8, 0))
            button.bind("<Return>", lambda _e, value=answer: self.finish(value))
            self.buttons[label] = button
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.window.geometry("%dx%d+%d+%d" % (width, height, max(0, x), max(0, y)))
        from .display import style_titlebar
        style_titlebar(self.window, pal == DARK, pal["bg"], pal["fg"])

    def finish(self, result):
        self.result = result
        self.window.destroy()

    def show(self):
        self.window.deiconify()
        self.window.wait_visibility()
        previous_grab = self.window.grab_current()
        self.window.grab_set()
        self.buttons[self.default_button].focus_set()
        self.window.wait_window()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        return self.result


class FolderArrows:
    """Right-aligned equilateral expanders with a clockwise quarter-turn."""
    def __init__(self, owner, tree):
        self.owner, self.tree = owner, tree
        self.canvas = owner._tk.Canvas(tree, width=owner.px(28), bd=0, highlightthickness=0)
        self.canvas.place(relx=1, x=-2, y=2, anchor="ne", relheight=1, height=-4)
        self.angles = {}
        self.pending = None
        self.canvas.bind("<Button-1>", self.click)
        self.canvas.bind("<MouseWheel>", self.wheel)
        for event in ("<Configure>", "<<TreeviewSelect>>", "<<TreeviewOpen>>", "<<TreeviewClose>>"):
            tree.bind(event, lambda _e: self.schedule(), add="+")

    def wheel(self, event):
        self.tree.yview_scroll(-int(event.delta / 120) * 3, "units")
        return "break"

    def schedule(self):
        if self.pending is None:
            self.pending = self.owner.root.after_idle(self.draw)

    def draw(self):
        import math
        self.pending = None
        pal = self.owner.pal
        self.canvas.configure(bg=pal["tree_bg"])
        self.canvas.delete("all")
        seen = set()
        for y in range(0, self.tree.winfo_height(), max(1, self.owner.px(8))):
            row = self.tree.identify_row(y)
            if not row or row in seen:
                continue
            seen.add(row)
            box = self.tree.bbox(row)
            if not box:
                continue
            _, top, _, height = box
            if row in self.tree.selection():
                self.canvas.create_rectangle(0, top - 2, self.owner.px(28), top + height - 2,
                                             fill=pal["sel"], outline="")
            if not self.tree.get_children(row):
                continue
            angle = self.angles.get(row, 90 if self.tree.item(row, "open") else 0)
            cx, cy, radius = self.owner.px(14), top + height / 2 - 2, self.owner.px(6)
            points = []
            for offset in (0, 120, 240):
                radians = math.radians(angle + offset)
                points.extend((cx + radius * math.cos(radians), cy + radius * math.sin(radians)))
            self.canvas.create_polygon(*points, fill=pal["fg"], outline="", tags=(row,))

    def click(self, event):
        row = self.tree.identify_row(event.y + 2)
        if row and self.tree.get_children(row):
            self.tree.selection_set(row)
            self.tree.focus(row)
            self.tree.focus_set()
            self.toggle(row)
        return "break"

    def toggle(self, row):
        if row in self.angles:
            return
        opened = bool(self.tree.item(row, "open"))
        self.tree.item(row, open=not opened)
        start, finish = (90, 0) if opened else (0, 90)
        def step(frame=0):
            if not self.tree.exists(row):
                self.angles.pop(row, None)
                return
            self.angles[row] = start + (finish - start) * frame / 6
            self.draw()
            if frame < 6:
                self.owner.root.after(20, lambda: step(frame + 1))
            else:
                self.angles.pop(row, None)
        step()


class RenamePrompt:
    """Theme-aware title input, centred on the owning application window."""

    def __init__(self, parent, title, pal, scale=1, *, project_parent=None):
        import tkinter as tk
        self.result = None
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        heading = "新建项目" if project_parent is not None else "更改标题"
        self.window.title(heading)
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.configure(bg=pal["bg"])
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.finish(None))
        self.window.bind("<Escape>", lambda _e: self.finish(None))
        body = tk.Frame(self.window, bg=pal["bg"], padx=round(26 * scale), pady=round(22 * scale))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=heading, bg=pal["bg"], fg=pal["fg"],
                 font=(UI_FONT, 14, "bold"), anchor="w").pack(fill="x")
        hint = "项目名称：" if project_parent is not None else "输入新标题，文件扩展名会自动保留。"
        tk.Label(body, text=hint, bg=pal["bg"],
                 fg=pal["muted"], anchor="w", pady=round(12 * scale)).pack(fill="x")
        self.entry = tk.Entry(body, width=36, font=(UI_FONT, UI_SIZE),
                              bg=pal["tree_bg"], fg=pal["fg"], insertbackground=pal["fg"],
                              selectbackground=pal["sel"], selectforeground=pal["fg"],
                              relief="flat", bd=0, highlightthickness=1,
                              highlightbackground=pal["rule"], highlightcolor=pal["accent"])
        self.entry.pack(fill="x", ipady=round(8 * scale))
        self.entry.insert(0, title)
        self.entry.selection_range(0, "end")
        self.entry.bind("<Return>", lambda _e: self.finish(self.entry.get()))
        if project_parent is not None:
            from tkinter import filedialog
            self.location = tk.StringVar(self.window, value=project_parent)
            tk.Label(body, text="保存位置（将在此处新建项目文件夹）：", bg=pal["bg"],
                     fg=pal["muted"], anchor="w", pady=round(12 * scale)).pack(fill="x")
            location_row = tk.Frame(body, bg=pal["bg"])
            location_row.pack(fill="x")
            self.location_entry = tk.Entry(location_row, textvariable=self.location,
                bg=pal["tree_bg"], fg=pal["fg"], insertbackground=pal["fg"],
                selectbackground=pal["sel"], selectforeground=pal["fg"],
                relief="flat", highlightthickness=1, highlightbackground=pal["rule"],
                highlightcolor=pal["accent"], font=(UI_FONT, UI_SIZE))
            self.location_entry.pack(side="left", fill="x", expand=True, ipady=round(8 * scale))
            def browse():
                folder = filedialog.askdirectory(title="选择项目保存位置", parent=self.window,
                                                 initialdir=self.location.get(), mustexist=True)
                if folder:
                    self.location.set(folder)
            tk.Button(location_row, text="浏览…", command=browse, relief="flat", bd=0,
                      bg=pal["button"], fg=pal["fg"], activebackground=pal["hover"],
                      activeforeground=pal["fg"], padx=10, pady=7).pack(side="right", padx=(8, 0))
        actions = tk.Frame(body, bg=pal["bg"])
        actions.pack(fill="x", pady=(round(20 * scale), 0))
        self.buttons = {}
        for label, primary in (("确定", True), ("取消", False)):
            command = (lambda: self.finish(self.entry.get())) if primary else (lambda: self.finish(None))
            button = tk.Button(actions, text=label, width=8, relief="flat", bd=0, pady=7,
                               bg=pal["accent"] if primary else pal["button"],
                               fg=pal["bg"] if primary else pal["fg"],
                               activebackground=pal["accent"] if primary else pal["hover"],
                               activeforeground=pal["bg"] if primary else pal["fg"],
                               highlightbackground=pal["bg"], highlightcolor=pal["accent"],
                               command=command)
            button.pack(side="right", padx=(8, 0))
            button.bind("<Return>", lambda _e, run=command: run())
            self.buttons[label] = button
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.window.geometry("%dx%d+%d+%d" % (width, height, max(0, x), max(0, y)))
        from .display import style_titlebar
        style_titlebar(self.window, pal == DARK, pal["bg"], pal["fg"])

    def finish(self, result):
        self.result = result
        self.window.destroy()

    def show(self):
        self.window.deiconify()
        self.window.wait_visibility()
        previous_grab = self.window.grab_current()
        self.window.grab_set()
        self.entry.focus_set()
        self.window.wait_window()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        return self.result


class InfoDialog:
    """Themed, centred read-only message box（关于 / 快捷键说明）。

    ``lines`` holds plain sentences, or ``(label, value)`` pairs that are laid
    out as a two-column list (used for the shortcut table).
    """

    def __init__(self, parent, title, heading, lines, pal, scale=1):
        import tkinter as tk
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title(title)
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.configure(background=pal["bg"])
        self.window.protocol("WM_DELETE_WINDOW", self.finish)
        self.window.bind("<Escape>", lambda _e: self.finish())
        body = tk.Frame(self.window, bg=pal["bg"], padx=round(26 * scale), pady=round(22 * scale))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=heading, bg=pal["bg"], fg=pal["fg"],
                 font=(UI_FONT, 14, "bold"), anchor="w").pack(fill="x")
        stack = tk.Frame(body, bg=pal["bg"])
        stack.pack(fill="x", pady=round(14 * scale))
        for item in lines:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                row = tk.Frame(stack, bg=pal["bg"])
                row.pack(fill="x", pady=1)
                tk.Label(row, text=_s(item[0]), bg=pal["bg"], fg=pal["fg"], anchor="w",
                         width=11, font=(UI_FONT, UI_SIZE)).pack(side="left")
                tk.Label(row, text=_s(item[1]), bg=pal["bg"], fg=pal["muted"],
                         anchor="w", justify="left").pack(side="left")
            else:
                tk.Label(stack, text=_s(item), bg=pal["bg"], fg=pal["muted"], anchor="w",
                         justify="left", wraplength=round(430 * scale)).pack(fill="x", pady=1)
        self.button = tk.Button(body, text="确定", width=8, relief="flat", bd=0, pady=7,
                                bg=pal["accent"], fg=pal["bg"], activebackground=pal["hover"],
                                activeforeground=pal["bg"], highlightbackground=pal["bg"],
                                highlightcolor=pal["accent"], command=self.finish)
        self.button.pack(side="right")
        self.button.bind("<Return>", lambda _e: self.finish())
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        self.window.geometry("%dx%d+%d+%d" % (width, height, max(0, x), max(0, y)))
        from .display import style_titlebar
        style_titlebar(self.window, pal == DARK, pal["bg"], pal["fg"])

    def finish(self):
        self.window.destroy()

    def show(self):
        self.window.deiconify()
        self.window.wait_visibility()
        previous_grab = self.window.grab_current()
        self.window.grab_set()
        self.button.focus_set()
        self.window.wait_window()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()


class PathDialog(InfoDialog):
    def __init__(self, parent, path, pal, scale=1):
        import tkinter as tk
        super().__init__(parent, "查看文件地址", "文件地址", [], pal, scale)
        body = self.button.master
        value = tk.StringVar(self.window, value=path)
        self.entry = tk.Entry(body, textvariable=value, state="readonly", width=52,
                              font=(UI_FONT, UI_SIZE), readonlybackground=pal["tree_bg"],
                              fg=pal["fg"], selectbackground=pal["sel"], selectforeground=pal["fg"],
                              relief="flat", highlightthickness=1,
                              highlightbackground=pal["rule"], highlightcolor=pal["accent"])
        self.entry.pack(before=self.button, fill="x", ipady=round(8 * scale), pady=(0, round(18 * scale)))
        self.entry.selection_range(0, "end")
        def copy():
            self.window.clipboard_clear()
            self.window.clipboard_append(path)
        tk.Button(body, text="复制地址", command=copy, relief="flat", bd=0, pady=7, padx=12,
                  bg=pal["button"], fg=pal["fg"], activebackground=pal["hover"],
                  activeforeground=pal["fg"]).pack(side="right", padx=8)
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.window.geometry("%dx%d+%d+%d" % (width, height, max(0, x), max(0, y)))


ALIGN_CHOICES = (("left", "左对齐"), ("center", "居中"), ("right", "右对齐"))
ALIGN_BY_LABEL = {label: key for key, label in ALIGN_CHOICES}
ALIGN_LABEL = {key: label for key, label in ALIGN_CHOICES}

#: 对话框里一次显示的表格规模。超出的表格不进对话框（保留源码，提示直接改文本），
#: 而不是只显示一部分——那样一提交就会把没显示出来的行列删掉。
DIALOG_MAX_ROWS = 60
DIALOG_MAX_COLUMNS = 16


class TableDialog:
    """插入或编辑管道表格：列数、数据行数、表头与逐列对齐。

    单元格只接收单行文本：带换行的内容会被明确拒绝（走 ``tables.check_cell``），
    不会为了“能插进去”而把换行写进 Markdown。提交结果由窗口当作**一次编辑**
    写回编辑器，因此一次插入/一次结构调整对应一次撤销。
    """

    def __init__(self, parent, pal, scale=1, *, mode="insert", table=None,
                 columns=3, rows=2, header=True):
        import tkinter as tk
        from tkinter import ttk
        self._tk = tk
        self._ttk = ttk
        self.result = None
        self.mode = mode
        columns = max(1, min(TB.MAX_COLUMNS, int(columns or 1)))
        source = table or {}
        self.model = {
            "header": list(source.get("header") or [""] * columns),
            "rows": [list(row) for row in (source.get("rows") or [[""] * columns for _ in range(max(0, int(rows)))])],
            "aligns": list(source.get("aligns") or ["left"] * columns),
        }
        if not self.model["header"]:
            self.model["header"] = [""] * columns
        if not self.model["rows"] and mode == "insert":
            self.model["rows"] = [[""] * columns for _ in range(max(0, int(rows)))]
        self.header_row = bool(header) if mode == "insert" else any(self.model["header"])
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("插入表格" if mode == "insert" else "编辑表格")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.configure(background=pal["bg"])
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.finish(None))
        self.window.bind("<Escape>", lambda _e: self.finish(None))
        body = tk.Frame(self.window, bg=pal["bg"], padx=round(20 * scale), pady=round(18 * scale))
        body.pack(fill="both", expand=True)

        tk.Label(body, text="插入 Markdown 表格" if mode == "insert" else "编辑这张表格",
                 bg=pal["bg"], fg=pal["fg"], font=(UI_FONT, 13, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text="单元格只支持单行文本；内容里的竖线和反斜杠会自动转义。",
                 bg=pal["bg"], fg=pal["muted"], anchor="w", justify="left",
                 wraplength=round(460 * scale)).pack(fill="x", pady=(round(6 * scale), round(10 * scale)))

        settings = tk.Frame(body, bg=pal["bg"])
        settings.pack(fill="x", pady=(0, round(8 * scale)))
        self.columns_var = tk.IntVar(self.window, value=len(self.model["header"]))
        self.rows_var = tk.IntVar(self.window, value=len(self.model["rows"]))
        self.header_var = tk.BooleanVar(self.window, value=self.header_row)
        tk.Label(settings, text="列数", bg=pal["bg"], fg=pal["fg"]).pack(side="left")
        self.columns_box = tk.Spinbox(settings, from_=1, to=TB.MAX_COLUMNS, width=4,
                                      textvariable=self.columns_var, command=self.on_counts_changed,
                                      bg=pal["tree_bg"], fg=pal["fg"], relief="flat", justify="center",
                                      buttonbackground=pal["button"])
        self.columns_box.pack(side="left", padx=(6, 14))
        tk.Label(settings, text="数据行数", bg=pal["bg"], fg=pal["fg"]).pack(side="left")
        self.rows_box = tk.Spinbox(settings, from_=0, to=TB.MAX_ROWS, width=5,
                                   textvariable=self.rows_var, command=self.on_counts_changed,
                                   bg=pal["tree_bg"], fg=pal["fg"], relief="flat", justify="center",
                                   buttonbackground=pal["button"])
        self.rows_box.pack(side="left", padx=(6, 14))
        self.header_check = tk.Checkbutton(settings, text="首行作表头", variable=self.header_var,
                                           command=self.on_counts_changed, bg=pal["bg"], fg=pal["fg"],
                                           selectcolor=pal["tree_bg"], activebackground=pal["bg"],
                                           activeforeground=pal["fg"], highlightthickness=0, bd=0)
        self.header_check.pack(side="left")

        holder = tk.Frame(body, bg=pal["bg"])
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bd=0, highlightthickness=0, bg=pal["bg"],
                                height=round(210 * scale), width=round(470 * scale))
        bar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.grid_host = tk.Frame(self.canvas, bg=pal["bg"])
        self.canvas.create_window((0, 0), window=self.grid_host, anchor="nw")
        self.grid_host.bind("<Configure>",
                            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        actions = tk.Frame(body, bg=pal["bg"])
        actions.pack(fill="x", pady=(round(14 * scale), 0))
        self.buttons = {}
        for label, answer in (("取消", None), ("应用" if mode == "edit" else "插入", True)):
            primary = answer is True
            button = tk.Button(actions, text=label, width=10, relief="flat", bd=0, pady=7,
                               bg=pal["accent"] if primary else pal["button"],
                               fg=pal["bg"] if primary else pal["fg"],
                               activebackground=pal["accent"] if primary else pal["hover"],
                               activeforeground=pal["bg"] if primary else pal["fg"],
                               highlightbackground=pal["bg"], highlightcolor=pal["accent"],
                               command=lambda value=answer: self.finish(value))
            button.pack(side="right", padx=(8, 0))
            self.buttons[label] = button
        self.pal, self.scale = pal, scale
        self._rebuild()
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.window.geometry("%dx%d+%d+%d" % (width, height, max(0, x), max(0, y)))
        from .display import style_titlebar
        style_titlebar(self.window, pal == DARK, pal["bg"], pal["fg"])

    # -- 表格内容 --------------------------------------------------------
    def _entry(self, parent, value, width=14):
        tk = self._tk
        var = tk.StringVar(self.window, value=value)
        entry = tk.Entry(parent, textvariable=var, width=width, font=(UI_FONT, UI_SIZE),
                         bg=self.pal["tree_bg"], fg=self.pal["fg"], relief="flat",
                         highlightthickness=1, highlightbackground=self.pal["rule"],
                         highlightcolor=self.pal["accent"], insertbackground=self.pal["fg"])
        return var, entry

    def _rebuild(self):
        tk = self._tk
        for child in list(self.grid_host.winfo_children()):
            child.destroy()
        pal, scale = self.pal, self.scale
        columns = len(self.model["header"])
        self.align_vars, self.header_vars, self.row_vars = [], [], []
        for index in range(columns):
            tk.Label(self.grid_host, text="第 %d 列" % (index + 1), bg=pal["bg"], fg=pal["muted"]
                     ).grid(row=0, column=index, padx=2, pady=(0, 2), sticky="w")
        for index in range(columns):
            var = tk.StringVar(self.window, value=ALIGN_LABEL.get(self.model["aligns"][index], "左对齐"))
            box = self._ttk.Combobox(self.grid_host, values=[label for _k, label in ALIGN_CHOICES],
                                        textvariable=var, state="readonly", width=8)
            box.grid(row=1, column=index, padx=2, pady=(0, 6))
            self.align_vars.append(var)
        for index, value in enumerate(self.model["header"]):
            var, entry = self._entry(self.grid_host, value)
            entry.grid(row=2, column=index, padx=2, pady=2, sticky="ew")
            self.header_vars.append(var)
        tk.Label(self.grid_host, text="表头行（不勾选“首行作表头”时留空）", bg=pal["bg"],
                 fg=pal["muted"]).grid(row=3, column=0, columnspan=max(1, columns), sticky="w", pady=(2, 4))
        for r, row in enumerate(self.model["rows"]):
            vars_row = []
            for c in range(columns):
                var, entry = self._entry(self.grid_host, row[c] if c < len(row) else "")
                entry.grid(row=4 + r, column=c, padx=2, pady=2, sticky="ew")
                vars_row.append(var)
            self.row_vars.append(vars_row)

    def _collect(self):
        """把控件里的内容读回模型（切换行列数之前必须先做这一步）。"""
        if not self.header_vars:
            return
        for index, var in enumerate(self.header_vars):
            self.model["header"][index] = var.get()
        for r, vars_row in enumerate(self.row_vars):
            for c, var in enumerate(vars_row):
                self.model["rows"][r][c] = var.get()
        for index, var in enumerate(self.align_vars):
            self.model["aligns"][index] = ALIGN_BY_LABEL.get(var.get(), "left")

    def on_counts_changed(self):
        self._collect()
        columns = max(1, min(TB.MAX_COLUMNS, int(self.columns_var.get() or 1)))
        rows = max(0, min(TB.MAX_ROWS, int(self.rows_var.get() or 0)))
        header = list(self.model["header"])[:columns] + [""] * max(0, columns - len(self.model["header"]))
        aligns = list(self.model["aligns"])[:columns] + ["left"] * max(0, columns - len(self.model["aligns"]))
        body = []
        for index in range(rows):
            row = list(self.model["rows"][index]) if index < len(self.model["rows"]) else []
            body.append(row[:columns] + [""] * max(0, columns - len(row)))
        self.model = {"header": header, "rows": body, "aligns": aligns}
        self._rebuild()

    def finish(self, answer):
        if answer is True:
            self._collect()
            problem = None
            for group in (self.model["header"], *self.model["rows"]):
                for value in group:
                    problem = problem or TB.check_cell(value)
            if problem:
                from tkinter import messagebox
                messagebox.showwarning("表格内容有问题", problem, parent=self.window)
                return
            columns = len(self.model["header"])
            header = self.model["header"] if self.header_var.get() else [""] * columns
            self.result = {"columns": columns, "rows": len(self.model["rows"]),
                           "has_header": bool(self.header_var.get()),
                           "header": list(header), "aligns": list(self.model["aligns"]),
                           "fills": [list(header)] + [list(row) for row in self.model["rows"]]}
        else:
            self.result = None
        self.window.destroy()

    def show(self):
        self.window.deiconify()
        self.window.wait_visibility()
        previous_grab = self.window.grab_current()
        self.window.grab_set()
        self.buttons["插入" if self.mode == "insert" else "应用"].focus_set()
        self.window.wait_window()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        return self.result


class TablePasteDialog:
    """粘贴前先看清楚：几列几行、哪些内容不能原样处理。"""

    def __init__(self, parent, pal, scale=1, *, parsed, header=True):
        import tkinter as tk
        self._tk = tk
        self.result = None
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("粘贴为表格")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.configure(background=pal["bg"])
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.finish(None))
        self.window.bind("<Escape>", lambda _e: self.finish(None))
        body = tk.Frame(self.window, bg=pal["bg"], padx=round(22 * scale), pady=round(18 * scale))
        body.pack(fill="both", expand=True)
        rows = parsed.get("rows") or []
        tk.Label(body, text="粘贴为表格：%d 列 × %d 行" % (parsed.get("columns", 0), len(rows)),
                 bg=pal["bg"], fg=pal["fg"], font=(UI_FONT, 13, "bold"), anchor="w").pack(fill="x")
        warnings = parsed.get("warnings") or []
        if warnings:
            tk.Label(body, text="\n".join("· " + message for message in warnings[:4]),
                     bg=pal["bg"], fg=pal["muted"], justify="left", anchor="w",
                     wraplength=round(420 * scale)).pack(fill="x", pady=(round(8 * scale), 0))
        preview = tk.Text(body, height=min(10, max(3, len(rows))), width=48, bd=0, wrap="none",
                          font=(MONO_FONT, UI_SIZE), bg=pal["tree_bg"], fg=pal["fg"],
                          relief="flat", highlightthickness=1, highlightbackground=pal["rule"])
        preview.pack(fill="both", expand=True, pady=round(10 * scale))
        preview.insert("1.0", "\n".join("\t".join(row) for row in rows[:40]))
        preview.configure(state="disabled", tabs=tuple(round(110 * scale) * (n + 1) for n in range(8)))
        self.header_var = tk.BooleanVar(self.window, value=bool(header))
        tk.Checkbutton(body, text="第一行作表头", variable=self.header_var, bg=pal["bg"], fg=pal["fg"],
                       selectcolor=pal["tree_bg"], activebackground=pal["bg"], activeforeground=pal["fg"],
                       highlightthickness=0, bd=0).pack(anchor="w")
        actions = tk.Frame(body, bg=pal["bg"])
        actions.pack(fill="x", pady=(round(12 * scale), 0))
        self.buttons = {}
        for label, answer in (("取消", None), ("插入表格", True)):
            primary = answer is True
            button = tk.Button(actions, text=label, width=10, relief="flat", bd=0, pady=7,
                               bg=pal["accent"] if primary else pal["button"],
                               fg=pal["bg"] if primary else pal["fg"],
                               activebackground=pal["accent"] if primary else pal["hover"],
                               activeforeground=pal["bg"] if primary else pal["fg"],
                               highlightbackground=pal["bg"], highlightcolor=pal["accent"],
                               command=lambda value=answer: self.finish(value))
            button.pack(side="right", padx=(8, 0))
            self.buttons[label] = button
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.window.geometry("%dx%d+%d+%d" % (width, height, max(0, x), max(0, y)))
        from .display import style_titlebar
        style_titlebar(self.window, pal == DARK, pal["bg"], pal["fg"])

    def finish(self, answer):
        self.result = {"header": bool(self.header_var.get())} if answer is True else None
        self.window.destroy()

    def show(self):
        self.window.deiconify()
        self.window.wait_visibility()
        previous_grab = self.window.grab_current()
        self.window.grab_set()
        self.buttons["插入表格"].focus_set()
        self.window.wait_window()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        return self.result


class ExportSourceDialog:
    """未保存时的来源选择（F06）：默认「当前编辑内容」。"""

    def __init__(self, parent, pal, scale=1, *, name=""):
        import tkinter as tk
        self.result = None
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("导出哪一份内容")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.configure(background=pal["bg"])
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.finish(None))
        self.window.bind("<Escape>", lambda _e: self.finish(None))
        body = tk.Frame(self.window, bg=pal["bg"], padx=round(24 * scale), pady=round(20 * scale))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="这篇文档有未保存的修改", bg=pal["bg"], fg=pal["fg"],
                 font=(UI_FONT, 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text=("「%s」还没有保存到磁盘。导出用哪一份内容？\n"
                            "导出不会替你保存源文件；选好之后导出的是那一刻的固定内容。"
                            % (name or "当前文档")),
                 bg=pal["bg"], fg=pal["muted"], justify="left", anchor="w",
                 wraplength=round(420 * scale)).pack(fill="x", pady=(round(10 * scale), round(16 * scale)))
        actions = tk.Frame(body, bg=pal["bg"])
        actions.pack(fill="x")
        self.buttons = {}
        for label, value, primary in (("取消", None, False),
                                      ("磁盘已保存版本", "disk", False),
                                      ("当前编辑内容（推荐）", "buffer", True)):
            button = tk.Button(actions, text=label, relief="flat", bd=0, pady=7, padx=12,
                               bg=pal["accent"] if primary else pal["button"],
                               fg=pal["bg"] if primary else pal["fg"],
                               activebackground=pal["accent"] if primary else pal["hover"],
                               activeforeground=pal["bg"] if primary else pal["fg"],
                               highlightbackground=pal["bg"], highlightcolor=pal["accent"],
                               command=lambda item=value: self.finish(item))
            button.pack(side="right", padx=(8, 0))
            self.buttons[value] = button
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.window.geometry("%dx%d+%d+%d" % (width, height, max(0, x), max(0, y)))
        from .display import style_titlebar
        style_titlebar(self.window, pal == DARK, pal["bg"], pal["fg"])

    def finish(self, value):
        self.result = value
        self.window.destroy()

    def show(self):
        self.window.deiconify()
        self.window.wait_visibility()
        previous_grab = self.window.grab_current()
        self.window.grab_set()
        self.buttons["buffer"].focus_set()
        self.window.wait_window()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        return self.result


class ExportReportDialog:
    """导出预检报告（F06）：错误必须返回修改，提示可以确认继续。"""

    def __init__(self, parent, pal, scale=1, *, dest="", report=None, allow_confirm=True):
        import tkinter as tk
        self.result = False
        report = report or {}
        errors = report.get("errors") or []
        warnings = report.get("warnings") or []
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("导出前检查")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.configure(background=pal["bg"])
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.finish(False))
        self.window.bind("<Escape>", lambda _e: self.finish(False))
        body = tk.Frame(self.window, bg=pal["bg"], padx=round(22 * scale), pady=round(18 * scale))
        body.pack(fill="both", expand=True)
        heading = ("有必须先处理的问题" if errors else
                   ("可以继续，但有几条提示" if warnings else "没有发现问题"))
        tk.Label(body, text=heading, bg=pal["bg"], fg=pal["fg"] if not errors else pal["accent"],
                 font=(UI_FONT, 14, "bold"), anchor="w").pack(fill="x")
        lines = []
        for item in errors:
            lines.append("✗ %s" % item["message"])
        for item in warnings:
            lines.append("· %s" % item["message"])
        stats = report.get("stats") or {}
        if stats:
            lines.append("")
            lines.append("文档：%s 字 / %s 行 · 图片 %s · 公式 %s · 表格 %s · 链接 %s"
                         % (stats.get("chars", 0), stats.get("lines", 0), stats.get("images", 0),
                            stats.get("formulas", 0), stats.get("tables", 0), stats.get("links", 0)))
        if dest:
            lines.append("导出到：%s" % dest)
        box = tk.Text(body, height=min(12, max(4, len(lines) + 1)), width=54, bd=0, wrap="word",
                      font=(UI_FONT, UI_SIZE), bg=pal["tree_bg"], fg=pal["fg"], relief="flat",
                      highlightthickness=1, highlightbackground=pal["rule"])
        box.pack(fill="both", expand=True, pady=round(10 * scale))
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")
        actions = tk.Frame(body, bg=pal["bg"])
        actions.pack(fill="x")
        self.buttons = {}
        choices = [("返回修改", False, not errors and not warnings)]
        if allow_confirm and not errors:
            choices.append(("仍然导出（按提示）" if warnings else "开始导出", True, True))
        for label, value, primary in choices:
            button = tk.Button(actions, text=label, relief="flat", bd=0, pady=7, padx=14,
                               bg=pal["accent"] if primary else pal["button"],
                               fg=pal["bg"] if primary else pal["fg"],
                               activebackground=pal["accent"] if primary else pal["hover"],
                               activeforeground=pal["bg"] if primary else pal["fg"],
                               highlightbackground=pal["bg"], highlightcolor=pal["accent"],
                               command=lambda item=value: self.finish(item))
            button.pack(side="right", padx=(8, 0))
            self.buttons[label] = button
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.window.geometry("%dx%d+%d+%d" % (width, height, max(0, x), max(0, y)))
        from .display import style_titlebar
        style_titlebar(self.window, pal == DARK, pal["bg"], pal["fg"])

    def finish(self, value):
        self.result = bool(value)
        self.window.destroy()

    def show(self):
        self.window.deiconify()
        self.window.wait_visibility()
        previous_grab = self.window.grab_current()
        self.window.grab_set()
        list(self.buttons.values())[0].focus_set()
        self.window.wait_window()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        return self.result


class LinkReportDialog:
    """链接检查报告（F09）：列出问题，可以定位源码或重新选择文件。"""

    def __init__(self, parent, pal, scale=1, *, report=None, name=""):
        import tkinter as tk
        self.result = None
        report = report or {}
        issues = report.get("issues") or []
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("检查当前文档链接")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.configure(background=pal["bg"])
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.finish({"action": None}))
        self.window.bind("<Escape>", lambda _e: self.finish({"action": None}))
        body = tk.Frame(self.window, bg=pal["bg"], padx=round(22 * scale), pady=round(18 * scale))
        body.pack(fill="both", expand=True)
        errors = report.get("errors") or []
        warnings = report.get("warnings") or []
        tk.Label(body, text=report.get("summary") or "检查完成", bg=pal["bg"],
                 fg=pal["accent"] if errors else pal["fg"],
                 font=(UI_FONT, 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text=("「%s」：检查了 %d 处本地引用（外部网址不检查）。\n"
                            "双击一条可以跳到源码；图片找不到时可以重新选一个文件。"
                            % (name or "当前文档", report.get("checked", 0))),
                 bg=pal["bg"], fg=pal["muted"], justify="left", anchor="w",
                 wraplength=round(470 * scale)).pack(fill="x", pady=(round(8 * scale), round(10 * scale)))
        holder = tk.Frame(body, bg=pal["bg"])
        holder.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(holder, height=min(12, max(4, len(issues))), width=64, bd=0,
                                  activestyle="none", selectmode="browse", exportselection=False,
                                  bg=pal["tree_bg"], fg=pal["fg"], relief="flat",
                                  highlightthickness=1, highlightbackground=pal["rule"],
                                  font=(UI_FONT, UI_SIZE))
        self.listbox.pack(side="left", fill="both", expand=True)
        bar = self._tk_scrollbar(holder, pal)
        bar.pack(side="right", fill="y")
        self.issues = issues
        for item in issues:
            mark = "✗" if item["level"] == "error" else "·"
            self.listbox.insert("end", "%s 第 %d 行 · %s · %s" % (mark, item["line"],
                                                                 item["source"], item["message"]))
        if issues:
            self.listbox.selection_set(0)
        self.listbox.bind("<Double-1>", lambda _e: self.finish({"action": "locate",
                                                               "index": self.selected()}))
        actions = tk.Frame(body, bg=pal["bg"])
        actions.pack(fill="x", pady=(round(12 * scale), 0))
        self.buttons = {}
        for label, action, primary in (("关闭", None, False),
                                       ("重新选择文件…", "choose", False),
                                       ("定位到源码", "locate", True)):
            button = tk.Button(actions, text=label, relief="flat", bd=0, pady=7, padx=12,
                               bg=pal["accent"] if primary else pal["button"],
                               fg=pal["bg"] if primary else pal["fg"],
                               activebackground=pal["accent"] if primary else pal["hover"],
                               activeforeground=pal["bg"] if primary else pal["fg"],
                               highlightbackground=pal["bg"], highlightcolor=pal["accent"],
                               command=lambda item=action: self.finish({"action": item,
                                                                        "index": self.selected()}))
            button.pack(side="right", padx=(8, 0))
            self.buttons[label] = button
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.window.geometry("%dx%d+%d+%d" % (width, height, max(0, x), max(0, y)))
        from .display import style_titlebar
        style_titlebar(self.window, pal == DARK, pal["bg"], pal["fg"])

    def _tk_scrollbar(self, parent, pal):
        from tkinter import ttk
        return ttk.Scrollbar(parent, orient="vertical", command=self.listbox.yview)

    def selected(self) -> int:
        picked = self.listbox.curselection()
        return int(picked[0]) if picked else 0

    def finish(self, value):
        self.result = value or {"action": None}
        self.window.destroy()

    def show(self):
        self.window.deiconify()
        self.window.wait_visibility()
        previous_grab = self.window.grab_current()
        self.window.grab_set()
        self.buttons["定位到源码"].focus_set()
        self.window.wait_window()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        return self.result


SHORTCUTS = (
    ("Ctrl+N", "新建临时文档"),    ("Ctrl+O", "打开本地文件"),
    ("Ctrl+S", "保存（临时文档写回原文件）"),
    ("Ctrl+W", "关闭当前标签"),
    ("Ctrl+Tab", "切换标签"),
    ("Ctrl+E", "预览 / 源码"),
    ("Ctrl+B", "显示 / 隐藏侧栏"),
    ("Delete", "项目树：删除选中的文件或文件夹（需确认）"),
    ("Ctrl+M", "项目树：更改选中目标的标题"),
    ("双击标题", "项目树：更改标题并同步本地名称"),
    ("Ctrl+Y", "项目树：查看选中目标的文件地址"),
    ("Enter", "项目树：打开选中的文档"),
    ("F5", "刷新当前文档"),
    ("Ctrl+滚轮", "调整正文字号"),
    ("Ctrl+Shift+T", "在光标处插入一张表格"),
    ("Ctrl+Shift+M", "插入行内数学公式"),
    ("Ctrl+Shift+V", "把剪贴板里的截图插到光标处（需图片插入插件）"),
    ("拖入窗口", "直接阅读并修改 .md 原文件；拖入图片则插到光标处"),
)

_FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})[ \t]*([^\s`]*)")
_ATX_RE = re.compile(r"^[ \t]{0,3}(#{1,6})(?=[ \t]|$)[ \t]*(.+?)[ \t]*#*[ \t]*$")
_HR_RE = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
_TABLE_SEP_RE = re.compile(r"^ {0,3}\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)*\|?[ \t]*$")
_LI_RE = re.compile(r"^([ \t]*)([-*+]|\d{1,9}[.)])([ \t]+|$)(.*)$")
_TASK_RE = re.compile(r"^\[([ xX])\][ \t]+")
# A line that clearly opens a new block, used to end a paragraph.
_BLOCK_RE = re.compile(r"^[ \t]*((#{1,6}[ \t])|(```|~~~)|(>|\[!\w+\])|([-*+][ \t])"
                       r"|(\d{1,9}[.)][ \t])|((?:[-*_][ \t]*){3,}$))")


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------

def _s(value) -> str:
    """str() that survives None, broken __str__ and odd objects."""
    if value is None:
        return ""
    try:
        return value if isinstance(value, str) else str(value)
    except Exception:
        return ""


def char_width(ch: str) -> int:
    """Display columns of one character: CJK wide/fullwidth count as two."""
    if ch == "\t":
        return 4
    o = ord(ch)
    if o < 0x300 or 0x2000 <= o <= 0x206F:
        return 1
    try:
        return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    except Exception:
        return 1


def display_width(text: str) -> int:
    return sum(char_width(ch) for ch in _s(text))


def truncate_display(text: str, limit: int) -> str:
    """Cut to ``limit`` display columns, appending an ellipsis when cut."""
    if limit <= 0:
        return ""
    if display_width(text) <= limit:
        return text
    out, used = [], 0
    for ch in text:
        w = char_width(ch)
        if used + w > max(1, limit - 1):
            break
        out.append(ch)
        used += w
    return "".join(out) + "\u2026"


def _strip_markers(text: str) -> str:
    """Approximate plain text (markers dropped) — used for table cells."""
    s = _html.unescape(_s(text))
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]", lambda m: m.group(2) or m.group(1), s)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"~~([^~]*)~~", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"(?<=\S)_(?=\S)", "", s)
    s = re.sub(r"(?<=\S)\*(?=\S)", "", s)
    s = re.sub(r"<[^>]{0,200}>", "", s)
    s = re.sub(r"^[ \t]*([-*+][ \t]+|\d{1,9}[.)][ \t]+)", "", s)
    return s.strip()


def _cell_text(text: str, width: int) -> str:
    """One aligned table field: ``width`` display columns plus a space."""
    plain = truncate_display(_strip_markers(text), width)
    return plain + " " * (max(0, width - display_width(plain)) + 1)


def squeeze(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _s(text)).strip()


# --------------------------------------------------------------------------
# block parser
# --------------------------------------------------------------------------

class _Parser:
    """Line based Markdown -> [(text, (tag, ...)), ...]."""

    def __init__(self, text: str):
        self.lines = _s(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.out: list[tuple[str, tuple[str, ...]]] = []
        self.meta: dict = {}
        self.tokens: list[dict] = []
        self.link_tags: dict[str, str] = {}     # target -> the one tag that carries it

    # -- segment helpers -------------------------------------------------
    def add(self, text: str, *tags: str) -> None:
        if text:
            self.out.append((text, tags))

    def blank(self, half: bool = False) -> None:
        self.out.append(("\n", ("blank_lo" if half else "blank",)))

    def rule(self) -> None:
        self.out.append((RAY * 78 + "\n", ("rule",)))

    def peek(self, i: int) -> str | None:
        """First non-blank line at or after ``i`` (consumes nothing)."""
        while i < len(self.lines) and not self.lines[i].strip():
            i += 1
        return self.lines[i] if i < len(self.lines) else None

    def para(self, text: str, extra: tuple = ()) -> None:
        if text.strip():
            self.out.extend(tokenize_inline(text, self, extra))
            self.add("\n", *extra)

    def head(self, text: str, level: int) -> None:
        """Headings reuse the inline parser so code spans, entities and stray
        HTML tags behave exactly as they do in prose."""
        for chunk, tags in tokenize_inline(text, self):
            self.out.append((chunk, ("h%d" % level,) + tags))
        self.add("\n", "h%d" % level)

    def _gap(self) -> None:
        """A small blank line before a block, unless one is already pending."""
        if self.out and self.out[-1][1] not in (("blank",), ("blank_lo",)):
            self.blank(True)

    # -- entry point -----------------------------------------------------
    def run(self) -> tuple[list[tuple[str, tuple[str, ...]]], dict]:
        try:
            from .render import split_front_matter
            meta, body = split_front_matter(_s("\n".join(self.lines)))
            if meta:
                self.meta = meta
                self.lines = body.replace("\r\n", "\n").split("\n")
        except Exception:
            self.meta = {}
        for key, value in self.meta.items():        # compact front-matter strip
            self.add("%s: %s\n" % (key, value), "meta")
        if self.meta:
            self.rule()
            self.blank(True)

        lines, i = self.lines, 0
        while i < len(lines):
            line = lines[i]
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            m = _FENCE_RE.match(line)
            if not line.strip():
                self.blank()
                i += 1
            elif _HR_RE.match(line):
                self.rule()
                i += 1
            elif line.lstrip().startswith("$$"):
                i = self.formula_block(i)
            elif m:
                i = self.code_fence(i, m.group(2), m.group(3))
            elif (m := _ATX_RE.match(line)):
                self.head(squeeze(m.group(2)), len(m.group(1)))
                i += 1
            elif len(nxt) >= 2 and len(set(nxt)) == 1 and nxt[0] in "=-":
                self.head(squeeze(line), 1 if nxt[0] == "=" else 2)   # setext
                i += 2
            elif line.lstrip().startswith(">") and len(line) - len(line.lstrip()) <= 3:
                i = self.quote(i)
            elif _LI_RE.match(line[:160]):
                i = self.list(i, 0)
            elif re.match(r"^ {4,}\S", line) and self.peek(i + 1) is not None:
                i = self.code_indented(i)
            elif self.table_at(i):
                i = self.table(i)
            else:
                i = self.paragraph(i)
        return self.out, self.meta

    # -- blocks ----------------------------------------------------------
    def code_fence(self, i: int, fence: str, info: str):
        n, body, j = len(self.lines), [], i + 1
        close = fence[0] * len(fence)
        while j < n:
            raw = self.lines[j]
            if raw.strip().startswith(close) and set(raw.strip()) <= {fence[0], " ", "\t"}:
                j += 1
                break
            body.append(raw.expandtabs(4))
            j += 1
        self._gap()
        lang = info.strip().split()[0].lower()[:20] if info.strip() else ""
        tag = "cb_%s" % (re.sub(r"[^a-z0-9_+#.-]", "", lang) or "txt")
        for ln in body:
            self.add(ln + "\n", "code", tag)
        self.add("\n", "code", "blank_lo", tag)
        return j

    def code_indented(self, i: int):
        body, j = [], i
        while j < len(self.lines):
            raw = self.lines[j]
            if not raw.strip():
                k = j
                while k < len(self.lines) and not self.lines[k].strip():
                    k += 1
                if k < len(self.lines) and re.match(r"^ {4,}\S", self.lines[k]):
                    body.append("")
                    j += 1
                    continue
                break
            if not re.match(r"^ {4,}", raw):
                break
            body.append(raw[4:].rstrip())
            j += 1
        if body:
            self._gap()
            for ln in body:
                self.add(ln + "\n", "code", "cb_txt")
            self.add("\n", "code", "blank_lo", "cb_txt")
        return max(j, i + 1)

    def quote(self, i: int):
        run, j = [], i
        while j < len(self.lines):
            raw = self.lines[j]
            if raw.lstrip().startswith(">") and len(raw) - len(raw.lstrip()) <= 3:
                content = raw.lstrip()[1:]
                run.append(content[1:] if content.startswith(" ") else content)
                j += 1
            elif not raw.strip():
                run.append("")
                j += 1
            else:
                break
        while run and not run[-1].strip():
            run.pop()
        for ln in run:
            self.add(GUTTER, "quote")
            if ln.strip():
                self.para(ln.rstrip(), ("quote",))
            else:
                self.add("\n", "quote")
        return j

    def list(self, i: int, base: int):
        j, item_base = i, None
        while j < len(self.lines):
            raw = self.lines[j]
            if not raw.strip():
                nxt = self.peek(j + 1)
                nm = _LI_RE.match(nxt[:160]) if nxt else None
                if not (nm and len(nm.group(1).expandtabs(4)) > (item_base or base)):
                    break
                self.blank(True)
                j += 1
                continue
            m = _LI_RE.match(raw[:160])
            if m:
                ind = len(m.group(1).expandtabs(4))
                if ind < base:
                    break
                item_base = ind if item_base is None else item_base
                depth = min(5, max(0, (ind - item_base) // 2 + (1 if base else 0)))
                marker, text = m.group(2), m.group(4)
                if marker[0] in "-*+":
                    tm = _TASK_RE.match(text)
                    if tm:
                        bullet = CHECK_ON if tm.group(1).lower() == "x" else CHECK_OFF
                        text = text[tm.end():]
                    else:
                        bullet = BULLETS[depth % len(BULLETS)]
                else:
                    bullet = marker if marker.endswith(".") else marker + "."
                self.add(bullet + "\t", "list", "list_%d" % depth)
                self.para(text.rstrip(), ("li", "li_%d" % depth))
                j += 1
                continue
            if re.match(r"^[ \t]{2,}\S", raw):      # continuation of an item
                self.para(raw.strip(), ("li", "li_%d" % min(5, (item_base or base) // 2)))
                j += 1
                continue
            break
        return j

    def table_at(self, i: int) -> bool:
        if i + 1 >= len(self.lines) or "|" not in self.lines[i]:
            return False
        sep = self.lines[i + 1].strip()
        return "|" in sep and "-" in sep and bool(_TABLE_SEP_RE.match(sep))

    def table(self, i: int):
        def cells(line: str) -> list[str]:
            s = line.strip()
            s = s[1:] if s.startswith("|") else s
            s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
            return [c.replace("\\|", "|").strip() for c in s.split("|")]

        header, aligns = cells(self.lines[i]), []
        for spec in cells(self.lines[i + 1]):
            left, right = spec.startswith(":"), spec.endswith(":")
            aligns.append("center" if left and right else ("right" if right else "left"))
        rows, j = [], i + 2
        while j < len(self.lines) and self.lines[j].strip() and "|" in self.lines[j] \
                and len(rows) < 300:
            rows.append(cells(self.lines[j]))
            j += 1
        ncol = max(len(header), max((len(r) for r in rows), default=0))
        if ncol <= 0:
            return max(j, i + 1)
        header, aligns = (header + [""] * ncol)[:ncol], (aligns + ["left"] * ncol)[:ncol]

        # Keep the semantic cells as well as the text fallback.  The native
        # preview consumes this token to build a real grid of labels; callers
        # of parse_markdown still receive readable plain text.
        table_token = len(self.tokens)
        self.tokens.append({"kind": "table", "header": header,
                            "rows": [(r + [""] * ncol)[:ncol] for r in rows],
                            "aligns": aligns})

        widths = []
        for c in range(ncol):
            values = [header[c]] + [(r + [""] * ncol)[c] for r in rows]
            widest = max((max((display_width(p) for p in _strip_markers(v).split("\n")), default=0)
                          for v in values), default=0)
            widths.append(max(2, min(40, widest)))
        guard = 0
        while sum(widths) > 150 - 3 * ncol and guard < 400:
            widest = max(range(ncol), key=lambda k: widths[k])
            if widths[widest] <= 2:
                break
            widths[widest] -= 1
            guard += 1

        def line(values: list[str], tag: str) -> None:
            values = (values + [""] * ncol)[:ncol]
            self.add("".join(_cell_text(v, widths[c]) + "|" for c, v in enumerate(values)) + "\n",
                     tag, "tbl", "__table_%d" % table_token)

        self._gap()
        line(header, "th")
        self.add("".join(RAY * (w + 1) + "+" for w in widths) + "\n", "tbl_sep", "tbl",
                 "__table_%d" % table_token)
        for r in rows:
            line(r, "td")
        self.add("\n", "tbl_end", "blank_lo", "__table_%d" % table_token)
        return j

    def formula_block(self, i: int):
        """``$$…$$`` 独立公式块（可以跨行）。

        **没有闭合的 ``$$`` 按普通正文处理**：宁可把这两行当成文字，也不能把后面
        整篇文档都吞进公式里（用例 `test_unclosed_display_formula_does_not_eat_...`
        锁的就是这条）。
        """
        stripped = self.lines[i].strip()
        body = stripped[2:]
        j = i + 1
        if body.endswith("$$") and len(body) >= 2:
            tex, end = body[:-2], j
        else:
            parts, closed = [body], False
            while j < len(self.lines):
                item = self.lines[j].strip()
                if item.endswith("$$"):
                    parts.append(item[:-2])
                    j += 1
                    closed = True
                    break
                parts.append(self.lines[j])
                j += 1
            if not closed:
                return i + 1
            tex, end = "\n".join(parts), j
        tex = tex.strip()
        self._gap()
        self.add("$$%s$$" % tex, "formula", FORMULA_TAG + "d" + tex)
        self.add("\n", "blank_lo")
        return max(end, i + 1)

    def paragraph(self, i: int):
        buf, j = [], i
        while j < len(self.lines):
            raw = self.lines[j]
            if not raw.strip() or (buf and (_BLOCK_RE.match(raw) or re.match(r"^ {4,}\S", raw))):
                break
            body = raw.strip()
            if body.endswith("  "):
                body += "\n"        # two trailing spaces = hard line break
            buf.append(re.sub(r"[ \t]{2,}$", "", body))
            j += 1
        start = 0
        for ln in buf:              # hard-broken lines stay separate paragraphs
            if not ln.endswith("\n"):
                break
            self.para(ln, ("p",))
            start += 1
        rest = " ".join(buf[start:])
        if rest.strip():
            self.para(rest, ("p",))
        return max(j, i + 1)


# --------------------------------------------------------------------------
# inline parser
# --------------------------------------------------------------------------

def _link_tag(parser: _Parser, target: str, base: tuple) -> tuple[str, ...]:
    """Register a link target and return the tag tuple that carries it.

    One tag per *distinct* target, not per occurrence: Tk binds a Tcl command
    for every ``tag_bind``, so a document that repeats a link thousands of times
    used to build thousands of tags and re-bind all of them on each restyle.
    Links that share a target also share a colour, a cursor and an open action,
    so nothing observable changes.
    """
    target = _s(target).strip()
    if not target:
        return base
    tag = parser.link_tags.get(target)
    if tag is None:
        parser.tokens.append({"kind": "link", "url": target})
        tag = parser.link_tags[target] = "__link_%d" % (len(parser.tokens) - 1)
    return base + (tag,)


def tokenize_inline(text: str, parser: _Parser, base: tuple = ()) -> list[tuple[str, tuple[str, ...]]]:
    """Split one line of prose into styled segments.

    ``matchers`` is tried in order at every position holding an inline marker;
    the first pattern that matches consumes text and yields segments.  A handler
    returning ``[]`` means "not a marker here", so the character stays literal.
    Code spans are matched first, which is what keeps ``**`` inside them inert.
    """
    out: list[tuple[str, tuple[str, ...]]] = []

    def emph(tag):
        return lambda t, m, b: [(_html.unescape(m.group(1)), b + (tag,))]

    def code(t, m, b):
        if not m.group(2).strip():
            return []
        return [(" " + _html.unescape(m.group(2).strip()) + " ", ("code",))]

    def angle(t, m, b):
        """One rule for ``<...>``: autolinks become links, other tags are dropped."""
        raw = m.group(0)
        auto = re.match(r"<((?:https?|mailto|file|ftp)[^>\s]+)>$", raw)
        if auto:
            return [(auto.group(1), _link_tag(parser, auto.group(1), b))]
        return [] if re.match(r"</?[A-Za-z!]", raw) else [(_html.unescape(raw), b)]

    def entity(t, m, b):
        return [(_html.unescape(m.group(0)), b)]

    def image(t, m, b):
        return [("\U0001f5bc " + (_html.unescape(m.group(1)).strip() or "图片") + " ", ("img",)),
                ("\u2039" + m.group(2) + "\u203a", ("img_path",))]

    def formula(t, m, b):
        """行内公式：交给预览渲染成图片；识别不了的（首尾空白、跨行）保持原文。"""
        raw = m.group(0)
        tex = m.group(1)
        if not tex.strip() or tex[:1].isspace() or tex[-1:].isspace():
            return []
        return [(raw, ("formula", FORMULA_TAG + "i" + tex.strip()))]

    def wiki_image(t, m, b):
        return [("\U0001f5bc " + (m.group(2) or m.group(1)), ("img",)),
                ("(" + m.group(1) + ")", ("img_path",))]

    def wikilink(t, m, b):
        return [(_html.unescape((m.group(2) or m.group(1)).strip()),
                 _link_tag(parser, m.group(1), b))]

    def link(t, m, b):
        return [(_html.unescape(m.group(1)).strip() or m.group(2),
                 _link_tag(parser, m.group(2), b))]

    matchers = (
        (re.compile(r"(`+)(.+?)\1", re.S), code),
        (re.compile(r"(?<!\\)\$(?!\$)([^\n$]+?)(?<!\\)\$(?!\$)"), formula),
        (re.compile(r"<[^<>\n]{0,400}>"), angle),
        (re.compile(r"!\[\[([^\]|]+)(?:\|([^\]]*))?\]\]"), wiki_image),
        (re.compile(r"!\[([^\]]*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)"), image),
        (re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]"), wikilink),
        (re.compile(r"\[([^\]\n]*(?:\[[^\]\n]*\][^\]\n]*)*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)"), link),
        (re.compile(r"\*{3}(?=\S)(.+?)(?<=\S)\*{3}", re.S), emph("bi")),
        (re.compile(r"\*{2}(?=\S)(.+?)(?<=\S)\*{2}", re.S), emph("b")),
        (re.compile(r"\*(?=[^\s*])(.+?)(?<=[^\s*])\*", re.S), emph("i")),
        (re.compile(r"_{2}(?=\S)(.+?)(?<=\S)_{2}(?![A-Za-z0-9])", re.S), emph("b")),
        (re.compile(r"_(?=\S)(.+?)(?<=\S)_(?![A-Za-z0-9])", re.S), emph("i")),
        (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S), emph("s")),
        (re.compile(r"==(?=\S)(.+?)(?<=\S)==", re.S), emph("mark")),
        (re.compile(r"&(?:#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"), entity),
    )
    stops = "*_`[!<~=&$"

    i = 0
    while i < len(text):
        if text[i] not in stops:
            nxt = min([p for p in (text.find(c, i + 1) for c in stops) if p >= 0] or [len(text)])
            out.append((_html.unescape(text[i:nxt]), base))
            i = nxt
            continue
        if text[i] == "_" and i and text[i - 1].isalnum():
            out.append((text[i], base))             # intra-word underscore is literal
            i += 1
            continue
        for pattern, handler in matchers:
            m = pattern.match(text, i)
            if m:
                produced = handler(text, m, base)
                if produced:
                    out.extend(produced)
                    i = m.end()
                    break
        else:
            out.append((text[i], base))
            i += 1
    return [(t, tags) for t, tags in out if t]


def parse_markdown(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Parse Markdown into ``(text, tags)`` segments.  No tkinter involved."""
    parser = _Parser(text)
    return _restore(parser.run()[0], parser.tokens)


def parse_document(text: str) -> tuple[list[tuple[str, tuple[str, ...]]], dict, dict]:
    """Like :func:`parse_markdown`, plus front matter and link targets."""
    parser = _Parser(text)
    segments, meta = parser.run()
    links = {"__link_%d" % k: t["url"] for k, t in enumerate(parser.tokens)
             if t.get("kind") == "link"}
    return _restore(segments, parser.tokens), meta, links


def _parse_document_for_widget(text: str):
    """Internal rich parse result used by the tkinter renderer."""
    parser = _Parser(text)
    segments, meta = parser.run()
    links = {"__link_%d" % k: t["url"] for k, t in enumerate(parser.tokens)
             if t.get("kind") == "link"}
    tables = {"__table_%d" % k: t for k, t in enumerate(parser.tokens)
              if t.get("kind") == "table"}
    return _restore(segments, parser.tokens), meta, links, tables


def _restore(segments: list[tuple[str, tuple[str, ...]]], tokens: list[dict]) -> list[tuple[str, tuple[str, ...]]]:
    """Re-hide emphasis markers that were protected inside ``code`` spans."""
    masked = {"%s%d%s" % (MASK_L, k, MASK_R): (t["text"], tuple(t["tags"]))
              for k, t in enumerate(tokens) if t.get("kind") == "chip"}
    if not masked:
        return segments
    out: list[tuple[str, tuple[str, ...]]] = []
    for text, tags in segments:
        for placeholder, (chunk, chunk_tags) in masked.items():
            if placeholder in text:
                head, _, text = text.partition(placeholder)
                if head:
                    out.append((head, tags))
                out.append((chunk, chunk_tags))
        if text:
            out.append((text, tags))
    return out


# --------------------------------------------------------------------------
# rendering into a tk.Text
# --------------------------------------------------------------------------

_FONT_CACHE: dict = {}
_FAMILY_CACHE: dict = {}


def _font(base, size: int, weight: str = "normal", slant: str = "roman", mono: bool = False):
    """Font tuple, falling back when the preferred family is unavailable.

    Resolving a family costs a Tcl round trip and ``tkfont.Font`` builds a Tcl
    font command per call, yet one restyle asks for the same handful of faces
    dozens of times.  Both answers are therefore memoised per widget: a Tk
    widget hashes by its stable Tcl path, so the entry lives exactly as long as
    the widget that owns it, and the tuple returned here is what Tk compares
    against the tag's current value to decide the tag needs no work.
    """
    key = (base, bool(mono))
    cached = _FONT_CACHE.get(key)
    if cached is None:
        cached = _FONT_CACHE[key] = {}
    spec = (size, weight, slant)
    value = cached.get(spec)
    if value is None:
        family = _FAMILY_CACHE.get(key)
        if family is None:
            try:
                import tkinter.font as tkfont
                tkfont.Font(root=base, family=MONO_FONT if mono else UI_FONT,
                            size=size, weight=weight, slant=slant)
                family = MONO_FONT if mono else UI_FONT
            except Exception:
                family = MONO_FONT_FALLBACK if mono else UI_FONT_FALLBACK
            _FAMILY_CACHE[key] = family
        value = cached[spec] = ((family, size, weight, slant) if slant != "roman"
                                else (family, size, weight))
    return value


def _open_url(url: str) -> None:
    if not url:
        return
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def _set_cursor(widget, name: str) -> None:
    try:
        widget.configure(cursor=name)
    except Exception:
        pass


def _style_link(widget, tag: str, pal: dict) -> None:
    """Colour/underline a link tag; a plain click opens its target.

    ``cursor`` is a widget option (not a tag option), hence the enter/leave
    bindings that swap the widget cursor instead.
    """
    widget.tag_configure(tag, foreground=pal["accent"], underline=True)
    url = (getattr(widget, "_links", {}) or {}).get(tag, "")
    widget.tag_bind(tag, "<Button-1>", lambda _e, u=url: _open_url(u))
    widget.tag_bind(tag, "<Enter>", lambda _e: _set_cursor(widget, "hand2"))
    widget.tag_bind(tag, "<Leave>", lambda _e: _set_cursor(widget, "xterm"))


def ensure_link_tags(widget, links: dict, pal: dict) -> None:
    """Bring link tags up to date without re-walking the whole link set.

    Each link tag owns three Tcl command bindings, so styling every target on
    every restyle is what made a zoom gesture O(links): a 1 MB document with
    ~15k links spent ~2.3 s per wheel tick in here alone.  The palette identity
    decides whether the existing tags still have the right colours, and only
    tags that no restyle has touched yet are bound.
    """
    if not links:
        return
    styled = getattr(widget, "_styled_links", None)
    if styled == (pal, len(links)):
        return
    known = widget.tag_names() if styled is None else set(widget.tag_names())
    if styled is None or styled[0] is not pal:
        for tag in links:                   # a new palette repaints every target
            _style_link(widget, tag, pal)
    else:
        for tag in links:                   # same palette: only the new targets
            if tag not in known:
                if tag not in widget.tag_names():
                    widget.tag_configure(tag)
                _style_link(widget, tag, pal)
    widget._styled_links = (pal, len(links))             # type: ignore[attr-defined]


def _tag_plan(widget, pal: dict, base_size: int) -> tuple[list, list]:
    """(theme options, font options) for every tag, computed once per size.

    ``theme`` entries carry colours, margins and spacing; ``font`` entries carry
    a face.  Only the font half has to be re-sent when the reader changes the
    text size, and Tk compares both values against the tag's current options, so
    re-sending an unchanged entry costs a dictionary compare instead of a
    re-layout.
    """
    body, mono = _font(widget, base_size), _font(widget, base_size - 1, mono=True)
    small = (body[0], max(4, base_size - 5))
    theme: list = [
        ("p", {"foreground": pal["fg"], "spacing1": 2, "spacing3": 7}),
        ("placeholder", {"foreground": pal["faint"], "lmargin1": 6, "lmargin2": 6,
                         "spacing1": 4, "spacing3": 4}),
        ("code", {"background": pal["code_bg"], "foreground": pal["code_fg"], "lmargin1": 16,
                  "lmargin2": 16, "wrap": "none", "spacing1": 2, "spacing3": 2}),
        ("cb_txt", {"background": pal["code_bg"], "foreground": pal["code_fg"], "lmargin1": 16,
                    "lmargin2": 16, "wrap": "none", "spacing1": 2, "spacing3": 2}),
        ("quote", {"foreground": pal["quote_fg"], "background": pal["quote_bg"], "lmargin1": 14,
                   "lmargin2": 14, "spacing1": 3, "spacing3": 5}),
        ("s", {"overstrike": True, "foreground": pal["muted"]}),
        ("mark", {"background": pal["chip_bg"], "foreground": pal["fg"]}),
        ("img", {"foreground": pal["accent"]}),
        ("img_path", {"foreground": pal["faint"]}),
        ("meta", {"foreground": pal["muted"], "lmargin1": 6, "lmargin2": 6,
                  "spacing1": 1, "spacing3": 1}),
        ("tbl", {"lmargin1": 4, "lmargin2": 4, "spacing1": 0, "spacing3": 1, "wrap": "none"}),
        ("th", {"foreground": pal["head"], "background": pal["code_bg"]}),
        ("td", {"foreground": pal["fg"]}),
        ("tbl_sep", {"foreground": pal["rule"]}),
    ]
    fonts: list = [
        ("p", {"font": body}),
        ("rule", {"font": mono}),
        ("blank", {"font": (body[0], max(4, base_size - 5))}),
        ("blank_lo", {"font": (body[0], max(3, base_size - 6))}),
        ("placeholder", {"font": body}),
        ("code", {"font": mono}),
        ("cb_txt", {"font": mono}),
        ("quote", {"font": (body[0], base_size, "normal", "italic")}),
        ("b", {"font": _font(widget, base_size, weight="bold")}),
        ("i", {"font": _font(widget, base_size, slant="italic")}),
        ("bi", {"font": _font(widget, base_size, weight="bold", slant="italic")}),
        ("img", {"font": body}),
        ("img_path", {"font": mono}),
        ("formula_error", {"font": mono}),
        ("meta", {"font": mono}),
        ("tbl", {"font": mono}),
        ("th", {"font": _font(widget, base_size - 1, weight="bold", mono=True)}),
        ("td", {"font": mono}),
        ("tbl_sep", {"font": mono}),
        ("tbl_end", {"font": small}),
    ]
    head_size = {1: 10, 2: 7, 3: 4, 4: 3, 5: 2, 6: 1}
    head_gap = {1: (16, 12), 2: (13, 10), 3: (10, 8)}
    for level in range(1, 7):
        before, after = head_gap.get(level, (8, 6))
        name = "h%d" % level
        theme.append((name, {"foreground": pal["head"], "spacing1": before, "spacing3": after}))
        fonts.append((name, {"font": _font(widget, base_size + head_size[level], weight="bold")}))
    for depth in range(6):
        left = 8 + depth * TAB_STEP
        hang = left + 18
        theme.append(("list_%d" % depth, {"foreground": pal["muted"], "lmargin1": left,
                                          "lmargin2": hang, "spacing1": 3, "spacing3": 1,
                                          "tabs": "%d" % (left + 16)}))
        fonts.append(("list_%d" % depth, {"font": body}))
        theme.append(("li_%d" % depth, {"foreground": pal["fg"], "lmargin1": left,
                                        "lmargin2": hang, "spacing1": 3, "spacing3": 1}))
        fonts.append(("li_%d" % depth, {"font": body}))
    for kind in ("cb_python", "cb_javascript", "cb_bash", "cb_text"):
        theme.append((kind, {"background": pal["code_bg"], "foreground": pal["code_fg"]}))
    return theme, fonts


def configure_tags(widget, pal: dict, base_size: int = PREVIEW_SIZE) -> None:
    """(Re)configure every Text tag for the given palette and text size.

    Changing the text size only touches the tags that carry a face: the theme
    half (colours, margins, spacing) is re-sent solely when the palette itself
    is a different object.  Both halves are idempotent, so re-sending them would
    be correct but pointless work on every wheel tick of a zoom gesture.
    """
    widget._preview_size = base_size                    # type: ignore[attr-defined]
    previous = getattr(widget, "_tag_plan_key", None)
    if previous == (pal, base_size):
        return
    repaint = previous is None or previous[0] is not pal
    widget.configure(background=pal["bg"], foreground=pal["fg"], selectbackground=pal["sel"],
                     selectforeground=pal["fg"], insertbackground=pal["fg"],
                     inactiveselectbackground=pal["sel"])
    theme, fonts = _tag_plan(widget, pal, base_size)
    if repaint:
        for name, options in theme:
            widget.tag_configure(name, **options)
    for name, options in fonts:
        widget.tag_configure(name, **options)
    widget._tag_plan_key = (pal, base_size)             # type: ignore[attr-defined]
    if not repaint:
        return
    # One tag_names() per restyle: on a long document that call is not free, and
    # both sweeps below need the same list.
    names = widget.tag_names()
    for tag in names:                           # per-language code tags stay in sync
        if tag.startswith("cb_") and tag not in ("cb_txt", "cb_python", "cb_javascript",
                                                "cb_bash", "cb_text"):
            widget.tag_configure(tag, background=pal["code_bg"], foreground=pal["code_fg"])
    for tag in names:
        if tag.startswith("__link_"):
            _style_link(widget, tag, pal)       # last, so links win inside other tags
    widget._styled_links = (pal, len(getattr(widget, "_links", ()) or ()))


def _insert_table_widget(widget, table: dict, pal: dict, base_size: int) -> None:
    """Insert a proper bordered table into a Text preview."""
    import tkinter as tk
    import tkinter.font as tkfont

    frame = tk.Frame(widget, background=pal["rule"], bd=0, highlightthickness=0)
    rows = [table.get("header") or []] + list(table.get("rows") or [])
    aligns = table.get("aligns") or []
    columns = max((len(row) for row in rows), default=0)
    labels = []
    for row_index, row in enumerate(rows):
        for col in range(columns):
            raw = row[col] if col < len(row) else ""
            alignment = aligns[col] if col < len(aligns) else "left"
            anchor = {"right": "e", "center": "center"}.get(alignment, "w")
            bg = pal["code_bg"] if row_index == 0 else (
                pal["quote_bg"] if row_index % 2 == 0 else pal["bg"])
            label = tk.Label(
                frame, text=_strip_markers(raw), anchor=anchor, justify=alignment,
                background=bg, foreground=pal["head"] if row_index == 0 else pal["fg"],
                font=_font(widget, base_size - 1, weight="bold" if row_index == 0 else "normal"),
                padx=10, pady=7, bd=0, highlightthickness=0,
            )
            label.grid(row=row_index, column=col, sticky="nsew", padx=(1, 0), pady=(1, 0))
            labels.append((col, label))
            label.bind("<MouseWheel>", lambda event: _table_wheel(widget, event))
            label.bind("<Control-MouseWheel>", lambda event: widget.event_generate(
                "<Control-MouseWheel>", delta=event.delta) or "break")
    font = tkfont.Font(root=widget, font=_font(widget, base_size - 1))
    scale = widget.winfo_fpixels("1i") / 96
    minimum = round(100 * scale)
    floor = round(28 * scale)
    widths = [max(minimum, min(round(460 * scale), max(
        (font.measure(_strip_markers(row[col])) + 24 for row in rows if col < len(row)), default=minimum)))
        for col in range(columns)]

    last_available = [None]
    def resize(_event=None):
        if not frame.winfo_exists() or not columns:
            return
        span = widget.winfo_width() - round(64 * scale)
        # 列很多时按窗口宽度压低每列的最小宽度：宁可单元格里多换几行，也不让
        # 最后几列被挤出窗口看不见。
        per_column = max(floor, min(minimum, span // max(1, columns))) if span > 0 else minimum
        available = max(columns * per_column, span)
        if available == last_available[0]:
            return
        last_available[0] = available
        total = max(1, sum(widths))
        for col, label in labels:
            width = max(per_column, int(available * widths[col] / total))
            label.configure(wraplength=max(24, width - 24))
    def restyle(palette, size):
        # A table is a grid of Tk labels: reconfiguring them relayouts the grid,
        # so an unchanged palette+size pair is skipped instead of re-sent.
        applied = getattr(frame, "_table_style", None)
        if applied is not None and applied[0] is palette and applied[1] == size:
            return
        frame._table_style = (palette, size)
        frame.configure(bg=palette["rule"])
        for index, (_col, label) in enumerate(labels):
            row = index // max(1, columns)
            label.configure(font=_font(widget, size - 1, weight="bold" if row == 0 else "normal"),
                            bg=palette["code_bg"] if row == 0 else palette["quote_bg"] if row % 2 == 0 else palette["bg"],
                            fg=palette["head"] if row == 0 else palette["fg"])
    frame._restyle_table = restyle
    frame._table_style = (pal, base_size)
    frame.bind("<MouseWheel>", lambda event: _table_wheel(widget, event))
    frame.bind("<Control-MouseWheel>", lambda event: widget.event_generate(
        "<Control-MouseWheel>", delta=event.delta) or "break")
    binding = widget.bind("<Configure>", resize, add="+")
    frame.bind("<Destroy>", lambda e: widget.unbind("<Configure>", binding) if e.widget is frame else None)
    resize()
    widget.window_create("end", window=frame, padx=4, pady=8)
    widget.insert("end", "\n", ("blank_lo",))


def _table_wheel(widget, event):
    """Embedded table cells forward scrolling to their owning document."""
    widget.event_generate("<MouseWheel>", delta=event.delta)
    return "break"


#: 公式段落用这个前缀的标签携带表达式：``F:i<tex>`` / ``F:d<tex>``。
FORMULA_TAG = "F:"


def formula_pixel_size(widget, point_size) -> int:
    """预览字号（点）→ 公式图片的像素字号（跟随 DPI 与缩放）。"""
    try:
        per_point = float(widget.winfo_fpixels("1p"))
    except Exception:
        per_point = 1.333
    return max(8, int(round(float(point_size) * per_point * 0.95)))


def insert_preview_formula(widget, tex: str, display: bool = False) -> bool:
    """把公式画成图片嵌进预览。

    不用 PIL：Tk 8.6 自己就能读 PNG（缓存目录里的文件直接用，内存里的图用 base64
    交给 Tk）。画不出来（不支持的命令、字号超限）时返回 False，由调用方把**原始
    表达式**留在正文里——渲染失败不该让公式消失。
    """
    import base64
    import tkinter as tk

    from . import formula as FX
    cache = getattr(widget, "_formula_cache", "")
    theme = getattr(widget, "_formula_theme", "light")
    base = getattr(widget, "_preview_size", PREVIEW_SIZE)
    color, background = FX.colors_for(theme)
    pixels = formula_pixel_size(widget, base)
    result = FX.cached(cache, tex, size=pixels, display=display, theme=theme,
                       color=color, background=background,
                       scale=getattr(widget, "_formula_scale", 1.0))
    if not result.get("ok"):
        return False
    try:
        if result.get("path"):
            photo = tk.PhotoImage(file=result["path"], master=widget)
        else:
            photo = tk.PhotoImage(
                data=base64.b64encode(result["png"]).decode("ascii"), master=widget)
    except Exception:
        return False
    label = tk.Label(widget, image=photo, bg=widget.cget("bg"), bd=0)
    label.image = photo
    label._formula_tex = tex
    label._formula_display = display
    label.bind("<MouseWheel>", lambda event: widget.event_generate(
        "<MouseWheel>", delta=event.delta) or "break")
    label.bind("<Control-MouseWheel>", lambda event: widget.event_generate(
        "<Control-MouseWheel>", delta=event.delta) or "break")

    applied = [pixels, theme]

    def restyle(palette, size):
        """缩放或换主题时按新的字号/配色重画这一张图（缓存命中，代价很低）。"""
        wanted = formula_pixel_size(widget, size)
        theme_now = getattr(widget, "_formula_theme", theme)
        if not _s(getattr(widget, "_formula_cache", "")):
            return
        if [wanted, theme_now] == applied:
            return
        colors = FX.colors_for(theme_now)
        again = FX.cached(getattr(widget, "_formula_cache", ""), tex, size=wanted,
                          display=display, theme=theme_now, color=colors[0],
                          background=colors[1], scale=getattr(widget, "_formula_scale", 1.0))
        if not again.get("ok") or not label.winfo_exists():
            return
        try:
            image = tk.PhotoImage(file=again["path"], master=widget)
        except Exception:
            return
        label.configure(image=image, bg=palette.get("bg", widget.cget("bg")))
        label.image = image
        applied[0], applied[1] = wanted, theme_now

    label._restyle_formula = restyle
    widget.insert("end", "\n", ())
    widget.window_create("end", window=label, padx=4, pady=6)
    widget.insert("end", "\n", ())
    return True


def render_to_text(widget, markdown_text: str, palette: dict | None = None, links: dict | None = None) -> None:
    """Render Markdown into a Text widget using the configured tag styles."""
    pal = palette or LIGHT
    widget.configure(state="normal")
    for child in widget.winfo_children():
        child.destroy()
    widget.delete("1.0", "end")
    widget._links = dict(links or {})                       # type: ignore[attr-defined]
    segments, _meta, seg_links, tables = _parse_document_for_widget(markdown_text)
    widget._links.update(seg_links)                         # type: ignore[attr-defined]
    # Style link tags *before* inserting: a tag that does not exist yet would
    # leave its text unstyled.  This is the one full pass over the link set; the
    # restyle paths only have to touch what changed.
    for tag in widget._links:                               # type: ignore[attr-defined]
        _style_link(widget, tag, pal)
    widget._styled_links = (pal, len(widget._links))        # type: ignore[attr-defined]

    if not _s(markdown_text).strip():
        widget.insert("end", "\n", ())
        widget.insert("end", "  这篇文章还是空的。\n", ("placeholder",))
        widget.insert("end", "  切到「源码」开始写，或按「＋ 新建项目」创建内容。\n", ("placeholder",))
        return
    from .media import image_matches, insert_preview_image
    image_refs = {}
    for match in image_matches(markdown_text):
        image_refs.setdefault(match.group(2), []).append(match)
    inserted_tables = set()
    for text, tags in segments:
        formula_tag = next((tag for tag in tags if tag.startswith(FORMULA_TAG)), "")
        if formula_tag:
            display = formula_tag.startswith(FORMULA_TAG + "d")
            tex = formula_tag[len(FORMULA_TAG) + 1:]
            if insert_preview_formula(widget, tex, display):
                continue
            widget.insert("end", text or ("$$%s$$" % tex if display else "$%s$" % tex),
                          ("formula_error",))
            continue
        table_tag = next((tag for tag in tags if tag.startswith("__table_")), "")
        if table_tag:
            if table_tag not in inserted_tables and table_tag in tables:
                _insert_table_widget(widget, tables[table_tag], pal,
                                     getattr(widget, "_preview_size", PREVIEW_SIZE))
                inserted_tables.add(table_tag)
            continue
        if "img_path" in tags:
            source = text.strip("‹›")
            references = image_refs.get(source, [])
            match = references.pop(0) if references else None
            if insert_preview_image(widget, source, match):
                continue
        widget.insert("end", text, tags)
    if widget.index("end-1c") != "1.0" and widget.get("end-2c", "end-1c") != "\n":
        widget.insert("end", "\n", ())


class ScrollCoalescer:
    """Turn a burst of wheel messages into one scroll per frame.

    A wheel gesture arrives as a stream of messages; Tk's own binding scrolls
    for every one of them and repaints the viewport each time.  The handler here
    only accumulates ``%D`` (one Tcl call per message) and the position is
    applied once per frame (``INTERVAL`` ms).  Only the wheel is ours: click,
    drag, keyboard and the scrollbar stay with Tk.
    """

    INTERVAL = 8
    SHIFT = 0x1                                     # event.state: Shift is held
    # ``%D`` on Windows is +-120 per notch, exactly as Tk's own binding assumes.

    def __init__(self, root, widget):
        self.root, self.widget = root, widget
        self.vertical = 0
        self.horizontal = 0
        self.job = None

    def on_wheel(self, event):
        delta = getattr(event, "delta", 0) or 0
        if not delta:
            return "break"
        if (getattr(event, "state", 0) or 0) & self.SHIFT:
            self.horizontal += delta
        else:
            self.vertical += delta
        self.schedule()
        return "break"

    def schedule(self):
        if self.job is None and self.root.winfo_exists():
            self.job = self.root.after(self.INTERVAL, self.apply)

    def cancel(self):
        if self.job is not None:
            try:
                self.root.after_cancel(self.job)
            except Exception:
                pass
            self.job = None

    def units(self, delta):
        """Mirror Tk's own ``%W yview scroll [expr {...}] pixels`` rule.

        Tcl's integer division truncates toward zero, so the pixel count for a
        notch has to be truncated the same way: rounding would make a downward
        notch 41 px against Tk's 40 and drift away from the position the user
        gets with the default binding.
        """
        pixels = (-delta / 3.0) if delta >= 0 else ((2 - delta) / 3.0)
        return int(pixels)

    def apply(self):
        self.job = None
        if not self.widget.winfo_exists():
            return
        if self.vertical:
            delta, self.vertical = self.vertical, 0
            units = self.units(delta)
            if units:
                self.widget.yview_scroll(units, "pixels")
        if self.horizontal:
            delta, self.horizontal = self.horizontal, 0
            units = self.units(delta)
            if units:
                self.widget.xview_scroll(units, "pixels")


# --------------------------------------------------------------------------
# drag & drop (Windows shell)
# --------------------------------------------------------------------------

class FileDrop:
    """Receive shell drops through the native bridge, then deliver from Tk."""
    def __init__(self, tkroot, callback):
        self.root, self.callback, self.ok, self.job = tkroot, callback, False, None
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            from .native_bridge import NativeBridge
            parent = ctypes.windll.user32.GetParent
            parent.argtypes, parent.restype = [wintypes.HWND], wintypes.HWND
            self.bridge = NativeBridge(parent(tkroot.winfo_id()) or tkroot.winfo_id())
            self.bridge.dll.md_drop_enable(self.bridge.hwnd)
            self.ok = True
            self.job = tkroot.after(80, self.poll)
            tkroot.bind("<Destroy>", lambda e: self.close() if e.widget is tkroot else None, add="+")
        except (OSError, AttributeError):
            pass

    def poll(self):
        self.job = None
        paths = self.bridge.drops()
        if paths:
            self.callback(paths)
        if self.ok:
            self.job = self.root.after(80, self.poll)

    def close(self):
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        if self.ok:
            self.bridge.detach()
            self.ok = False

# --------------------------------------------------------------------------
# window
# --------------------------------------------------------------------------

class MarkdownWindow(DocumentActions):
    """Tkinter preview + navigation window over a MDReader workspace."""

    def __init__(self, workspace_root: str, url: str = "", on_open_browser=None):
        from .display import enable_high_dpi
        enable_high_dpi()
        import tkinter as tk
        from tkinter import ttk
        import tkinter.font as tkfont

        self._tk = tk
        self.url = _s(url)
        self.on_open_browser = on_open_browser
        self.warning = ""
        try:
            self.ws = core.Workspace(workspace_root)
        except Exception as exc:                    # unreadable workspace
            self.warning = "无法打开工作区，已改用默认位置：%s" % _s(exc)
            self.ws = core.Workspace(core.default_workspace())

        self.root = tk.Tk()
        try:
            from .appicon import apply_window_icon
            apply_window_icon(self.root)
        except Exception:
            pass
        self.root.bind("<Destroy>", self._cancel_window_jobs, add="+")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name, root=self.root).configure(family=UI_FONT, size=UI_SIZE)
        tkfont.nametofont("TkFixedFont", root=self.root).configure(family=MONO_FONT, size=11)
        self.ui_scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.root.title(core.APP_NAME)
        self.root.minsize(self.px(900), self.px(560))
        try:
            width = min(self.px(1280), self.root.winfo_screenwidth() - self.px(40))
            height = min(self.px(850), self.root.winfo_screenheight() - self.px(100))
            self.root.geometry("%dx%d+%d+%d" % (width, height,
                                                max(0, (self.root.winfo_screenwidth() - width) // 2),
                                                max(0, (self.root.winfo_screenheight() - height) // 3)))
        except Exception:
            self.root.geometry("1200x800")

        self.preferences = read_ui_preferences(self.ws.root)
        self.theme = self.preferences.get("theme", "light")
        if self.theme not in THEMES:
            self.theme = "light"
        self.pal, self.dark, self.base_size = dict(THEMES[self.theme]), self.theme == "dark", PREVIEW_SIZE
        self.mode = "preview"
        self.projects: list[dict] = []
        self.docs: list[dict] = []
        self.dirs: list[str] = []
        self.cur_pid: str | None = None
        self.cur_doc: dict | None = None
        self.source, self.dirty, self._loading = "", False, False
        self._notice_job: str | None = None
        self._recovery_job = None
        self._recovery_last = 0
        self.tabs: list[dict] = []
        self.active_tab: dict | None = None
        # 项目树的目录行 → 相对路径；「＋」用它决定新建到哪个目录
        self._project_nodes: dict = {}
        # One source editor per open document, plus a separate read-only preview
        # widget. Swapping views or tabs therefore never rewrites the buffer of
        # an editor that is not on screen, which is what used to drop the Tk
        # undo stack (E01).
        self.editor: "tk.Text | None" = None
        self._editors: dict = {}
        self._imes: list = []

        # 临时查看：直接读写工作区之外的原文件，不导入
        self.loose = core.LooseDocs(self.ws)
        self.cur_loose: dict | None = None
        self.recent: list[dict] = []
        self.visible_recent: list[dict] = []

        # 我的文件夹（F01）：已授权的原文件夹 + 其中的 Markdown 树
        self.roots: list[dict] = []
        self.cur_root: str | None = None
        self.root_docs: list[dict] = []
        self._root_nodes: dict = {}
        self._folder_tick: str | None = None
        self._folder_signature = None
        self._roots_seen = "unset"
        self._folders_dirty = False
        self._deleted_tab_notice = ""

        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass
        for name in ("TCombobox", "Vertical.TScrollbar", "Treeview", "Treeview.Heading"):
            try:
                self.style.configure(name, font=(UI_FONT, UI_SIZE))
            except Exception:
                pass
        self.style.configure("Treeview", rowheight=self.px(28))

        self._build()
        from .ime import InlineIME
        self._imes.append(InlineIME(self.preview))
        # The search field is an entry, so it has no inline composition of its
        # own; it gets exactly the same themed preedit as the document editor.
        self.ime_search = InlineIME(self.recent_search, palette=lambda: self.pal)
        self._imes.append(self.ime_search)
        self.ime_search.set_editable(True)
        self.apply_theme()
        self._bind_keys()
        self.refresh_roots()
        self._roots_seen = tuple(r.get("id") for r in self.roots)
        self.refresh_projects()
        self.refresh_recent()
        self.start_folder_watch()
        self.schedule_folder_watch()
        self.enable_file_drop()
        self.root.after(500, self.offer_recovery)
        self.watch_shared_theme()
        if self.warning:
            self.notice(self.warning, error=True)

    # -- widgets ---------------------------------------------------------
    @property
    def ime(self):
        """The composition adapter of the editor on screen (preview included)."""
        return self._ime_for(self.text)

    def _ime_for(self, widget):
        for adapter in getattr(self, "_imes", ()):
            if getattr(adapter, "editor", None) is widget:
                return adapter
        return None

    def _refresh_imes(self, widget=None):
        """Re-apply composition chrome after a restyle.

        With ``widget`` given, only the adapter of the widget that was restyled
        is touched: every other editor is off screen, and the surface of a
        hidden editor has nothing to place.
        """
        adapters = [self._ime_for(widget)] if widget is not None else list(getattr(self, "_imes", ()))
        for adapter in adapters:
            if adapter is None:
                continue
            try:
                adapter.refresh()
            except Exception:
                pass

    def _cancel_window_jobs(self, event):
        if event.widget is self.root:
            hook = getattr(getattr(self, "ime_search", None), "hook", None)
            for adapter in list(getattr(self, "_imes", ())):
                try:
                    adapter.close()
                except Exception:
                    pass
            # Wheel bursts hold a pending timer per document widget; drop them
            # before the generic sweep so no apply() runs against a dead widget.
            for owner in (self.preview, *getattr(self, "_editors", ())):
                coalescer = getattr(owner, "_wheel_coalescer", None)
                if coalescer is not None:
                    coalescer.cancel()
            if hook is not None:
                try:
                    hook.close()
                except Exception:
                    pass
            try:
                self.ws.folders.watch().stop()
            except Exception:
                pass
            try:
                self.ws.plugins.shutdown()
            except Exception:
                pass
            for job in self.root.tk.call("after", "info"):
                self.root.after_cancel(job)

    def px(self, value):
        return round(value * self.ui_scale)

    def _build(self) -> None:
        tk = self._tk
        from tkinter import ttk

        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.outer = tk.Frame(self.root, bd=0, highlightthickness=0)
        self.outer.grid(row=0, column=0, sticky="nsew")
        self.outer.columnconfigure(1, weight=1)
        self.outer.rowconfigure(0, weight=1)

        # -- sidebar -----------------------------------------------------
        self.side = tk.Frame(self.outer, width=self.px(310), bd=0, highlightthickness=0)
        self.side.grid(row=0, column=0, sticky="nsw")
        self.side.grid_propagate(False)
        self.side.columnconfigure(0, weight=1)
        self.side.rowconfigure(0, weight=2)      # 我的文件夹
        self.side.rowconfigure(1, weight=3)      # 项目与最近打开

        # ---- 我的文件夹（F01：直接读写原件，不导入）----------------------
        folder_box = tk.Frame(self.side, bd=0, highlightthickness=0)
        folder_box.grid(row=0, column=0, sticky="nsew")
        folder_box.columnconfigure(0, weight=1)
        folder_box.rowconfigure(3, weight=1)
        tk.Label(folder_box, text="我的文件夹", anchor="w").grid(
            row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        rowf = tk.Frame(folder_box, bd=0, highlightthickness=0)
        rowf.grid(row=1, column=0, sticky="ew", padx=10)
        rowf.columnconfigure(0, weight=1)
        self.cmb_root = ttk.Combobox(rowf, state="readonly", values=[])
        self.cmb_root.grid(row=0, column=0, sticky="ew")
        self.cmb_root.bind("<<ComboboxSelected>>", lambda _e: self.on_root_change())
        self.btn_open_folder = tk.Button(rowf, text="\U0001f4c2 打开文件夹", command=self.open_user_folder,
                                         relief="flat", bd=0, cursor="hand2", padx=8, pady=3)
        self.btn_open_folder.grid(row=0, column=1, padx=(6, 0))
        rowf2 = tk.Frame(folder_box, bd=0, highlightthickness=0)
        rowf2.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 0))
        self.btn_new_md = tk.Button(rowf2, text="\uff0b 新建 Markdown", command=self.new_folder_doc,
                                    relief="flat", bd=0, cursor="hand2", padx=8, pady=2)
        self.btn_new_md.pack(side="left")
        self.btn_root_refresh = tk.Button(rowf2, text="\u27f3", command=lambda: self.refresh_folder_tree(force=True),
                                          relief="flat", bd=0, cursor="hand2", padx=8, pady=2)
        self.btn_root_refresh.pack(side="left", padx=(4, 0))
        self.folder_menu_button = tk.Button(rowf2, text="\u22ef", command=self.show_folder_menu,
                                            relief="flat", bd=0, cursor="hand2", padx=8, pady=2)
        self.folder_menu_button.pack(side="left", padx=(4, 0))
        self.style.layout("Folder.Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
            ("Treeitem.image", {"side": "left", "sticky": ""}),
            ("Treeitem.text", {"side": "left", "sticky": ""})]})])
        self.root_tree = ttk.Treeview(folder_box, show="tree", selectmode="browse", style="Folder.Treeview",
                                      columns=("toggle",))
        self.root_tree.column("toggle", width=self.px(28), minwidth=self.px(28), stretch=False)
        self.root_tree.column("#0", minwidth=self.px(60), stretch=True)
        self.root_tree.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=(10, 4), pady=(8, 4))
        vsb_root = ttk.Scrollbar(folder_box, orient="vertical", command=self.root_tree.yview)
        vsb_root.grid(row=3, column=2, sticky="ns", pady=(8, 4), padx=(0, 6))
        self.folder_arrows = FolderArrows(self, self.root_tree)
        self.root_tree.configure(yscrollcommand=lambda a, b: (vsb_root.set(a, b), self.folder_arrows.schedule()))
        self.root_tree.bind("<Double-1>", self.on_folder_activate)
        self.root_tree.bind("<Return>", self.on_folder_activate)
        self.root_tree.bind("<Button-3>", self.on_folder_menu)
        self.root_tree.bind("<Button-1>", self.on_folder_select)
        self.lbl_root = tk.Label(folder_box, text="", anchor="w", justify="left", wraplength=240)
        self.lbl_root.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))

        # ---- 项目 -------------------------------------------------------
        self.project_box = tk.Frame(self.side, bd=0, highlightthickness=0)
        self.project_box.grid(row=1, column=0, sticky="nsew")
        self.project_box.columnconfigure(0, weight=1)
        self.project_box.rowconfigure(3, weight=1)
        tk.Label(self.project_box, text="项目", anchor="w").grid(row=0, column=0, sticky="ew", padx=12,
                                                                 pady=(12, 4))
        rowp = tk.Frame(self.project_box, bd=0, highlightthickness=0)
        rowp.grid(row=1, column=0, sticky="ew", padx=10)
        rowp.columnconfigure(0, weight=1)
        self.cmb = ttk.Combobox(rowp, state="readonly", values=[])
        self.cmb.grid(row=0, column=0, sticky="ew")
        self.cmb.bind("<<ComboboxSelected>>", lambda _e: self.on_project_change())
        self.btn_new = tk.Button(rowp, text="\uff0b 新建项目", command=self.new_project,
                                 relief="flat", bd=0, cursor="hand2", padx=8, pady=3)
        self.btn_new.grid(row=0, column=1, padx=(6, 0))
        self.tree = ttk.Treeview(self.project_box, show="tree", selectmode="browse")
        self.tree.grid(row=3, column=0, sticky="nsew", padx=(10, 4), pady=(10, 4))
        vsb = ttk.Scrollbar(self.project_box, orient="vertical", command=self.tree.yview)
        vsb.grid(row=3, column=1, sticky="ns", pady=(10, 4), padx=(0, 6))
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.bind("<Double-1>", self.on_project_tree_rename)
        self.tree.bind("<Control-m>", self.on_project_tree_rename)
        self.tree.bind("<Control-M>", self.on_project_tree_rename)
        self.tree.bind("<Control-y>", self.on_project_tree_path)
        self.tree.bind("<Control-Y>", self.on_project_tree_path)
        self.tree.bind("<Return>", self.on_tree_activate)
        self.tree.bind("<Button-3>", self.on_project_tree_menu)
        self.tree.bind("<Delete>", self.on_project_tree_delete)
        # 目录行末尾的「＋」是独立可点的：只在目录行上生效，文件行不受影响
        self.tree.tag_bind("plus", "<Button-1>", self.on_plus_click)

        # -- 最近打开（临时查看的本地文件）------------------------------
        recent_box = tk.Frame(self.project_box, bd=0, highlightthickness=0)
        recent_box.grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 0))
        recent_box.columnconfigure(0, weight=1)
        self.lbl_recent = tk.Label(recent_box, text="最近打开", anchor="w")
        self.lbl_recent.grid(row=0, column=0, columnspan=2, sticky="ew")
        btnrow = tk.Frame(recent_box, bd=0, highlightthickness=0)
        btnrow.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 3))
        self.btn_open_local = tk.Button(btnrow, text="📂 打开本地文件", command=self.open_local_files,
                                        relief="flat", bd=0, cursor="hand2", padx=8, pady=2)
        self.btn_open_local.pack(side="left")
        self.btn_new_loose = tk.Button(btnrow, text="＋ 新建", command=self.new_loose_draft,
                                       relief="flat", bd=0, cursor="hand2", padx=8, pady=2)
        self.btn_new_loose.pack(side="left", padx=(4, 0))
        self.recent_query = tk.StringVar(self.root)
        searchrow = tk.Frame(recent_box)
        searchrow.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        searchrow.columnconfigure(1, weight=1)
        tk.Label(searchrow, text="搜索").grid(row=0, column=0, padx=(0, 5))
        self.recent_search = ttk.Entry(searchrow, textvariable=self.recent_query)
        self.recent_search.grid(row=0, column=1, sticky="ew")
        self.recent_query.trace_add("write", lambda *_: self.filter_recent())
        self.recent_search.bind("<Escape>", lambda _e: self.recent_query.set(""))
        self.recent_list = tk.Listbox(recent_box, height=7, activestyle="none", selectmode="browse",
                                      bd=0, highlightthickness=0, exportselection=False)
        self.recent_list.grid(row=3, column=0, sticky="ew")
        rsb = ttk.Scrollbar(recent_box, orient="vertical", command=self.recent_list.yview)
        rsb.grid(row=3, column=1, sticky="ns")
        self.recent_list.configure(yscrollcommand=rsb.set)
        self.recent_list.bind("<Double-1>", lambda _e: self.open_selected_recent())
        self.recent_list.bind("<Return>", lambda _e: self.open_selected_recent())
        self.recent_list.bind("<Button-3>", self.on_recent_menu)
        self.recent_list.bind("<Delete>", lambda _e: self.forget_selected_recent())
        self.recent_list.bind("<<ListboxSelect>>", lambda _e: self.update_recent_selection())
        self.btn_forget_recent = tk.Button(recent_box, text="删除选中记录", state="disabled",
                                          command=self.forget_selected_recent, relief="flat")
        self.btn_forget_recent.grid(row=4, column=0, columnspan=2, sticky="ew", pady=5)

        self.lbl_path = tk.Label(self.project_box, text="", anchor="w", justify="left", wraplength=240)
        self.lbl_path.grid(row=5, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 12))

        # -- main area ---------------------------------------------------
        self.main = tk.Frame(self.outer, bd=0, highlightthickness=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.columnconfigure(0, weight=1)
        self.main.rowconfigure(2, weight=1)
        self.tabbar = tk.Frame(self.main, bd=0)
        self.tabbar.grid(row=0, column=0, sticky="ew")
        self.tabbar.columnconfigure(0, weight=1)
        self.tab_canvas = tk.Canvas(self.tabbar, height=self.px(42), bd=0, highlightthickness=0)
        self.tab_canvas.grid(row=0, column=0, sticky="ew")
        self.tab_items = tk.Frame(self.tab_canvas)
        self.tab_canvas.create_window(0, 0, window=self.tab_items, anchor="nw")
        self.tab_items.bind("<Configure>", lambda _e: self.tab_canvas.configure(scrollregion=self.tab_canvas.bbox("all")))
        for column, (caption, amount) in enumerate((("‹", -2), ("›", 2)), 1):
            tk.Button(self.tabbar, text=caption, relief="flat", command=lambda n=amount: self.tab_canvas.xview_scroll(n, "units")).grid(row=0, column=column, sticky="ns")
        self.bar = tk.Frame(self.main, bd=0, highlightthickness=0)
        self.bar.grid(row=1, column=0, sticky="ew")
        self.bar.columnconfigure(0, weight=1)
        self.lbl_doc = tk.Label(self.bar, text="", anchor="w", width=1)
        self.lbl_doc.grid(row=0, column=0, sticky="ew", padx=14, pady=8)
        self.buttons = tk.Frame(self.bar, bd=0, highlightthickness=0)
        self.buttons.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        for text, cmd in (("\U0001f4d6 预览", self.toggle_mode),
                          ("\u270e 源码", self.toggle_mode),
                          ("\U0001f4be 保存", self.save_doc),
                          ("\u27f3 刷新", self.reload_doc),
                          ("\U0001f5c0 目录", self.open_folder)):
            self._tool_button(text, cmd)
        self.btn_theme = self._tool_button("\U0001f3a8 主题 ▾", self.show_theme_menu)
        self.theme_popup = tk.Frame(self.outer, bd=0, highlightthickness=1)
        self.theme_buttons = {}
        self._theme_leave_job = None
        for theme, label in THEME_LABELS.items():
            button = tk.Button(self.theme_popup, text=label, relief="flat", bd=0,
                               padx=18, pady=8, anchor="w", width=10, cursor="hand2",
                               command=lambda key=theme: self.choose_theme(key))
            button.pack(fill="x", padx=4, pady=2)
            self.theme_buttons[theme] = button
        for widget in (self.btn_theme, self.theme_popup, *self.theme_buttons.values()):
            widget.bind("<Enter>", lambda _e: self.show_theme_menu())
            widget.bind("<Leave>", self.schedule_theme_hide)
        self.root.bind("<Button-1>", self.dismiss_theme_on_click, add="+")
        self.root.bind("<Escape>", lambda _e: self.hide_theme_menu(), add="+")
        self.root.bind("<Configure>", lambda e: self.hide_theme_menu() if e.widget is self.root else None, add="+")
        if self.url:
            self._tool_button("\U0001f310 浏览器视图", self.open_browser)
        self.btn_more = self._tool_button("\u22ef 更多", self.show_more_menu)

        # -- 表格与常用格式（F04）-----------------------------------------
        # 按钮只对源码缓冲区生效：在预览模式下点一下会先切到源码，再按同一套
        # 规则改文本（与网页端 /api/edit/* 用的是同一份实现）。
        self.format_bar = tk.Frame(self.bar, bd=0, highlightthickness=0)
        self.format_bar.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        self.format_buttons = {}
        for text, command in (("\u25a6 插入表格\u2026", self.insert_table_dialog),
                              ("∑ 公式 ▾", self.show_formula_menu),
                              ("H 标题 \u25be", self.show_heading_menu),
                              ("B 粗体", lambda: self.format_selection("bold")),
                              ("I 斜体", lambda: self.format_selection("italic")),
                              ("\u2022 列表", lambda: self.format_selection("bullets")),
                              ("1. 有序", lambda: self.format_selection("ordered")),
                              ("\u275d 引用", lambda: self.format_selection("quote")),
                              ("</> 代码", lambda: self.format_selection("code")),
                              ("\U0001f517 链接", lambda: self.format_selection("link"))):
            self.format_buttons[text] = self._format_button(text, command)
        self.heading_button = self.format_buttons["H 标题 \u25be"]
        self.formula_button = self.format_buttons["∑ 公式 ▾"]

        body = tk.Frame(self.main, bd=0, highlightthickness=0)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.body = body
        # Every document editor has the same geometry, so switching tabs is a
        # raise/lower and never a re-layout of the text the user is looking at.
        self.preview = tk.Text(body, wrap="word", bd=0, highlightthickness=0, undo=False,
                               padx=22, pady=14, spacing1=1, spacing3=2)
        self.preview.grid(row=0, column=0, sticky="nsew")
        self.preview.bind("<Control-MouseWheel>", self.on_zoom)
        self.wheel = ScrollCoalescer(self.root, self.preview)
        self.preview._wheel_coalescer = self.wheel
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>"):
            self.preview.bind(sequence, self.wheel.on_wheel)
        self.text = self.preview          # the widget on screen right now
        tsb = ttk.Scrollbar(body, orient="vertical", command=lambda *args: self.text.yview(*args))
        self.doc_vscroll = tsb
        tsb.grid(row=0, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=lambda a, b: self._document_scroll(self.preview, a, b))
        hsb = ttk.Scrollbar(body, orient="horizontal", command=lambda *args: self.text.xview(*args))
        self.doc_hscroll = hsb
        hsb.grid(row=1, column=0, sticky="ew")
        self.preview.configure(xscrollcommand=lambda a, b: self._document_scroll(self.preview, a, b, True), font=(UI_FONT, PREVIEW_SIZE))
        self.empty = tk.Label(body, text="", anchor="center", justify="center")

        self.status = tk.Frame(self.main, bd=0, highlightthickness=0)
        self.status.grid(row=3, column=0, sticky="ew")
        self.status.columnconfigure(0, weight=1)
        self.lbl_status = tk.Label(self.status, text="就绪", anchor="w")
        self.lbl_status.grid(row=0, column=0, sticky="ew", padx=14, pady=5)
        self.lbl_count = tk.Label(self.status, text="", anchor="e")
        self.lbl_count.grid(row=0, column=1, sticky="e", padx=8, pady=5)
        self.lbl_state = tk.Label(self.status, text="", anchor="e")
        self.lbl_state.grid(row=0, column=2, sticky="e", padx=14, pady=5)

    def _tool_button(self, text: str, command):
        btn = self._tk.Button(self.buttons, text=text, command=command, relief="flat", bd=0,
                              cursor="hand2", padx=10, pady=4)
        btn.pack(side="left", padx=3)
        return btn

    def _format_button(self, text: str, command):
        btn = self._tk.Button(self.format_bar, text=text, command=command, relief="flat", bd=0,
                              cursor="hand2", padx=8, pady=3)
        btn.pack(side="left", padx=2)
        return btn

    def _bind_keys(self) -> None:
        self.root.bind('<Control-Shift-S>', self._key(self.save_as))
        self.root.bind('<Control-f>', self._key(self.find_in_document))
        self.root.bind('<Control-l>', self._key(self.show_outline))
        for seq, fn in (("<Control-s>", self.save_doc), ("<F5>", self.reload_doc),
                        ("<Control-e>", self.toggle_mode), ("<Control-b>", self.toggle_sidebar),
                        ("<Control-o>", self.open_local_files), ("<Control-w>", self.close_active_tab),
                        ("<Control-n>", self.new_loose_draft),
                        ("<Control-Shift-T>", self.insert_table_dialog),
                        ("<Control-Shift-M>", lambda: self.format_selection("formula_inline")),
                        ("<Control-Shift-m>", lambda: self.format_selection("formula_inline")),
                        # Ctrl+Shift+V：Tk 里大写 V 与小写 v 是两个不同的 keysym，
                        # 两个都绑上才不管系统上报哪个都能用（同一次按键只会命中一个）。
                        ("<Control-Shift-V>", self.insert_clipboard_image),
                        ("<Control-Shift-v>", self.insert_clipboard_image),
                        ("<Control-Tab>", self.next_tab)):
            self.root.bind(seq, self._key(fn))
        self.root.bind("<Control-MouseWheel>", self.on_zoom)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _key(self, fn):
        def handler(_event):
            try:
                fn()
            except Exception as exc:
                self.notice("操作失败：%s" % _s(exc), error=True)
            return "break"
        return handler

    # -- open documents (separate from recent-file history) ---------------
    def _tab_key(self, loose=None, doc=None, pid=None):
        if loose:
            path = loose.get("path")
            return ("loose", os.path.normcase(os.path.realpath(path)) if path else loose["id"])
        return ("project", pid or self.cur_pid, (doc or {}).get("id"))

    def stash_tab(self):
        if self.active_tab is None:
            return
        editor = self.active_tab.get("editor")
        visible = editor if editor is not None and editor.winfo_exists() else None
        if self.mode == "source" and visible is not None:
            self.source = self.get_text()
            self.active_tab["cursor"] = visible.index("insert")
        # The scroll position belongs to the widget on screen, whichever it is.
        try:
            self.active_tab["scroll"] = self.text.yview()[0]
        except Exception:
            pass
        self.active_tab.update(source=self.source, dirty=self.dirty, mode=self.mode,
                               loose=self.cur_loose, doc=self.cur_doc)

    def remember_tab(self):
        key = self._tab_key(self.cur_loose, self.cur_doc)
        tab = next((t for t in self.tabs if t["key"] == key), None)
        if tab is None:
            tab = {"key": key, "pid": self.cur_pid, "source": self.source,
                   "dirty": self.dirty, "mode": self.mode, "loose": self.cur_loose,
                   "doc": self.cur_doc}
            self.tabs.append(tab)
            self.editor_for(tab)
            self.mark_baseline(tab)
        self.active_tab = tab
        self.stash_tab()
        self.draw_tabs()

    def draw_tabs(self):
        for child in self.tab_items.winfo_children():
            child.destroy()
        pal = self.pal
        self.tab_canvas.configure(background=pal["side"])
        self.tab_items.configure(background=pal["side"])
        for tab in self.tabs:
            title = tab_title(tab)
            bg = pal["bg"] if tab is self.active_tab else pal["code_bg"]
            frame = self._tk.Frame(self.tab_items, background=bg, highlightthickness=1,
                                   highlightbackground=pal["accent"] if tab is self.active_tab else pal["rule"])
            frame.pack(side="left", padx=(3, 0), pady=3)
            self._tk.Button(frame, text=title, command=lambda t=tab: self.activate_tab(t),
                            relief="flat", bd=0, background=bg, foreground=pal["fg"],
                            activebackground=pal["sel"], activeforeground=pal["fg"],
                            highlightbackground=bg, padx=10, pady=5).pack(side="left")
            self._tk.Button(frame, text="×", command=lambda t=tab: self.close_tab(t),
                            relief="flat", bd=0, background=bg, foreground=pal["muted"],
                            activebackground=pal["sel"], activeforeground=pal["fg"],
                            highlightbackground=bg, padx=7, pady=5).pack(side="left")
            tab["widget"] = frame
        self.btn_add_tab = self._tk.Button(
            self.tab_items, text="＋", font=(UI_FONT, 16), width=2, pady=0,
            relief="flat", bd=0, cursor="hand2", background=pal["side"],
            foreground=pal["fg"], activebackground=pal["hover"], activeforeground=pal["fg"],
            highlightbackground=pal["side"], command=self.new_loose_draft)
        self.btn_add_tab.pack(side="left", padx=7, pady=3)
        self.btn_add_tab.bind("<Enter>", lambda e: e.widget.configure(background=self.pal["hover"]))
        self.btn_add_tab.bind("<Leave>", lambda e: e.widget.configure(background=self.pal["side"]))

    def reveal_active_tab(self):
        self.root.update_idletasks()
        if self.active_tab is None:
            return
        frame = self.active_tab.get("widget")
        if frame is None:
            return
        total = max(1, self.tab_items.winfo_reqwidth())
        left = self.tab_canvas.canvasx(0)
        width = self.tab_canvas.winfo_width()
        start, end = frame.winfo_x(), frame.winfo_x() + frame.winfo_width()
        if self.active_tab is self.tabs[-1]:
            end = self.btn_add_tab.winfo_x() + self.btn_add_tab.winfo_width()
        if start < left:
            self.tab_canvas.xview_moveto(start / total)
        elif end > left + width:
            self.tab_canvas.xview_moveto(max(0, end - width) / total)

    def activate_tab(self, tab):
        if tab is self.active_tab:
            return
        self.stash_tab()
        self.active_tab = tab
        self.cur_loose, self.cur_doc = tab.get("loose"), tab.get("doc")
        if self.cur_doc:
            self.cur_pid = tab["pid"]
            self.select_current_project()
            try:
                pdir = self.ws.require_project(self.cur_pid)
                self.docs = list(self.ws.scan_docs(pdir))
                self.fill_tree(self.docs)
                self.lbl_path.configure(text=pdir)
            except Exception as exc:
                self.notice("项目暂时无法读取：%s" % exc, error=True)
        self.source, self.dirty = tab["source"], tab["dirty"]
        self.editor = self.editor_for(tab)
        self.hide_empty()
        if tab["mode"] == "source":
            # The tab keeps its own editor and its own undo stack: nothing is
            # reloaded here, the widget is only brought back to the front.
            self.editor = self.editor_for(tab)
            self._show(self.editor)
            self.mode = "source"
            if tab.get("cursor"):
                try:
                    self.editor.mark_set("insert", tab["cursor"])
                except Exception:
                    pass
        else:
            self.render()
        self.root.update_idletasks()
        if tab["mode"] == "source" and self.editor is not None:
            self.editor.yview_moveto(tab.get("scroll", 0))
        else:
            self.preview.yview_moveto(tab.get("scroll", 0))
        self.update_title()
        self.update_status()
        self.draw_tabs()
        self.reveal_active_tab()

    def close_active_tab(self):
        if self.active_tab is not None:
            self.close_tab(self.active_tab)

    def close_tab(self, tab):
        if tab not in self.tabs:
            return
        previous = self.active_tab
        self.stash_tab()
        if tab_needs_save(tab):
            self.activate_tab(tab)
            if not self.confirm_switch():
                if previous is not None and previous is not tab:
                    self.activate_tab(previous)
                return
        self._release_editor(tab)
        index = self.tabs.index(tab)
        self.tabs.remove(tab)
        if self.active_tab is tab:
            self.active_tab = None
            self.editor = None
            if self.tabs:
                target = previous if previous in self.tabs else self.tabs[min(index, len(self.tabs) - 1)]
                self.activate_tab(target)
            else:
                self.cur_doc = self.cur_loose = None
                self.source, self.dirty = "", False
                self.show_empty("没有打开的文档\n点击「打开本地文件」或按 Ctrl+O。")
        self._sync_view()
        self.draw_tabs()

    def _release_editor(self, tab):
        """Destroy a closed tab's editor, its IME surface and its bindings."""
        editor = tab.pop("editor", None)
        if editor is None:
            return
        self._editors.pop(editor, None)
        adapter = next((ime for ime in self._imes
                        if getattr(ime, "editor", None) is editor), None)
        if adapter is not None:
            self._imes.remove(adapter)
            try:
                adapter.close()
            except Exception:
                pass
        try:
            editor.destroy()
        except Exception:
            pass
        if self.editor is editor:
            self.editor = None
        if self.text is editor:
            self.text = self.preview

    def next_tab(self):
        if self.tabs:
            index = self.tabs.index(self.active_tab) if self.active_tab in self.tabs else -1
            self.activate_tab(self.tabs[(index + 1) % len(self.tabs)])

    # -- theme -----------------------------------------------------------
    def hide_theme_menu(self):
        if self._theme_leave_job is not None:
            self.root.after_cancel(self._theme_leave_job)
            self._theme_leave_job = None
        self.theme_popup.place_forget()

    def show_theme_menu(self):
        if self._theme_leave_job is not None:
            self.root.after_cancel(self._theme_leave_job)
            self._theme_leave_job = None
        if self.root.grab_current() is not None:
            return
        self.root.update_idletasks()
        x = self.btn_theme.winfo_rootx() - self.outer.winfo_rootx()
        y = self.btn_theme.winfo_rooty() - self.outer.winfo_rooty() + self.btn_theme.winfo_height()
        x = max(0, min(x, self.outer.winfo_width() - self.theme_popup.winfo_reqwidth()))
        self.theme_popup.place(x=x, y=y)
        self.theme_popup.lift()

    def schedule_theme_hide(self, _event=None):
        if self._theme_leave_job is not None:
            self.root.after_cancel(self._theme_leave_job)
        def check():
            self._theme_leave_job = None
            x, y = self.root.winfo_pointerxy()
            for widget in (self.btn_theme, self.theme_popup):
                if (widget.winfo_rootx() <= x < widget.winfo_rootx() + widget.winfo_width() and
                        widget.winfo_rooty() <= y < widget.winfo_rooty() + widget.winfo_height()):
                    return
            self.hide_theme_menu()
        self._theme_leave_job = self.root.after(200, check)

    def dismiss_theme_on_click(self, event):
        if event.widget not in (self.btn_theme, self.theme_popup, *self.theme_buttons.values()):
            self.hide_theme_menu()

    def choose_theme(self, theme):
        self.hide_theme_menu()
        self.set_theme(theme)

    # -- 更多 ------------------------------------------------------------
    def show_more_menu(self):
        """Toolbar overflow: the place new secondary actions go."""
        self.hide_theme_menu()
        popup = self._tk.Menu(self.root, tearoff=0, background=self.pal["side"],
                              foreground=self.pal["fg"], activebackground=self.pal["sel"],
                              activeforeground=self.pal["fg"], bd=0)
        # 插件贡献的命令是动态的：每次打开菜单现取一遍，装/卸/启停后立刻反映
        entries = self.plugin_menu_entries()
        label, action = entries["insert"]
        popup.add_command(label=label, command=action)
        popup.add_command(label="粘贴剪贴板图片（截图）…  Ctrl+Shift+V",
                          command=self.insert_clipboard_image)
        if self.plugin_commands(core.PL.CAP_IMAGE_INSERT):
            from .media_ui import resize_image
            popup.add_command(label="调整图片大小…", command=lambda: resize_image(self))
        if entries["exports"]:
            formats = self._tk.Menu(popup, tearoff=0, background=self.pal["side"],
                                    foreground=self.pal["fg"], activebackground=self.pal["sel"],
                                    activeforeground=self.pal["fg"], bd=0)
            for label, action in entries["exports"]:
                formats.add_command(label=label, command=action)
            popup.add_cascade(label="用插件导出", menu=formats)
        else:
            popup.add_command(label="用插件导出（没有启用的导出插件）…", command=self.manage_plugins)
        label, action = entries["manage"]
        popup.add_command(label=label, command=action)
        popup.add_separator()
        popup.add_command(label="检查当前文档链接…", command=self.check_document_links)
        popup.add_command(label="插入表格…  Ctrl+Shift+T", command=self.insert_table_dialog)
        popup.add_command(label="插入行内公式…  Ctrl+Shift+M",
                          command=lambda: self.format_selection("formula_inline"))
        popup.add_command(label="插入独立公式…", command=lambda: self.format_selection("formula_block"))
        popup.add_command(label="编辑当前表格…", command=self.edit_table_dialog)
        popup.add_command(label="粘贴为表格…", command=self.paste_as_table)
        popup.add_command(label="代码块", command=lambda: self.format_selection("code_block"))
        popup.add_separator()
        popup.add_command(label="关于 %s" % core.APP_NAME, command=self.show_about)
        popup.add_command(label="打开工作区文件夹", command=self.open_workspace_folder)
        popup.add_command(label="快捷键说明", command=self.show_shortcuts)
        popup.add_command(label="另存为…  Ctrl+Shift+S", command=self.save_as)
        popup.add_command(label="文档内查找  Ctrl+F", command=self.find_in_document)
        popup.add_command(label="标题导航  Ctrl+L", command=self.show_outline)
        popup.add_command(label="恢复未保存文档", command=self.show_recovery)
        try:
            popup.tk_popup(self.btn_more.winfo_rootx(),
                           self.btn_more.winfo_rooty() + self.btn_more.winfo_height())
        finally:
            popup.grab_release()

    # -- 表格与常用格式（F04）---------------------------------------------
    def _abs_index(self, editor, index) -> int:
        """控件里的 ``line.col`` → 整篇偏移量。"""
        value = editor.count("1.0", index, "chars")
        if isinstance(value, (tuple, list)):
            value = value[0] if value else 0
        return int(value or 0)

    def _offset_index(self, editor, offset: int) -> str:
        """整篇偏移量 → 控件里的 ``line.col``。"""
        offset = max(0, int(offset))
        prefix = editor.get("1.0", "end-1c")[:offset]
        line = prefix.count("\n") + 1
        return "%d.%d" % (line, offset - (prefix.rfind("\n") + 1))

    def _caret_line(self, editor) -> int:
        return max(0, int(str(editor.index("insert")).split(".")[0]) - 1)

    def _selection_offsets(self, editor) -> tuple[int, int]:
        """当前选区（没有选区时就是光标位置）。"""
        try:
            ranges = editor.tag_ranges("sel")
        except Exception:
            ranges = ()
        if len(ranges) == 2:
            return self._abs_index(editor, ranges[0]), self._abs_index(editor, ranges[1])
        caret = self._abs_index(editor, "insert")
        return caret, caret

    def _apply_editor_result(self, editor, result) -> None:
        """把 ``result["text"]`` 的最小差异写回编辑器，并把选区放到 ``result`` 说的位置。

        整篇重写也能撤销，但会丢掉标记与滚动位置；这里只替换真正变化的片段。

        **一次动作必须能一次撤销**：写回用一次 ``replace``（Tk 把它记成一个撤销
        单元），而不是“先删后插”——后者会被 Tk 的自动分隔（``autoseparators``）
        切成两组，第一次撤销只回退一半（正文少了字、插入的标记还留着）。这条
        真的发生过一次，所以这里显式关掉自动分隔，结束时恢复原设置，不影响别的
        编辑路径的撤销粒度。
        """
        old = editor.get("1.0", "end-1c")
        new = result.get("text")
        if not isinstance(new, str) or new == old:
            return
        head = 0
        limit = min(len(old), len(new))
        while head < limit and old[head] == new[head]:
            head += 1
        tail = 0
        while tail < limit - head and old[len(old) - 1 - tail] == new[len(new) - 1 - tail]:
            tail += 1
        autoseparators = bool(editor.cget("autoseparators"))
        editor.configure(autoseparators=False)
        try:
            editor.edit_separator()
            editor.replace(self._offset_index(editor, head), self._offset_index(editor, len(old) - tail),
                           new[head:len(new) - tail])
            start, end = result.get("start"), result.get("end")
            if isinstance(start, int):
                editor.tag_remove("sel", "1.0", "end")
                editor.mark_set("insert", self._offset_index(editor, start))
                editor.tag_add("sel", self._offset_index(editor, start),
                               self._offset_index(editor, int(end if isinstance(end, int) else start)))
            editor.edit_separator()
        finally:
            editor.configure(autoseparators=autoseparators)
        editor.see("insert")
        editor.focus_set()

    def composing(self) -> bool:
        """输入法还在组合（有未确认的拼音）时为真。"""
        ime = self.ime
        surface = getattr(ime, "surface", None)
        return bool(surface is not None and getattr(surface, "text", ""))

    def editor_context(self):
        """当前编辑控件与它的正文；不在源码模式时先切到源码。"""
        editor = self.editor_for(self.active_tab) or self.editor
        if editor is None or not editor.winfo_exists():
            self.notice("没有打开的文档", error=True)
            return None, ""
        if self.mode != "source":
            self.show_source()
        return editor, editor.get("1.0", "end-1c")

    def format_selection(self, action: str, **options) -> bool:
        """工具栏动作：改的是缓冲区，不改磁盘，一次动作一次撤销。"""
        editor, text = self.editor_context()
        if editor is None:
            return False
        if self.composing():
            self.notice("输入法组合中，先完成这次输入再套格式", error=True)
            return False
        start, end = self._selection_offsets(editor)
        try:
            result = FM.apply(text, start, end, action, **options)
        except FM.FormatError as error:
            self.notice(str(error), error=True)
            return False
        if not result.get("ok"):
            self.notice(result.get("reason") or "这一步没有可修改的内容", error=True)
            return False
        self._apply_editor_result(editor, result)
        self.notice(result.get("note") or "已应用格式")
        return True

    def show_heading_menu(self, event=None):
        """标题级别下拉：H1–H6。"""
        popup = self._tk.Menu(self.root, tearoff=0, background=self.pal["side"],
                              foreground=self.pal["fg"], activebackground=self.pal["sel"],
                              activeforeground=self.pal["fg"], bd=0)
        for level in FM.HEADING_LEVELS:
            popup.add_command(label="H%d 标题" % level,
                              command=lambda n=level: self.format_selection("heading", level=n))
        button = getattr(self, "heading_button", None)
        x = button.winfo_rootx() if button is not None else self.root.winfo_rootx()
        y = (button.winfo_rooty() + button.winfo_height()) if button is not None else self.root.winfo_rooty()
        try:
            popup.tk_popup(x, y)
        finally:
            popup.grab_release()

    def show_formula_menu(self, event=None):
        popup = self._tk.Menu(self.root, tearoff=0, background=self.pal["side"],
                              foreground=self.pal["fg"], activebackground=self.pal["sel"],
                              activeforeground=self.pal["fg"], bd=0)
        popup.add_command(label="行内公式  $x$", command=lambda: self.format_selection("formula_inline"))
        popup.add_command(label="独立公式  $$…$$", command=lambda: self.format_selection("formula_block"))
        button = self.formula_button
        try:
            popup.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            popup.grab_release()

    def _table_result(self, editor, result, note: str) -> bool:
        if not result.get("ok"):
            self.notice(result.get("reason") or "表格没有改动", error=True)
            return False
        self._apply_editor_result(editor, result)
        extra = result.get("warnings") or []
        self.notice(note + ("（%d 条提示）" % len(extra) if extra else ""))
        return True

    def insert_table_dialog(self) -> bool:
        """「插入表格」：列数、数据行数、表头与逐列对齐，一次写入一次撤销。"""
        editor, text = self.editor_context()
        if editor is None:
            return False
        if self.composing():
            self.notice("输入法组合中，先完成这次输入", error=True)
            return False
        data = TableDialog(self.root, self.pal, self.ui_scale, mode="insert",
                           columns=3, rows=2).show()
        if not data:
            self.notice("已取消插入表格")
            return False
        fills = data["fills"] if data["has_header"] else data["fills"][1:]
        result = TB.operate(text, "insert_table", line=self._caret_line(editor),
                            columns=data["columns"], rows=data["rows"],
                            header=data["has_header"], aligns=data["aligns"], fills=fills)
        return self._table_result(editor, result, "已插入表格")

    def edit_table_dialog(self) -> bool:
        """「编辑表格」：行列增删、表头与对齐；结构看不懂时保留源码并说明。"""
        editor, text = self.editor_context()
        if editor is None:
            return False
        if self.composing():
            self.notice("输入法组合中，先完成这次输入", error=True)
            return False
        line = self._caret_line(editor)
        info = TB.read(text, line=line)
        if not info["found"]:
            self.notice("把光标放在某张表格里，再用「编辑表格」", error=True)
            return False
        if not info["ok"]:
            self.notice("这个表格不能安全修改：%s。已保留源码。" % info["reason"], error=True)
            return False
        rows = len(info["table"]["rows"])
        columns = len(info["table"]["header"])
        if rows > DIALOG_MAX_ROWS or columns > DIALOG_MAX_COLUMNS:
            self.notice("这张表格有 %d 列 %d 行，对话框不便编辑；可以直接在源码里改。"
                        % (columns, rows), error=True)
            return False
        data = TableDialog(self.root, self.pal, self.ui_scale, mode="edit",
                           table=info["table"]).show()
        if not data:
            self.notice("已取消编辑表格")
            return False
        result = TB.write_model(text, data["header"], data["fills"][1:], data["aligns"], line=line)
        return self._table_result(editor, result, "已更新表格")

    def paste_as_table(self, payload: str = None) -> bool:
        """把剪贴板（或传入的文本）当 TSV 读进来：先预览与提示，确认后才写入。"""
        editor, text = self.editor_context()
        if editor is None:
            return False
        if payload is None:
            try:
                payload = self.root.clipboard_get()
            except Exception:
                self.notice("剪贴板里没有文本", error=True)
                return False
        parsed = TB.parse_tsv(payload)
        if not parsed["ok"]:
            self.notice(parsed["warnings"][0] if parsed["warnings"] else "剪贴板里没有表格文本",
                        error=True)
            return False
        if parsed["columns"] < 2:
            self.notice("这段文本只有一列，用普通粘贴更合适", error=True)
            return False
        choice = TablePasteDialog(self.root, self.pal, self.ui_scale, parsed=parsed).show()
        if not choice:
            self.notice("已取消粘贴表格")
            return False
        start, end = self._selection_offsets(editor)
        result = TB.paste_tsv(text, payload, start=start, end=end, header=choice["header"])
        return self._table_result(editor, result, "已粘贴为表格")

    def on_editor_paste(self, event=None):
        """编辑器粘贴：剪贴板是多行多列文本时先给预览，普通粘贴交回 Tk。"""
        widget = event.widget if event is not None else self.editor
        if widget is None or widget is not self.editor or self.mode != "source":
            return None
        try:
            payload = self.root.clipboard_get()
        except Exception:
            return None
        if "\t" not in payload or "\n" not in payload.strip():
            return None
        parsed = TB.parse_tsv(payload)
        if not parsed["ok"] or parsed["columns"] < 2:
            return None
        return "break" if self.paste_as_table(payload) else "break"

    def show_about(self):
        InfoDialog(self.root, "关于 %s" % core.APP_NAME,
                   "%s %s" % (core.APP_NAME, core.APP_VERSION),
                   ["本地 Markdown 阅读与项目管理工具。",
                    "工作区：%s" % self.ws.root,
                    "当前主题：%s" % THEME_LABELS.get(self.theme, self.theme)],
                   self.pal, self.ui_scale).show()

    def show_shortcuts(self):
        rows = [list(item) for item in SHORTCUTS]
        InfoDialog(self.root, "快捷键说明", "常用快捷键", rows,
                   self.pal, self.ui_scale).show()

    def open_workspace_folder(self):
        try:
            self.ws.open_in_explorer(self.ws.root, select=False)
            self.notice("已打开工作区文件夹")
        except Exception as exc:
            self.notice("打开工作区失败：%s" % _s(exc), error=True)

    def apply_theme(self) -> None:
        pal = self.pal
        # Paint every classic Tk descendant, including anonymous container
        # frames, labels and disabled buttons that the old theme skipped.
        def paint(widget, background):
            if widget in (self.side, self.bar, self.tabbar, self.status, self.theme_popup):
                background = pal["side"]
            kind = widget.winfo_class()
            if kind in ("Tk", "Frame", "Label", "Canvas"):
                widget.configure(background=background)
            if kind == "Label":
                widget.configure(foreground=pal["fg"])
            elif kind == "Button":
                widget.configure(background=pal["button"], foreground=pal["fg"],
                                 activebackground=pal["hover"], activeforeground=pal["fg"],
                                 disabledforeground=pal["faint"], relief="flat", bd=0,
                                 highlightbackground=background, highlightcolor=pal["accent"],
                                 highlightthickness=1)
            elif kind == "Listbox":
                widget.configure(background=pal["tree_bg"], foreground=pal["fg"],
                                 selectbackground=pal["sel"], selectforeground=pal["fg"],
                                 highlightthickness=0)
            if widget is self.text:
                return
            for child in widget.winfo_children():
                paint(child, background)
        paint(self.root, pal["bg"])
        for label in (self.lbl_path, self.lbl_count, self.lbl_recent, self.lbl_state, self.lbl_root):
            label.configure(foreground=pal["muted"])
        self.style.configure(".", background=pal["side"], foreground=pal["fg"],
                             troughcolor=pal["side"], bordercolor=pal["rule"],
                             lightcolor=pal["rule"], darkcolor=pal["rule"])
        for name in ("TEntry", "TCombobox"):
            self.style.configure(name, fieldbackground=pal["bg"], background=pal["button"],
                                 foreground=pal["fg"], insertcolor=pal["fg"], padding=5,
                                 arrowcolor=pal["muted"], bordercolor=pal["rule"],
                                 lightcolor=pal["rule"], darkcolor=pal["rule"],
                                 selectbackground=pal["sel"], selectforeground=pal["fg"])
            self.style.map(name, fieldbackground=[("disabled", pal["side"]), ("readonly", pal["bg"])],
                           foreground=[("disabled", pal["faint"]), ("readonly", pal["fg"])],
                           bordercolor=[("focus", pal["accent"])],
                           background=[("active", pal["hover"])])
        self.style.configure("Treeview", background=pal["tree_bg"], fieldbackground=pal["tree_bg"],
                             foreground=pal["fg"], borderwidth=0)
        self.style.configure("Treeview.Heading", background=pal["side"], foreground=pal["muted"])
        self.style.map("Treeview", background=[("selected", pal["sel"])], foreground=[("selected", pal["fg"])])
        if hasattr(self, "folder_arrows"):
            self.folder_arrows.schedule()
        for orientation in ("Vertical", "Horizontal"):
            name = orientation + ".TScrollbar"
            self.style.configure(name, background=pal["thumb"], troughcolor=pal["side"],
                                 bordercolor=pal["side"], lightcolor=pal["thumb"],
                                 darkcolor=pal["thumb"], arrowsize=self.px(12), borderwidth=0)
            self.style.map(name, background=[("active", pal["muted"]), ("pressed", pal["accent"])])
            self.style.layout(name, [(orientation + ".Scrollbar.trough", {"sticky": "nswe", "children": [
                (orientation + ".Scrollbar.thumb", {"sticky": "nswe", "expand": "1"})]})])
        # The combobox popup is a classic listbox outside the widget tree.
        for option, value in (("background", pal["bg"]), ("foreground", pal["fg"]),
                              ("selectBackground", pal["sel"]), ("selectForeground", pal["fg"])):
            self.root.option_add("*TCombobox*Listbox." + option, value)
        popup = self.root.tk.call("ttk::combobox::PopdownWindow", self.cmb)
        self.root.tk.call(str(popup) + ".f.l", "configure", "-background", pal["bg"],
                          "-foreground", pal["fg"], "-selectbackground", pal["sel"],
                          "-selectforeground", pal["fg"])
        self._invalidate_styles()
        self._style_widget(self.text)            # 只重排屏幕上的控件
        self._style_widget(self.preview)
        self._refresh_imes()
        self.draw_tabs()
        for key, button in self.theme_buttons.items():
            button.configure(text=("✓ " if key == self.theme else "") + THEME_LABELS[key],
                             background=pal["sel"] if key == self.theme else pal["button"])
        self.theme_popup.configure(highlightbackground=pal["rule"])
        from .display import style_titlebar
        style_titlebar(self.root, self.dark, pal["side"], pal["fg"])
        self.update_title()
        self.update_status()

    def set_theme(self, theme, persist=True):
        if theme not in THEMES:
            return
        position = self.text.yview()[0]
        self.stash_tab()
        self.theme, self.dark, self.pal = theme, theme == "dark", dict(THEMES[theme])
        self.apply_theme()
        if self.mode == "preview" and self.active_tab is not None:
            self.render()
            self.root.update_idletasks()
            self.preview.yview_moveto(position)
        self.preferences["theme"] = theme
        if not persist:
            self.notice("已切换到%s模式（来自浏览器视图）" % THEME_LABELS[theme])
            return
        try:
            core.write_ui_settings(self.ws.root, theme)
        except OSError as exc:
            self.notice("主题已切换，但偏好未能保存：%s" % exc, error=True)
            return
        self.notice("已切换到%s模式" % THEME_LABELS[theme])

    def watch_shared_theme(self):
        """Follow a theme changed in the browser view (same workspace file)."""
        job = getattr(self, "_theme_watch_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._theme_watch_job = None
        if not self.root.winfo_exists():
            return
        try:
            stored = core.read_ui_settings(self.ws.root)["theme"]
        except Exception:
            stored = self.theme
        if stored != self.theme and stored in THEMES:
            try:
                self.set_theme(stored, persist=False)
            except Exception as exc:
                self.notice("同步浏览器视图主题失败：%s" % _s(exc), error=True)
        self._theme_watch_job = self.root.after(1500, self.watch_shared_theme)

    def toggle_theme(self) -> None:
        keys = list(THEMES)
        self.set_theme(keys[(keys.index(self.theme) + 1) % len(keys)])

    def on_zoom(self, event=None) -> None:
        """One Ctrl+wheel tick: aim the size, then re-typeset at most per frame.

        ``base_size`` is the *target* the reader is aiming at, so a fast gesture
        never loses ticks, while the actual re-typeset is coalesced: on a long
        document one restyle costs tens of milliseconds (Tk re-measures the
        whole text), and queueing one per wheel message made the window fall
        further behind with every tick.  The first tick is applied immediately so
        the size still follows the wheel 1:1 when the reader turns it slowly.
        """
        import time
        previous = self.base_size
        if event is not None and getattr(event, "delta", 0):
            self.base_size = min(20, max(8, self.base_size + (1 if event.delta > 0 else -1)))
        if self.base_size == previous:
            return "break"
        now = time.perf_counter()
        if now - getattr(self, "_zoom_last", 0.0) >= .032:
            self._apply_zoom()
        elif getattr(self, "_zoom_job", None) is None:
            self._zoom_job = self.root.after(32, self._apply_zoom)
        return "break"

    def _apply_zoom(self):
        import time
        self._zoom_job = None
        widget = self.text
        # Read the anchor *before* restyling and only restore it when the text
        # actually reflowed onto different lines; yview() itself forces a full
        # layout pass, which is the most expensive part of a zoom tick.
        anchor = widget.index("@0,0")
        self._invalidate_styles()
        self._style_widget(widget)
        self._refresh_imes(widget)
        if widget.index("@0,0") != anchor:
            widget.yview(anchor)
        self._zoom_last = time.perf_counter()

    def _document_scroll(self, widget, first, last, horizontal=False):
        if widget is self.text:
            (self.doc_hscroll if horizontal else self.doc_vscroll).set(first, last)

    # -- projects --------------------------------------------------------
    def refresh_projects(self, keep: bool = True) -> None:
        """Re-read the workspace, keeping the selection when asked to."""
        try:
            self.projects = list(self.ws.list_projects())
        except Exception as exc:
            self.projects = []
            self.notice("读取项目失败：%s" % _s(exc), error=True)
        self.cmb.configure(values=["%s (%d)" % (p.get("name") or p.get("id"), p.get("doc_count") or 0)
                                   for p in self.projects])
        if not self.projects:
            self.cur_pid = None
            try:
                self.cmb.set("")
                self.cmb.configure(state="disabled")
                self.tree.delete(*self.tree.get_children())
            except Exception:
                pass
            self.lbl_path.configure(text="尚未创建任何项目")
            if self.active_tab is not None:
                return
            self.cur_doc, self.source = None, ""
            self.show_empty("还没有项目。\n点击左上角「＋ 新建项目」开始。")
            return
        self.cmb.configure(state="readonly")
        idx = 0
        if keep and self.cur_pid:
            idx = next((k for k, p in enumerate(self.projects) if p.get("id") == self.cur_pid), 0)
        self.cmb.current(idx)
        self.cur_pid = _s(self.projects[idx].get("id"))
        self.load_project(self.cur_pid, keep_doc=keep)

    def on_project_change(self) -> None:
        idx = self.cmb.current()
        if idx < 0 or idx >= len(self.projects) or _s(self.projects[idx].get("id")) == self.cur_pid:
            return
        self.stash_tab()
        self.active_tab = None
        self.cur_pid, self.cur_doc, self.source = _s(self.projects[idx].get("id")), None, ""
        self.cur_loose = None
        self.set_dirty(False)
        self.load_project(self.cur_pid, keep_doc=False)

    def select_current_project(self) -> None:
        for k, p in enumerate(self.projects):
            if p.get("id") == self.cur_pid:
                try:
                    self.cmb.current(k)
                except Exception:
                    pass
                return

    def load_project(self, pid: str, keep_doc: bool = True) -> None:
        try:
            pdir = self.ws.require_project(pid)
            self.docs = list(self.ws.scan_docs(pdir))
            self.dirs = list(self.ws.list_dirs(pdir))
        except Exception as exc:
            self.docs = []
            self.dirs = []
            self.notice("读取项目失败：%s" % _s(exc), error=True)
            self.show_empty("这个项目暂时无法读取。")
            return
        self.fill_tree(self.docs, self.dirs)
        self.lbl_path.configure(text=pdir)
        if keep_doc and self.cur_loose:
            return
        current = next((d for d in self.docs if d["id"] == (self.cur_doc or {}).get("id")), None)
        if keep_doc and current and not self.dirty:
            self.load_doc(current)                  # re-read the file from disk
        elif keep_doc and self.dirty and current:
            pass                                    # unsaved edits win over a refresh
        elif self.docs:
            if self.cur_doc and not self.confirm_switch():
                return                              # the open file vanished: ask first
            self.open_doc(self.docs[0]["id"], force=True)
        else:
            if self.cur_doc and not self.confirm_switch():
                return
            self.cur_doc, self.source = None, ""
            self.show_empty("这个项目里还没有文档。\n把 .md 文件放进项目目录，或用 F5 刷新。")

    def fill_tree(self, docs, dirs=None) -> None:
        """把项目文档画成树。**每个目录行都带一个「＋」**，点它就在该目录新建 Untitled.md。

        目录行不只来自文档所在的目录：空目录（还没有任何 .md 的子目录）也要出现，
        否则用户没法在空目录里新建第一篇。
        """
        try:
            self.tree.delete(*self.tree.get_children())
        except Exception:
            return
        self._project_nodes = {}
        folders: dict[str, str] = {}

        def ensure_dir(path: str) -> str:
            """按需建出各级目录行，返回最深层那一行的 iid。"""
            parent, built = "", ""
            for part in [p for p in path.split("/") if p]:
                built = built + "/" + part if built else part
                if built not in folders:
                    folders[built] = self.tree.insert(
                        parent, "end", text="\U0001f4c1 " + part + "   \uff0b",
                        open=True, tags=("plus",))
                    self._project_nodes[folders[built]] = built
                parent = folders[built]
            return parent

        for doc in docs:
            parts = [p for p in _s(doc.get("id")).split("/") if p]
            parent = ensure_dir("/".join(parts[:-1]))
            iid = "d:" + _s(doc.get("id"))
            self.tree.insert(parent, "end", iid=iid, text="\U0001f4c4 " + _s(doc.get("name")))
            self._project_nodes[iid] = _s(doc.get("id"))
        for path in sorted(dirs or []):
            if path:
                ensure_dir(path)
        if self.cur_doc:
            self.select_in_tree(_s(self.cur_doc.get("id")))

    def on_project_tree_menu(self, event) -> str:
        row = self.tree.identify_row(event.y)
        if not row or row not in self._project_nodes:
            return "break"
        self.tree.selection_set(row)
        self.tree.focus(row)
        self.tree.focus_set()
        previous_menu = getattr(self, "_project_menu", None)
        if previous_menu is not None:
            previous_menu.destroy()
        menu = self._tk.Menu(self.root, tearoff=False, bg=self.pal["side"],
                             fg=self.pal["fg"])
        self._project_menu = menu
        if row.startswith("d:"):
            menu.add_command(label="打开", command=self.on_tree_activate)
        menu.add_command(label="更改标题…", accelerator="Ctrl+M",
                         command=self.on_project_tree_rename)
        menu.add_command(label="查看文件地址", accelerator="Ctrl+Y",
                         command=self.on_project_tree_path)
        menu.add_command(label="删除…", accelerator="Delete",
                         command=self.on_project_tree_delete)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def on_project_tree_path(self, event=None) -> str:
        selected = self.tree.selection()
        if not self.cur_pid or not selected:
            return "break"
        relative = self._project_nodes.get(selected[0])
        if relative is None:
            return "break"
        try:
            path = core.safe_join(self.ws.require_project(self.cur_pid), relative)
            PathDialog(self.root, path, self.pal, self.ui_scale).show()
        except Exception as exc:
            self.notice("无法查看地址：%s" % exc, error=True)
        return "break"

    def on_project_tree_rename(self, event=None) -> str:
        if event is not None and getattr(event, "num", None) == 1:
            row = self.tree.identify_row(event.y)
            if not row or "indicator" in self.tree.identify_element(event.x, event.y):
                return "break"
            self.tree.selection_set(row)
            self.tree.focus(row)
        selected = self.tree.selection()
        if not self.cur_pid or not selected:
            return "break"
        relative = self._project_nodes.get(selected[0])
        if not relative:
            return "break"
        try:
            pdir = self.ws.require_project(self.cur_pid)
            old_path = core.safe_join(pdir, relative)
            old_title = os.path.basename(old_path)
            if os.path.isfile(old_path):
                old_title = os.path.splitext(old_title)[0]
            title = RenamePrompt(self.root, old_title, self.pal, self.ui_scale).show()
            if title is None or title == old_title:
                return "break"
            self.stash_tab()
            result = self.ws.rename_project_entry(self.cur_pid, relative, title)
        except Exception as exc:
            self.notice("更改标题失败：%s" % exc, error=True)
            return "break"
        if result["path"] == old_path:
            return "break"
        for tab in self.tabs:
            info = tab.get("loose") or tab.get("doc") or {}
            path = info.get("path") or info.get("_abs")
            if not path or not core.is_within(old_path, path):
                continue
            identity = self.recovery_identity(tab)
            suffix = os.path.relpath(path, old_path)
            new_path = result["path"] if suffix == "." else os.path.join(result["path"], suffix)
            fresh = dict(info)
            fresh.update(_abs=new_path, file=os.path.basename(new_path),
                         name=os.path.splitext(os.path.basename(new_path))[0])
            if tab.get("loose"):
                fresh.update(path=new_path, id=new_path, dir=os.path.dirname(new_path))
                self.loose._opened.add(self.loose._key(os.path.realpath(new_path)))
                tab["loose"] = fresh
                tab["key"] = self._tab_key(loose=fresh)
            else:
                rel = os.path.relpath(new_path, self.ws.require_project(tab["pid"])).replace(os.sep, "/")
                fresh.update(id=rel, dir=os.path.dirname(rel))
                tab["doc"] = fresh
                tab["key"] = self._tab_key(doc=fresh, pid=tab["pid"])
            if tab is self.active_tab:
                self.cur_doc, self.cur_loose = tab.get("doc"), tab.get("loose")
            try:
                if tab.get("dirty"):
                    self.ws.recovery.save(new_path, tab["source"], new_path, fresh.get("revision", "missing"))
                if identity != new_path:
                    self.ws.recovery.discard(identity)
            except OSError as exc:
                self.notice("标题已修改，但恢复快照未更新：%s" % exc, error=True)
        try:
            recent = self.loose._read()
            for item in recent:
                if core.is_within(old_path, item["path"]):
                    suffix = os.path.relpath(item["path"], old_path)
                    item["path"] = result["path"] if suffix == "." else os.path.join(result["path"], suffix)
                    item["name"] = os.path.splitext(os.path.basename(item["path"]))[0]
            self.loose._write(recent)
            self.refresh_recent()
        except OSError:
            pass
        self.docs = list(self.ws.scan_docs(pdir))
        self.dirs = list(self.ws.list_dirs(pdir))
        self.fill_tree(self.docs, self.dirs)
        for row, path in self._project_nodes.items():
            if path == result["relative"]:
                self.tree.selection_set(row)
                self.tree.focus(row)
                self.tree.see(row)
                break
        self.draw_tabs()
        self.update_title()
        self.update_status()
        self.notice("标题已修改，本地名称已同步为「%s」" % os.path.basename(result["path"]))
        return "break"

    def on_project_tree_delete(self, event=None) -> str:
        selected = self.tree.selection()
        if not self.cur_pid or not selected:
            return "break"
        relative = self._project_nodes.get(selected[0])
        if not relative:
            return "break"
        pid = self.cur_pid
        try:
            pdir = self.ws.require_project(pid)
            target = core.safe_join(pdir, relative)
            if not os.path.exists(target):
                raise FileNotFoundError("文件或文件夹已经不存在")
            self.stash_tab()
            affected = []
            for tab in self.tabs:
                info = tab.get("loose") or tab.get("doc") or {}
                path = info.get("path") or info.get("_abs")
                if path and core.is_within(target, path):
                    affected.append(tab)
            message = "将删除「%s」，并将本地内容移入 Windows 回收站。" % relative
            if os.path.isdir(target):
                message += "\n文件夹内的所有文件和子文件夹也会一起删除。"
            message += "\n\n完整路径：%s" % target
            dirty = sum(tab_needs_save(tab) for tab in affected)
            if dirty:
                message += "\n\n其中 %d 个已打开的文档有未保存修改；继续删除将丢弃这些修改，且无法从回收站恢复。" % dirty
            if SavePrompt(self.root, message, self.pal, self.ui_scale, deleting=True).show() is not True:
                return "break"
            self.ws.delete_project_entry(pid, relative)
        except Exception as exc:
            self.notice("删除失败：%s" % exc, error=True)
            return "break"
        # Only discard buffers after the filesystem operation succeeds.
        active_removed = self.active_tab in affected
        for tab in affected:
            try:
                self.ws.recovery.discard(self.recovery_identity(tab))
            except OSError:
                pass  # A stale recovery snapshot must not keep a deleted tab writable.
            self._release_editor(tab)
            self.tabs.remove(tab)
        if active_removed:
            self.active_tab = None
            self.cur_doc = self.cur_loose = None
            self.source, self.dirty = "", False
            if self.tabs:
                self.activate_tab(self.tabs[-1])
            else:
                self.show_empty("没有打开的文档\n点击「打开本地文件」或按 Ctrl+O。")
        self.projects = list(self.ws.list_projects())
        self.cmb.configure(values=["%s (%d)" % (p.get("name") or p["id"], p.get("doc_count") or 0)
                                   for p in self.projects])
        self.select_current_project()
        if self.cur_pid:
            current_dir = self.ws.require_project(self.cur_pid)
            self.docs = list(self.ws.scan_docs(current_dir))
            self.dirs = list(self.ws.list_dirs(current_dir))
            self.fill_tree(self.docs, self.dirs)
        self.draw_tabs()
        self._sync_view()
        self.notice("已将「%s」及其本地内容移入回收站" % relative)
        return "break"

    def on_plus_click(self, event=None) -> str:
        """目录行末尾的「＋」：在该目录里新建一篇未命名文档。"""
        row = ""
        try:
            if event is not None:
                from tkinter import font
                row = self.tree.identify_row(event.y)
                if not row or row.startswith("d:"):
                    return "break"
                # A folder's text and expander remain available for selection/rename.
                if "text" not in self.tree.identify_element(event.x, event.y):
                    return
                text = self.tree.item(row, "text")
                face = font.Font(font=self.style.lookup("Treeview", "font") or "TkDefaultFont")
                # Locate the text element's left edge (includes tree indentation).
                left = event.x
                while left > 0 and "text" in self.tree.identify_element(left - 1, event.y):
                    left -= 1
                start = left + face.measure(text.rstrip("＋"))
                if not start <= event.x <= start + face.measure("＋") + self.px(4):
                    return
                self.tree.selection_set(row)
                self.tree.focus(row)
            else:
                row = _s(self.tree.focus())
        except Exception:
            return "break"
        path = self._project_nodes.get(row)
        if path is not None:
            self.new_untitled_in(path)
        return "break"

    def new_untitled_in(self, subdir: str = "") -> None:
        """在项目里的某个目录新建 ``Untitled.md``（重名自动顺延为 ``Untitled-2.md``）。"""
        if not self.cur_pid:
            return
        try:
            info = self.ws.create_doc(self.ws.require_project(self.cur_pid),
                                      "Untitled", subdir=subdir or "")
        except Exception as exc:
            self.notice("新建失败：%s" % _s(exc), error=True)
            return
        self.load_project(self.cur_pid, keep_doc=True)
        self.select_in_tree(_s(info.get("id")))
        self.open_doc(_s(info.get("id")), force=True)
        self.notice("已在「%s」新建 %s" % (subdir or "项目根目录", _s(info.get("file"))))

    def select_in_tree(self, did: str) -> None:
        iid = "d:" + did
        try:
            if did and self.tree.exists(iid):
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
        except Exception:
            pass

    # -- 我的文件夹（F01）：直接读写用户原目录，不复制不导入 ----------------
    def refresh_roots(self, force: bool = False) -> None:
        """重读已登记的原文件夹，尽量保持当前选择。"""
        try:
            self.roots = list(self.ws.folders.roots())
        except Exception as exc:
            self.roots = []
            self.notice("读取文件夹失败：%s" % _s(exc), error=True)
        self.cmb_root.configure(values=[_s(r.get("name") or r.get("path")) for r in self.roots])
        if not self.roots:
            self.cur_root = None
            try:
                self.cmb_root.set("")
                self.cmb_root.configure(state="disabled")
                self.root_tree.delete(*self.root_tree.get_children())
            except Exception:
                pass
            self.root_docs = []
            self._root_nodes = {}
            self.lbl_root.configure(text="用「打开文件夹」直接管理你自己的目录")
            self._folder_signature = None
            return
        self.cmb_root.configure(state="readonly")
        index = 0
        if self.cur_root:
            index = next((k for k, r in enumerate(self.roots) if r.get("id") == self.cur_root), 0)
        try:
            self.cmb_root.current(index)
        except Exception:
            pass
        self.cur_root = _s(self.roots[index].get("id"))
        self.refresh_folder_tree(force=force)

    def current_root(self) -> dict | None:
        return next((r for r in self.roots if r.get("id") == self.cur_root), None)

    def on_root_change(self) -> None:
        index = self.cmb_root.current()
        if index < 0 or index >= len(self.roots):
            return
        rid = _s(self.roots[index].get("id"))
        if rid == self.cur_root:
            return
        self.cur_root = rid
        self._folder_signature = None
        self.refresh_folder_tree(force=True)

    def open_user_folder(self) -> None:
        """选择一个用户自己的文件夹，登记后直接读写原件。"""
        try:
            paths = core._dialog_folder()
        except Exception as exc:
            self.notice("打开文件夹失败：%s" % _s(exc), error=True)
            return
        if not paths:
            return
        try:
            info = self.ws.folders.add(paths[0])
        except Exception as exc:
            self.notice("无法打开这个文件夹：%s" % _s(exc), error=True)
            return
        self.cur_root = _s(info.get("id"))
        self._root_tree_id = None  # Each explicit open starts with every folder collapsed.
        self._folder_signature = None
        self.start_folder_watch()
        self.refresh_roots(force=True)
        self._roots_seen = tuple(r.get("id") for r in self.roots)
        self.notice("已打开文件夹「%s」，修改直接写回原文件" % _s(info.get("name")))

    def start_folder_watch(self) -> None:
        try:
            watcher = self.ws.folders.watch()
            if self._mark_folders_dirty not in getattr(watcher, "_listeners", ()):
                watcher.add_listener(self._mark_folders_dirty)
            watcher.start()
        except Exception:
            pass

    def refresh_folder_tree(self, force: bool = False) -> None:
        """扫一遍当前根目录并把树画出来；内容没变就不重画（保住阅读位置）。"""
        if not self.cur_root:
            return
        try:
            tree = self.ws.folders.scan(self.cur_root, force=force)
        except Exception as exc:
            self.root_docs = []
            self._root_nodes = {}
            try:
                self.root_tree.delete(*self.root_tree.get_children())
            except Exception:
                pass
            self.lbl_root.configure(text="这个文件夹暂时读不到：%s" % _s(exc))
            return
        files = tree.get("files", tree["docs"])
        signature = (tuple((d["id"], d["mtime_ts"], d["size"]) for d in files),
                     tuple(d["id"] for d in tree.get("dirs", [])))
        if not force and signature == self._folder_signature:
            self.lbl_root.configure(text=self._root_caption(tree))
            return
        self._folder_signature = signature
        self.root_docs = list(tree["docs"])
        self.fill_root_tree(files, tree.get("dirs", []))
        self.lbl_root.configure(text=self._root_caption(tree))

    def _root_caption(self, tree: dict) -> str:
        root = self.current_root() or {}
        text = _s(root.get("path"))
        counts = tree.get("counts") or {}
        text += "\n%d 个文件 · %d 个文件夹" % (int(counts.get("files", counts.get("docs")) or 0),
                                                  int(counts.get("dirs") or 0))
        if tree.get("truncated"):
            text += "\n⚠ " + _s(tree["truncated"])
        if tree.get("skipped_links"):
            text += "\n已跳过越界链接：%s" % ", ".join(_s(x) for x in tree["skipped_links"][:3])
        return text

    def fill_root_tree(self, docs, dirs=None) -> None:
        same_root = getattr(self, "_root_tree_id", None) == self.cur_root
        expanded = {row for row in self._root_nodes if self.root_tree.exists(row)
                    and self.root_tree.item(row, "open")} if same_root else set()
        selected = self.root_tree.selection() if same_root else ()
        try:
            self.root_tree.delete(*self.root_tree.get_children())
        except Exception:
            return
        self._root_nodes = {}
        self._root_tree_id = self.cur_root
        folders: dict[str, str] = {}
        def ensure_dir(relative):
            parent, path = "", ""
            for part in relative.split("/"):
                if not part:
                    continue
                path = path + "/" + part if path else part
                if path not in folders:
                    iid = "r:" + path
                    folders[path] = self.root_tree.insert(parent, "end",
                                                          iid=iid, text="\U0001f4c1 " + part,
                                                          open=iid in expanded)
                    self._root_nodes[folders[path]] = path
                parent = folders[path]
            return parent
        for directory in dirs or []:
            ensure_dir(directory["id"])
        for doc in docs:
            parent = ensure_dir(os.path.dirname(_s(doc.get("id"))).replace(os.sep, "/"))
            iid = "f:" + _s(doc.get("id"))
            self.root_tree.insert(parent, "end", iid=iid, text="\U0001f4c4 " + _s(doc.get("file") or doc.get("name")))
            self._root_nodes[iid] = _s(doc.get("id"))
        for row in selected:
            if self.root_tree.exists(row):
                self.root_tree.selection_set(row)
                self.root_tree.focus(row)
        self.folder_arrows.schedule()

    def on_folder_select(self, event):
        if not self.root_tree.identify_row(event.y):
            self.root_tree.selection_remove(*self.root_tree.selection())


    def select_in_root_tree(self, info: dict) -> None:
        """让当前打开的文档在文件夹树里高亮（只认根目录 + 相对路径）。"""
        if not self.cur_root:
            return
        path = _s((info or {}).get("path"))
        if not path:
            return
        root = self.current_root() or {}
        try:
            rel = os.path.relpath(path, _s(root.get("path"))).replace(os.sep, "/")
        except Exception:
            return
        iid = "f:" + rel
        try:
            if self.root_tree.exists(iid):
                self.root_tree.selection_set(iid)
                self.root_tree.focus(iid)
                self.root_tree.see(iid)
        except Exception:
            pass

    def _ask(self, title: str, message: str, yes: str, no: str, error: bool = False) -> bool:
        """是/否确认框：默认焦点在“否”（取消）一侧。"""
        from tkinter import messagebox
        try:
            return bool(messagebox.askyesno(title, message, parent=self.root,
                                            default=messagebox.NO,
                                            icon=(messagebox.WARNING if error else messagebox.QUESTION)))
        except Exception:
            return False

    def _loose_tab_for(self, path: str):
        for tab in self.tabs:
            loose = tab.get("loose") or {}
            current = _s(loose.get("path"))
            if current and os.path.normcase(current) == os.path.normcase(path):
                return tab
        return None

    def _confirm_unsaved_before_delete(self, info: dict):
        """有未保存修改时的三选一：另存后继续 / 放弃修改并继续 / 取消。"""
        from tkinter import messagebox
        try:
            choice = messagebox.askyesnocancel(
                "删除本地文件…",
                "「%s」有未保存的修改。\n\n"
                "是：先另存修改，再删除原文件\n"
                "否：放弃修改并继续删除\n"
                "取消：什么都不做" % _s(info.get("file")), parent=self.root)
        except Exception:
            return None
        if choice is None:
            return None
        if choice:
            # 「另存修改后继续」：**必须另存到新位置**，不能写回即将被删除的原文件，
            # 否则用户选的是“保留修改”，结果修改随原文件一起进回收站。
            ok = self.save_as() if (self.cur_loose or {}).get("path") else self.save_doc()
            if not ok:
                return None                  # 另存失败或取消：不删除
        else:
            if not self._ask(
                    "删除本地文件…",
                    "未保存的修改也会一并丢弃，且不会进入回收站。\n\n"
                    "确定删除「%s」吗？\n完整路径：%s"
                    % (_s(info.get("file")), _s(info.get("_abs"))), "移入回收站", "取消", error=True):
                return None
        return True

    def delete_folder_doc(self, did: str) -> None:
        """删除**用户的本地文件**：默认移入回收站，失败绝不改为永久删除。"""
        if not self.cur_root or not did:
            return
        try:
            info = self.ws.folders.doc_info(self.cur_root, did)
        except Exception as exc:
            self.notice("找不到这个文件：%s" % _s(exc), error=True)
            self.refresh_folder_tree(force=True)
            return
        path = _s(info["_abs"])
        tab = self._loose_tab_for(path)
        if tab is not None and tab is self.active_tab:
            if self._confirm_unsaved_before_delete(info) is None:
                return
        elif tab is not None and tab_needs_save(tab):
            if not self._ask(
                    "删除本地文件…",
                    "「%s」还有未保存的修改。\n\n继续删除会连同这些未保存内容一起丢弃"
                    "（它们不在回收站里，无法找回）。\n\n删除这个文件吗？"
                    % _s(info.get("file")), "继续删除", "取消", error=True):
                return
        elif not self._ask(
                "删除本地文件…",
                "将删除本地文件「%s」，并移入 Windows 回收站。\n"
                "它会从原文件夹消失，不只是从 MDReader 列表中移除。\n"
                "其他文档和附件不会删除。\n\n完整路径：%s"
                % (_s(info.get("file")), path), "移入回收站", "取消", error=True):
            return
        try:
            removed = self.ws.folders.remove_doc(self.cur_root, did,
                                                expected_revision=info.get("revision"))
        except Exception as exc:
            # 失败一律保留列表与编辑缓冲，并说明原因
            self.notice("删除失败，文件已保留：%s" % _s(exc), error=True)
            self.refresh_folder_tree(force=True)
            return
        self._after_folder_delete(removed)
        # 顺序很重要：先给“已移入回收站”，如果这份文档还开着，再把“只能另存”
        # 覆盖上去——否则用户只会看到删除成功，看不到自己的缓冲已经无处可存。
        if getattr(self, "_deleted_tab_notice", ""):
            self.set_status(self._deleted_tab_notice)
            self._deleted_tab_notice = ""
        else:
            self.notice("已把「%s」移入回收站，可从回收站还原" % _s(removed.get("file")))

    def _after_folder_delete(self, removed: dict) -> None:
        """删除成功后：刷新树，把“源文件已不在”的标签留在只能另存的状态。"""
        path = _s(removed.get("path"))
        self.refresh_folder_tree(force=True)
        tab = self._loose_tab_for(path)
        if tab is None:
            return
        info = dict(tab.get("loose") or {})
        info["deleted"] = True
        info["missing"] = True
        tab["loose"] = info
        if self.active_tab is tab:
            self.cur_loose = info
            self.update_title()
            self.draw_tabs()
            self._deleted_tab_notice = (
                "已把「%s」移入回收站；未保存内容还在缓冲里，只能「另存为」到新位置"
                % _s(removed.get("file")))

    def on_folder_delete(self, did: str = "") -> None:
        if not did:
            try:
                selection = self.root_tree.selection()
            except Exception:
                selection = ()
            did = self._root_nodes.get(selection[0], "") if selection else ""
        if did:
            self.delete_folder_doc(did)

    def on_root_plus_click(self, event=None) -> str:
        """原文件夹树里目录行末尾的「＋」：在该目录新建 Untitled.md。"""
        if not self.cur_root:
            return "break"
        row = ""
        try:
            row = _s(self.root_tree.focus())
        except Exception:
            return "break"
        subdir = self._root_nodes.get(row)
        if subdir is None:
            return "break"
        try:
            info = self.ws.folders.create_doc(self.cur_root, "Untitled", subdir=subdir or "",
                                              unique=True)
        except Exception as exc:
            self.notice("新建失败：%s" % _s(exc), error=True)
            return "break"
        self.refresh_folder_tree(force=True)
        self.notice("已在「%s」新建 %s" % (subdir or "根目录", _s(info.get("file"))))
        self.open_local_files([info["_abs"]])
        return "break"

    def on_folder_activate(self, event=None) -> None:
        selection = ()
        try:
            selection = self.root_tree.selection()
        except Exception:
            return
        if not selection:
            return
        did = self._root_nodes.get(selection[0])
        if not did or not self.cur_root:
            return
        if selection[0].startswith("r:"):
            self.folder_arrows.toggle(selection[0])
            return "break"
        if not did.lower().endswith(core.DOC_EXTS):
            self.reveal_folder_item(did)
            return "break"
        try:
            info = self.ws.folders.doc_info(self.cur_root, did)
        except Exception as exc:
            self.notice("打开失败：%s" % _s(exc), error=True)
            self.refresh_folder_tree(force=True)
            return
        self.open_local_files([info["_abs"]])

    def on_folder_menu(self, event) -> None:
        row = self.root_tree.identify_row(event.y)
        if row:
            try:
                self.root_tree.selection_set(row)
            except Exception:
                pass
        self.show_folder_menu(event)

    def show_folder_menu(self, event=None) -> None:
        """文件夹树的菜单：新建 / 刷新 / 在资源管理器中打开 / 从列表移除。"""
        tk = self._tk
        from tkinter import simpledialog
        menu = tk.Menu(self.root, tearoff=0)
        selected = ()
        try:
            selected = self.root_tree.selection()
        except Exception:
            pass
        did = self._root_nodes.get(selected[0]) if selected else ""
        menu.add_command(label="新建 Markdown…", command=self.new_folder_doc)
        menu.add_command(label="刷新", command=lambda: self.refresh_folder_tree(force=True))
        if did:
            menu.add_separator()
            menu.add_command(label="在资源管理器中显示",
                             command=lambda: self.reveal_folder_item(did))
        if did and selected[0].startswith("f:") and did.lower().endswith(core.DOC_EXTS):
            menu.add_command(label="重命名…", command=lambda: self.rename_folder_doc(did))
            # 与「从列表移除」严格分开：这条会真的动磁盘上的文件
            menu.add_command(label="删除本地文件…", command=lambda: self.delete_folder_doc(did))
        menu.add_separator()
        menu.add_command(label="在资源管理器中打开文件夹", command=self.reveal_root)
        menu.add_command(label="从列表移除此文件夹", command=self.remove_current_root)
        try:
            if event is not None:
                menu.tk_popup(event.x_root, event.y_root)
            else:
                menu.tk_popup(self.folder_menu_button.winfo_rootx(),
                              self.folder_menu_button.winfo_rooty() + self.px(28))
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def new_folder_doc(self) -> None:
        """在原目录新建一个 Markdown 文件并立刻打开。"""
        from tkinter import simpledialog
        if not self.cur_root:
            self.notice("请先打开一个文件夹", error=True)
            return
        subdir = self._selected_root_dir()
        try:
            name = simpledialog.askstring(
                "新建 Markdown", "文件名（默认 .md）\n位置：%s" % (subdir or "根目录"),
                parent=self.root)
        except Exception as exc:
            self.notice("无法打开输入框：%s" % _s(exc), error=True)
            return
        if not name or not name.strip():
            return
        try:
            info = self.ws.folders.create_doc(self.cur_root, name.strip(), subdir=subdir)
        except Exception as exc:
            self.notice("新建失败：%s" % _s(exc), error=True)
            return
        self.refresh_folder_tree(force=True)
        self.notice("已创建 %s" % _s(info.get("file")))
        self.open_local_files([info["_abs"]])

    def _selected_root_dir(self) -> str:
        """Only a selected folder is a creation target; otherwise use the root."""
        try:
            selection = self.root_tree.selection()
        except Exception:
            return ""
        if not selection:
            return ""
        did = self._root_nodes.get(selection[0])
        if not did:
            return ""
        return did if selection[0].startswith("r:") else ""

    def rename_folder_doc(self, did: str) -> None:
        """重命名原文件。必须明确操作，且不覆盖已存在的文件。"""
        from tkinter import simpledialog
        if not self.cur_root or not did:
            return
        old = os.path.splitext(os.path.basename(did))[0]
        try:
            name = simpledialog.askstring("重命名", "新的文件名：", initialvalue=old, parent=self.root)
        except Exception as exc:
            self.notice("无法打开输入框：%s" % _s(exc), error=True)
            return
        if not name or name.strip() == old:
            return
        try:
            target = self.ws.folders.doc_info(self.cur_root, did)["_abs"]
            info = self.ws.folders.rename_doc(self.cur_root, did, name.strip())
        except Exception as exc:
            self.notice("重命名失败：%s" % _s(exc), error=True)
            return
        self.refresh_folder_tree(force=True)
        self.notice("已重命名为 %s" % _s(info.get("file")))
        # 已打开的标签要跟着换身份，否则保存会写回旧路径
        for tab in list(self.tabs):
            loose = tab.get("loose") or {}
            if _s(loose.get("path")) and os.path.normcase(loose["path"]) == os.path.normcase(target):
                self._retarget_loose_tab(tab, info["_abs"])
                break

    def _retarget_loose_tab(self, tab: dict, new_path: str) -> None:
        old = _s((tab.get("loose") or {}).get("path"))
        if not old or os.path.normcase(old) == os.path.normcase(new_path):
            return
        fresh = self.loose.open_path(new_path)
        tab["loose"] = fresh
        tab["key"] = self._tab_key(loose=fresh)
        try:
            self.loose.forget(old)
        except Exception:
            pass
        if self.active_tab is tab:
            self.cur_loose = fresh
            self.update_title()
            self.draw_tabs()

    def reveal_folder_item(self, did: str) -> None:
        try:
            root = self.ws.folders.require(self.cur_root)
            path = core.safe_join(root["path"], did)
            self.ws.open_in_explorer(path, select=True)
        except Exception as exc:
            self.notice("定位失败：%s" % _s(exc), error=True)

    def reveal_root(self) -> None:
        root = self.current_root()
        if not root:
            return
        try:
            self.ws.open_in_explorer(_s(root.get("path")))
            self.notice("已在资源管理器中打开")
        except Exception as exc:
            self.notice("打开目录失败：%s" % _s(exc), error=True)

    def remove_current_root(self) -> None:
        """只从列表移除登记，不删除磁盘目录。"""
        root = self.current_root()
        if not root:
            return
        try:
            from tkinter import messagebox
            keep = messagebox.askyesno(
                "从列表移除",
                "只从 MDReader 的列表里移除「%s」，磁盘上的文件夹和文件都不会被删除。\n\n"
                "继续吗？" % _s(root.get("name")), parent=self.root)
        except Exception:
            keep = False
        if not keep:
            return
        try:
            self.ws.folders.forget(_s(root.get("id")))
        except Exception as exc:
            self.notice("移除失败：%s" % _s(exc), error=True)
            return
        self.cur_root = None
        self._folder_signature = None
        self.refresh_roots(force=True)
        self.notice("已从列表移除「%s」，磁盘文件没有改动" % _s(root.get("name")))

    def folder_watch_tick(self) -> None:
        """Tk 主线程上的 1.5 秒检查：有变化才重画树，没有就什么都不做。

        后台监听线程只置一个“脏”标记，**绝不自己碰控件**：在非主线程里调
        ``after`` / ``configure`` 既不是线程安全的，也会让正在销毁的窗口报错。
        """
        if self._folder_tick is None:
            return
        try:
            self._follow_root_registration()
            if self._folders_dirty:
                self._folders_dirty = False
                self.refresh_folder_tree(force=False)
        except Exception:
            pass

    def _mark_folders_dirty(self, _rid=None) -> None:
        """由后台监听线程调用：只置标记，界面在主线程上自行刷新。"""
        self._folders_dirty = True

    def _follow_root_registration(self) -> None:
        """另一入口（网页）新开/移除了文件夹时，这里也要跟上。

        用「已登记的根目录集合」判断，而不是文件时间戳：轮询周期本来就短，
        而且 ``refresh_roots(force=False)`` 在树没变时不会再走一遍目录。
        """
        try:
            registered = tuple(r.get("id") for r in self.ws.folders.roots())
        except Exception:
            return
        if registered == self._roots_seen:
            return
        self._roots_seen = registered
        self.refresh_roots(force=False)

    def _registration_stamp(self):
        try:
            stat = os.stat(os.path.join(self.ws.root, "folders.json"))
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def schedule_folder_watch(self) -> None:
        try:
            self._folder_tick = self.root.after(int(self.px(1500)), self._folder_watch_repeat)
        except Exception:
            self._folder_tick = None

    def _folder_watch_repeat(self) -> None:
        self.folder_watch_tick()
        self.schedule_folder_watch()

    # -- 插件（P01）：桌面端与网页端共用同一套授权、快照与提交规则 ---------
    def plugin_bridge(self):
        """插件调用走与网页端**完全同一份**实现，避免两端各写一套规则。"""
        bridge = getattr(self, "_plugin_bridge", None)
        if bridge is None:
            bridge = core.Api(self.ws, os.path.join(core.app_dir(), "webui"))
            bridge.loose = self.loose          # 与窗口共用同一份「最近打开」
            bridge.mode = "server"
            self._plugin_bridge = bridge
        return bridge

    def plugin_commands(self, capability: str = "") -> list:
        try:
            commands = self.ws.plugins.commands()
        except Exception:
            return []
        return [c for c in commands if not capability or c["capability"] == capability]

    def manage_plugins(self) -> None:
        from .plugin_ui import PluginDialog
        PluginDialog(self).show()

    def plugin_menu_entries(self) -> dict:
        """「更多」菜单里的插件条目；界面与测试都从这里取，避免两处不一致。"""
        images = self.plugin_commands(core.PL.CAP_IMAGE_INSERT)
        exports = self.plugin_commands(core.PL.CAP_EXPORT)
        return {
            "insert": (("插入图片（%s）…" % images[0]["plugin_name"]) if images
                       else "插入图片（插件未启用）…",
                       self.insert_image_with_plugin if images else self.manage_plugins),
            "exports": [("%s（.%s）" % (row["title"], row["extension"]),
                         (lambda item=row: self.plugin_export_document(item)))
                        for row in exports],
            "manage": ("插件管理…", self.manage_plugins),
        }

    def on_plugins_changed(self) -> None:
        """插件启停后刷新状态栏；命令列表每次现取，不做常驻注册。"""
        self.update_status()
        usable = len(self.plugin_commands())
        if usable:
            self.notice("插件状态已更新，当前可用命令 %d 个" % usable)

    def _plugin_document_context(self) -> dict | None:
        """当前文档在磁盘上的身份与修订；没有磁盘文件时返回 None。"""
        if self.cur_pid and self.cur_doc:
            try:
                info = self.ws.doc_info(self.ws.require_project(self.cur_pid), _s(self.cur_doc.get("id")))
            except Exception:
                return None
            return {"pid": self.cur_pid, "doc": _s(self.cur_doc.get("id")),
                    "revision": info.get("revision")}
        path = _s((self.cur_loose or {}).get("path"))
        if path and os.path.isfile(path):
            revision = _s((self.cur_loose or {}).get("revision")) or core.D.revision(path)
            return {"doc": path, "revision": revision}
        return None

    def _insert_plugin_markdown(self, text: str) -> None:
        """插件产物只回一段标准 Markdown：正文提交仍然走编辑器（可一次撤销）。"""
        if not self.active_tab:
            self.notice("没有打开的文档", error=True)
            return
        if self.mode != "source":
            self.show_source()
        widget = self.text
        try:
            widget.insert("insert", text)
        except Exception:
            self.notice("无法写入编辑器", error=True)
            return
        widget.focus_set()
        self.source = self.get_text()
        self.set_dirty(True)
        self.update_status()

    def insert_image_with_plugin(self) -> None:
        commands = self.plugin_commands(core.PL.CAP_IMAGE_INSERT)
        if not commands:
            self.notice("图片插入插件没有启用：先在「更多 → 插件管理…」里安装并启用", error=True)
            return
        # 先确认“插得进去”，再弹选择框：没有可写的文档时不要让用户白挑一张图。
        if self._plugin_document_context() is None:
            self.notice("请先保存当前文档，再用插件插入图片（图片放在文档旁边的 assets 目录）",
                        error=True)
            return
        paths = core._dialog_images()
        if not paths:
            return
        self.insert_image_file(paths[0], commands[0], title="插入图片")

    def insert_clipboard_image(self) -> None:
        """F03：把剪贴板里的截图先落成临时文件，再走同一条图片插入通道。

        只在用户显式操作时读剪贴板（菜单项 / ``Ctrl+Shift+V``），程序不监视剪贴板。
        图片先落盘、再由插件复制进 ``assets/``，**不把图片数据写进 Markdown**；
        临时文件是本次操作新建的，插入流程走完就删掉（插件此时已经复制走原件）。
        """
        commands = self.plugin_commands(core.PL.CAP_IMAGE_INSERT)
        if not commands:
            self.notice("图片插入插件没有启用：先在「更多 → 插件管理…」里安装并启用", error=True)
            return
        if self._plugin_document_context() is None:
            self.notice("请先保存当前文档，再把剪贴板里的图片插进来", error=True)
            return
        try:
            path = MD.clipboard_image()
        except Exception as exc:
            self.notice("读取剪贴板图片失败：%s" % _s(exc), error=True)
            return
        if not path:
            self.notice("剪贴板里没有图片：先按 Win+Shift+S 截图，或复制一张图片再试", error=True)
            return
        try:
            self.insert_image_file(path, commands[0], title="插入截图")
        finally:
            # 只删我们自己新建的临时文件：剪贴板里若是“复制的图片文件”，
            # 那是用户的原件，一个字节都不能动。
            if MD.owns_clipboard_file(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def insert_dropped_images(self, paths) -> bool:
        """F03：把拖进窗口的图片插到光标处；按 Shift 拖入仍走原来的打开/导入规则。"""
        commands = self.plugin_commands(core.PL.CAP_IMAGE_INSERT)
        if not commands:
            self.notice("拖入的是图片：先在「更多 → 插件管理…」里安装并启用图片插入插件",
                        error=True)
            return False
        if self._plugin_document_context() is None:
            self.notice("拖入的是图片：请先打开并保存要插入图片的文档", error=True)
            return False
        ok = self.insert_image_file(paths[0], commands[0], title="插入图片")
        if ok and len(paths) > 1:
            self.notice("一次只插入一张图片：已插入第 1 张，其余 %d 张没有处理" % (len(paths) - 1))
        return ok

    def insert_image_file(self, path: str, command: dict, title: str = "插入图片") -> bool:
        """插入一张**已经落盘的**图片；插件只给方案，落附件与正文提交都由核心决定。"""
        context = self._plugin_document_context()
        if context is None:
            self.notice("请先保存当前文档，再用插件插入图片（图片放在文档旁边的 assets 目录）",
                        error=True)
            return False
        width = 720
        if command.get("plugin") == "mdreader.image-insert":
            from .media_ui import choose_width
            width = choose_width(self, title=title)
            if width is None:
                return False
        self.stash_tab()
        payload = {"command": command["command"], "path": path, "entry": "desktop",
                   "wait": 90, "options": {"width": width}}
        payload.update(context)
        target_tab, original_source = self.active_tab, self.source
        try:
            from .media_ui import run_job
            bridge = self.plugin_bridge()
            self.notice("正在处理图片…")
            result = run_job(self, lambda: bridge.post("/api/plugins/insert", payload))
        except Exception as exc:
            self.notice("插入图片失败：%s" % _s(exc), error=True)
            return False
        if result.get("pending"):
            self.notice("插件还在处理这张图片，请稍后再试", error=True)
            return False
        self.stash_tab()
        if self.active_tab is not target_tab or self.source != original_source:
            for asset in result.get("assets", []):
                landed = asset.get("path", "")
                if landed and core.is_within(result["assets_dir"], landed):
                    try:
                        os.remove(landed)
                    except OSError:
                        pass
            self.notice("文档在图片处理期间已切换或修改，请重新插入图片", error=True)
            return False
        self._insert_plugin_markdown(result["markdown"])
        self.notice("已插入图片，附件在：%s" % _s(result.get("assets_dir")))
        return True

    def plugin_export_document(self, command: dict) -> None:
        """走核心的统一导出服务（F06）：先问来源、再预检，最后才真的转换。"""
        if not command:
            return
        context = self._plugin_document_context()
        if context is None:
            self.notice("请先保存当前文档，再用插件导出", error=True)
            return
        name = _s((self.cur_loose or self.cur_doc or {}).get("file"))
        stem = os.path.splitext(os.path.basename(name) or "导出")[0]
        dest = core._dialog_save("%s.%s" % (stem, command["extension"]),
                                 "%s (*.%s)|*.%s|所有文件 (*.*)|*.*"
                                 % (command["title"], command["extension"], command["extension"]))
        if not dest:
            return
        self.stash_tab()
        payload = {"command": command["command"], "dest": dest, "entry": "desktop", "wait": 180}
        payload.update(context)
        if self.dirty:
            payload["markdown"] = self.source      # 未保存时告诉宿主“缓冲区与磁盘不一致”
        # ① 有未保存修改时先问用哪一份内容（默认当前编辑内容）
        plan = self._export_plan_call(payload)
        if plan is None:
            return
        if plan.get("needs_source"):
            choice = ExportSourceDialog(self.root, self.pal, self.ui_scale,
                                        name=plan.get("name") or name).show()
            if not choice:
                self.notice("已取消导出")
                return
            payload["source"] = choice
            plan = self._export_plan_call(payload)
            if plan is None:
                return
        # ② 预检报告：错误只能返回修改，提示可以确认继续
        report = plan.get("preflight") or {}
        if (report.get("errors") or report.get("warnings")) and not plan.get("conflict"):
            if not ExportReportDialog(self.root, self.pal, self.ui_scale, dest=plan.get("dest") or dest,
                                      report=report).show():
                self.notice("已返回修改，导出没有开始")
                return
            payload["confirm"] = True
        elif report.get("errors"):
            ExportReportDialog(self.root, self.pal, self.ui_scale, dest=plan.get("dest") or dest,
                               report=report, allow_confirm=False).show()
            return
        if not self._plugin_export_call(payload, dest):
            return

    def _export_plan_call(self, payload: dict):
        """问一次核心：这篇文档导出前有什么问题、目标在哪。"""
        try:
            from .media_ui import run_job
            bridge = self.plugin_bridge()
            return run_job(self, lambda: bridge.post("/api/export/check", payload))
        except Exception as exc:
            self.notice("导出前检查失败：%s" % _s(exc), error=True)
            return None

    def _plugin_export_call(self, payload: dict, dest: str) -> bool:
        from tkinter import messagebox
        try:
            from .media_ui import run_job
            bridge = self.plugin_bridge()
            self.notice("正在导出，仍可滚动和阅读文章…")
            result = run_job(self, lambda: bridge.post("/api/plugins/export", payload))
        except Exception as exc:
            self.notice("导出失败，原目标未改动：%s" % _s(exc), error=True)
            return False
        if result.get("blocked"):
            ExportReportDialog(self.root, self.pal, self.ui_scale, dest=dest,
                               report=result.get("preflight") or {}, allow_confirm=False).show()
            return False
        if result.get("needs_source"):
            self.notice("这篇文档有未保存的修改，请重新导出并选择来源", error=True)
            return False
        if result.get("needs_confirm"):
            report = result.get("preflight") or {}
            if not ExportReportDialog(self.root, self.pal, self.ui_scale, dest=dest,
                                      report=report).show():
                return False
            payload = dict(payload, confirm=True)
            return self._plugin_export_call(payload, dest)
        if result.get("conflict"):
            if not messagebox.askyesno("确认覆盖", "目标已存在，覆盖它吗？\n%s" % dest,
                                       parent=self.root):
                return False
            payload = dict(payload, overwrite=True)
            return self._plugin_export_call(payload, dest)
        if result.get("pending"):
            self.notice("插件还在生成文件，请稍后再试", error=True)
            return False
        warnings = result.get("warnings") or []
        self.notice("已导出：%s%s" % (result["path"],
                                    ("（%d 条提示）" % len(warnings)) if warnings else ""))
        return True

    def check_document_links(self) -> bool:
        """F09：检查当前文档的本地引用；可以定位源码，也可以重新选一个文件补上。"""
        context = self._plugin_document_context()
        if context is None:
            self.notice("请先保存当前文档，再检查链接", error=True)
            return False
        while True:
            payload = {"entry": "desktop"}
            payload.update(context)
            if self.dirty:
                payload["markdown"] = self.source
            try:
                from .media_ui import run_job
                bridge = self.plugin_bridge()
                outcome = run_job(self, lambda: bridge.post("/api/check/links", payload))
            except Exception as exc:
                self.notice("检查链接失败：%s" % _s(exc), error=True)
                return False
            report = outcome.get("report") or {}
            if not report.get("issues"):
                self.notice("检查了 %d 处本地引用，没有发现问题" % report.get("checked", 0))
                return True
            choice = LinkReportDialog(self.root, self.pal, self.ui_scale, report=report,
                                      name=outcome.get("name") or "").show()
            action = (choice or {}).get("action")
            index = (choice or {}).get("index") or 0
            if action is None:
                return False
            if action == "locate":
                self._locate_link_issue(report["issues"][index])
                return True
            if action == "choose":
                if not self._relink_issue(report["issues"][index], context):
                    return False
                continue
            # 关闭、取消或不认识的答复：结束这一次检查。**循环必须有出口**，
            # 否则界面会一遍遍重问同一个对话框（用例里传了个不认识的答复就挂住了）。
            return False

    def _locate_link_issue(self, issue: dict) -> None:
        """把光标（并选中整行）放到出问题的那一行。"""
        self.show_source()
        editor = self.editor
        if editor is None:
            return
        line = max(1, int(issue.get("line") or 1))
        editor.mark_set("insert", "%d.0" % line)
        editor.tag_remove("sel", "1.0", "end")
        editor.tag_add("sel", "%d.0" % line, "%d.end" % line)
        editor.see("%d.0" % line)
        editor.focus_set()
        self.notice("已跳到第 %d 行" % line)

    def _relink_issue(self, issue: dict, context: dict) -> bool:
        """让用户重新选一个文件，复制到文档旁边的 assets/ 并改掉正文里的引用。"""
        if issue.get("action") != "choose" or issue.get("kind") != "image":
            self.notice("这一条只能定位源码：%s" % issue.get("source", ""), error=True)
            return False
        paths = core._dialog_images(multi=False)
        if not paths:
            return False
        editor = self.editor_for(self.active_tab) or self.editor
        if editor is None:
            return False
        payload = {"path": paths[0], "source": issue["source"], "entry": "desktop"}
        payload.update(context)
        payload["markdown"] = editor.get("1.0", "end-1c")
        try:
            from .media_ui import run_job
            bridge = self.plugin_bridge()
            result = run_job(self, lambda: bridge.post("/api/check/links/relink", payload))
        except Exception as exc:
            self.notice("替换链接失败：%s" % _s(exc), error=True)
            return False
        if not result.get("ok"):
            self.notice(result.get("reason") or "替换失败", error=True)
            return False
        self.show_source()
        self._apply_editor_result(editor, {"text": result["markdown"],
                                          "start": None, "end": None})
        self.set_dirty(True)
        self.notice("已改成 %s（文件复制到文档旁边的 assets/）" % result["link"])
        return True

    def new_project(self) -> None:
        try:
            dialog = RenamePrompt(self.root, "", self.pal, self.ui_scale,
                                  project_parent=self.ws.projects_dir)
            name = dialog.show()
            location = dialog.location.get()
        except Exception as exc:
            self.notice("无法打开输入框：%s" % _s(exc), error=True)
            return
        if not name or not name.strip():
            return
        try:
            created = self.ws.create_project(name.strip(), parent_dir=location)
        except Exception as exc:
            self.notice("新建项目失败：%s" % _s(exc), error=True)
            return
        self.stash_tab()
        self.active_tab = None
        self.cur_loose = None
        self.cur_doc, self.source = None, ""
        self.set_dirty(False)
        self.cur_pid = _s(created.get("id"))
        self.refresh_projects(keep=True)
        self.notice("已创建项目「%s」" % _s(created.get("name")))

    # -- documents -------------------------------------------------------
    def on_tree_activate(self, event=None) -> None:
        try:
            sel = self.tree.selection()
        except Exception:
            return
        if sel and sel[0].startswith("d:"):
            self.open_doc(sel[0][2:])

    def open_doc(self, did: str, force: bool = False) -> None:
        if not self.cur_pid or not self._load(did, force):
            return
        self._sync_view()
        self.select_in_tree(did)
        self.notice("已打开 %s" % _s((self.cur_doc or {}).get("file")))

    def load_doc(self, info: dict) -> None:
        self._load(_s(info.get("id")), force=True)

    def _load(self, did: str, force: bool = False) -> bool:
        """Read ``did`` into the viewer.  False when the switch was cancelled."""
        key = self._tab_key(doc={"id": did})
        existing = next((t for t in self.tabs if t["key"] == key), None)
        if existing is not None:
            self.activate_tab(existing)
            return True
        self.stash_tab()
        try:
            pdir = self.ws.require_project(self.cur_pid)      # type: ignore[arg-type]
            info = self.ws.doc_info(pdir, did)
            raw = self.ws.read_doc(pdir, did)
            info['revision'] = self.ws._versions[info['_abs']]
            self.active_tab = None
            self.cur_doc = info
            self.cur_loose = None            # 回到项目文档，退出临时查看
            self.load_doc_text(raw)
            self.remember_tab()
            self.reveal_active_tab()
            self.restore_reading_position()
            self.refresh_recent()
        except Exception as exc:
            self.notice("打开文档失败：%s" % _s(exc), error=True)
            return False
        return True

    def load_doc_text(self, text: str) -> None:
        self.source = _s(text)
        self.dirty = False
        if self.mode == "source":
            self._set_widget(self.source)
        else:
            self.render()
        if self.active_tab is not None:
            self.active_tab["source"] = self.source
        self.mark_baseline()
        self.update_title()
        self.update_status()

    def reload_doc(self) -> None:
        if self.active_tab is None or not self.confirm_switch():
            return
        try:
            if self.cur_loose:
                if not self.cur_loose.get("path"):
                    return
                info = self.loose.open_path(self.cur_loose["path"])
                raw = core.read_text_file(info["path"])
                self.cur_loose = info
            else:
                raw = self.ws.read_doc(self.ws.require_project(self.cur_pid), self.cur_doc["id"])
            self.load_doc_text(raw)
            self.stash_tab()
            self.draw_tabs()
            self.notice("已重新读取磁盘")
        except Exception as exc:
            self.notice("刷新失败：%s" % exc, error=True)

    def render(self) -> None:
        """Draw ``self.source`` into the read-only preview widget.

        One preview widget is shared by every tab (E01 规格：每个标签持有独立源码
        编辑控件、预览单独展示), so switching to a different document in preview
        mode re-typesets it. That cost is inherent to the shared preview and is
        recorded in docs/DEVELOPMENT.md §3.2.
        """
        self._show(self.preview)
        self.ime.set_editable(False)
        self.hide_empty()
        self.preview.configure(state="normal")
        info = self.cur_loose or self.cur_doc or {}
        image_document = info.get("path") or info.get("_abs") or ""
        self.preview._image_document = image_document
        self.preview._image_root = (self.ws.require_project(self.cur_pid) if self.cur_doc and self.cur_pid
                                    else os.path.dirname(image_document))
        # 公式（F05）：缓存放在工作区里，主题与 DPI 一并进缓存键
        self.preview._formula_cache = core.formula_cache_dir(self.ws.root)
        self.preview._formula_theme = self.theme
        self.preview._formula_scale = 1.0
        from .media_ui import resize_image
        self.preview._image_edit = lambda offset: resize_image(self, offset)
        try:
            render_to_text(self.preview, self.source, self.pal)
            ensure_link_tags(self.preview, getattr(self.preview, "_links", {}) or {}, self.pal)
        except Exception as exc:
            self.preview.delete("1.0", "end")
            self.preview.insert("end", "渲染失败：%s\n" % _s(exc), ("placeholder",))
        self.preview.configure(state="disabled")
        self.mode = "preview"
        self.update_status()

    # -- per-tab editors -------------------------------------------------
    def _style_widget(self, widget, force=False):
        """Apply the current palette and font size to one Text widget, once.

        Restyling a Text means ~40 ``tag_configure`` calls *plus one full layout
        pass over the whole document*, so with one editor per open document it
        must not happen for widgets that are not on screen: a hidden editor
        keeps the previous styling until ``_show`` brings it back.  Without this,
        changing the font size with 20 tabs open costs about 200 ms per wheel
        tick and a theme switch about 300 ms.  The per-tag work is idempotent
        and cheap now (see :func:`_tag_plan`); the layout pass is what the
        revision guard avoids, together with the redundant ``tag_names`` sweeps.
        """
        rev = getattr(self, "_style_rev", 0)
        if not force and getattr(widget, "_style_rev", None) == rev:
            return
        widget.configure(font=(UI_FONT, self.base_size))
        configure_tags(widget, self.pal, self.base_size)
        if widget is self.preview:
            self._restyle_tables()
        ensure_link_tags(widget, getattr(widget, "_links", {}) or {}, self.pal)
        widget._style_rev = rev

    def _restyle_tables(self):
        """Let every embedded table follow the current palette and text size.

        Each table keeps the pair it was last painted with, so a restyle that
        changes nothing (the usual case on a zoom tick) costs one attribute read
        per table instead of a label-grid relayout.
        """
        for child in self.preview.winfo_children():
            restyle = getattr(child, "_restyle_table", None)
            if restyle is not None:
                restyle(self.pal, self.base_size)
            formula = getattr(child, "_restyle_formula", None)
            if formula is not None:
                formula(self.pal, self.base_size)

    def _invalidate_styles(self):
        """Mark every widget stale; they are restyled when they are shown."""
        self._style_rev = getattr(self, "_style_rev", 0) + 1

    def _new_editor(self):
        """A source editor for one tab, with its own Tk undo stack."""
        tk = self._tk
        editor = tk.Text(self.body, wrap="word", bd=0, highlightthickness=0, undo=True,
                         padx=22, pady=14, spacing1=1, spacing3=2)
        editor.grid(row=0, column=0, sticky="nsew")
        editor.grid_remove()
        editor.bind("<<Modified>>", self.on_modified)
        # 粘贴 TSV 时先给预览（F04）；普通粘贴不拦，交回 Tk 默认行为。
        editor.bind("<<Paste>>", self.on_editor_paste, add="+")
        editor.bind("<Control-MouseWheel>", self.on_zoom)
        wheel = ScrollCoalescer(self.root, editor)
        editor._wheel_coalescer = wheel
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>"):
            editor.bind(sequence, wheel.on_wheel)
        editor.configure(yscrollcommand=lambda a, b: self._document_scroll(editor, a, b),
                         xscrollcommand=lambda a, b: self._document_scroll(editor, a, b, True))
        self._style_widget(editor, force=True)
        self._editors[editor] = None            # bound to a tab on first use
        return editor

    def editor_for(self, tab, create=True):
        """The source editor bound to ``tab`` (created on first need)."""
        if tab is None:
            return None
        editor = tab.get("editor")
        if editor is not None and editor.winfo_exists():
            return editor
        if not create:
            return None
        editor = self._new_editor()
        from .ime import InlineIME
        self._imes.append(InlineIME(editor))
        tab["editor"] = editor
        self._editors[editor] = tab
        return editor

    def _bind_editor(self, tab):
        """Load ``tab``'s buffer into its editor and show it (source mode)."""
        editor = self.editor_for(tab)
        if editor is None:
            return None
        self._load_editor(editor, tab.get("source", ""))
        self.editor = editor
        self._show(editor)
        tab["mode"] = "source"
        self.mode = "source"
        self.text = editor
        if tab.get("cursor"):
            try:
                editor.mark_set("insert", tab["cursor"])
            except Exception:
                pass
        editor.focus_set()
        return editor

    def show_source(self, text=None):
        """Switch to the source editor of the open document.

        The editor is only refilled when its buffer is not already the document
        text, because refilling an editor is what drops its undo history.
        """
        editor = self.editor_for(self.active_tab) or self.editor
        if editor is None:
            self.notice("没有打开的文档", error=True)
            return None
        self.hide_empty()
        wanted = self.source if text is None else text
        try:
            current = editor.get("1.0", "end-1c")
        except Exception:
            current = None
        if current != wanted:
            self._load_editor(editor, wanted)
        self.editor = editor
        self._show(editor)
        self.mode = "source"
        if self.active_tab is not None:
            self.active_tab["mode"] = "source"
        editor.focus_set()
        self.update_status()
        return editor

    def _load_editor(self, editor, text):
        """Fill an editor that does not have a history yet, and reset that history."""
        self._loading = True
        try:
            editor.configure(state="normal")
            for child in editor.winfo_children():
                child.destroy()
            editor.delete("1.0", "end")
            if text:
                editor.insert("1.0", text)
            editor.edit_modified(False)
            editor.edit_reset()
        finally:
            self._loading = False

    def _show(self, widget, cleared=None):
        """Put one of the stacked body widgets on top of the others.

        Composition state is cleaned up here rather than in the caller so that
        hiding an editor (tab switch, or switching to the preview) can never
        leave a preedit surface or a stale caret rectangle behind on a hidden
        widget.

        Editability is decided here too, and only here: ``InlineIME`` starts out
        non-editable, and while it is non-editable ``handle_message`` forwards
        ``WM_IME_SETCONTEXT`` untouched — which makes Windows draw its own white
        composition box instead of the themed inline preedit. Setting the flag
        from a "load the document" path looked fine and was wrong the moment one
        widget per document was introduced (0.2.8 regression: every tab editor
        stayed non-editable).
        """
        if cleared is None:
            cleared = [ime for ime in self._imes if ime.editor is not widget]
        else:
            cleared = list(cleared)
        for ime in cleared:
            try:
                ime._focus_out()
            except Exception:
                pass
        self._style_widget(widget)          # a hidden editor may be stale
        for candidate in (self.preview, *self._editors):
            if candidate is not widget:
                try:
                    candidate.grid_remove()
                except Exception:
                    pass
        try:
            widget.grid()
            widget.tkraise()
        except Exception:
            pass
        self.text = widget
        self.doc_vscroll.set(*widget.yview())
        self.doc_hscroll.set(*widget.xview())
        adapter = self._ime_for(widget)
        if adapter is not None:
            adapter.set_editable(widget is not self.preview)

    def _sync_view(self):
        """Make the widget on screen and ``self.text`` agree with ``self.mode``.

        ``mode`` says which view the user asked for; this is the single place
        that brings the stacked body widgets in line with it, so a closed tab or
        a cancelled switch can never leave the preview on screen while the app
        still believes it is in source mode.
        """
        if self.active_tab is None or self.editor is None or not self.editor.winfo_exists():
            self.mode = "preview"
            self._show(self.preview)
            return
        if self.mode == "source":
            self._show(self.editor)
        else:
            self.render()

    def _set_widget(self, text: str) -> None:
        """Replace the open document's buffer (loading, recovering, test setup).

        This is a content replacement, not a view switch: the target is always
        the document's own editor, and the caller-visible source follows it so
        that a later save writes exactly what was loaded.
        """
        editor = self.editor_for(self.active_tab) or self.editor
        if editor is None:
            self.source = _s(text)
            self.mode = "preview"
            self.render()
            return
        self.editor = editor
        self._load_editor(editor, text)
        editor.yview_moveto(0.0)
        self.source = _s(text)
        if self.active_tab is not None:
            self.active_tab["source"] = self.source

    def toggle_mode(self) -> None:
        if self.mode == "preview":
            editor = self.active_tab.get("editor") if self.active_tab is not None else None
            if editor is None or not editor.winfo_exists():
                # No document: stay in the read-only empty state instead of
                # entering a source mode that has nothing to edit.
                self.mode = "preview"
                self._show(self.preview)
                self.notice("没有打开的文档", error=True)
                self.update_status()
                return
            self.show_source()
            self.notice("源码模式，可直接编辑")
        else:
            raw = self.get_text()
            if raw != self.source:
                self.source = raw
                self.set_dirty(True)
            self.render()
            self.notice("预览模式")
        self.update_status()

    def get_text(self) -> str:
        widget = self.editor if self.mode == "source" else None
        if widget is None:
            return self.source
        try:
            return widget.get("1.0", "end-1c")
        except Exception:
            return self.source

    def mark_baseline(self, tab=None):
        """Remember the text that "已保存" refers to, so undo can clear the marker."""
        tab = tab if tab is not None else self.active_tab
        if tab is not None:
            tab["baseline"] = _text_hash(tab.get("source", self.source))

    def at_baseline(self, tab=None) -> bool:
        """True when the buffer is exactly the last saved (or loaded) content."""
        tab = tab if tab is not None else self.active_tab
        if tab is None:
            return False
        baseline = tab.get("baseline")
        return baseline is not None and baseline == _text_hash(tab.get("source", self.source))

    def on_modified(self, event=None) -> None:
        if self._loading:
            return
        widget = event.widget if event is not None else self.editor
        if widget is None or widget is not self.editor or self.mode != "source":
            return
        try:
            if not widget.edit_modified():
                return
            widget.edit_modified(False)
        except Exception:
            return
        self.source = self.get_text()
        if self.active_tab is not None:
            self.active_tab["source"] = self.source
        # 撤销回保存基线时必须去掉未保存标记，重做离开基线时再恢复，
        # 所以标记由内容决定，而不是只由“发生过输入事件”决定。
        self.set_dirty(not self.at_baseline())

    def save_doc(self) -> bool:
        if self.cur_loose:
            return self.save_loose()
        if not self.cur_pid or not self.cur_doc:
            self.notice("还没有打开文档")
            return False
        if self.mode == "source":
            self.source = self.get_text()
        try:
            pdir = self.ws.require_project(self.cur_pid)
            self.cur_doc = self.ws.save_doc(pdir, _s(self.cur_doc.get("id")), self.source,
                                            expected=self.cur_doc.get('revision'))
        except core.D.ConflictError as exc:
            return self.resolve_conflict(exc)
        except Exception as exc:
            self.notice("保存失败：%s" % _s(exc), error=True)
            return False
        self.set_dirty(False)
        self.mark_baseline()
        info = self.cur_doc
        iid = "d:" + _s(info.get("id"))
        try:
            if self.tree.exists(iid):
                self.tree.item(iid, text="\U0001f4c4 %s  \u00b7 %s" % (_s(info.get("name")),
                                                                       _s(info.get("mtime"))))
        except Exception:
            pass
        if self.mode == "preview":
            self.render()
        self.update_title()
        self.notice("已保存 %s" % datetime.datetime.now().strftime("%H:%M:%S"))
        return True

    def open_folder(self) -> None:
        try:
            if self.cur_loose:
                target = _s(self.cur_loose.get("path")) or self.ws.root
                self.ws.open_in_explorer(target, select=bool(_s(self.cur_loose.get("path"))))
                self.notice("已在资源管理器中定位")
                return
            if self.cur_pid and self.cur_doc:
                pdir = self.ws.require_project(self.cur_pid)
                target = _s(self.ws.doc_info(pdir, _s(self.cur_doc.get("id"))).get("_abs"))
            elif self.cur_pid:
                target = self.ws.require_project(self.cur_pid)
            elif self.projects:
                target = _s(self.projects[0].get("path"))
            else:
                target = self.ws.root
            self.ws.open_in_explorer(target, select=False)
            self.notice("已在资源管理器中打开")
        except Exception as exc:
            self.notice("打开目录失败：%s" % _s(exc), error=True)

    def open_browser(self) -> None:
        try:
            if callable(self.on_open_browser):
                self.on_open_browser()
                return
            _open_url(self.url)
            self.notice("已在浏览器中打开")
        except Exception as exc:
            self.notice("打开浏览器失败：%s" % _s(exc), error=True)

    def confirm_switch(self, discard_marks_clean=True) -> bool:
        """True when it is safe to leave the current document."""
        if not self.dirty and not (self.cur_loose and not self.cur_loose.get("path")):
            return True
        answer = self.ask_save_changes()
        if answer is None:
            return False
        if answer:
            return self.save_doc()
        if discard_marks_clean:
            if self.active_tab:
                self.ws.recovery.discard(self.recovery_identity(self.active_tab))
            self.set_dirty(False)
        return True

    def ask_save_changes(self):
        self.hide_theme_menu()
        current = self.cur_loose or self.cur_doc or {}
        title = current.get("file") or current.get("name") or "Untitled"
        return SavePrompt(self.root, title, self.pal, self.ui_scale).show()

    # -- 临时查看：直接读写工作区之外的文件 -------------------------------
    def enable_file_drop(self) -> None:
        """Turn on Explorer drag & drop for this window."""
        try:
            self._drop = FileDrop(self.root, self.on_files_dropped)
            if self._drop.ok:
                self.notice("可以直接把 .md 文件拖进窗口，修改后 Ctrl+S 写回原文件")
        except Exception as exc:
            self._drop = None
            self.notice("拖放不可用：%s" % _s(exc), error=True)

    def on_files_dropped(self, paths) -> None:
        """Handle a shell drop: markdown opens temporarily, folders import, images insert."""
        files, folders, images = [], [], []
        for path in paths or []:
            if os.path.isdir(path):
                folders.append(path)
            elif MD.is_image_path(path):
                images.append(path)
            else:
                files.append(path)
        if images:
            self.insert_dropped_images(images)
        if files:
            self.open_local_files(files)
        if folders:
            if self.cur_pid:
                try:
                    result = self.ws.import_paths(self.ws.require_project(self.cur_pid), folders)
                    self.refresh_projects(keep=True)
                    self.notice("已导入 %d 个文件" % int(result.get("count") or 0))
                except Exception as exc:
                    self.notice("导入文件夹失败：%s" % _s(exc), error=True)
            else:
                self.notice("拖入的是文件夹：请先在左侧选择一个项目再拖入", error=True)

    def open_local_files(self, paths=None) -> None:
        """Open .md files from anywhere on disk without importing them."""
        if paths is None:
            paths = self._ask_open_files()
        if not paths:
            return
        opened, failed = [], []
        for path in paths:
            try:
                key = self._tab_key(loose={"path": path})
                existing = next((t for t in self.tabs if t["key"] == key), None)
                if existing is not None:
                    self.activate_tab(existing)
                    opened.append(existing.get("loose"))
                    continue
                info = self.loose.open_path(path)
                self.show_loose(info)
                opened.append(info)
            except Exception as exc:
                failed.append("%s：%s" % (os.path.basename(_s(path)), _s(exc)))
        if not opened:
            self.notice("；".join(failed) or "没有可打开的文件", error=True)
            return
        self.refresh_recent()
        if len(opened) > 1:
            self.notice("已打开 %d 个文件，可在顶部标签页切换" % len(opened))
        if failed:
            self.notice("；".join(failed), error=True)

    def _ask_open_files(self):
        from tkinter import filedialog
        try:
            chosen = filedialog.askopenfilenames(
                title="打开 Markdown 文件（临时查看，不导入）",
                filetypes=[("Markdown 文件", "*.md *.markdown *.mdown *.mkd *.txt"),
                           ("所有文件", "*.*")])
        except Exception as exc:
            self.notice("打开文件失败：%s" % _s(exc), error=True)
            return []
        return list(chosen or [])

    def new_loose_draft(self) -> None:
        try:
            self.show_loose(self.loose.new_draft("Untitled"))
            self.toggle_mode()
            # 未命名的草稿没有磁盘基线：在用户第一次保存前始终是未保存状态。
            if self.active_tab is not None:
                self.active_tab["baseline"] = None
            self.stash_tab()
            self.notice("新文档尚未保存，Ctrl+S 选择保存位置")
        except Exception as exc:
            self.notice("新建失败：%s" % _s(exc), error=True)

    def show_loose(self, info: dict) -> None:
        """Render a loose document (already read from disk).

        打开临时文档时总是回到预览，和打开项目文档一致：先读，再改。
        """
        existing = next((t for t in self.tabs if t["key"] == self._tab_key(loose=info)), None)
        if existing is not None:
            self.activate_tab(existing)
            return
        self.stash_tab()
        self.active_tab = None
        self.cur_loose = info
        self.cur_doc = None
        try:
            text = info.get("text")
            if text is None:
                if info.get("path"):
                    text = core.read_text_file(_s(info["path"]))
                else:
                    text = ""
        except Exception as exc:
            self.notice("读取失败：%s" % _s(exc), error=True)
            text = ""
        self.source = _s(text)
        self.set_dirty(False)
        self.render()
        self.remember_tab()
        self.reveal_active_tab()
        self.update_title()
        self.restore_reading_position()
        self.update_status()
        self.lbl_doc.configure(text="🗎 %s%s" % (_s(info.get("name")),
                                                 "（未保存）" if not info.get("path") else ""))

    def save_loose(self) -> bool:
        if not self.cur_loose:
            return False
        if self.mode == "source":
            self.source = self.get_text()
        path = _s(self.cur_loose.get("path"))
        if self.cur_loose.get("deleted"):
            # 文件是被明确删除的：绝不能因为按了保存就把它悄悄建回原路径
            self.notice("原文件已被删除，不能直接保存；请用「另存为」保存到新位置", error=True)
            return self.save_as()
        if not path:
            path = self._ask_save_path(_s(self.cur_loose.get("name")) + ".md")
            if not path:
                self.notice("已取消保存")
                return False
        try:
            saved = self.loose.save(_s(self.cur_loose.get("id")), self.source, path or None,
                                    expected=self.cur_loose.get('revision'))
        except core.D.ConflictError as exc:
            return self.resolve_conflict(exc)
        except Exception as exc:
            self.notice("保存失败：%s" % _s(exc), error=True)
            return False
        if self.active_tab:
            self.ws.recovery.discard(self.recovery_identity(self.active_tab), self.source)
        self.cur_loose = saved.get("info") or {}
        if self.active_tab is not None:
            self.active_tab["key"] = self._tab_key(loose=self.cur_loose)
        self.set_dirty(False)
        self.mark_baseline()
        self.draw_tabs()
        self.refresh_recent()
        if self.mode == "preview":
            self.render()
        self.update_title()
        self.notice("已保存到原文件 %s" % datetime.datetime.now().strftime("%H:%M:%S"))
        return True

    def _ask_save_path(self, suggested: str) -> str:
        from tkinter import filedialog
        try:
            return _s(filedialog.asksaveasfilename(
                parent=self.root, title="保存为", defaultextension=".md", initialfile=_s(suggested),
                filetypes=[("Markdown 文件", "*.md"), ("所有文件", "*.*")]))
        except Exception:
            return ""

    def refresh_recent(self) -> None:
        try:
            self.recent = self.loose.list_recent()
        except Exception:
            self.recent = []
        self.filter_recent()

    def filter_recent(self):
        query = self.recent_query.get().strip().casefold()
        selected = self.recent_list.curselection()
        selected_path = self.visible_recent[int(selected[0])]["path"] if selected and int(selected[0]) < len(self.visible_recent) else None
        self.visible_recent = [item for item in self.recent
                               if query in (item.get("name", "") + " " + item.get("path", "")).casefold()]
        try:
            self.recent_list.delete(0, "end")
            for item in self.visible_recent:
                mark = "⚠ " if item.get("missing") else "🗎 "
                self.recent_list.insert("end", "%s%s" % (mark, _s(item.get("name"))))
            if selected_path:
                for index, item in enumerate(self.visible_recent):
                    if item.get("path") == selected_path:
                        self.recent_list.selection_clear(0, "end")
                        self.recent_list.selection_set(index)
                        self.recent_list.see(index)
                        break
        except Exception:
            pass
        self.lbl_recent.configure(text="最近打开 · %d / %d" % (len(self.visible_recent), len(self.recent)))
        self.update_recent_selection()

    def update_recent_selection(self):
        selected = self.recent_list.curselection()
        self.btn_forget_recent.configure(state="normal" if selected else "disabled")
        if selected and int(selected[0]) < len(self.visible_recent):
            self.set_status(self.visible_recent[int(selected[0])]["path"])

    def forget_selected_recent(self):
        selection = self.recent_list.curselection()
        if selection:
            self.forget_recent(int(selection[0]))

    def open_selected_recent(self) -> None:
        try:
            selection = self.recent_list.curselection()
        except Exception:
            return
        if not selection:
            return
        index = int(selection[0])
        if not (0 <= index < len(self.visible_recent)):
            return
        item = self.visible_recent[index]
        if item.get("missing"):
            self.notice("文件已不在原位置：%s" % _s(item.get("path")), error=True)
            return
        self.open_local_files([_s(item.get("path"))])

    def on_recent_menu(self, event) -> None:
        """Right click a recent entry: remove it from the list (file untouched)."""
        try:
            index = self.recent_list.nearest(event.y)
            if not (0 <= index < len(self.visible_recent)):
                return
            bounds = self.recent_list.bbox(index)
            if not bounds or not (bounds[1] <= event.y < bounds[1] + bounds[3]):
                return
            self.recent_list.selection_clear(0, "end")
            self.recent_list.selection_set(index)
        except Exception:
            return
        try:
            popup = self._tk.Menu(self.root, tearoff=0, background=self.pal["side"],
                                  foreground=self.pal["fg"], activebackground=self.pal["sel"],
                                  activeforeground=self.pal["fg"], bd=0)
            popup.add_command(label="删除打开记录（保留文件）",
                              command=lambda: self.forget_recent(index))
            popup.add_command(label="在资源管理器中定位",
                              command=lambda: self.reveal_recent(index))
            popup.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass
        finally:
            if "popup" in locals():
                popup.grab_release()

    def forget_recent(self, index: int) -> None:
        if not (0 <= index < len(self.visible_recent)):
            return
        item = self.visible_recent[index]
        try:
            self.loose.forget(_s(item.get("path")))
        except Exception as exc:
            self.notice("移除失败：%s" % _s(exc), error=True)
            return
        self.refresh_recent()
        self.notice("已从列表移除（文件未删除）")

    def reveal_recent(self, index: int) -> None:
        if not (0 <= index < len(self.visible_recent)):
            return
        try:
            self.ws.open_in_explorer(_s(self.visible_recent[index].get("path")), select=True)
        except Exception as exc:
            self.notice("定位失败：%s" % _s(exc), error=True)

    def toggle_sidebar(self) -> None:
        # winfo_manager() is "grid" while shown and "" while grid_remove()d; it
        # is reliable even when the toplevel is not mapped.
        if self.side.winfo_manager():
            self.side.grid_remove()
            self.notice("已隐藏侧栏")
        else:
            self.side.grid()
            self.notice("已显示侧栏")

    # -- state -----------------------------------------------------------
    def set_dirty(self, value: bool) -> None:
        self.dirty = bool(value)
        if self.active_tab is not None:
            identity = self.recovery_identity(self.active_tab)
            if not value:
                self.ws.recovery.discard(identity, self.source)
            else:
                import time
                if self._recovery_job is not None:
                    self.root.after_cancel(self._recovery_job)
                self._recovery_job = self.root.after(2000, self.flush_recovery)
                if time.monotonic() - self._recovery_last >= 15:
                    self.flush_recovery()
        if self.active_tab is not None:
            was_dirty = self.active_tab.get("dirty")
            self.stash_tab()
            if was_dirty != self.dirty:
                self.draw_tabs()
        self.update_title()
        self.update_status()

    def update_title(self) -> None:
        parts = [core.APP_NAME + " " + core.APP_VERSION]
        if self.cur_loose:
            parts.append("临时查看")
            parts.append(_s(self.cur_loose.get("name")))
        else:
            proj = self.current_project()
            if proj:
                parts.append(_s(proj.get("name")))
            if self.cur_doc:
                parts.append(_s(self.cur_doc.get("name")))
        try:
            self.root.title(" \u2014 ".join(parts) + (" \u2022" if self.dirty else ""))
            if self.cur_loose:
                if self.cur_loose.get("path"):
                    self.lbl_doc.configure(text="\U0001f5ce %s  \u00b7  临时查看（改的是原文件）"
                                          % _s(self.cur_loose.get("file")))
                else:
                    self.lbl_doc.configure(text="#Untitled  ·  新文档（尚未保存）")
            else:
                info = self.cur_doc or {}
                self.lbl_doc.configure(text="%s  \u00b7  %s" % (_s(info.get("file")) or "未打开文档",
                                                              _s(info.get("dir"))))
        except Exception:
            pass

    def current_project(self) -> dict | None:
        return next((p for p in self.projects if p.get("id") == self.cur_pid), None)

    def update_status(self) -> None:
        try:
            text = self.source if self.mode == "preview" else self.get_text()
        except Exception:
            text = self.source
        try:
            if self.cur_loose:
                where = _s(self.cur_loose.get("path")) or "新文档（未保存）"
                self.lbl_status.configure(text="临时查看：%s" % where)
            else:
                self.lbl_status.configure(text="文档：%s" % (_s((self.cur_doc or {}).get("id")) or "—"))
            self.lbl_count.configure(text="%d 字符 · %d 行"
                                          % (len(text), text.count("\n") + 1 if text else 0))
            unsaved = self.dirty or (self.cur_loose and not self.cur_loose.get("path"))
            self.lbl_state.configure(text="# 未保存" if unsaved else
                                     ("已保存" if self.cur_loose or self.cur_doc else ""))
        except Exception:
            pass

    def set_status(self, message: str, error: bool = False) -> None:
        try:
            self.lbl_status.configure(text=message,
                                      foreground=self.pal["accent"] if error else self.pal["fg"])
        except Exception:
            pass

    def notice(self, message: str, error: bool = False) -> None:
        """Transient status-line message, cleared after a few seconds."""
        self.set_status(message, error)
        if self._notice_job:
            try:
                self.root.after_cancel(self._notice_job)
            except Exception:
                pass
        try:
            self._notice_job = self.root.after(4000, lambda: self.set_status("就绪"))
        except Exception:
            self._notice_job = None

    def show_empty(self, message: str) -> None:
        """Friendly placeholder shown when there is nothing to display."""
        self._show(self.preview)
        ime = self.ime
        if ime is not None:
            ime.set_editable(False)
        try:
            self.text.configure(state="normal")
            for child in self.text.winfo_children():
                child.destroy()
            self.text.delete("1.0", "end")
            self.text.insert("end", "\n", ())
            for line in message.split("\n"):
                self.text.insert("end", "  " + line + "\n", ("placeholder",))
            self.text.configure(state="disabled")
            self.empty.configure(text=message)
            self.empty.place(relx=0.5, rely=0.42, anchor="center")
        except Exception:
            pass
        self.mode = "preview"
        self.update_title()
        self.update_status()

    def hide_empty(self) -> None:
        try:
            self.empty.place_forget()
        except Exception:
            pass

    # -- lifecycle -------------------------------------------------------
    def on_close(self) -> None:
        self.save_reading_positions()
        self.stash_tab()
        for tab in list(self.tabs):
            if tab_needs_save(tab):
                self.activate_tab(tab)
                if not self.confirm_switch(discard_marks_clean=False):
                    return
        for tab in self.tabs:
            self.ws.recovery.discard(self.recovery_identity(tab))
        try:
            drop = getattr(self, "_drop", None)
            if drop is not None:
                drop.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def _self_check() -> int:
    sample = ("---\ntitle: 演示\nauthor: MDReader\n---\n\n# 标题一\n\n正文 **粗体** 与 `code`。\n\n"
              "| 名称 | 数量 |\n|:--|--:|\n| 苹果 | 12 |\n")
    segments = parse_markdown(sample)
    print("segments:", len(segments))
    for text, tags in segments:
        print("  %-30r %s" % (text, ",".join(tags)))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--gui" in argv:
        root = core.default_workspace()
        if "--workspace" in argv:
            idx = argv.index("--workspace")
            if idx + 1 < len(argv):
                root = argv[idx + 1]
        MarkdownWindow(root).run()
        return 0
    return _self_check()


if __name__ == "__main__":
    raise SystemExit(main())
