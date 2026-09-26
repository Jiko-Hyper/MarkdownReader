import math
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests import test_folders


class FolderTreeTests(unittest.TestCase):
    setUp = test_folders.FolderDesktopTests.setUp
    # setUp 通过 dialog_result 拿到「选择框的回传值」，借用 setUp 时也要一并借过来
    dialog_result = test_folders.FolderDesktopTests.dialog_result
    tearDown = test_folders.FolderDesktopTests.tearDown
    destroy_window = staticmethod(test_folders.FolderDesktopTests.destroy_window)
    write = test_folders.FolderDesktopTests.write

    def open(self):
        self.write("子目录/深层/图片.png")
        self.write("archive.zip")
        self.write(".hidden")
        os.makedirs(os.path.join(self.user, "空目录"))
        self.win.open_user_folder()
        self.win.ws.folders.watch().stop()
        return self.win

    def settle(self):
        done = self.win._tk.BooleanVar()
        self.win.root.after(180, lambda: done.set(True))
        self.win.root.wait_variable(done)

    def test_all_files_empty_folders_and_nested_folders_start_collapsed(self):
        w = self.open()
        for row in ("r:子目录", "r:子目录/深层", "r:空目录", "f:archive.zip",
                    "f:.hidden", "f:子目录/深层/图片.png"):
            self.assertTrue(w.root_tree.exists(row), row)
        for row in w._root_nodes:
            if row.startswith("r:"):
                self.assertFalse(w.root_tree.item(row, "open"))
        w.root_tree.selection_set("r:子目录/深层")
        w.root_tree.focus("r:子目录/深层")
        self.assertEqual(w._selected_root_dir(), "子目录/深层")

    def test_triangle_rotates_about_center_and_nested_expansion_is_independent(self):
        w = self.open()
        w.root.deiconify()
        w.root.geometry("1200x1000")
        w.root.update()
        row = "r:子目录"
        w.root_tree.see(row)
        w.root.update()
        w.folder_arrows.draw()
        start = w.folder_arrows.canvas.coords(w.folder_arrows.canvas.find_withtag(row)[0])
        _, y, _, h = w.root_tree.bbox(row)
        w.folder_arrows.click(SimpleNamespace(y=y + h / 2 - 2))
        self.assertIn(row, w.folder_arrows.angles)
        self.assertTrue(w.root_tree.item(row, "open"))
        self.settle()
        end = w.folder_arrows.canvas.coords(w.folder_arrows.canvas.find_withtag(row)[0])
        center = lambda pts: (sum(pts[::2]) / 3, sum(pts[1::2]) / 3)
        self.assertEqual(tuple(round(x, 5) for x in center(start)), tuple(round(x, 5) for x in center(end)))
        cx, cy = center(start)
        self.assertAlmostEqual(start[0] - cx, end[1] - cy)
        self.assertAlmostEqual(end[3], end[5])  # the top side is horizontal
        lengths = [math.dist(end[i:i+2], end[(i+2)%6:(i+2)%6+2]) for i in (0, 2, 4)]
        self.assertAlmostEqual(lengths[0], lengths[1])
        self.assertAlmostEqual(lengths[1], lengths[2])
        self.assertFalse(w.root_tree.item("r:子目录/深层", "open"))
        w.folder_arrows.toggle("r:子目录/深层")
        self.settle()
        self.assertTrue(w.root_tree.bbox("f:子目录/深层/图片.png"))
        w.refresh_folder_tree(force=True)
        self.assertTrue(w.root_tree.item(row, "open"))
        w.open_user_folder()
        self.assertFalse(w.root_tree.item(row, "open"))
        self.assertFalse(w.root_tree.item("r:子目录/深层", "open"))

    def test_new_markdown_uses_selected_folder_otherwise_root(self):
        w = self.open()
        for selected, filename, expected in (("r:空目录", "空目录新建", "空目录"),
                                              ("r:子目录/深层", "嵌套新建", "子目录/深层"),
                                              ("f:子目录/深入.md", "选中文件", ""),
                                              (None, "没有选择", "")):
            w.root_tree.selection_remove(*w.root_tree.selection())
            if selected:
                w.root_tree.selection_set(selected)
            with patch("tkinter.simpledialog.askstring", return_value=filename):
                w.new_folder_doc()
            self.assertTrue(os.path.isfile(os.path.join(self.user, expected, filename + ".md")))

    def test_watcher_notices_non_markdown_files_and_empty_directories(self):
        w = self.open()
        watcher = w.ws.folders.watch()
        watcher.poll_once()
        self.write("新附件.pdf")
        self.assertIn(w.cur_root, watcher.poll_once())
        w.refresh_folder_tree()
        self.assertTrue(w.root_tree.exists("f:新附件.pdf"))
        os.makedirs(os.path.join(self.user, "新空目录"))
        self.assertIn(w.cur_root, watcher.poll_once())
        w.refresh_folder_tree()
        self.assertTrue(w.root_tree.exists("r:新空目录"))
