"""Exercise real Tk widgets with an isolated workspace, never user history."""
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from mdreader import core, winui


class NativeTabsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-tabs-")
        self.addCleanup(self.temp.cleanup)
        self.files = []
        for name in ("中文 A.md", "report B.md", "第三个.md"):
            path = Path(self.temp.name, name)
            path.write_text("# " + name + "\n\n原始内容\n", encoding="utf-8")
            self.files.append(str(path))
        self.drop = patch.object(winui.MarkdownWindow, "enable_file_drop")
        self.drop.start()
        self.addCleanup(self.drop.stop)
        self.win = winui.MarkdownWindow(os.path.join(self.temp.name, "workspace"))
        self.win.root.withdraw()
        self.addCleanup(self.destroy_window, self.win.root)

    @staticmethod
    def destroy_window(root):
        # Tk timers outlive destroyed widgets unless explicitly cancelled.
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()

    def edit(self, text):
        if self.win.mode != "source":
            self.win.toggle_mode()
        self.win._set_widget(text)
        self.win.source = text
        self.win.set_dirty(True)

    def test_open_switch_and_close_twenty_documents_releases_every_editor(self):
        """E01 验收 6：反复开关标签后，编辑控件、输入法适配项和计时器都回收。

        20 份文档覆盖了“标签数量大于屏幕上能同时显示的编辑器”的情况：每个
        标签一个编辑控件，关闭标签必须真正销毁它，否则窗口会越用越重。
        """
        w = self.win
        paths = []
        for index in range(20):
            path = Path(self.temp.name, "批量 %02d.md" % index)
            path.write_text("# 文档 %d\n\n第 %d 份内容\n" % (index, index), encoding="utf-8")
            paths.append(str(path))
        baseline_jobs = len(w.root.tk.call("after", "info"))
        canvases_before = len([c for c in w.body.winfo_children() if c.winfo_class() == "Canvas"])
        w.open_local_files(paths)
        self.assertEqual(len(w.tabs), 20)
        self.assertEqual(len(w._editors), 20, "每个标签应有自己的编辑控件")
        self.assertEqual(len(w._imes), 22, "20 个编辑控件加预览和搜索框，共用窗口钩子")
        hooks = {id(adapter.hook) for adapter in w._imes if adapter.hook is not None}
        self.assertEqual(len(hooks), 1,
                         "整窗只应安装一个原生钩子：不能为每个标签各装一个窗口子类")
        self.assertEqual(w.ime_search.hook.targets.count(w.ime_search), 1,
                         "同一个适配项不应被重复注册到钩子")
        for tab in w.tabs:
            w.activate_tab(tab)
            w.toggle_mode()                 # 源码
            w.toggle_mode()                 # 预览
        with patch.object(w, "ask_save_changes", return_value=False):
            for tab in list(w.tabs):
                w.close_tab(tab)
        self.assertEqual(w.tabs, [])
        self.assertEqual(w._editors, {}, "关闭标签后不应残留编辑控件")
        self.assertEqual(len(w._imes), 2, "只应剩下预览和搜索框的组合适配项")
        self.assertEqual(len([c for c in w.body.winfo_children() if c.winfo_class() == "Canvas"]),
                         canvases_before, "关闭标签后不应残留组合浮层画布")
        self.assertIsNone(w.editor)
        self.assertTrue(w.preview.winfo_exists())
        self.assertLessEqual(len(w.root.tk.call("after", "info")), baseline_jobs + 2,
                             "关闭标签后不应残留计时器")

    def test_closing_a_tab_mid_composition_releases_editor_and_adapter(self):
        """组合进行中关闭标签：控件、适配项和浮层都要干净，别的标签还能用。"""
        w = self.win
        w.open_local_files(self.files[:2])
        first, second = w.tabs
        w.activate_tab(first)
        w.show_source()
        adapter, editor = w.ime, w.editor
        adapter.surface.show("zhong", 5)        # 一次未确认的组合
        w.root.update_idletasks()
        self.assertEqual(adapter.surface.text, "zhong")
        with patch.object(w, "ask_save_changes", return_value=False):
            w.close_tab(first)
        w.root.update()
        self.assertFalse(editor.winfo_exists(), "关闭标签必须销毁编辑控件")
        self.assertNotIn(adapter, w._imes)
        self.assertEqual(adapter.surface.text, "", "关闭标签应清掉该控件的组合浮层")

        w.activate_tab(second)
        w.show_source()
        w.editor.insert("end", "还能输入\n")
        self.assertIn("还能输入", w.get_text(), "其余标签必须仍可编辑")
        hook = w.ime_search.hook
        stale = [t for t in hook.targets
                 if getattr(t, "editor", None) is not None and not t.editor.winfo_exists()]
        self.assertEqual(stale, [], "共享钩子里不得残留已销毁的控件")

    def test_destroying_the_window_mid_composition_stops_the_shared_timer(self):
        """窗口关闭时共享轮询必须停下，且不因销毁中的编辑控件报错。"""
        other = winui.MarkdownWindow(os.path.join(self.temp.name, "closing-workspace"))
        try:
            other.root.withdraw()
            other.open_local_files(self.files[:1])
            other.show_source()
            adapter = other.ime
            adapter.surface.show("ni'hao", 6)
            hook = adapter.hook
            self.assertIsNotNone(hook, "没有窗口钩子时该用例无意义")
            other.root.update()
            other.root.destroy()
            self.assertIsNone(hook._loop_job, "窗口销毁后共享轮询必须停止")
            self.assertEqual(adapter.surface.text, "", "窗口销毁应清掉组合浮层")
        finally:
            try:
                other.root.destroy()
            except Exception:
                pass

    def test_each_tab_keeps_its_own_scroll_position(self):
        """E01：滚动位置和光标、选区一样，属于每份文档自己的视图状态。"""
        w = self.win
        long_text = "# 长文\n\n" + "\n".join("第 %d 行内容" % index for index in range(400)) + "\n"
        first = Path(self.temp.name, "长文 A.md")
        second = Path(self.temp.name, "长文 B.md")
        first.write_text(long_text, encoding="utf-8")
        second.write_text(long_text, encoding="utf-8")
        w.open_local_files([str(first), str(second)])
        w.root.geometry("900x520+40+40")
        w.root.deiconify()
        w.root.update()
        first_tab, second_tab = w.tabs
        for tab in (first_tab, second_tab):
            w.activate_tab(tab)
            w.show_source()

        w.activate_tab(first_tab)
        w.editor.yview_moveto(0.45)
        w.root.update_idletasks()
        w.root.update()
        position_a = w.editor.yview()[0]
        self.assertGreater(position_a, 0.2, "前提：长文确实能滚动")
        w.activate_tab(second_tab)
        w.editor.yview_moveto(0.10)
        w.root.update_idletasks()
        w.root.update()
        position_b = w.editor.yview()[0]
        self.assertLess(position_b, position_a)

        w.activate_tab(first_tab)
        w.root.update_idletasks()
        w.root.update()
        self.assertAlmostEqual(w.editor.yview()[0], position_a, places=2,
                               msg="切回 A 必须还原 A 的源码滚动位置")
        w.activate_tab(second_tab)
        w.root.update_idletasks()
        w.root.update()
        self.assertAlmostEqual(w.editor.yview()[0], position_b, places=2,
                               msg="切回 B 必须还原 B 的源码滚动位置")

    def test_each_tab_keeps_a_nearby_reading_position_in_preview(self):
        """预览模式下切标签应回到附近的段落。

        预览是**共用的一份**控件（E01 规格：预览单独展示），切到另一份文档
        必须重新排版，所以这里按 `开发框架.md` 5.4 的“合理降级到附近位置”判定，
        不承诺精确到旧像素。
        """
        w = self.win
        long_text = "# 长文\n\n" + "\n".join("第 %d 行内容" % index for index in range(400)) + "\n"
        first = Path(self.temp.name, "预览 A.md")
        second = Path(self.temp.name, "预览 B.md")
        first.write_text(long_text, encoding="utf-8")
        second.write_text(long_text, encoding="utf-8")
        w.open_local_files([str(first), str(second)])
        w.root.geometry("900x520+40+40")
        w.root.deiconify()
        w.root.update()
        first_tab, second_tab = w.tabs

        w.activate_tab(second_tab)              # 让预览先装 B
        w.preview.yview_moveto(0.05)
        w.root.update_idletasks()
        w.root.update()
        w.activate_tab(first_tab)               # 再切到 A 并滚到中段
        w.preview.yview_moveto(0.5)
        w.root.update_idletasks()
        w.root.update()
        top_line = w.preview.index("@0,0")
        w.activate_tab(second_tab)
        w.root.update_idletasks()
        w.root.update()
        w.activate_tab(first_tab)
        w.root.update_idletasks()
        w.root.update()
        back_line = w.preview.index("@0,0")
        drift = abs(int(back_line.split(".")[0]) - int(top_line.split(".")[0]))
        self.assertLessEqual(drift, 10, "回到 A 应落在附近段落（偏差 %d 行）" % drift)
        self.assertIn("长文", w.preview.get("1.0", "2.0"), "预览内容必须还是 A")

    def test_every_tab_editor_is_composition_ready_without_manual_setup(self):
        """0.2.8 白框回归：显示源码时编辑控件必须已经可组合。

        `InlineIME` 默认不可编辑，而**真实机制在 C 桥里**：`native_bridge.c` 只在
        `s->active` 为真时才剥掉 `ISC_SHOWUICOMPOSITIONWINDOW`，`s->active` 由
        Python 每帧调用 `native.active(wanted)` 驱动，`wanted` 又取决于
        `owner.editable`。因此这里不做任何手工设置，只走正常的打开 / 切换流程，
        然后同时断言两件事：标志位正确，以及**真实原生桥**被要求采集/停止采集。
        （`ime.py::handle_message` 自 C 桥接管后已不在生产路径上，见下面的说明，
        所以不能拿它当证据。）
        """
        w = self.win
        w.open_local_files(self.files[:2])
        for tab in w.tabs:
            w.activate_tab(tab)
            w.show_source()
            self.assertIs(w.ime.editor, w.editor)
            self.assertTrue(w.ime.editable, "显示源码时该控件必须已可组合")

        bridge = w.ime_search.hook.native
        calls = []
        original = bridge.active
        bridge.active = lambda flag: (calls.append(bool(flag)), original(flag))[1]
        try:
            # 无头窗口拿不到真实焦点，用生产代码里 <FocusIn> 的处理函数模拟
            # （焦点不是被测前提，可组合性才是）。
            w.ime._focus_in()
            w.ime_search.hook.poll()
            self.assertTrue(calls and calls[-1], "编辑控件可见且可组合时必须保持原生采集")
            self.assertTrue(w.ime.editable)

            w.toggle_mode()                          # 切到只读预览
            self.assertFalse(w.ime.editable, "预览控件不得参与组合")
            w.ime_search.hook.poll()
            self.assertFalse(calls[-1], "只读预览时应停止原生采集")

            w.toggle_mode()                          # 回到源码
            self.assertTrue(w.ime.editable, "回到源码必须重新可组合")
            w.ime._focus_in()
            w.ime_search.hook.poll()
            self.assertTrue(calls[-1], "回到源码后必须重新保持原生采集")
        finally:
            bridge.active = original

    def test_the_search_field_legitimately_owns_the_composition(self):
        """侧栏搜索框是归属权的唯一例外：用户点它时组合归它（E07 修好也要保留）。"""
        w = self.win
        w.open_local_files(self.files[:1])
        w.show_source()
        hook = w.ime_search.hook
        w.ime._focus_out()
        self.win.ime_search._focus_in()
        self.assertIs(hook.owner(), w.ime_search)
        self.assertTrue(w.ime_search.editable)

    def test_composition_is_owned_by_the_widget_that_is_on_screen(self):
        """切走控件后它不得再持有组合归属权（0.2.8 白框回归的一般形式）。

        那次回归是“编辑控件没有被设为可组合”；这里锁住同一类问题的另一半：
        归属者必须是屏幕上那个控件的适配项，切走之后不能还是它——否则拼音会被
        画到看不见的地方，或者系统画回白框。无头窗口拿不到真实焦点，所以用生产
        代码里 `<FocusIn>` 的处理函数显式驱动焦点（这是模拟外部条件，不是代设
        被测前提：被测的是“切走后是否清理”）。

        侧栏搜索框是唯一例外：用户明确把焦点放在那里时，组合归它。收起侧栏后
        焦点仍留在隐藏的搜索框上，是 0.2.7 起既有的**独立缺陷**（见
        `docs/NEXT_ITERATION.md` 候选 E07），不在本用例范围内。
        """
        w = self.win
        w.open_local_files(self.files[:2])
        hook = w.ime_search.hook
        self.assertIsNotNone(hook, "没有窗口钩子时该用例无意义")

        w.show_source()
        first = w.ime
        first._focus_in()
        self.assertIs(hook.owner(), first, "屏幕上显示编辑控件时，归属者应是它的适配项")

        w.toggle_mode()                       # 切到只读预览
        self.assertFalse(first.focused, "切走的编辑控件不得继续持有焦点")
        self.assertIsNot(hook.owner(), first, "组合不得再归给已经切走的控件")

        w.toggle_mode()                       # 回源码：又是它
        w.ime._focus_in()
        self.assertIs(hook.owner(), w.ime)

        for index, tab in enumerate(w.tabs):
            w.activate_tab(tab)
            w.show_source()
            w.ime._focus_in()
            self.assertIs(w.ime.editor, w.text, "切换标签 %d 后屏幕与编辑控件应一致" % index)
            self.assertIs(hook.owner(), w.ime, "切换标签 %d 后归属者应是当前标签的控件" % index)

    def test_hidden_editors_are_restyled_only_when_they_come_back(self):
        """换主题/缩放不得重排隐藏的编辑控件，但回到标签时必须补上。

        E01 让每个标签各持一个编辑控件后，若缩放与换主题遍历全部控件，20 个
        标签时一次 Ctrl+滚轮会从 16 ms 涨到 200 ms（实测）。这里锁住“惰性重排”：
        隐藏控件先保持旧样式，切回来时由 `_show` 补新样式。
        """
        w = self.win
        w.open_local_files(self.files)
        first, second = w.tabs[0], w.tabs[1]
        for tab in (first, second):
            w.activate_tab(tab)
            w.show_source()
        w.activate_tab(first)
        first_editor, second_editor = w.editor_for(first), w.editor_for(second)
        self.assertIsNot(first_editor, second_editor)
        self.assertFalse(second_editor.winfo_ismapped(), "前提：第二个控件此刻是隐藏的")

        w.set_theme("dark")
        self.assertEqual(first_editor.tag_cget("p", "foreground"), winui.DARK["fg"])
        self.assertNotEqual(second_editor.tag_cget("p", "foreground"), winui.DARK["fg"],
                            "换主题不应重排隐藏标签的控件")
        w.activate_tab(second)
        self.assertEqual(second_editor.tag_cget("p", "foreground"), winui.DARK["fg"],
                         "回到标签时必须补上新主题")

        w.activate_tab(first)
        w.on_zoom(type("E", (), {"delta": 120})())
        self.assertEqual(first_editor._preview_size, w.base_size)
        self.assertNotEqual(second_editor._preview_size, w.base_size,
                            "缩放不应重排隐藏标签的控件")
        w.activate_tab(second)
        self.assertEqual(second_editor._preview_size, w.base_size,
                         "回到标签时必须补上新字号")

    def test_switch_duplicate_and_close_preserve_unsaved_buffers(self):
        w = self.win
        w.open_local_files(self.files)
        self.assertEqual(len(w.tabs), 3)
        first, second, third = w.tabs
        w.activate_tab(first)
        self.edit("未保存 A")
        w.activate_tab(second)
        self.edit("未保存 B")
        w.open_local_files([self.files[0]])
        self.assertEqual(len(w.tabs), 3)
        self.assertEqual(w.get_text(), "未保存 A")
        self.assertTrue(w.dirty)
        with patch.object(w, "ask_save_changes", return_value=None):
            w.close_tab(second)
        self.assertEqual(len(w.tabs), 3)
        self.assertIs(w.active_tab, first)
        with patch.object(w, "ask_save_changes", return_value=True):
            w.close_tab(second)
        self.assertEqual(Path(self.files[1]).read_text(encoding="utf-8"), "未保存 B")
        self.assertEqual(w.get_text(), "未保存 A")
        w.close_tab(third)
        self.assertEqual(len(w.loose.list_recent()), 3)
        with patch.object(w, "ask_save_changes", return_value=False):
            w.close_tab(first)
        self.assertEqual(len(w.tabs), 0)
        self.assertIsNone(w.cur_loose)
        self.assertIn("原始内容", Path(self.files[0]).read_text(encoding="utf-8"))

    def test_filtered_forget_keeps_open_dirty_document_and_disk_file(self):
        w = self.win
        w.open_local_files(self.files)
        w.activate_tab(w.tabs[0])
        self.edit("unsaved")
        w.recent_query.set("中文 a")
        self.assertEqual(len(w.visible_recent), 1)
        w.recent_list.selection_set(0)
        w.forget_selected_recent()
        self.assertEqual(len(w.visible_recent), 0)
        self.assertEqual(len(w.loose.list_recent()), 2)
        self.assertEqual(len(w.tabs), 3)
        self.assertTrue(w.dirty)
        self.assertEqual(w.get_text(), "unsaved")
        self.assertTrue(Path(self.files[0]).exists())
        w.recent_query.set("REPORT")
        self.assertEqual(w.visible_recent[0]["path"], self.files[1])
        w.recent_query.set(self.temp.name)
        self.assertEqual(len(w.visible_recent), 2)
        w.recent_query.set("没有匹配")
        self.assertEqual(w.btn_forget_recent["state"], "disabled")

    def test_failed_save_cannot_close_dirty_tab(self):
        w = self.win
        w.open_local_files(self.files[:1])
        self.edit("must survive")
        with patch.object(w, "ask_save_changes", return_value=True), patch.object(w.loose, "save", side_effect=OSError("disk full")):
            w.close_active_tab()
        self.assertEqual(len(w.tabs), 1)
        self.assertTrue(w.dirty)
        self.assertEqual(w.get_text(), "must survive")

    def test_exit_checks_background_dirty_tabs_and_cancel_keeps_window(self):
        w = self.win
        w.open_local_files(self.files[:1])
        self.edit("pending")
        w.open_local_files(self.files[1:])
        self.assertFalse(w.dirty)
        with patch.object(w, "ask_save_changes", return_value=None) as prompt:
            w.on_close()
        prompt.assert_called_once()
        self.assertTrue(w.root.winfo_exists())
        self.assertEqual(w.get_text(), "pending")

    def test_table_widgets_and_block_breaks(self):
        w = self.win
        w.open_local_files(self.files[:1])
        w.source = "# 标题\n\n- 第一项\n- 第二项\n\n| 组件 | 接口 |\n|---|---|\n| 摄像头 | USB |\n"
        w.render()
        self.assertEqual(len(w.text.window_names()), 1)
        text = w.text.get("1.0", "end")
        self.assertIn("标题\n", text)
        self.assertIn("第一项\n", text)
        w.render()
        self.assertEqual(len(w.text.winfo_children()), 1)
        w.toggle_mode()
        self.assertEqual(len(w.text.winfo_children()), 0)

    def test_draft_save_updates_existing_tab(self):
        w = self.win
        w.new_loose_draft()
        self.edit("new content")
        path = os.path.join(self.temp.name, "saved.md")
        with patch.object(w, "_ask_save_path", return_value=path):
            self.assertTrue(w.save_doc())
        self.assertEqual(len(w.tabs), 1)
        self.assertEqual(w.active_tab["key"], w._tab_key(loose=w.cur_loose))
        w.open_local_files([path])
        self.assertEqual(len(w.tabs), 1)
        self.assertEqual(w.get_text(), "new content")

    def test_plus_creates_independent_unsaved_untitled_tabs(self):
        w = self.win
        self.assertEqual(w.btn_add_tab["text"], "＋")
        w.btn_add_tab.invoke()
        first = w.active_tab
        self.assertEqual(winui.tab_title(first), "#Untitled")
        self.assertEqual(w.mode, "source")
        self.assertIn("未保存", w.lbl_state["text"])
        self.edit("first draft")
        w.btn_add_tab.invoke()
        self.assertEqual(len(w.tabs), 2)
        self.assertNotEqual(first["key"], w.active_tab["key"])
        self.assertEqual(w.get_text(), "")
        self.assertEqual(winui.tab_title(w.active_tab), "#Untitled")
        w.activate_tab(first)
        self.assertEqual(w.get_text(), "first draft")
        self.assertEqual(w.loose.list_recent(), [])

    def test_blank_draft_close_prompts_and_cancelled_save_keeps_tab(self):
        w = self.win
        w.new_loose_draft()
        self.assertFalse(w.dirty)
        with patch.object(w, "ask_save_changes", return_value=None) as prompt:
            w.close_active_tab()
        prompt.assert_called_once()
        self.assertEqual(len(w.tabs), 1)
        with patch.object(w, "ask_save_changes", return_value=True), patch.object(w, "_ask_save_path", return_value=""):
            w.close_active_tab()
        self.assertEqual(len(w.tabs), 1)
        self.assertEqual(winui.tab_title(w.active_tab), "#Untitled")
        with patch.object(w, "ask_save_changes", return_value=False):
            w.close_active_tab()
        self.assertEqual(w.tabs, [])
        self.assertEqual(w.btn_add_tab.winfo_manager(), "pack")

    def test_save_clears_hash_even_for_blank_draft_and_edit_restores_it(self):
        w = self.win
        w.new_loose_draft()
        path = os.path.join(self.temp.name, "Untitled.md")
        with patch.object(w, "_ask_save_path", return_value=path) as save_path:
            self.assertTrue(w.save_doc())
        save_path.assert_called_once_with("Untitled.md")
        self.assertEqual(winui.tab_title(w.active_tab), "Untitled.md")
        self.assertEqual(w.active_tab["widget"].winfo_children()[0]["text"], "Untitled.md")
        self.assertEqual(w.lbl_state["text"], "已保存")
        self.edit("changed")
        self.assertEqual(winui.tab_title(w.active_tab), "#Untitled.md")
        self.assertTrue(w.save_doc())
        self.assertEqual(winui.tab_title(w.active_tab), "Untitled.md")
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "changed")

    def test_control_s_saves_typed_content_and_control_n_creates_tab(self):
        w = self.win
        w.root.deiconify()
        w.root.update()
        w.root.focus_force()
        w.root.event_generate("<Control-n>")
        w.root.update()
        self.assertEqual(len(w.tabs), 1)
        self.assertEqual(w.mode, "source")
        w.text.insert("1.0", "键盘保存内容")
        w.root.update()
        self.assertEqual(winui.tab_title(w.active_tab), "#Untitled")
        path = os.path.join(self.temp.name, "keyboard.md")
        with patch.object(w, "_ask_save_path", return_value=path):
            w.text.event_generate("<Control-s>")
            w.root.update()
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "键盘保存内容")
        self.assertEqual(winui.tab_title(w.active_tab), "keyboard.md")

    def test_cancel_exit_keeps_previously_declined_changes_unsaved(self):
        w = self.win
        w.open_local_files(self.files[:1])
        self.edit("first pending")
        first = w.active_tab
        w.new_loose_draft()
        self.edit("second pending")
        with patch.object(w, "ask_save_changes", side_effect=[False, None]):
            w.on_close()
        self.assertTrue(w.root.winfo_exists())
        self.assertTrue(first["dirty"])
        self.assertTrue(w.dirty)
        w.activate_tab(first)
        self.assertEqual(w.get_text(), "first pending")

    def test_save_prompt_centered_with_colored_yes_and_all_answers(self):
        w = self.win
        w.root.geometry("1000x700+100+100")
        w.root.deiconify()
        w.root.update()
        for theme, label, result in (("light", "是", True), ("dark", "否", False), ("eye", "取消", None)):
            with self.subTest(theme=theme):
                prompt = winui.SavePrompt(w.root, "Untitled", winui.THEMES[theme], w.ui_scale)
                self.assertEqual(prompt.buttons["是"]["background"], winui.THEMES[theme]["accent"])
                self.assertNotEqual(prompt.buttons["是"]["background"], prompt.buttons["否"]["background"])
                prompt.window.deiconify()
                prompt.window.update()
                center_x = prompt.window.winfo_rootx() + prompt.window.winfo_width() / 2
                center_y = prompt.window.winfo_rooty() + prompt.window.winfo_height() / 2
                self.assertAlmostEqual(center_x, w.root.winfo_rootx() + w.root.winfo_width() / 2, delta=20)
                self.assertAlmostEqual(center_y, w.root.winfo_rooty() + w.root.winfo_height() / 2, delta=55)
                self.assertTrue(prompt.window.bind("<Escape>"))
                # show() waits for visibility; hide before exercising its modal loop.
                prompt.window.withdraw()
                w.root.after(100, prompt.buttons[label].invoke)
                self.assertIs(prompt.show(), result)
                self.assertIsNone(w.root.grab_current())

    def test_theme_dropdown_opens_on_hover_and_click_and_closes_after_choice(self):
        w = self.win
        self.assertEqual(w.theme_popup.winfo_manager(), "")
        self.assertTrue(w.btn_theme.bind("<Enter>"))
        w.root.deiconify()
        w.root.update()
        w.btn_theme.event_generate("<Enter>")
        w.root.update()
        self.assertTrue(w.theme_popup.winfo_ismapped())
        self.assertEqual(w.theme_popup.winfo_rooty(), w.btn_theme.winfo_rooty() + w.btn_theme.winfo_height())
        w.hide_theme_menu()
        w.btn_theme.invoke()
        self.assertEqual(w.theme_popup.winfo_manager(), "place")
        w.theme_buttons["eye"].invoke()
        self.assertEqual(w.theme, "eye")
        self.assertEqual(w.theme_popup.winfo_manager(), "")
        w.show_theme_menu()
        from types import SimpleNamespace
        w.dismiss_theme_on_click(SimpleNamespace(widget=w.text))
        self.assertEqual(w.theme_popup.winfo_manager(), "")

    def test_refresh_reads_disk_and_preserves_other_tabs(self):
        w = self.win
        w.open_local_files(self.files)
        w.toggle_mode()
        Path(self.files[-1]).write_text("磁盘新内容", encoding="utf-8")
        w.reload_doc()
        self.assertEqual(w.get_text(), "磁盘新内容")
        self.assertFalse(w.dirty)
        self.assertEqual(len(w.tabs), 3)

    def test_project_and_loose_tabs_switch_and_save_to_correct_file(self):
        w = self.win
        project = w.ws.create_project("Project")
        w.refresh_projects()
        project_tab = w.active_tab
        original_id = w.cur_doc["id"]
        self.edit("project edits")
        w.open_local_files(self.files[:1])
        self.edit("loose edits")
        w.activate_tab(project_tab)
        self.assertEqual(w.get_text(), "project edits")
        self.assertTrue(w.save_doc())
        self.assertEqual(w.ws.read_doc(w.ws.require_project(project["id"]), original_id), "project edits")
        w.activate_tab(w.tabs[-1])
        self.assertEqual(w.get_text(), "loose edits")

    def test_windows_dpi_awareness_enabled_before_window(self):
        if os.name != "nt":
            self.skipTest("Windows only")
        import ctypes
        awareness = ctypes.c_int()
        result = ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
        self.assertEqual(result, 0)
        self.assertEqual(awareness.value, 2)

    def test_widget_sizes_follow_the_display_scale(self):
        """常见显示缩放：尺寸必须都按同一比例算，而不是各写各的像素值。"""
        w = self.win
        self.assertGreaterEqual(w.ui_scale, 1.0)
        self.assertEqual(int(w.side["width"]), w.px(310))
        self.assertEqual(int(w.style.lookup("Treeview", "rowheight")), w.px(28))
        self.assertEqual(int(w.tab_canvas["height"]), w.px(42))
        self.assertEqual(int(w.root.minsize()[0]), w.px(900))

    def test_theme_covers_nested_controls_and_keeps_empty_state(self):
        w = self.win
        def visit(widget):
            yield widget
            for child in widget.winfo_children():
                yield from visit(child)
        for theme in ("dark", "eye", "light", "dark"):
            w.set_theme(theme)
            pal = w.pal
            allowed = set(pal.values())
            for widget in visit(w.root):
                if widget.winfo_class() in ("Frame", "Label", "Button", "Canvas", "Listbox", "Text"):
                    self.assertIn(str(widget.cget("background")), allowed,
                                  "%s has an unthemed background in %s" % (widget, theme))
            self.assertEqual(w.style.lookup("TEntry", "fieldbackground"), pal["bg"])
            self.assertEqual(w.style.lookup("TCombobox", "fieldbackground", ("readonly",)), pal["bg"])
            self.assertEqual(w.style.lookup("Horizontal.TScrollbar", "troughcolor"), pal["side"])
            self.assertEqual(w.style.lookup("Vertical.TScrollbar", "background"), pal["thumb"])
            self.assertEqual(w.btn_forget_recent["state"], "disabled")
            self.assertEqual(w.empty.winfo_manager(), "place")

    def test_theme_switch_preserves_edits_and_persists_for_next_launch(self):
        w = self.win
        w.open_local_files(self.files[:1])
        self.edit("unsaved changes\nsecond line")
        w.text.mark_set("insert", "2.3")
        w.text.tag_add("sel", "1.0", "1.7")
        for theme in ("dark", "eye"):
            w.set_theme(theme)
            self.assertEqual(w.mode, "source")
            self.assertTrue(w.dirty)
            self.assertEqual(w.get_text(), "unsaved changes\nsecond line")
            self.assertEqual(w.text.index("insert"), "2.3")
            self.assertEqual(tuple(map(str, w.text.tag_ranges("sel"))), ("1.0", "1.7"))
        self.assertEqual(winui.read_ui_preferences(w.ws.root)["theme"], "eye")
        reopened = winui.MarkdownWindow(w.ws.root)
        try:
            reopened.root.withdraw()
            self.assertEqual(reopened.theme, "eye")
            self.assertEqual(reopened.text["background"], winui.EYE["bg"])
        finally:
            self.destroy_window(reopened.root)

    def test_theme_changed_in_the_browser_view_reaches_the_window(self):
        """The page and the desktop window share one theme in the workspace."""
        w = self.win
        w.set_theme("light")
        self.assertEqual(core.read_ui_settings(w.ws.root)["theme"], "light")
        core.write_ui_settings(w.ws.root, "eye")     # exactly what the page does
        w.watch_shared_theme()                       # the check the timer runs
        self.assertEqual(w.theme, "eye")
        self.assertEqual(w.text["background"], winui.EYE["bg"])
        self.assertEqual(w.pal, winui.EYE)
        # A theme this window switched itself must not look like an external edit.
        w.set_theme("dark")
        w.watch_shared_theme()
        self.assertEqual(w.theme, "dark")
        self.assertEqual(core.read_ui_settings(w.ws.root)["theme"], "dark")

    def test_more_button_lists_the_secondary_actions(self):
        """「⋯ 更多」 is the place new secondary features get added."""
        w = self.win
        self.assertIn("更多", w.btn_more.cget("text"))
        recorded = []
        cascades = []

        class FakeMenu:
            def __init__(self, *args, **kwargs):
                pass

            def add_command(self, label=None, command=None):
                recorded.append((label, command))

            def add_cascade(self, label=None, menu=None):
                cascades.append(label)

            def add_separator(self):
                pass

            def tk_popup(self, *args):
                pass

            def grab_release(self):
                pass

        with patch.object(w._tk, "Menu", FakeMenu):
            w.show_more_menu()
        labels = [label for label, _ in recorded]
        # The menu is the overflow for secondary actions, so it may grow; the
        # entries the app promises must stay reachable from it.
        self.assertGreaterEqual(len(labels), 3)
        self.assertTrue(any("关于" in label for label in labels), labels)
        self.assertTrue(any("工作区" in label for label in labels), labels)
        self.assertIn("快捷键说明", labels)
        self.assertTrue(any("插件管理" in label for label in labels), labels)
        self.assertTrue(any("插入图片" in label for label in labels), labels)
        for _, command in recorded:
            self.assertTrue(callable(command), "菜单条目无法点击")

    def test_more_menu_actions_are_wired(self):
        w = self.win
        with patch.object(w, "show_about") as about:
            about.return_value = None
            w.show_about()
            about.assert_called_once_with()
        with patch.object(w.ws, "open_in_explorer") as opened:
            w.open_workspace_folder()
            opened.assert_called_once_with(w.ws.root, select=False)

    def test_info_dialog_shows_heading_rows_and_close_button(self):
        w = self.win
        dialog = winui.InfoDialog(w.root, "快捷键说明", "常用快捷键",
                                  [list(item) for item in winui.SHORTCUTS], w.pal, w.ui_scale)
        try:
            dialog.window.update_idletasks()
            self.assertTrue(dialog.window.winfo_exists())
            self.assertEqual(dialog.button.cget("text"), "确定")
            self.assertEqual(dialog.window.title(), "快捷键说明")
        finally:
            dialog.finish()

    def test_shortcut_table_documents_the_real_bindings(self):
        bound = "\n".join("%s %s" % pair for pair in winui.SHORTCUTS)
        for key in ("Ctrl+S", "Ctrl+O", "Ctrl+N", "Ctrl+W", "Ctrl+Tab", "Ctrl+E", "Ctrl+B", "F5"):
            self.assertIn(key, bound, "快捷键说明缺少 %s" % key)

