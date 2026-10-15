"""Theme-matched optional component setup; all I/O runs off the Tk thread."""
from .components import ComponentStore, NAMES, missing_for


def show_components(owner, format_name=None):
    from tkinter import filedialog
    from .export_ui import window, label, button, reveal
    from .media_ui import run_job
    import threading
    store = ComponentStore(owner.ws.root)
    dialog = window(owner, '识别组件管理')
    label(owner, dialog, '识别组件只需安装一次，主程序升级会继续复用。\n下载完成后可离线识别。也可导入官方离线组件包。')
    rows = {}
    controls = []
    busy = [False]
    def close():
        if not busy[0]:
            dialog.destroy()
        return 'break'
    dialog.protocol('WM_DELETE_WINDOW', close)
    dialog.bind('<Escape>', lambda _e: close())

    def refresh():
        for name, widget in rows.items():
            available = store.locate(name)
            size = store.catalog.get(name, {}).get('bytes')
            widget.configure(text=NAMES[name] + ('：已就绪' if available else '：未安装') +
                             ('（下载约 %.1f MB）' % (size / 1e6) if size and not available else ''))

    def install(name, local=False):
        archive = None
        if local:
            archive = filedialog.askopenfilename(parent=dialog, title='选择' + NAMES[name] + '组件包', filetypes=[('官方组件包', '*.zip')])
            if not archive:
                return
        for control in controls:
            control.configure(state='disabled')
        busy[0] = True
        cancel = threading.Event()
        progress = {'text': '正在准备安装组件…'}
        try:
            run_job(owner, lambda: store.install(name, archive, cancel=cancel,
                    progress=lambda text: progress.update(text=text)), cancel=cancel,
                    status=lambda: progress['text'], title='安装识别组件')
            status.configure(text=NAMES[name] + '已就绪。')
        except Exception as exc:
            status.configure(text=str(exc))
        finally:
            busy[0] = False
            if dialog.winfo_exists():
                for control in controls:
                    control.configure(state='normal')
                refresh()

    for name in NAMES:
        rows[name] = label(owner, dialog, '')
        actions = owner._tk.Frame(dialog, bg=owner.pal['bg'])
        actions.pack(padx=15, pady=(0, 6))
        controls.append(button(owner, actions, '下载并安装', lambda n=name: install(n)))
        controls.append(button(owner, actions, '导入离线组件包', lambda n=name: install(n, True)))
    status = label(owner, dialog, '首次使用缺少组件的识别功能时，也会打开此窗口。')
    actions = owner._tk.Frame(dialog, bg=owner.pal['bg'])
    actions.pack(pady=(0, 16))
    controls.append(button(owner, actions, '完成', close))
    refresh()
    reveal(owner, dialog)
    if format_name:
        dialog.grab_set()
        owner.root.wait_window(dialog)
        return not missing_for(store, format_name)


def ensure_components(owner, format_name):
    store = ComponentStore(owner.ws.root)
    if not missing_for(store, format_name):
        return True
    return show_components(owner, format_name)
