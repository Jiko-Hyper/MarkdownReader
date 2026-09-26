"""Modeless, themed document search with all-match highlighting."""


class FindDialog:
    def __init__(self, owner):
        from .winui import UI_FONT
        self.owner, self.text, self.tab = owner, owner.text, owner.active_tab
        self.tk = tk = owner._tk
        self.matches, self.current = [], -1
        self.pending = None
        self.closed = False
        self.last_query = None
        self.window = dialog = tk.Toplevel(owner.root)
        dialog.withdraw()
        dialog.title('文档内查找')
        dialog.transient(owner.root)
        dialog.resizable(False, False)  # Native title bar remains draggable.
        scale = owner.ui_scale
        self.body = tk.Frame(dialog, padx=round(20*scale), pady=round(16*scale))
        self.body.pack(fill='both', expand=True)
        self.heading = tk.Label(self.body, text='搜索文章', anchor='w', font=(UI_FONT, 12, 'bold'))
        self.heading.pack(fill='x', pady=(0, round(10*scale)))
        self.query = tk.StringVar(master=dialog)
        self.entry = tk.Entry(self.body, textvariable=self.query, font=(UI_FONT, 11),
                              relief='flat', highlightthickness=1, width=42)
        self.entry.pack(fill='x', ipady=round(5*scale))
        self.status = tk.Label(self.body, text='请输入搜索内容 · 共 0 个匹配', anchor='w',
                               font=(UI_FONT, 10))
        self.status.pack(fill='x', pady=round(12*scale))
        if self.tab is None:
            self.status.configure(text='请先打开一篇文章 · 共 0 个匹配')
        self.actions = tk.Frame(self.body)
        self.actions.pack(fill='x')
        self.buttons = {}
        for column, (label, action) in enumerate((('goto the First', 'first'), ('back', 'back'),
                                                ('next', 'next'), ('goto the Last', 'last'))):
            button = tk.Button(self.actions, text=label, command=lambda a=action: self.go(a),
                               relief='flat', bd=0, padx=round(10*scale), pady=round(6*scale),
                               font=(UI_FONT, 10), state='disabled')
            button.grid(row=0, column=column, padx=(0 if column == 0 else round(6*scale), 0), sticky='ew')
            self.actions.columnconfigure(column, weight=1)
            self.buttons[label] = button
        self.apply_theme()
        from .ime import InlineIME
        self.ime = InlineIME(self.entry, palette=lambda: {
            **owner.pal, 'bg': self.entry.cget('background')})
        self.ime.set_editable(True)
        self.trace = self.query.trace_add('write', self.schedule)
        self.outside_binding = owner.root.bind('<ButtonPress-1>', self.outside_click, add='+')
        dialog.protocol('WM_DELETE_WINDOW', self.close)
        dialog.bind('<Escape>', lambda _e: self.close())
        # Entry's class binding can consume Ctrl+F before the toplevel sees it.
        # Handle it on the entry as well and stop propagation after one toggle.
        for widget in (dialog, self.entry):
            for sequence in ('<Control-f>', '<Control-F>'):
                widget.bind(sequence, owner._key(owner.toggle_find))
        self.entry.bind('<Return>', lambda _e: self.go('next'))
        self.entry.bind('<Shift-Return>', lambda _e: self.go('back'))
        dialog.bind('<Destroy>', self.on_destroy, add='+')
        dialog.update_idletasks()
        width, height = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
        x = owner.root.winfo_rootx() + (owner.root.winfo_width()-width)//2
        y = owner.root.winfo_rooty() + (owner.root.winfo_height()-height)//2
        dialog.geometry('%dx%d+%d+%d' % (width, height, max(0, x), max(0, y)))
        dialog.deiconify()
        self.focus_query()

    def apply_theme(self):
        from .display import style_titlebar
        pal = self.owner.pal
        for widget in (self.window, self.body, self.actions, self.heading, self.status):
            widget.configure(bg=pal['bg'])
        self.heading.configure(fg=pal['fg'])
        self.status.configure(fg=pal['muted'])
        self.entry.configure(bg=pal['code_bg'], fg=pal['fg'], insertbackground=pal['fg'],
                             selectbackground=pal['sel'], selectforeground=pal['fg'],
                             highlightbackground=pal['rule'], highlightcolor=pal['accent'])
        for button in self.buttons.values():
            button.configure(bg=pal['button'], fg=pal['head'], activebackground=pal['hover'],
                             activeforeground=pal['head'], disabledforeground=pal['head'],
                             highlightbackground=pal['bg'])
        # The same blue-grey selection fill as the supplied night-theme example.
        self.text.tag_configure('find_hit', background=pal['sel'], foreground=pal['fg'])
        self.text.tag_configure('find_current', background=pal['sel'], foreground=pal['fg'], underline=True)
        self.text.tag_raise('find_hit')
        self.text.tag_raise('find_current')
        style_titlebar(self.window, self.owner.dark, pal['bg'], pal['fg'])
        if hasattr(self, 'ime'):
            self.ime.refresh()

    def focus_query(self):
        self.window.lift()
        self.entry.focus_set()
        self.entry.selection_range(0, 'end')
        return 'break'

    def schedule(self, *_):
        if self.pending:
            self.window.after_cancel(self.pending)
        for button in self.buttons.values():
            button.configure(state='disabled')
        self.pending = self.window.after(100, self.refresh)

    def refresh(self, reset=True):
        if self.closed:
            return
        if self.pending:
            self.window.after_cancel(self.pending)
            self.pending = None
        if self.owner.active_tab is not self.tab or self.owner.text is not self.text:
            self.close()
            return
        self.text.tag_remove('find_hit', '1.0', 'end')
        self.text.tag_remove('find_current', '1.0', 'end')
        needle = self.query.get()
        self.last_query = needle
        self.matches = []
        if needle and self.tab is not None:
            # One Tcl search, literal and case-insensitive. Python's character
            # length works with Tk index arithmetic even for non-BMP text.
            starts = self.text.tk.call(self.text._w, 'search', '-all', '-nocase', '--',
                                       needle, '1.0', 'end-1c')
            for start in self.text.tk.splitlist(starts):
                first = str(start)
                end = self.text.index('%s+%dc' % (first, len(needle)))
                self.matches.append((first, end))
            if self.matches:
                self.text.tag_add('find_hit', *(index for pair in self.matches for index in pair))
        for button in self.buttons.values():
            button.configure(state='normal' if self.matches else 'disabled')
        if not self.matches:
            self.current = -1
            self.status.configure(text=('请先打开一篇文章 · 共 0 个匹配' if self.tab is None else
                                        '共 0 个匹配 · 无匹配结果' if needle else
                                        '请输入搜索内容 · 共 0 个匹配'))
            return
        self.current = 0 if reset else min(max(0, self.current), len(self.matches)-1)
        self.reveal()

    def reveal(self):
        first, end = self.matches[self.current]
        self.text.tag_remove('find_current', '1.0', 'end')
        self.text.tag_add('find_current', first, end)
        self.text.mark_set('insert', first)
        self.text.see(first)
        self.text.update_idletasks()
        line = self.text.dlineinfo(first)
        if line and self.window.winfo_ismapped():
            top = self.text.winfo_rooty() + line[1]
            popup_top = self.window.winfo_rooty()
            if top < popup_top + self.window.winfo_height() and top + line[3] > popup_top:
                self.text.yview(first)
        self.status.configure(text='共 %d 个匹配 · 当前 %d / %d' %
                              (len(self.matches), self.current+1, len(self.matches)))

    def go(self, action):
        if self.last_query != self.query.get():
            self.refresh()
            return 'break'
        if self.matches:
            self.current = {'first': 0, 'last': len(self.matches)-1,
                            'back': (self.current-1) % len(self.matches),
                            'next': (self.current+1) % len(self.matches)}[action]
            self.reveal()
        return 'break'

    def outside_click(self, event):
        if event.widget.winfo_toplevel() != self.window:
            self.close()

    def on_destroy(self, event):
        if event.widget == self.window:
            self.cleanup()

    def cleanup(self):
        if self.closed:
            return
        self.closed = True
        self.ime.close()
        if self.pending:
            self.window.after_cancel(self.pending)
            self.pending = None
        self.query.trace_remove('write', self.trace)
        self.owner.root.unbind('<ButtonPress-1>', self.outside_binding)
        if self.text.winfo_exists():
            self.text.tag_remove('find_hit', '1.0', 'end')
            self.text.tag_remove('find_current', '1.0', 'end')
        if getattr(self.owner, '_find_dialog', None) is self:
            self.owner._find_dialog = None

    def close(self):
        if self.closed:
            return
        self.cleanup()
        self.window.destroy()
