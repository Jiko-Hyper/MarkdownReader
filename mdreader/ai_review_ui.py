"""Themed review of independent changes, using the existing editor callback."""
import tkinter as tk
from tkinter import ttk
from .display import style_titlebar


class ReviewDialog:
    def __init__(self, owner, review, changed=lambda: None):
        self.owner, self.review, self.changed = owner, review, changed
        self.timer = None
        self.closed = False
        p = owner.pal
        self.window = tk.Toplevel(owner.window)
        self.window.withdraw()
        self.window.title('逐项核对 AI 修改')
        self.window.transient(owner.window)
        self.window.configure(bg=p['bg'])
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda _e: self.close())
        self.label(self.window, '选择一处修改，核对原文与建议，再决定是否采用。').pack(fill='x', padx=16, pady=(14,4))
        self.label(self.window, '同一段落、代码块或公式中的修改合并核对；保存后才写入文件。').pack(fill='x', padx=16)
        if review.note:
            self.label(self.window, review.note).pack(fill='x', padx=16)
        list_frame=tk.Frame(self.window,bg=p['bg']); list_frame.pack(fill='x',padx=16,pady=8)
        self.list=tk.Listbox(list_frame,height=5,exportselection=False,bg=p['side'],fg=p['fg'],
                             selectbackground=p['sel'],selectforeground=p['fg'],relief='flat')
        scroll=ttk.Scrollbar(list_frame,command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y'); self.list.pack(fill='x')
        self.list.bind('<<ListboxSelect>>',lambda _e:self.show_change())
        panes=tk.Frame(self.window,bg=p['bg']); panes.pack(fill='both',expand=True,padx=16)
        panes.columnconfigure(0,weight=1); panes.columnconfigure(1,weight=1); panes.rowconfigure(0,weight=1)
        self.texts=[]
        for column,title in enumerate(('原文（删除或替换前）','建议（新增或替换后）')):
            frame=tk.Frame(panes,bg=p['bg']);frame.grid(row=0,column=column,sticky='nsew',padx=(0,8) if column==0 else (8,0))
            self.label(frame,title).pack(fill='x',pady=4)
            text=tk.Text(frame,wrap='none',height=12,width=35,bg=p['side'],fg=p['fg'],
                         relief='flat',font=('Consolas',11),padx=8,pady=8)
            y=ttk.Scrollbar(frame,command=text.yview);x=ttk.Scrollbar(frame,orient='horizontal',command=text.xview)
            text.configure(yscrollcommand=y.set,xscrollcommand=x.set)
            y.pack(side='right',fill='y'); x.pack(side='bottom',fill='x');text.pack(fill='both',expand=True)
            text.tag_configure('changed',background=p['sel'])
            self.texts.append(text)
        self.notice=self.label(self.window,'');self.notice.pack(fill='x',padx=16,pady=8)
        row=tk.Frame(self.window,bg=p['bg']);row.pack(fill='x',padx=16,pady=(0,14))
        self.accept_button=self.button(row,'采用此处',lambda:self.perform('accept'))
        self.keep_button=self.button(row,'保留原文',lambda:self.perform('retain'))
        self.all_button=self.button(row,'采用全部待核对',lambda:self.perform('all'))
        self.button(row,'关闭核对',self.close).pack(side='right')
        width=min(1080,self.window.winfo_screenwidth()-80)
        height=min(760,self.window.winfo_screenheight()-100)
        self.window.geometry('%dx%d+%d+%d' % (width,height,
            max(0,min(owner.window.winfo_rootx()+(owner.window.winfo_width()-width)//2,self.window.winfo_screenwidth()-width)),
            max(0,min(owner.window.winfo_rooty()+(owner.window.winfo_height()-height)//2,self.window.winfo_screenheight()-height))))
        self.refresh()
        self.window.update_idletasks()
        style_titlebar(self.window,p['bg']=='#22262d',p['bg'],p['fg'])
        self.window.deiconify()
        self.window.grab_set()
        self.poll()

    def label(self,parent,text):
        return tk.Label(parent,text=text,bg=self.owner.pal['bg'],fg=self.owner.pal['fg'],
                        anchor='w',justify='left',wraplength=1000,font=('Microsoft YaHei UI',10))

    def button(self,parent,text,command):
        p=self.owner.pal
        button=tk.Button(parent,text=text,command=command,bg=p['button'],fg=p['fg'],
                         activebackground=p['hover'],activeforeground=p['fg'],relief='flat',padx=12,pady=8)
        button.pack(side='left',padx=(0,8))
        return button

    def selected(self):
        selection=self.list.curselection()
        return selection[0] if selection else None

    def refresh(self):
        reason=''
        try:
            self.review.refresh(self.owner.get_snapshot())
        except ValueError as exc:
            reason=str(exc)
        selected=self.selected()
        self.list.delete(0,'end')
        for n,change in enumerate(self.review.changes):
            kind='新增' if not change.original else '删除' if not change.replacement else '替换'
            self.list.insert('end','%s · 原文第 %d–%d 行 · %s' % (self.review.state(n),change.line,change.last_line,kind))
        if self.review.changes:
            self.list.selection_set(0 if selected is None else selected)
        pending=len(self.review.pending())
        self.notice.configure(text=reason or '已采用 %d 处 · 保留 %d 处 · 待核对 %d 处；关闭后可在编辑器撤销，再打开核对。' %
                              (len(self.review.accepted),len(self.review.retained),pending))
        self.reason=reason
        self.show_change()
        self.all_button.configure(state='normal' if pending and not reason else 'disabled')

    def show_change(self):
        n=self.selected()
        if n is None:
            values=('','')
        else:
            change=self.review.changes[n]
            values=change.original,change.replacement
        for text,value in zip(self.texts,values):
            text.configure(state='normal');text.delete('1.0','end')
            text.insert('1.0',value if value else '（无文字）','changed')
            text.configure(state='disabled')
        enabled=n is not None and self.review.state(n)=='待核对' and not self.reason
        self.accept_button.configure(state='normal' if enabled else 'disabled')
        self.keep_button.configure(state='normal' if enabled else 'disabled')

    def perform(self,action):
        n=self.selected()
        if action!='all' and n is None:
            return
        try:
            self.review.refresh(self.owner.get_snapshot())
            if action=='retain':
                self.review.retain(n,self.owner.get_snapshot)
            else:
                indices=self.review.pending() if action=='all' else [n]
                self.review.accept(indices,self.owner.get_snapshot,self.owner.apply_edit)
        except ValueError as exc:
            self.refresh()
            self.notice.configure(text=str(exc))
            return
        self.changed()
        self.refresh()

    def poll(self):
        if self.closed:
            return
        # Recheck stale text without rebuilding the list or scrolling the details.
        before=self.review.accepted
        try:
            self.review.refresh(self.owner.get_snapshot())
            reason=''
        except ValueError as exc:
            reason=str(exc)
        if reason!=self.reason or before!=self.review.accepted:
            self.refresh()
            self.changed()
        self.timer=self.window.after(400,self.poll)

    def close(self):
        if self.closed:
            return
        self.closed=True
        if self.timer:
            self.window.after_cancel(self.timer)
        self.window.destroy()

    def apply_theme(self,palette):
        p=palette
        def paint(widget):
            kind=widget.winfo_class()
            if kind in ('Toplevel','Frame','Label'):
                widget.configure(bg=p['bg'])
            if kind=='Label':
                widget.configure(fg=p['fg'])
            if kind in ('Text','Listbox'):
                widget.configure(bg=p['side'],fg=p['fg'],selectbackground=p['sel'],selectforeground=p['fg'])
            if kind=='Text':
                widget.tag_configure('changed',background=p['sel'])
            if kind=='Button':
                widget.configure(bg=p['button'],fg=p['fg'],activebackground=p['hover'],activeforeground=p['fg'])
            for child in widget.winfo_children():
                paint(child)
        paint(self.window)
        style_titlebar(self.window,p['bg']=='#22262d',p['bg'],p['fg'])
