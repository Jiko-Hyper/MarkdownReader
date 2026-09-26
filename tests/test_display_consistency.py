# -*- coding: utf-8 -*-
"""显示与交互的一致性：两端配色必须相同，文字对比度必须够看（对应 B08）。

这些检查把「网页看起来和软件一样」和「三种主题都读得清」变成可回归的断言，
不再依赖肉眼确认。

    python -m unittest tests.test_display_consistency
"""
from __future__ import annotations

import os
import re
import unittest

from mdreader import winui

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(HERE, "webui", "app.css")
JS = os.path.join(HERE, "webui", "app.js")

#: WCAG AA for normal text; secondary text must still be comfortably readable.
BODY_MIN = 4.5
MUTED_MIN = 4.5


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def luminance(color: str) -> float:
    value = color.lstrip("#")
    channels = [int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    parts = [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) for c in channels]
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def contrast(foreground: str, background: str) -> float:
    one, two = luminance(foreground), luminance(background)
    lighter, darker = max(one, two), min(one, two)
    return (lighter + 0.05) / (darker + 0.05)


def css_block(source: str, selector: str) -> dict:
    match = re.search(re.escape(selector) + r"\{(.*?)\}", source, re.S)
    if not match:
        return {}
    return {name: value.strip() for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", match.group(1))}


class ThemeParityTests(unittest.TestCase):
    """网页与桌面窗口共用同一组颜色，任何一边改了另一边必须跟上。"""

    @classmethod
    def setUpClass(cls):
        cls.css = read(CSS)

    def pairs(self):
        return ((":root", winui.LIGHT, "light"),
                ('html[data-theme="dark"]', winui.DARK, "dark"),
                ('html[data-theme="eye"]', winui.EYE, "eye"))

    def test_page_background_matches_the_window_surface(self):
        for selector, palette, name in self.pairs():
            with self.subTest(name):
                values = css_block(self.css, selector)
                self.assertEqual(values.get("bg"), palette["side"],
                                 "%s 主题：网页底色与桌面侧栏不一致" % name)
                self.assertEqual(values.get("panel"), palette["bg"],
                                 "%s 主题：网页正文底色与桌面正文不一致" % name)
                self.assertEqual(values.get("fg"), palette["fg"], "%s 主题：正文颜色不一致" % name)
                self.assertEqual(values.get("fg-muted"), palette["muted"],
                                 "%s 主题：次要文字颜色不一致" % name)

    def test_page_border_and_hover_follow_the_palette(self):
        for selector, palette, name in self.pairs():
            with self.subTest(name):
                values = css_block(self.css, selector)
                self.assertEqual(values.get("border"), palette["rule"], "%s 主题：描边不一致" % name)
                self.assertEqual(values.get("hover"), palette["hover"], "%s 主题：悬停底色不一致" % name)
                self.assertEqual(values.get("accent"), palette["accent"], "%s 主题：强调色不一致" % name)


class ContrastTests(unittest.TestCase):
    """三种主题都要读得清；护眼主题曾因次要文字偏浅而不足 4.5。"""

    def test_body_text_has_enough_contrast(self):
        for name, palette in winui.THEMES.items():
            with self.subTest(name):
                self.assertGreaterEqual(contrast(palette["fg"], palette["bg"]), BODY_MIN,
                                        "%s 主题正文对比度不足" % name)
                self.assertGreaterEqual(contrast(palette["fg"], palette["side"]), BODY_MIN,
                                        "%s 主题侧栏正文对比度不足" % name)

    def test_secondary_text_has_enough_contrast(self):
        for name, palette in winui.THEMES.items():
            with self.subTest(name):
                self.assertGreaterEqual(contrast(palette["muted"], palette["bg"]), MUTED_MIN,
                                        "%s 主题次要文字对比度不足" % name)
                self.assertGreaterEqual(contrast(palette["muted"], palette["side"]), MUTED_MIN,
                                        "%s 主题侧栏次要文字对比度不足" % name)

    def test_code_text_has_enough_contrast(self):
        for name, palette in winui.THEMES.items():
            with self.subTest(name):
                self.assertGreaterEqual(contrast(palette["code_fg"], palette["code_bg"]), BODY_MIN,
                                        "%s 主题代码文字对比度不足" % name)

    def test_selection_keeps_text_readable(self):
        for name, palette in winui.THEMES.items():
            with self.subTest(name):
                self.assertGreaterEqual(contrast(palette["fg"], palette["sel"]), 3.0,
                                        "%s 主题选中项文字对比度不足" % name)


class ShortcutParityTests(unittest.TestCase):
    """两端都提供同一批基本快捷键，说明文档也要对得上。"""

    NATIVE = ("Ctrl+S", "Ctrl+N", "Ctrl+O", "Ctrl+W", "Ctrl+Tab", "Ctrl+E", "Ctrl+B", "F5")

    def test_native_shortcut_table_lists_the_bound_keys(self):
        documented = ["%s %s" % (key, label) for key, label in winui.SHORTCUTS]
        joined = "\n".join(documented)
        for key in self.NATIVE:
            self.assertIn(key, joined, "快捷键说明缺少 %s" % key)

    def test_bindings_match_the_documented_keys(self):
        source = read(os.path.join(HERE, "mdreader", "winui.py"))
        for sequence, name in (("<Control-s>", "Ctrl+S"), ("<Control-n>", "Ctrl+N"),
                               ("<Control-o>", "Ctrl+O"), ("<Control-w>", "Ctrl+W"),
                               ("<Control-Tab>", "Ctrl+Tab"), ("<Control-e>", "Ctrl+E"),
                               ("<Control-b>", "Ctrl+B"), ("<F5>", "F5")):
            self.assertIn('"%s"' % sequence, source, "%s 没有绑定" % name)

    def test_web_page_keeps_the_same_basics(self):
        script = read(JS)
        for key in ("'s'", "'b'", "'n'", "'e'", "'f'", "'l'"):
            self.assertRegex(script, r"e\.key === %s" % re.escape(key),
                             "网页缺少 %s 快捷键分支" % key)
        self.assertIn("openFind", script)
        self.assertIn("renderOutline", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
