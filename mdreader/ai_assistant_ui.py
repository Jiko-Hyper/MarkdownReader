"""Provider setup and chat in one native window, with network work off the UI thread."""
import queue
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser
import difflib

from .ai_providers import PROVIDERS, PROTOCOLS


class AssistantDialog:
    def __init__(self, parent, store, palette, get_document=None, insert_reply=None,
                 get_snapshot=None, apply_edit=None):
        self.store, self.pal = store, dict(palette)
        self.buttons = []
        self.settings = store.settings()
        self.get_document, self.insert_reply = get_document, insert_reply
        self.get_snapshot, self.apply_edit = get_snapshot, apply_edit
        self.proposals = {name: None for name in PROVIDERS}
        self.history = {name: [] for name in PROVIDERS}
        self.replies = {name: '' for name in PROVIDERS}
        self.feedback = {name: '' for name in PROVIDERS}
        self.queue = queue.Queue()
        self.busy = False
        self.closed = False
        self.timer = None
        self._complete = None
        self.refresh_timer = None
        p = palette
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title('AI 接入与助手 · GPT / Claude / DeepSeek')
        self.window.transient(parent)
        self.window.configure(bg=p['bg'])
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda e: self.close())
        body = tk.Frame(self.window, bg=p['bg'], padx=20, pady=16)
        body.pack(fill='both', expand=True)
        self.label(body, 'AI 文档助手', 16).pack(anchor='w', pady=(0, 10))
        form = tk.Frame(body, bg=p['bg'])
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        self.provider = tk.StringVar()
        self.model = tk.StringVar()
        self.key = tk.StringVar()
        self.base = tk.StringVar()
        self.protocol = tk.StringVar()
        self.labels = [v['label'] for v in PROVIDERS.values()]
        self.provider_box = ttk.Combobox(form, textvariable=self.provider, values=self.labels, state='readonly')
        self.model_box = ttk.Combobox(form, textvariable=self.model)
        self.key_entry = tk.Entry(form, textvariable=self.key, show='●', bg=p['side'], fg=p['fg'], insertbackground=p['fg'], relief='flat')
        self.base_entry = tk.Entry(form, textvariable=self.base, bg=p['side'], fg=p['fg'], insertbackground=p['fg'], relief='flat')
        self.protocol_box = ttk.Combobox(form, textvariable=self.protocol, values=list(PROTOCOLS.values()), state='readonly')
        for row, (label, widget) in enumerate([('服务商', self.provider_box), ('模型（可输入）', self.model_box),
                                             ('API Key', self.key_entry), ('接口地址', self.base_entry), ('接口格式', self.protocol_box)]):
            self.label(form, label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=5)
            widget.grid(row=row, column=1, sticky='ew', pady=5, ipady=3)
        self.model_button = self.button(form, '获取模型', self.fetch_models)
        self.model_button.grid(row=1, column=2, padx=(8, 0))
        self.button(form, '获取密钥', lambda: webbrowser.open(PROVIDERS[self.name]['key_url'])).grid(row=2, column=2, padx=(8, 0))
        self.button(form, '恢复官方地址', self.reset_base).grid(row=3, column=2, padx=(8, 0))
        self.label(body, '模型不限制版本：可获取账户模型列表，也可直接输入服务商提供的模型 ID。').pack(anchor='w', pady=4)
        self.key_hint = self.label(body, '')
        self.key_hint.pack(anchor='w', pady=4)
        actions = tk.Frame(body, bg=p['bg'])
        actions.pack(fill='x', pady=6)
        self.save_button = self.button(actions, '保存并获取模型', self.save_and_models, True)
        self.save_button.pack(side='left')
        self.test_button = self.button(actions, '测试连接', self.test)
        self.test_button.pack(side='left', padx=8)
        self.clear_key_button = self.button(actions, '清除已保存密钥', self.clear_key)
        self.clear_key_button.pack(side='left')
        self.status_label = self.label(body, '测试连接会发送一条简短请求，按服务商 API 规则计费。')
        self.status_label.pack(fill='x', pady=6)
        transcript = tk.Frame(body, bg=p['bg'])
        transcript.pack(fill='both', expand=True)
        self.output = tk.Text(transcript, height=10, width=76, wrap='word', state='disabled', relief='flat',
                              bg=p['side'], fg=p['fg'], font=('Microsoft YaHei UI', 10), padx=10, pady=8)
        scroll = ttk.Scrollbar(transcript, command=self.output.yview, style='Assistant.Vertical.TScrollbar')
        self.output.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.output.pack(side='left', fill='both', expand=True)
        self.include_document = tk.BooleanVar(value=False)
        self.context_toggle = tk.Checkbutton(body, text='附上当前文档给 AI（含未保存内容）；修改需点击应用',
                                             variable=self.include_document, bg=p['bg'], fg=p['fg'],
                                             selectcolor=p['bg'], activebackground=p['bg'])
        self.context_toggle.pack(anchor='w', pady=6)
        if get_document is None:
            self.context_toggle.config(state='disabled')
        self.label(body, '在这里输入问题（上方是对话记录）').pack(anchor='w', pady=(2, 4))
        self.prompt = tk.Text(body, height=3, width=76, wrap='word', bg=p['side'], fg=p['fg'],
                              insertbackground=p['fg'], relief='flat', font=('Microsoft YaHei UI', 10), padx=8, pady=6)
        self.prompt.pack(fill='x')
        self.prompt.bind('<Control-Return>', lambda e: (self.send(), 'break')[1])
        row = tk.Frame(body, bg=p['bg'])
        row.pack(fill='x', pady=(10, 0))
        self.send_button = self.button(row, '发送  Ctrl+Enter', self.send, True)
        self.send_button.pack(side='left')
        self.new_button = self.button(row, '新对话', self.clear_chat)
        self.new_button.pack(side='left', padx=8)
        self.copy_button = self.button(row, '复制回复', self.copy_reply)
        self.copy_button.pack(side='left')
        self.insert_button = self.button(row, '插入回复文本', self.insert)
        if insert_reply:
            self.insert_button.pack(side='left', padx=8)
        self.apply_button = self.button(row, '应用修改', self.apply_proposal, True)
        if apply_edit:
            self.apply_button.pack(side='left', padx=8)
        self.apply_button.config(state='disabled')
        self.button(row, '关闭', self.close).pack(side='right')
        self.provider.set(PROVIDERS[self.settings['selected']]['label'])
        self.provider_box.bind('<<ComboboxSelected>>', self.select_provider)
        self.select_provider()
        self.apply_theme(palette)
        self.window.update_idletasks()
        width = min(max(760, self.window.winfo_reqwidth()), self.window.winfo_screenwidth() - 50)
        height = min(self.window.winfo_reqheight(), self.window.winfo_screenheight() - 90)
        self.window.geometry('%dx%d+%d+%d' % (width, height, max(0, parent.winfo_rootx()), max(0, parent.winfo_rooty())))
        self.window.minsize(min(width, 700), min(height, 650))
        from .display import style_titlebar
        style_titlebar(self.window, p['bg'] == '#22262d', p['bg'], p['fg'])
        self.window.deiconify()
        self.poll()

    @property
    def name(self):
        return next(k for k, v in PROVIDERS.items() if v['label'] == self.provider.get())

    def label(self, parent, text, size=10):
        return tk.Label(parent, text=text, bg=self.pal['bg'], fg=self.pal['fg'], anchor='w', justify='left',
                        wraplength=740, font=('Microsoft YaHei UI', size))

    def button(self, parent, text, command, primary=False):
        button = tk.Button(parent, text=text, command=command, relief='flat', padx=10, pady=6,
                         bg=self.pal['accent'] if primary else self.pal['button'],
                         fg=self.pal['bg'] if primary else self.pal['fg'])
        self.buttons.append((button, primary))
        return button

    def paint_buttons(self):
        p = self.pal
        for button, primary in self.buttons:
            enabled = str(button.cget('state')) != 'disabled'
            background = (p['accent'] if primary else p['button']) if enabled else p['bg']
            foreground = p['bg'] if primary and enabled else p['fg']
            button.configure(bg=background, fg=foreground, disabledforeground=p['muted'],
                             activebackground=p['accent'] if primary else p['hover'],
                             activeforeground=p['bg'] if primary else p['fg'],
                             bd=0, highlightthickness=1, highlightbackground=background if enabled else p['rule'],
                             highlightcolor=p['accent'], font=('Microsoft YaHei UI', 10))

    def apply_theme(self, palette):
        """Repaint all control states when the main window changes theme."""
        self.pal = dict(palette)
        p = self.pal
        def paint(widget):
            kind = widget.winfo_class()
            if kind in ('Toplevel', 'Frame', 'Label', 'Checkbutton'):
                widget.configure(bg=p['bg'])
            if kind in ('Label', 'Checkbutton'):
                widget.configure(fg=p['fg'])
            if kind == 'Checkbutton':
                widget.configure(activebackground=p['bg'], activeforeground=p['fg'],
                                 selectcolor=p['side'], disabledforeground=p['muted'],
                                 highlightbackground=p['bg'], highlightcolor=p['accent'])
            if kind in ('Entry', 'Text'):
                widget.configure(bg=p['side'], fg=p['fg'], insertbackground=p['fg'],
                                 selectbackground=p['sel'], selectforeground=p['fg'],
                                 highlightthickness=1, highlightbackground=p['rule'],
                                 highlightcolor=p['accent'], bd=0)
                if kind == 'Entry':
                    widget.configure(disabledbackground=p['side'], disabledforeground=p['muted'],
                                     readonlybackground=p['side'])
            for child in widget.winfo_children():
                paint(child)
        paint(self.window)
        style = ttk.Style(self.window)
        combo = 'Assistant.TCombobox'
        style.configure(combo, fieldbackground=p['side'], background=p['button'], foreground=p['fg'],
                        arrowcolor=p['fg'], bordercolor=p['rule'], lightcolor=p['rule'], darkcolor=p['rule'],
                        insertcolor=p['fg'], selectbackground=p['sel'], selectforeground=p['fg'], padding=5)
        style.map(combo, fieldbackground=[('disabled', p['side']), ('readonly', p['side'])],
                  foreground=[('disabled', p['muted']), ('readonly', p['fg'])],
                  background=[('active', p['hover'])], bordercolor=[('focus', p['accent'])],
                  selectbackground=[('!focus', p['side'])], selectforeground=[('!focus', p['fg'])])
        for box in (self.provider_box, self.model_box, self.protocol_box):
            box.configure(style=combo, font=('Microsoft YaHei UI', 10))
            popup = self.window.tk.call('ttk::combobox::PopdownWindow', box)
            self.window.tk.call(str(popup) + '.f.l', 'configure', '-background', p['side'],
                                '-foreground', p['fg'], '-selectbackground', p['sel'],
                                '-selectforeground', p['fg'])
        style.configure('Assistant.Vertical.TScrollbar', background=p['thumb'], troughcolor=p['side'],
                        bordercolor=p['side'], lightcolor=p['thumb'], darkcolor=p['thumb'], arrowcolor=p['fg'])
        style.map('Assistant.Vertical.TScrollbar', background=[('active', p['muted'])])
        self.key_hint.configure(fg=p['muted'])
        self.status_label.configure(bg=p['side'], fg=p['fg'], padx=10, pady=6)
        self.output.configure(padx=14, pady=12, spacing1=3, spacing3=7)
        self.output.tag_configure('role', foreground=p['accent'], font=('Microsoft YaHei UI', 10, 'bold'), spacing1=10)
        self.output.tag_configure('message', foreground=p['fg'])
        self.output.tag_configure('hint', foreground=p['muted'])
        self.paint_buttons()
        from .display import style_titlebar
        style_titlebar(self.window, p['bg'] == '#22262d', p['bg'], p['fg'])

    def select_provider(self, event=None):
        profile = self.settings['profiles'][self.name]
        self.model.set(profile['model'])
        self.model_box.configure(values=profile.get('models') or [])
        self.protocol.set(PROTOCOLS.get(profile.get('protocol', 'auto'), PROTOCOLS['auto']))
        self.base.set(profile['base_url'])
        self.key.set('')
        self.key_hint.config(text='密钥已加密保存；留空保留，填入新密钥可更换。' if profile['has_key'] else '请粘贴此服务商的 API Key。密钥在本机加密保存。')
        self.include_document.set(False)
        self.render_history()
        if self.refresh_timer:
            self.window.after_cancel(self.refresh_timer)
        if profile['has_key'] and not profile.get('models'):
            self.refresh_timer = self.window.after_idle(self.refresh_models)

    def refresh_models(self):
        self.refresh_timer = None
        if not self.closed and not self.busy:
            self.fetch_models(skip_save=True)

    def save_and_models(self):
        if self.save() and self.settings['profiles'][self.name]['has_key']:
            self.fetch_models(skip_save=True)

    def reset_base(self):
        if not self.busy:
            self.base.set(PROVIDERS[self.name]['base_url'])

    def save(self):
        if self.busy:
            return False
        try:
            self.settings = self.store.save({'provider': self.name, 'model': self.model.get(),
                                             'base_url': self.base.get(), 'api_key': self.key.get(),
                                             'protocol': next(k for k, v in PROTOCOLS.items() if v == self.protocol.get())})
            profile = self.settings['profiles'][self.name]
            self.base.set(profile['base_url'])
            self.protocol.set(PROTOCOLS[profile['protocol']])
            self.key.set('')
            self.key_hint.config(text='密钥已加密保存；留空保留，填入新密钥可更换。' if self.settings['profiles'][self.name]['has_key'] else '尚未设置 API Key')
            self.status_label.config(text='设置已保存')
            return True
        except (ValueError, OSError) as exc:
            self.status_label.config(text=str(exc))
            return False

    def clear_key(self):
        try:
            self.settings = self.store.save({'provider': self.name, 'clear_key': True})
            self.select_provider()
            self.status_label.config(text='已清除密钥')
        except (ValueError, OSError) as exc:
            self.status_label.config(text=str(exc))

    def set_busy(self, value):
        self.busy = value
        for widget in (self.model_button, self.save_button, self.test_button, self.clear_key_button,
                       self.send_button, self.new_button, self.key_entry, self.base_entry, self.prompt):
            widget.config(state='disabled' if value else 'normal')
        self.provider_box.config(state='disabled' if value else 'readonly')
        self.model_box.config(state='disabled' if value else 'normal')
        self.protocol_box.config(state='disabled' if value else 'readonly')
        self.apply_button.config(state='normal' if not value and self.proposals[self.name] else 'disabled')
        self.insert_button.config(state='disabled' if value or self.proposals[self.name] else 'normal')
        self.paint_buttons()

    def work(self, fn, complete):
        if self.busy:
            return
        self.set_busy(True)
        self.status_label.config(text='正在连接，请稍候…')
        self._complete = complete
        results = self.queue
        def worker():
            try:
                results.put((fn(), None))
            except Exception as exc:
                results.put((None, str(exc) if isinstance(exc, (ValueError, OSError)) else '请求失败，请重试。'))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        if self.closed:
            return
        try:
            result, error = self.queue.get_nowait()
            complete, self._complete = self._complete, None
            self.set_busy(False)
            if error:
                self.status_label.config(text=error)
                self.feedback[self.name] = '操作失败：' + error + '\n问题已保留，可以修改设置后重新发送。'
                self.render_history()
            else:
                complete(result)
        except queue.Empty:
            pass
        self.timer = self.window.after(100, self.poll)

    def fetch_models(self, skip_save=False):
        if skip_save or self.save():
            name = self.name
            store = self.store
            def done(result):
                self.settings = self.store.settings()
                self.model_box.config(values=result['models'])
                if not self.model.get() and result['models']:
                    self.model.set(result['models'][0])
                self.status_label.config(text='已获取 %d 个模型，请从模型下拉框选择。' % len(result['models']))
            self.work(lambda: store.models(name), done)

    def test(self):
        if self.save():
            name = self.name
            store = self.store
            self.work(lambda: store.chat({'provider': name}, test=True),
                      lambda result: self.status_label.config(text='连接成功，模型已回复：' + result['text'][:80]))

    def send(self):
        if self.busy:
            return
        prompt = self.prompt.get('1.0', 'end-1c').strip()
        if not prompt:
            self.status_label.config(text='请输入问题')
            return
        if not self.save():
            return
        try:
            context = self.get_document() if self.include_document.get() and self.get_document else ''
            snapshot = self.get_snapshot() if self.include_document.get() and self.get_snapshot else None
            if snapshot is not None:
                context = snapshot['text']
        except ValueError as exc:
            self.status_label.config(text=str(exc))
            return
        name = self.name
        store = self.store
        self.proposals[name] = None
        edit_enabled = snapshot is not None
        messages = self.history[name] + [{'role': 'user', 'content': prompt}]
        self.feedback[name] = '你（等待回复）：\n' + prompt + '\n\n正在等待模型回复…'
        self.render_history()
        def done(result):
            self.history[name] = messages + [{'role': 'assistant', 'content': result['text']}]
            self.replies[name] = result['text']
            self.feedback[name] = ''
            document = result.get('document')
            if snapshot is not None and isinstance(document, str) and document != snapshot['text']:
                self.proposals[name] = (snapshot, document)
                diff = '\n'.join(difflib.unified_diff(snapshot['text'].splitlines(),
                              document.splitlines(), fromfile='修改前', tofile='修改后', lineterm=''))
                self.feedback[name] = '待应用修改（- 删除，+ 新增）：\n' + diff
            self.prompt.delete('1.0', 'end')
            self.render_history()
            self.status_label.config(text='请查看修改内容，点击「应用修改」写入当前文档。' if self.proposals[name]
                                     else result.get('note') or '回复完成，可复制或插入文档。')
        self.work(lambda: store.chat({'provider': name, 'messages': messages, 'context': context,
                                     'edit': edit_enabled}), done)

    def render_history(self):
        self.output.config(state='normal')
        self.output.delete('1.0', 'end')
        for msg in self.history[self.name]:
            self.output.insert('end', ('你' if msg['role'] == 'user' else 'AI 助手') + '\n', 'role')
            self.output.insert('end', msg['content'] + '\n\n', 'message')
        if not self.history[self.name]:
            self.output.insert('end', '可以让我解释代码、整理需求、编写开发计划，或改进当前文档。\n\n对话仅在此窗口保留；只有勾选下方选项才会发送文档内容。', 'hint')
        if self.feedback[self.name]:
            self.output.insert('end', '\n\n' + self.feedback[self.name])
        self.output.config(state='disabled')
        self.output.see('end')
        self.apply_button.config(state='normal' if self.proposals[self.name] and not self.busy else 'disabled')
        self.insert_button.config(state='disabled' if self.busy or self.proposals[self.name] else 'normal')
        self.paint_buttons()

    def clear_chat(self):
        self.history[self.name] = []
        self.replies[self.name] = ''
        self.feedback[self.name] = ''
        self.proposals[self.name] = None
        self.render_history()

    def apply_proposal(self):
        proposal = self.proposals[self.name]
        if self.busy or proposal is None or self.apply_edit is None:
            return
        try:
            self.apply_edit(*proposal)
        except ValueError as exc:
            self.status_label.config(text=str(exc))
            return
        self.proposals[self.name] = None
        self.feedback[self.name] = '修改已应用，可在编辑器撤销；保存后才会写入文件。'
        self.render_history()
        self.status_label.config(text=self.feedback[self.name])

    def copy_reply(self):
        reply = self.replies[self.name]
        if reply:
            self.window.clipboard_clear()
            self.window.clipboard_append(reply)
            self.status_label.config(text='回复已复制')

    def insert(self):
        if self.replies[self.name] and self.insert_reply:
            try:
                self.insert_reply(self.replies[self.name])
                self.status_label.config(text='已插入当前文档，可撤销；保存后才会写入文件。')
            except ValueError as exc:
                self.status_label.config(text=str(exc))

    def close(self):
        self.closed = True
        self._complete = None
        self.get_document = self.insert_reply = None
        self.get_snapshot = self.apply_edit = None
        self.proposals.clear()
        if self.timer:
            self.window.after_cancel(self.timer)
        if self.refresh_timer:
            self.window.after_cancel(self.refresh_timer)
        self.key.set('')
        self.window.destroy()
