# -*- coding: utf-8 -*-
"""桌面端的「插件」管理窗口。

只做三件事，且都通过 :class:`mdreader.plugins.PluginStore` 完成：

* 列出已安装插件与三个正式插件预留位（名称、版本、状态、兼容范围、依赖、失败原因）；
* 安装维护者发布的本地插件包、启用、禁用、卸载；
* 展示这些插件当前提供的命令（导出格式）。

界面只显示清单里校验过的文字，不加载插件自带 HTML 或界面代码；也不提供
「忽略来源检查」的开关——首版只接受受信清单里登记过哈希的包。
"""

from __future__ import annotations

import os

from . import core

STATE_LABELS = {
    "enabled": "已启用",
    "disabled": "已禁用",
    "unavailable": "不可用",
    "broken": "清单损坏",
    "restart": "重启后生效",
    "not-installed": "未安装",
}


class PluginDialog:
    """一个普通 Toplevel，风格与既有对话框一致。"""

    def __init__(self, owner):
        self.owner = owner                 # MarkdownWindow
        self.tk = owner._tk
        self.pal = owner.pal
        self.scale = owner.ui_scale
        self.ws = owner.ws
        self.dialog = None
        self.tree = None
        self.detail = None
        self.rows = []
        self.buttons = {}

    # -- 构建 -------------------------------------------------------------
    def px(self, value):
        return round(value * self.scale)

    def show(self):
        """建窗口 → 等用户关闭。测试只调 :meth:`build`，不进事件循环。"""
        self.build()
        self.owner.root.wait_window(self.dialog)

    def build(self):
        tk = self.tk
        pal = self.pal
        dialog = tk.Toplevel(self.owner.root)
        self.dialog = dialog
        dialog.title("插件管理")
        dialog.transient(self.owner.root)
        dialog.configure(bg=pal["bg"])
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)

        tk.Label(dialog, text="插件", anchor="w", bg=pal["bg"], fg=pal["fg"],
                 font=(None, 12, "bold")).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        tk.Label(dialog, anchor="w", justify="left", wraplength=self.px(560), bg=pal["bg"],
                 fg=pal["faint"],
                 text="首版只接收维护者发布、并在受信清单里登记过 SHA256 的本地插件包；"
                      "不做在线市场，也不执行任意第三方脚本。").grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 6))

        wrap = tk.Frame(dialog, bg=pal["bg"])
        wrap.grid(row=2, column=0, sticky="nsew", padx=16)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        from tkinter import ttk
        self.tree = ttk.Treeview(wrap, columns=("name", "version", "state", "note"),
                                 show="headings", selectmode="browse", height=9)
        for key, title, width in (("name", "插件", 210), ("version", "版本", 70),
                                  ("state", "状态", 90), ("note", "说明", 230)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=self.px(width), anchor="w", stretch=(key in ("name", "note")))
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.refresh_detail())
        self.tree.bind("<Double-1>", lambda _e: self.toggle())

        self.detail = tk.Label(dialog, anchor="w", justify="left", wraplength=self.px(600),
                               bg=pal["bg"], fg=pal["fg"])
        self.detail.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 0))

        row = tk.Frame(dialog, bg=pal["bg"])
        row.grid(row=4, column=0, sticky="ew", padx=16, pady=12)
        for text, command in (("安装本地插件包…", self.install),
                              ("启用", self.enable),
                              ("禁用", self.disable),
                              ("卸载", self.uninstall),
                              ("刷新", self.refresh),
                              ("关闭", self.close)):
            button = tk.Button(row, text=text, command=command, relief="flat", bd=0,
                               cursor="hand2", padx=12, pady=5, bg=pal["button"], fg=pal["fg"])
            button.pack(side="left", padx=(0, 8))
            self.buttons[text] = button

        self.refresh()
        dialog.bind("<Escape>", lambda _e: self.close())
        dialog.protocol("WM_DELETE_WINDOW", self.close)
        dialog.update_idletasks()
        dialog.geometry("+%d+%d" % (max(0, self.owner.root.winfo_rootx() + self.px(60)),
                                    max(0, self.owner.root.winfo_rooty() + self.px(80))))
        dialog.grab_set()
        return dialog

    def close(self):
        if self.dialog is not None:
            try:
                self.dialog.grab_release()
            except Exception:
                pass
            self.dialog.destroy()
        self.dialog = None

    # -- 数据 -------------------------------------------------------------
    def refresh(self):
        try:
            self.rows = self.ws.plugins.list_plugins()
        except Exception as exc:                     # 状态文件坏了也不能让窗口挂掉
            self.rows = []
            self.owner.notice("插件列表读取失败：%s" % exc, error=True)
        if self.tree is None:
            return
        selected = self.selected_id()
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(self.rows):
            state = STATE_LABELS.get(row["state"], row["state"])
            if row.get("enabled"):
                state = "已启用"
            note = row.get("reason") or row.get("description") or ""
            self.tree.insert("", "end", iid=str(index),
                             values=(row["name"], row["version"] or "—", state, note))
        if selected:
            for index, row in enumerate(self.rows):
                if row["id"] == selected:
                    self.tree.selection_set(str(index))
        elif self.rows:
            self.tree.selection_set("0")
        self.refresh_detail()
        self._sync_buttons()

    def selected_id(self):
        if self.tree is None:
            return ""
        picked = self.tree.selection()
        if not picked:
            return ""
        try:
            return self.rows[int(picked[0])]["id"]
        except (IndexError, ValueError):
            return ""

    def selected_row(self):
        pid = self.selected_id()
        return next((row for row in self.rows if row["id"] == pid), None)

    def refresh_detail(self):
        row = self.selected_row()
        if self.detail is None:
            return
        if row is None:
            self.detail.configure(text="")
            return
        lines = ["%s（%s）" % (row["name"], row["id"])]
        if row["description"]:
            lines.append(row["description"])
        lines.append("状态：%s%s" % (STATE_LABELS.get(row["state"], row["state"]),
                                    ("　%s" % row["reason"]) if row["reason"] else ""))
        if row["installed"]:
            lines.append("兼容范围：本程序 %s / 清单要求 %s（接口 %s）"
                         % (self.ws.plugins.app_version, row["app_version_range"] or "*",
                            row["api_version"] or "?"))
        for dep in row.get("dependencies") or []:
            lines.append("依赖：%s（%s）%s" % (dep["name"], "已启用" if dep["enabled"] else "未启用",
                                            ("　" + dep["reason"]) if dep["reason"] else ""))
        if row.get("commands"):
            lines.append("命令：" + "、".join(c["title"] for c in row["commands"]))
        if row.get("last_error"):
            lines.append("上次失败：" + row["last_error"])
        self.detail.configure(text="\n".join(lines))
        self._sync_buttons()

    def _sync_buttons(self):
        row = self.selected_row()
        state = {
            "启用": bool(row and row["installed"] and not row["enabled"]),
            "禁用": bool(row and row["installed"] and row["enabled"]),
            "卸载": bool(row and row["installed"]),
            "安装本地插件包…": True, "刷新": True, "关闭": True,
        }
        for label, enabled in state.items():
            button = self.buttons.get(label)
            if button is not None:
                button.configure(state="normal" if enabled else "disabled")

    # -- 动作 -------------------------------------------------------------
    def install(self):
        paths = core._dialog_plugin_package()
        if not paths:
            return
        self._run(lambda: self.ws.plugins.install(paths[0]),
                  "已安装插件；启用后它的命令才会出现在「更多」菜单里")

    def enable(self):
        pid = self.selected_id()
        if pid:
            self._run(lambda: self.ws.plugins.enable(pid), "已启用插件")

    def disable(self):
        pid = self.selected_id()
        if pid:
            self._run(lambda: self.ws.plugins.disable(pid), "已禁用插件；正在运行的任务已取消")

    def uninstall(self):
        pid = self.selected_id()
        row = self.selected_row()
        if not pid or row is None:
            return
        from tkinter import messagebox
        if not messagebox.askyesno(
                "卸载插件",
                "卸载「%s」？\n\n只会删除插件代码与它自己的缓存；\n"
                "已插入的图片、源 Markdown 和已经导出的文件都不会被删除。" % row["name"],
                parent=self.dialog):
            return
        self._run(lambda: self.ws.plugins.uninstall(pid), "已卸载插件；用户文件未受影响")

    def toggle(self):
        row = self.selected_row()
        if row is None or not row["installed"]:
            return
        (self.disable if row["enabled"] else self.enable)()

    def _run(self, action, success: str):
        from tkinter import messagebox
        try:
            result = action()
        except Exception as exc:
            messagebox.showerror("插件操作失败", str(exc), parent=self.dialog)
        else:
            note = result.get("note") if isinstance(result, dict) else ""
            self.owner.notice("%s%s" % (success, ("；" + note) if note else ""))
        self.refresh()
        callback = getattr(self.owner, "on_plugins_changed", None)
        if callable(callback):
            callback()


class ExportChoiceDialog:
    """插件提供的导出格式列表；让用户明确看到「用哪个插件导出」。"""

    def __init__(self, owner, commands, on_pick):
        self.owner = owner
        self.tk = owner._tk
        self.pal = owner.pal
        self.commands = commands
        self.on_pick = on_pick
        self.dialog = None
        self.listbox = None

    def show(self):
        tk = self.tk
        pal = self.pal
        dialog = tk.Toplevel(self.owner.root)
        self.dialog = dialog
        dialog.title("用插件导出")
        dialog.transient(self.owner.root)
        dialog.configure(bg=pal["bg"])
        tk.Label(dialog, text="选择导出格式", anchor="w", bg=pal["bg"], fg=pal["fg"]).pack(
            fill="x", padx=16, pady=(14, 4))
        self.listbox = tk.Listbox(dialog, width=52, height=min(10, len(self.commands)), bd=0,
                                  highlightthickness=0, activestyle="none",
                                  bg=pal["tree_bg"], fg=pal["fg"], selectbackground=pal["sel"],
                                  selectforeground=pal["fg"])
        for command in self.commands:
            self.listbox.insert("end", "%s　.%s（%s）" % (command["title"], command["extension"],
                                                        command["plugin_name"]))
        if self.commands:
            self.listbox.selection_set(0)
        self.listbox.pack(fill="both", expand=True, padx=16)
        row = tk.Frame(dialog, bg=pal["bg"])
        row.pack(fill="x", padx=16, pady=12)
        for text, command in (("导出", self.pick), ("取消", self.close)):
            tk.Button(row, text=text, command=command, relief="flat", bd=0, cursor="hand2",
                      padx=12, pady=5, bg=pal["button"], fg=pal["fg"]).pack(side="left", padx=(0, 8))
        self.listbox.bind("<Double-1>", lambda _e: self.pick())
        dialog.bind("<Escape>", lambda _e: self.close())
        dialog.protocol("WM_DELETE_WINDOW", self.close)
        dialog.grab_set()
        self.owner.root.wait_window(dialog)

    def pick(self):
        picked = self.listbox.curselection()
        command = self.commands[picked[0]] if picked else (self.commands[0] if self.commands else None)
        self.close()
        if command:
            self.on_pick(command)

    def close(self):
        if self.dialog is not None:
            try:
                self.dialog.grab_release()
            except Exception:
                pass
            self.dialog.destroy()
        self.dialog = None
