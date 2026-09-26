"""Themed image width controls; source images are never destructively resized."""
def run_job(owner, action):
    """Keep Tk painting and scrolling while a worker waits for the plugin process."""
    import queue
    import threading
    import gc
    if getattr(owner, '_plugin_busy', False):
        raise ValueError('已有插件任务正在处理，请稍候')
    owner._plugin_busy = True
    collect = gc.isenabled()
    gc.disable()  # Tk objects must be collected on the UI thread, never by the worker.
    results = queue.Queue()
    done = owner._tk.BooleanVar(owner.root, False)
    def work():
        try:
            results.put((True, action()))
        except Exception as exc:
            results.put((False, exc))
    def poll():
        if results.empty():
            owner.root.after(25, poll)
        else:
            done.set(True)
    threading.Thread(target=work, daemon=True, name='MDReader export').start()
    owner.root.after(25, poll)
    try:
        owner.root.wait_variable(done)
        ok, result = results.get_nowait()
        if not ok:
            raise result
        return result
    finally:
        owner._plugin_busy = False
        if collect:
            gc.enable()


def choose_width(owner, initial=720, title='调整图片大小'):
    tk, pal = owner._tk, owner.pal
    dialog = tk.Toplevel(owner.root)
    dialog.withdraw()
    dialog.title(title)
    dialog.transient(owner.root)
    dialog.configure(bg=pal['bg'])
    dialog.resizable(False, False)
    result = [None]
    value = tk.IntVar(dialog, value=initial)
    tk.Label(dialog, text='图片宽度（像素），高度按比例调整', bg=pal['bg'], fg=pal['fg'],
             font=('Microsoft YaHei UI', 11)).pack(padx=24, pady=(20, 8))
    tk.Scale(dialog, from_=40, to=2000, resolution=10, orient='horizontal', variable=value,
             length=owner.px(380), bg=pal['bg'], fg=pal['fg'], troughcolor=pal['button'],
             activebackground=pal['accent'], highlightthickness=0).pack(padx=24)
    actions = tk.Frame(dialog, bg=pal['bg'])
    actions.pack(padx=24, pady=18)
    def finish(answer):
        result[0] = answer
        dialog.destroy()
    for label, command in [('缩小 −', lambda: value.set(max(40, value.get() - 40))),
                            ('放大 ＋', lambda: value.set(min(2000, value.get() + 40))),
                            ('取消', lambda: finish(None)), ('确定', lambda: finish(value.get()))]:
        tk.Button(actions, text=label, command=command, bg=pal['button'], fg=pal['fg'],
                  activebackground=pal['hover'], activeforeground=pal['fg'], relief='flat', padx=12, pady=6).pack(side='left', padx=4)
    dialog.bind('<Escape>', lambda _e: finish(None))
    dialog.protocol('WM_DELETE_WINDOW', lambda: finish(None))
    dialog.update_idletasks()
    width, height = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    dialog.geometry('+%d+%d' % (max(0, owner.root.winfo_rootx() + (owner.root.winfo_width()-width)//2),
                               max(0, owner.root.winfo_rooty() + (owner.root.winfo_height()-height)//2)))
    from .display import style_titlebar
    style_titlebar(dialog, owner.dark, pal['bg'], pal['fg'])
    dialog.deiconify()
    dialog.grab_set()
    owner.root.wait_window(dialog)
    return result[0]


def resize_image(owner, offset=None):
    from .media import image_matches, width_of
    if not owner.plugin_commands('editor.image_insert'):
        owner.notice('请先启用图片插入与缩放插件', error=True)
        return
    owner.stash_tab()
    source = owner.source
    matches = image_matches(source)
    if not matches:
        owner.notice('当前文档没有图片', error=True)
        return
    if offset is None and owner.mode == 'source':
        offset = len(owner.editor.get('1.0', 'insert'))
    match = next((m for m in matches if m.start() <= (offset or 0) <= m.end()), matches[0])
    width = choose_width(owner, width_of(match.group(3)))
    if width is None:
        return
    replacement = '![%s](%s "width=%d")' % (match.group(1), match.group(2), width)
    preview = owner.mode == 'preview'
    owner.show_source()
    editor = owner.editor
    editor.edit_separator()
    separators = editor.cget('autoseparators')
    editor.configure(autoseparators=False)
    try:
        editor.delete('1.0+%dc' % match.start(), '1.0+%dc' % match.end())
        editor.insert('1.0+%dc' % match.start(), replacement)
    finally:
        editor.configure(autoseparators=separators)
    editor.edit_separator()
    owner.source = owner.get_text()
    owner.set_dirty(True)
    if preview:
        owner.render()
    owner.notice('图片宽度已改为 %d 像素，保存文档后生效；可撤销' % width)
