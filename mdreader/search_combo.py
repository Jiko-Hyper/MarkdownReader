"""Editable sidebar selectors with a themed, non-grabbing results popup."""
import tkinter as tk
from tkinter import ttk
from collections import Counter


class SearchableCombobox(ttk.Combobox):
    def __init__(self, master, *, palette, **options):
        self._palette = palette
        self._all_values = list(options.pop('values', ()))
        self._search_fields = []
        self._visible_indices = list(range(len(self._all_values)))
        self._committed_index = -1
        self._searching = False
        self._setting = False
        self._popup = self._list = None
        self._focus_job = None
        self._variable = tk.StringVar(master)
        options['state'] = 'normal'
        super().__init__(master, textvariable=self._variable, values=self._all_values, **options)
        self._trace = self._variable.trace_add('write', self._changed)
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<Down>', lambda event: self._move(1))
        self.bind('<Up>', lambda event: self._move(-1))
        self.bind('<Alt-Down>', lambda event: self._open())
        self.bind('<F4>', lambda event: self._open())
        self.bind('<Return>', self._accept)
        self.bind('<Escape>', self._escape)
        self.bind('<FocusIn>', self._focus_in, add='+')
        self.bind('<FocusOut>', self._focus_out, add='+')
        self.bind('<Unmap>', lambda event: self.hide_popup(), add='+')
        self.bind('<Destroy>', self._destroy, add='+')
        self._outside_binding = self.winfo_toplevel().bind('<ButtonPress-1>', self._outside, add='+')
        self._configure_binding = self.winfo_toplevel().bind('<Configure>', self._window_changed, add='+')

    def configure(self, cnf=None, **options):
        if isinstance(cnf, dict):
            options = {**cnf, **options}
            cnf = None
        if 'values' in options:
            self._all_values = list(options['values'])
            self._filter()
            options['values'] = [self._all_values[index] for index in self._visible_indices]
        if options.get('state') == 'readonly':
            options['state'] = 'normal'
        result = super().configure(cnf, **options)
        if options.get('state') == 'disabled':
            self.hide_popup()
        return result

    config = configure

    def set_search_fields(self, fields):
        self._search_fields = [str(value or '') for value in fields]
        self._filter()

    def current(self, newindex=None):
        # Callers index their complete data arrays. Filtered popup rows must
        # never change that contract or select/delete an unrelated history item.
        if newindex is None:
            return self._committed_index
        index = int(newindex)
        if not 0 <= index < len(self._all_values):
            raise tk.TclError('Index out of range')
        self._committed_index = index
        if not self._searching:
            self._write(self._all_values[index])

    def set(self, value):
        self._searching = False
        self._committed_index = next((index for index, label in enumerate(self._all_values)
                                      if label == value), -1)
        self._write(value)
        self._filter()
        self.hide_popup()

    def _write(self, value):
        self._setting = True
        try:
            super().set(value)
        finally:
            self._setting = False

    def _filter(self):
        query = self.get().strip().casefold() if self._searching else ''
        self._visible_indices = [index for index, label in enumerate(self._all_values)
                                 if query in (str(label) + ' ' + (self._search_fields[index]
                                 if index < len(self._search_fields) else '')).casefold()]
        super().configure(values=[self._all_values[index] for index in self._visible_indices])
        if self._popup is not None and self._popup.winfo_viewable():
            self._populate()

    def _changed(self, *_args):
        if self._setting:
            return
        self._searching = True
        self._filter()
        if self.winfo_ismapped() and self.focus_get() is self:
            self._open()

    def _make_popup(self):
        self._popup = tk.Toplevel(self, takefocus=False, background=self._palette()['bg'])
        self._popup.withdraw()
        self._popup.overrideredirect(True)
        self._popup.transient(self.winfo_toplevel())
        self._popup.columnconfigure(0, weight=1)
        self._popup.rowconfigure(0, weight=1)
        self._list = tk.Listbox(self._popup, exportselection=False, activestyle='none',
                               takefocus=False, bd=0, highlightthickness=1, font=self.cget('font'))
        self._list.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(self._popup, command=self._list.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self._list.configure(yscrollcommand=scrollbar.set)
        self._list.bind('<ButtonPress-1>', self._click_result)
        self.apply_theme()

    def _populate(self):
        selected = self._list.curselection()
        previous = self._list.get(selected[0]) if selected else None
        self._list.delete(0, 'end')
        labels = self._all_values
        counts = Counter(labels)
        shown = []
        for index in self._visible_indices:
            label = labels[index]
            if counts[label] > 1 and index < len(self._search_fields):
                label += ' · ' + self._search_fields[index]
            self._list.insert('end', label)
            shown.append(label)
        if not self._visible_indices:
            self._list.insert('end', '没有匹配项')
            self._list.itemconfigure(0, foreground=self._palette()['muted'])
        else:
            row = shown.index(previous) if previous in shown else (
                self._visible_indices.index(self._committed_index)
                if not self._searching and self._committed_index in self._visible_indices else 0)
            self._list.selection_set(row)
            self._list.activate(row)
        self._list.configure(height=max(1, min(8, len(self._visible_indices))))

    def _open(self):
        if str(self.cget('state')) == 'disabled':
            return 'break'
        if self._popup is None:
            self._make_popup()
        self._populate()
        self._popup.update_idletasks()
        width = max(self.winfo_width(), 180)
        height = self._popup.winfo_reqheight()
        x = min(self.winfo_rootx(), max(0, self.winfo_screenwidth() - width))
        below = self.winfo_rooty() + self.winfo_height()
        y = below if below + height < self.winfo_screenheight() else self.winfo_rooty() - height
        self._popup.geometry('%dx%d+%d+%d' % (width, height, max(0, x), max(0, y)))
        self._popup.deiconify()
        self._popup.lift()
        # Native ttk popdowns grab focus, preventing continued typing/IME.
        # This popup never grabs; input remains in the themed entry.
        self.focus_set()
        return 'break'

    def hide_popup(self):
        if self._popup is not None:
            self._popup.withdraw()

    def _press(self, event):
        if 'downarrow' in self.identify(event.x, event.y):
            if self._popup is not None and self._popup.winfo_viewable():
                self.hide_popup()
            else:
                self._open()
            return 'break'

    def _move(self, delta):
        if self._popup is None or not self._popup.winfo_viewable():
            return self._open()
        if self._visible_indices:
            selected = self._list.curselection()
            row = (int(selected[0]) + delta) % len(self._visible_indices) if selected else 0
            self._list.selection_clear(0, 'end')
            self._list.selection_set(row)
            self._list.activate(row)
            self._list.see(row)
        return 'break'

    def _accept(self, _event=None):
        if self._popup is not None and self._popup.winfo_viewable() and self._visible_indices:
            selected = self._list.curselection()
            self.choose(int(selected[0]) if selected else 0)
        return 'break'

    def choose(self, row):
        if not 0 <= row < len(self._visible_indices):
            return
        index = self._visible_indices[row]
        self._searching = False
        self._committed_index = index
        self._write(self._all_values[index])
        self._filter()
        self.hide_popup()
        self.focus_set()
        self.event_generate('<<ComboboxSelected>>')

    def _click_result(self, event):
        if self._visible_indices:
            self.choose(self._list.nearest(event.y))
        return 'break'

    def _escape(self, _event=None):
        self._searching = False
        self._write(self._all_values[self._committed_index]
                    if 0 <= self._committed_index < len(self._all_values) else '')
        self._filter()
        self.hide_popup()
        return 'break'

    def _outside(self, event):
        if event.widget is not self:
            self.hide_popup()

    def _window_changed(self, event):
        if event.widget is self.winfo_toplevel():
            self.hide_popup()

    def _focus_out(self, _event=None):
        if self._focus_job is not None:
            self.after_cancel(self._focus_job)
        def check():
            self._focus_job = None
            focus = self.focus_get()
            if focus is not self and (focus is None or self._popup is None
                                      or focus.winfo_toplevel() is not self._popup):
                self.hide_popup()
        self._focus_job = self.after_idle(check)

    def _focus_in(self, _event=None):
        if not self._searching:
            self.selection_range(0, 'end')

    def apply_theme(self):
        if self._popup is not None:
            pal = self._palette()
            self._popup.configure(background=pal['bg'])
            self._list.configure(background=pal['bg'], foreground=pal['fg'],
                                 selectbackground=pal['sel'], selectforeground=pal['fg'],
                                 highlightbackground=pal['rule'], highlightcolor=pal['accent'])
            if not self._visible_indices and self._list.size():
                self._list.itemconfigure(0, foreground=pal['muted'])

    def _destroy(self, event):
        if event.widget is not self:
            return
        if self._focus_job is not None:
            self.after_cancel(self._focus_job)
        self._variable.trace_remove('write', self._trace)
        root = self.winfo_toplevel()
        root.unbind('<ButtonPress-1>', self._outside_binding)
        root.unbind('<Configure>', self._configure_binding)
        if self._popup is not None:
            self._popup.destroy()
