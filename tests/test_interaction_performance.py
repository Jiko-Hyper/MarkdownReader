import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from tests import test_native_tabs
from mdreader import winui


class InteractionTests(unittest.TestCase):
    setUp = test_native_tabs.NativeTabsTests.setUp
    destroy_window = staticmethod(test_native_tabs.NativeTabsTests.destroy_window)

    def prepare(self):
        w = self.win
        w.open_local_files(self.files[:1])
        w.source = ("paragraph " * 30 + "\n\n") * 100 + "| A | B |\n|---|---|\n| C | D |\n"
        w.render()
        w.root.deiconify()
        w.root.geometry("1000x650")
        w.root.update()
        return w

    def test_zoom_reuses_text_and_tables_and_preserves_reading_anchor(self):
        w = self.prepare()
        w.preview.yview_moveto(.4)
        w.root.update()
        anchor = w.preview.index("@0,0")
        content = w.preview.get("1.0", "end-1c")
        tables = w.preview.winfo_children()
        with patch.object(winui, "_parse_document_for_widget", side_effect=AssertionError("zoom reparsed")):
            w.on_zoom(SimpleNamespace(delta=120))
            w.root.update()
        self.assertEqual(content, w.preview.get("1.0", "end-1c"))
        self.assertEqual(tables, w.preview.winfo_children())
        self.assertEqual(anchor.split(".")[0], w.preview.index("@0,0").split(".")[0])

    def test_scroll_over_table_and_source_scrollbar_target_visible_document(self):
        w = self.prepare()
        table = next(c for c in w.preview.winfo_children() if hasattr(c, "_restyle_table"))
        label = table.winfo_children()[0]
        with patch.object(w.preview, "event_generate") as forward:
            callback = label.bind("<MouseWheel>")
            self.assertTrue(callback)
            winui._table_wheel(w.preview, SimpleNamespace(delta=-120))
            forward.assert_called_once_with("<MouseWheel>", delta=-120)
        w.show_source(w.source)
        w.root.update()
        command = w.doc_vscroll.cget("command")
        w.root.tk.call(command, "moveto", .5)
        w.root.update()
        self.assertGreater(w.editor.yview()[0], .2)

    def test_zoom_burst_is_coalesced_and_applies_final_size(self):
        w = self.prepare()
        w._zoom_last = time.perf_counter()
        with patch.object(w, "_apply_zoom", wraps=w._apply_zoom) as apply:
            for _ in range(5):
                w.on_zoom(SimpleNamespace(delta=120))
            self.assertEqual(apply.call_count, 0)
            done = w._tk.BooleanVar()
            w.root.after(80, lambda: done.set(True))
            w.root.wait_variable(done)
            self.assertEqual(apply.call_count, 1)
        self.assertEqual(w.preview._preview_size, w.base_size)
