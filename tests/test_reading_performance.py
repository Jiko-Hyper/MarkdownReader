"""Lock the cost model of reading interactions: zoom and wheel scrolling.

The two behaviours here were user-visible stutter, not slow markup: a Ctrl+wheel
zoom re-bound a Tcl command for every link in the document on every tick, and the
wheel scrolled once per message.  Both are invisible in a functional test — the
document still ends up correct — so the tests below assert the *work done*, and
the integration test asserts the resulting behaviour rather than a wall clock.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mdreader import winui


class LinkTagBudgetTests(unittest.TestCase):
    """Restyling must not walk the document's link set again."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-perf-")
        self.addCleanup(self.temp.cleanup)
        self.drop = patch.object(winui.MarkdownWindow, "enable_file_drop")
        self.drop.start()
        self.addCleanup(self.drop.stop)
        self.win = winui.MarkdownWindow(os.path.join(self.temp.name, "workspace"))
        self.win.root.withdraw()
        self.addCleanup(self.destroy_window, self.win.root)

    @staticmethod
    def destroy_window(root):
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()

    def long_document(self, links=200, repeats=4):
        body = "".join("[%s](https://example.com/%d)" % ("链接 %d" % index, index) + "\n\n"
                       for index in range(links))
        path = Path(self.temp.name, "长文.md")
        path.write_text("# 长文\n\n" + body * repeats, encoding="utf-8")
        return str(path)

    def test_one_tag_per_distinct_target_not_per_occurrence(self):
        """同一个目标重复出现只应建立一个标签。

        每个链接标签要带三条 Tcl 命令绑定（点击、进入、离开），按“出现次数”
        建标签就是让一篇长文的标签数随正文里的链接数量线性增长。
        """
        path = self.long_document(links=3, repeats=5)
        self.win.open_local_files([path])
        links = self.win.preview._links
        self.assertEqual(len(links), 3, "三个不同的目标应有且只有三个链接标签")
        self.assertEqual(sorted(links.values()),
                         ["https://example.com/0", "https://example.com/1",
                          "https://example.com/2"])
        tags = [tag for tag in self.win.preview.tag_names() if tag.startswith("__link_")]
        self.assertEqual(len(tags), 3, "重复出现的链接不得各自建标签")

    def test_zoom_does_not_rebind_every_link(self):
        """缩放一次只应重排屏幕上那一个控件，且不再遍历链接标签。"""
        self.win.open_local_files([self.long_document(links=200)])
        editor = self.win.editor_for(self.win.tabs[0])
        binds = [0]

        def counted(*args, **kwargs):
            binds[0] += 1
            return ""

        with patch.object(type(editor), "tag_bind", counted):
            self.win.on_zoom(type("E", (), {"delta": 120})())
            delegated = binds[0]
            self.win.on_zoom(type("E", (), {"delta": 120})())
        self.assertLess(delegated, 20,
                        "一次缩放不该按链接数（此处 200 条）重绑标签，实测 %d 次" % delegated)
        self.assertLess(binds[0], 40, "第二次缩放不该再重绑已绑过的标签")

    def test_theme_repaint_touches_each_link_once(self):
        """换主题需要重算链接颜色，但每条链接也只应处理一次。"""
        self.win.open_local_files([self.long_document(links=200)])
        editor = self.win.editor_for(self.win.tabs[0])
        styled = []
        real = winui._style_link

        def counting(widget, tag, pal):
            styled.append(tag)
            return real(widget, tag, pal)

        with patch.object(winui, "_style_link", counting):
            self.win._invalidate_styles()
            self.win.activate_tab(self.win.tabs[0])
        self.assertEqual(len(styled), len(set(styled)), "同一个链接标签被重复处理")


class WheelPolicyTests(unittest.TestCase):
    """A wheel burst must become one scroll, without losing any motion."""

    def units(self, delta):
        return winui.ScrollCoalescer.units(None, delta)

    def test_wheel_units_match_tk_builtin_binding(self):
        """与 Tk 自带 ``yview scroll [expr {...}] pixels`` 的位移一致。

        Tcl 把 ``expr {-120/3}`` 当浮点除再取整（向零截断），所以这里也必须
        截断：四舍五入会让向下一格变成 41 像素，而 Tk 默认绑定是 40，滚轮手感
        会与源码模式不一致。
        """
        for delta in (120, -120, 240, -240, 60, -60, 480, -480, 1, -1, 360):
            pixels = int((-delta / 3.0) if delta >= 0 else ((2 - delta) / 3.0))
            self.assertEqual(self.units(delta), pixels, "delta=%d" % delta)
        self.assertEqual(self.units(120), -40)
        self.assertEqual(self.units(-120), 40)

    def test_bursts_accumulate_and_reset(self):
        """一串滚轮消息合并成一次位移，且方向相反的滚动会相互抵消。"""
        self.assertEqual(self.units(120 * 5), self.units(600),
                         "五格滚轮必须按累计量位移，而不是五格各自位移")
        self.assertLessEqual(abs(self.units(120 * 5)), abs(self.units(120)) * 5,
                             "合并后不得放大位移")
        self.assertEqual(self.units(120 + -120), 0, "来回各一格不应产生位移")
        self.assertEqual(self.units(0), 0)


class WheelScrollingBehaviourTests(unittest.TestCase):
    """The coalescer is inert until it is applied; applied once, it still scrolls."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-wheel-")
        self.addCleanup(self.temp.cleanup)
        self.drop = patch.object(winui.MarkdownWindow, "enable_file_drop")
        self.drop.start()
        self.addCleanup(self.drop.stop)
        self.win = winui.MarkdownWindow(os.path.join(self.temp.name, "workspace"))
        self.win.root.withdraw()
        self.addCleanup(self.destroy_window, self.win.root)

    @staticmethod
    def destroy_window(root):
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()

    def test_one_burst_moves_the_viewport_once(self):
        path = Path(self.temp.name, "滚动.md")
        path.write_text("# 滚动\n\n" + "正文内容 " * 400 + "\n\n" * 200, encoding="utf-8")
        self.win.open_local_files([str(path)])
        preview = self.win.preview
        before = preview.index("@0,0")
        for _ in range(5):
            preview.event_generate("<MouseWheel>", delta=-120)
        deadline = time.perf_counter() + 1.0
        while time.perf_counter() < deadline and self.win.wheel.job is not None:
            self.win.root.update()
            time.sleep(0.005)
        after = preview.index("@0,0")
        self.assertGreater(float(after), float(before), "滚轮必须真的滚动视口")
        self.assertIsNone(self.win.wheel.job, "合并计时器必须在应用后清空")

    def test_shift_wheel_scrolls_sideways_without_vertical_drift(self):
        path = Path(self.temp.name, "横向.md")
        path.write_text("# 横向\n\n" + "| 一个很宽的表格单元 | 另一个很宽的表格单元 |\n|---|---|\n"
                        "| 内容 | 内容 |\n\n" + "正文内容 " * 200 + "\n\n" * 50, encoding="utf-8")
        self.win.open_local_files([str(path)])
        preview = self.win.preview
        vertical, sideways = preview.yview()[0], preview.xview()[0]
        event = type("E", (), {"delta": -120, "state": 0x1})()
        self.win.wheel.on_wheel(event)
        self.win.wheel.apply()
        self.win.wheel.cancel()
        self.assertAlmostEqual(preview.yview()[0], vertical, places=6,
                               msg="Shift+滚轮只应横向滚动")
        self.assertGreaterEqual(preview.xview()[0], sideways)


if __name__ == "__main__":
    unittest.main()
