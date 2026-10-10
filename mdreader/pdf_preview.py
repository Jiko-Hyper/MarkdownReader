"""View actual PDF pages before the host commits the approved artifact."""
import threading
from collections import OrderedDict
from .media_ui import run_job
from .export_ui import window, reveal, button

_PDF_LOCK = threading.Lock()


class Pages:
    """Bounded image cache; no open PDF handles or bitmaps survive a render."""
    def __init__(self, path):
        self.path = path
        self.cache = OrderedDict()
        with _PDF_LOCK:
            import pypdfium2 as pdfium
            with pdfium.PdfDocument(path) as doc:
                self.count = len(doc)
        if not self.count:
            raise ValueError('PDF 没有可预览的页面')

    def render(self, number, width):
        number = max(0, min(number, self.count-1))
        width = max(240, min(int(width), 1800))
        key = number, width
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        with _PDF_LOCK:
            import pypdfium2 as pdfium
            with pdfium.PdfDocument(self.path) as doc:
                page = doc[number]
                try:
                    w, h = page.get_size()
                    scale = min(width/w, 2400/h)
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil().convert('RGB').copy()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        self.cache[key] = image
        while len(self.cache) > 3:
            _, old = self.cache.popitem(last=False)
            old.close()
        return image

    def close(self):
        for image in self.cache.values():
            image.close()
        self.cache.clear()


class Preview:
    def __init__(self, owner, path, warnings=()):
        tk, pal = owner._tk, owner.pal
        self.owner = owner
        self.pages = run_job(owner, lambda: Pages(path))
        self.result = False
        self.number = 0
        self.zoom = 1.0
        self.busy = False
        self.closing = None
        self.closed = False
        self.dialog = window(owner, 'PDF 页面预览')
        self.dialog.protocol('WM_DELETE_WINDOW', lambda: self.finish(False))
        self.dialog.bind('<Escape>', lambda _e: self.finish(False))
        width = min(owner.root.winfo_screenwidth()-80, max(640, owner.root.winfo_width()-80))
        height = min(owner.root.winfo_screenheight()-100, max(520, owner.root.winfo_height()-60))
        self.size = width, height
        self.dialog.geometry('%dx%d' % (width, height))
        tk.Label(self.dialog, text='实际 PDF 分页 · 确认保存前不会写入目标文件',
                 bg=pal['bg'], fg=pal['fg']).pack(pady=(12, 6))
        nav = tk.Frame(self.dialog, bg=pal['bg']); nav.pack(fill='x', padx=12)
        button(owner, nav, '上一页', lambda: self.move(-1))
        button(owner, nav, '下一页', lambda: self.move(1))
        self.counter = tk.Label(nav, bg=pal['bg'], fg=pal['fg']); self.counter.pack(side='left', padx=8)
        button(owner, nav, '缩小 −', lambda: self.resize(-.25))
        button(owner, nav, '放大 ＋', lambda: self.resize(.25))
        frame = tk.Frame(self.dialog, bg=pal['bg']); frame.pack(fill='both', expand=True, padx=12, pady=8)
        self.canvas = tk.Canvas(frame, bg=pal['tree_bg'], highlightthickness=0)
        ybar = tk.Scrollbar(frame, command=self.canvas.yview)
        xbar = tk.Scrollbar(frame, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        ybar.pack(side='right', fill='y'); xbar.pack(side='bottom', fill='x')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<MouseWheel>', lambda event: self.canvas.yview_scroll(-int(event.delta/120), 'units'))
        self.dialog.bind('<Left>', lambda _e: self.move(-1))
        self.dialog.bind('<Right>', lambda _e: self.move(1))
        if warnings:
            tk.Label(self.dialog, text='提示：'+'；'.join(warnings), wraplength=width-40,
                     bg=pal['bg'], fg=pal['fg'], anchor='w', justify='left').pack(fill='x', padx=16)
        actions = tk.Frame(self.dialog, bg=pal['bg']); actions.pack(pady=(4, 12))
        button(owner, actions, '返回修改', lambda: self.finish(False))
        self.save = button(owner, actions, '确认保存 PDF', lambda: self.finish(True))
        self.dialog.update_idletasks()
        # Withdrawn Tk windows report a 1px canvas. Use the planned viewport;
        # cap the base width so 200% A4 zoom remains within the render budget.
        self.base_width = max(300, min(800, width-60))
        try:
            self.draw()
        except Exception:
            self.finish(False)
            raise

    def draw(self):
        from PIL import ImageTk
        if self.busy:
            return
        self.busy = True
        succeeded = False
        self.save.configure(state='disabled')
        try:
            image = run_job(self.owner, lambda: self.pages.render(self.number, self.base_width*self.zoom))
            self.photo = ImageTk.PhotoImage(image, master=self.dialog)
            self.canvas.delete('all')
            self.canvas.create_image(12, 12, anchor='nw', image=self.photo)
            self.canvas.configure(scrollregion=(0, 0, image.width+24, image.height+24))
            self.canvas.yview_moveto(0)
            self.counter.configure(text='%d / %d 页 · %d%%' % (self.number+1, self.pages.count, self.zoom*100))
            succeeded = True
        finally:
            self.busy = False
            self.save.configure(state='normal' if succeeded else 'disabled')
            if self.closing is not None:
                self.finish(self.closing)

    def move(self, delta):
        if self.busy:
            return
        number = max(0, min(self.pages.count-1, self.number+delta))
        if number != self.number:
            self.number = number
            self.draw_or_report()

    def resize(self, delta):
        if self.busy:
            return
        zoom = max(.5, min(2.0, self.zoom+delta))
        if zoom != self.zoom:
            self.zoom = zoom
            self.draw_or_report()

    def draw_or_report(self):
        try:
            self.draw()
        except Exception:
            if not self.closed:
                self.counter.configure(text='页面预览失败，请返回修改或重试')

    def finish(self, answer):
        if self.closed:
            return
        if self.busy:
            self.closing = answer
            return
        self.result = answer
        self.closed = True
        self.pages.close()
        self.dialog.destroy()

    def show(self):
        if self.closed:
            return self.result
        reveal(self.owner, self.dialog, size=self.size)
        self.dialog.grab_set()
        self.owner.root.wait_window(self.dialog)
        return self.result


def preview_pdf(owner, path, warnings=()):
    return Preview(owner, path, warnings).show()
