"""Themed scan page selection; no accuracy promises."""
import re

def choose_pages(owner):
    tk, pal = owner._tk, owner.pal
    dialog = tk.Toplevel(owner.root)
    dialog.withdraw()
    dialog.title('扫描 PDF 识别')
    dialog.transient(owner.root)
    dialog.configure(bg=pal['bg'])
    value = tk.StringVar(dialog, '1-20')
    result = [None]
    tk.Label(dialog, text='输入 PDF 页码，例如 1-20、25、30-35；全部填 all\n正文和公式转为可编辑内容，公式使用 LaTeX。\n识别结果需要核对，不代表已达到 98% 准确率。',
             bg=pal['bg'], fg=pal['fg'], justify='left').pack(padx=24,pady=18)
    entry=tk.Entry(dialog,textvariable=value,bg=pal['side'],fg=pal['fg'],insertbackground=pal['fg'],width=44)
    entry.pack(padx=24,pady=8)
    error=tk.Label(dialog,text='',bg=pal['bg'],fg=pal['fg']);error.pack()
    def accept():
        text=value.get().strip().replace('，',',')
        if text!='all' and not re.fullmatch(r'[1-9]\d*(?:-[1-9]\d*)?(?:,[1-9]\d*(?:-[1-9]\d*)?)*',text):
            error.configure(text='请输入有效页码或 all');return
        result[0]=text;dialog.destroy()
    tk.Button(dialog,text='开始识别',command=accept,bg=pal['button'],fg=pal['fg']).pack(pady=14)
    entry.bind('<Return>', lambda _e: accept())
    dialog.bind('<Escape>', lambda _e: dialog.destroy())
    dialog.protocol('WM_DELETE_WINDOW',dialog.destroy)
    dialog.update_idletasks()
    x=owner.root.winfo_rootx()+(owner.root.winfo_width()-dialog.winfo_reqwidth())//2
    y=owner.root.winfo_rooty()+(owner.root.winfo_height()-dialog.winfo_reqheight())//2
    dialog.geometry(f'+{max(0,x)}+{max(0,y)}')
    from .display import style_titlebar
    style_titlebar(dialog,owner.dark,pal['bg'],pal['fg'])
    dialog.deiconify();dialog.grab_set();entry.focus_set()
    owner.root.wait_window(dialog)
    return result[0]
