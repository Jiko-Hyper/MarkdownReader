"""Native connection panel; no shell, subprocess or command-line setup."""
import json
import tkinter as tk
from urllib.request import Request, urlopen

from .ai_connection import configure, status


class AiConnectionDialog:
    def __init__(self, parent, api, palette):
        self.api, self.palette = api, palette
        p = palette
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title('AI 接入')
        self.window.transient(parent)
        self.window.configure(bg=p['bg'])
        self.window.resizable(False, False)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda e: self.close())
        self.timer = None
        body = tk.Frame(self.window, bg=p['bg'], padx=24, pady=20)
        body.pack(fill='both', expand=True)
        def label(text, size=10, color=None):
            item = tk.Label(body, text=text, bg=p['bg'], fg=color or p['fg'],
                            font=('Microsoft YaHei UI', size), anchor='w', justify='left', wraplength=650)
            item.pack(fill='x', pady=(0, 10))
            return item
        label('连接 AI，协助处理项目文档', 16)
        label('开启接入   →   复制接入信息   →   粘贴到本机 AI 助手', color=p['muted'])
        self.enabled = tk.BooleanVar()
        self.writable = tk.BooleanVar()
        self.toggle = tk.Checkbutton(body, text='开启 AI 接入', variable=self.enabled, command=self.apply,
                                     bg=p['bg'], fg=p['fg'], selectcolor=p['bg'], activebackground=p['bg'])
        self.toggle.pack(anchor='w', pady=4)
        self.write_toggle = tk.Checkbutton(body, text='允许 AI 修改文档（关闭时只能查看）', variable=self.writable,
                                           command=self.apply, bg=p['bg'], fg=p['fg'], selectcolor=p['bg'],
                                           activebackground=p['bg'])
        self.write_toggle.pack(anchor='w', pady=4)
        self.status_label = label('')
        self.address = tk.StringVar()
        self.key = tk.StringVar()
        for title, variable, mask in [('连接地址', self.address, ''), ('接入密钥', self.key, '●')]:
            label(title, color=p['muted'])
            row = tk.Frame(body, bg=p['bg'])
            row.pack(fill='x', pady=(0, 10))
            entry = tk.Entry(row, textvariable=variable, width=54, show=mask, state='readonly',
                             readonlybackground=p['side'], fg=p['fg'], relief='flat')
            entry.pack(side='left', fill='x', expand=True, ipady=7)
            self.button(row, '复制', lambda v=variable: self.copy(v.get())).pack(side='right', padx=(8, 0))
        actions = tk.Frame(body, bg=p['bg'])
        actions.pack(fill='x', pady=8)
        self.copy_button = self.button(actions, '复制接入信息', self.copy_instructions, True)
        self.copy_button.pack(side='left')
        self.test_button = self.button(actions, '检测连接', self.test_connection)
        self.test_button.pack(side='left', padx=8)
        self.reset_button = self.button(actions, '更换密钥', lambda: self.apply(reset=True))
        self.reset_button.pack(side='left')
        label('设置自动保存。更换密钥后，请重新复制接入信息。', color=p['muted'])
        label('支持能调用本机接口的 AI 助手，普通网页聊天无法直连。\n只访问已保存的项目文档，AI 修改后请重新加载查看。', color=p['muted'])
        self.message = label('')
        self.message.config(height=2)
        self.button(body, '完成', self.close).pack(anchor='e')
        self.refresh()
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = max(0, parent.winfo_rootx() + (parent.winfo_width() - width) // 2)
        y = max(0, parent.winfo_rooty() + (parent.winfo_height() - height) // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')
        from .display import style_titlebar
        style_titlebar(self.window, p['bg'] == '#22262d', p['bg'], p['fg'])
        self.window.deiconify()
        self.toggle.focus_set()
        self.poll()

    def button(self, parent, text, command, primary=False):
        p = self.palette
        return tk.Button(parent, text=text, command=command, relief='flat', padx=12, pady=7,
                         bg=p['accent'] if primary else p['button'], fg=p['bg'] if primary else p['fg'],
                         activebackground=p['hover'], activeforeground=p['fg'])

    def refresh(self):
        self.current = status(self.api)
        s = self.current
        self.enabled.set(s['enabled'])
        self.writable.set(s['writable'])
        self.address.set(s['address'] if s['enabled'] else '')
        self.key.set(s['token'])
        self.status_label.config(text=('已开启 · ' + ('允许修改' if s['writable'] else '只读') +
                                 (' · 最近连接 ' + s['last_access'] if s['last_access'] else ' · 等待 AI 接入'))
                                 if s['enabled'] else '未开启 · AI 无法访问')
        for control in (self.write_toggle, self.copy_button, self.test_button, self.reset_button):
            control.config(state='normal' if s['enabled'] else 'disabled')
        if s['error']:
            self.message.config(text=s['error'])

    def apply(self, reset=False):
        try:
            configure(self.api, {'enabled': self.enabled.get(), 'writable': self.writable.get(), 'reset_key': reset})
            self.message.config(text='密钥已更换，旧密钥已失效。' if reset else '设置已保存')
        except (OSError, ValueError) as exc:
            self.message.config(text='保存失败：' + str(exc))
        self.refresh()

    def copy(self, text):
        if not text:
            self.message.config(text='请先开启 AI 接入')
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(text)
        self.message.config(text='已复制，可粘贴到本机 AI 助手中。')

    def copy_instructions(self):
        self.refresh()
        self.copy(self.current['instructions'])

    def test_connection(self):
        self.refresh()
        try:
            req = Request(self.current['address'], headers={'Authorization': 'Bearer ' + self.current['token']})
            with urlopen(req, timeout=3) as response:
                data = json.load(response)
            self.message.config(text='检测通过：接口可用（%d 项工具）。' % len(data['tools']))
        except Exception as exc:
            self.message.config(text='连接失败：' + str(exc))
        self.refresh()

    def poll(self):
        self.refresh()
        self.timer = self.window.after(1500, self.poll)

    def close(self):
        if self.timer:
            self.window.after_cancel(self.timer)
        self.window.destroy()
