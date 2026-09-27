"""Desktop first-use export entry points; all writes use the existing plugin store."""
from pathlib import Path
import json
import os
import zipfile

FORMATS = {'pdf': ('PDF', 'mdreader.export-pdf'), 'docx': ('Word', 'mdreader.export-docx')}
PACKAGES = Path(__file__).resolve().parents[1] / 'plugins' / 'packages'


def bundled_package(plugin_id, folder=None):
    candidates = []
    for path in (Path(folder) if folder is not None else PACKAGES).glob('*.zip'):
        try:
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo('manifest.json')
                if info.file_size > 65536:
                    continue
                manifest = json.loads(archive.read(info))
            if manifest.get('id') == plugin_id:
                version = tuple(int(part) for part in manifest['version'].split('.'))
                candidates.append((version, path))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
    if not candidates:
        raise FileNotFoundError('没有找到对应的随包插件，请在插件管理中选择官方本地插件包。')
    return max(candidates)[1]


def prepare_format(store, extension):
    """Only install the requested official format; install still verifies its hash."""
    _, plugin_id = FORMATS[extension]
    row = next((item for item in store.list_plugins() if item['id'] == plugin_id), None)
    if not row or not row['installed']:
        store.install(str(bundled_package(plugin_id)))
    result = store.enable(plugin_id)
    if not result.get('enabled') or result.get('rolled_back'):
        raise ValueError('插件启动未完成%s：%s' % (
            '，已回退至上一版本' if result.get('rolled_back') else '',
            result.get('reason') or '请在插件管理中检查状态'))


def window(owner, title):
    tk, pal = owner._tk, owner.pal
    dialog = tk.Toplevel(owner.root)
    dialog.withdraw()
    dialog.title(title)
    dialog.transient(owner.root)
    dialog.configure(bg=pal['bg'])
    dialog.bind('<Escape>', lambda _e: dialog.destroy())
    return dialog


def reveal(owner, dialog):
    dialog.update_idletasks()
    dialog.geometry('+%d+%d' % (max(0, owner.root.winfo_rootx() + (owner.root.winfo_width()-dialog.winfo_reqwidth())//2),
                              max(0, owner.root.winfo_rooty() + (owner.root.winfo_height()-dialog.winfo_reqheight())//2)))
    from .display import style_titlebar
    style_titlebar(dialog, owner.dark, owner.pal['bg'], owner.pal['fg'])
    dialog.deiconify()
    return dialog


def label(owner, dialog, text):
    widget = owner._tk.Label(dialog, text=text, justify='left', anchor='w',
                            wraplength=owner.px(480), bg=owner.pal['bg'], fg=owner.pal['fg'])
    widget.pack(fill='x', padx=20, pady=12)
    return widget


def button(owner, parent, text, command):
    widget = owner._tk.Button(parent, text=text, command=command, relief='flat',
                             bg=owner.pal['button'], fg=owner.pal['fg'],
                             activebackground=owner.pal['hover'], activeforeground=owner.pal['fg'],
                             padx=12, pady=8)
    widget.pack(side='left', padx=5)
    return widget


def setup(owner, extension):
    title, plugin_id = FORMATS[extension]
    installed = any(row['id'] == plugin_id and row['installed'] for row in owner.ws.plugins.list_plugins())
    dialog = window(owner, '启用 %s 导出' % title)
    status = label(owner, dialog, ('%s 导出尚未启用。' % title) +
                   ('启用已有插件即可使用。' if installed else '可安装并启用随软件提供的对应插件。') +
                   '\n离线使用，不需要配置 AI。安装完成后请再次选择要导出的文档。')
    actions = owner._tk.Frame(dialog, bg=owner.pal['bg'])
    actions.pack(padx=15, pady=(0, 16))
    def prepare():
        from .media_ui import run_job
        enable.configure(state='disabled')
        try:
            run_job(owner, lambda: prepare_format(owner.ws.plugins, extension))
        except Exception as exc:
            if dialog.winfo_exists():
                status.configure(text='无法启用：%s\n可从插件管理检查原因或安装官方本地包。' % exc)
                enable.configure(state='normal')
            return
        owner.on_plugins_changed()
        if dialog.winfo_exists():
            status.configure(text='%s 导出已就绪。关闭此窗口，选择文档后再次点击“导出”。' % title)
    button(owner, actions, '插件管理', owner.manage_plugins)
    enable = button(owner, actions, '启用插件' if installed else '安装并启用', prepare)
    button(owner, actions, '关闭', dialog.destroy)
    return reveal(owner, dialog)


def choose_format(owner, extension):
    from . import core
    command = next((row for row in owner.plugin_commands(core.PL.CAP_EXPORT)
                    if row['extension'] == extension), None)
    if command is not None:
        owner.plugin_export_document(command)
    else:
        setup(owner, extension)


def show_menu(owner):
    owner.hide_theme_menu()
    popup = owner._tk.Menu(owner.root, tearoff=0, bg=owner.pal['side'], fg=owner.pal['fg'],
                         activebackground=owner.pal['sel'], activeforeground=owner.pal['fg'])
    current = owner.cur_loose or owner.cur_doc or {}
    name = current.get('name') or current.get('file') or '未保存文档'
    popup.add_command(label='当前文档：' + os.path.basename(name), state='disabled')
    for extension, (title, _) in FORMATS.items():
        popup.add_command(label='导出为 %s…' % title,
                          command=lambda ext=extension: choose_format(owner, ext))
    try:
        popup.tk_popup(owner.btn_export.winfo_rootx(), owner.btn_export.winfo_rooty()+owner.btn_export.winfo_height())
    finally:
        popup.grab_release()


def completed(owner, path, source, warnings):
    # Bind to the actual result, never the document active when a button is clicked.
    path = os.path.abspath(path)
    dialog = window(owner, '导出完成')
    status = label(owner, dialog, '文档：%s\n已导出：%s%s' %
                   (source, path, ('\n提示：' + '\n'.join(warnings)) if warnings else ''))
    actions = owner._tk.Frame(dialog, bg=owner.pal['bg'])
    actions.pack(padx=15, pady=(0, 16))
    def open_result(folder=False):
        try:
            if not os.path.isfile(path):
                raise FileNotFoundError('导出文件已移动或删除，请检查保存位置。')
            if folder:
                owner.ws.open_in_explorer(path, select=True)
            else:
                os.startfile(path)
        except OSError as exc:
            status.configure(text='无法打开：%s\n保存位置：%s' % (exc, path))
    button(owner, actions, '打开文件', open_result)
    button(owner, actions, '打开所在文件夹', lambda: open_result(True))
    button(owner, actions, '关闭', dialog.destroy)
    return reveal(owner, dialog)
