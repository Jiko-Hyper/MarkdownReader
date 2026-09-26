# -*- coding: utf-8 -*-
"""F04 常用格式动作的回归用例（两端共用同一份规则）。

    python -m unittest tests.test_formatting

重点：包裹/取消要可预测、行级动作只碰选区覆盖的行、行内代码的围栏与补空格
能让 markdown-it 渲染回原文（用真引擎交叉验证，而不是自己说了算）。
"""
from __future__ import annotations

import unittest

from mdreader import formatting


class WrapTests(unittest.TestCase):
    def test_formula_controls_make_editable_markdown(self):
        from mdreader import formula
        inline = formatting.apply("速度", 0, 2, "formula_inline")
        self.assertEqual(inline["text"], "$速度$")
        self.assertEqual(inline["text"][inline["start"]:inline["end"]], "速度")
        self.assertEqual(formula.scan(inline["text"])[0]["tex"], "速度")
        empty = formatting.apply("", 0, 0, "formula_inline")
        self.assertEqual(empty["text"], "$x$")
        self.assertEqual(empty["text"][empty["start"]:empty["end"]], "x")

    def test_display_formula_has_boundaries_and_editable_selection(self):
        from mdreader import formula
        result = formatting.apply("前后", 1, 1, "formula_block")
        self.assertEqual(result["text"][result["start"]:result["end"]], r"\frac{a}{b}")
        self.assertIn("\n\n$$\n", result["text"])
        self.assertIn("\n$$\n\n", result["text"])
        found = formula.scan(result["text"])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["display"])
        self.assertEqual(found[0]["error"], "")

    def test_multiline_cannot_be_wrapped_as_inline_formula(self):
        result = formatting.apply("a\nb", 0, 3, "formula_inline")
        self.assertFalse(result["ok"])
        self.assertIn("跨行", result["reason"])

    def test_bold_wraps_the_selection_and_selects_the_inner_text(self):
        result = formatting.apply("这是重点", 2, 4, "bold")
        self.assertEqual(result["text"], "这是**重点**")
        self.assertEqual(result["text"][result["start"]:result["end"]], "重点")

    def test_bold_again_removes_the_markers(self):
        once = formatting.apply("这是重点", 2, 4, "bold")
        twice = formatting.apply(once["text"], once["start"], once["end"], "bold")
        self.assertEqual(twice["text"], "这是重点")
        self.assertEqual(twice["note"], "已取消格式")

    def test_bold_without_selection_inserts_a_template(self):
        result = formatting.apply("", 0, 0, "bold")
        self.assertEqual(result["text"], "****")
        self.assertEqual(result["start"], 2)
        self.assertEqual(result["end"], 2, "空选区只插入标记，光标落在中间")

    def test_italic_uses_a_single_star(self):
        result = formatting.apply("斜体", 0, 2, "italic")
        self.assertEqual(result["text"], "*斜体*")

    def test_wrapping_never_touches_text_outside_the_selection(self):
        source = "前 中 后"
        result = formatting.apply(source, 2, 3, "italic")
        self.assertEqual(result["text"], "前 *中* 后")
        self.assertTrue(result["text"].startswith("前 "))
        self.assertTrue(result["text"].endswith(" 后"))

    def test_unknown_action_is_a_programming_error(self):
        with self.assertRaises(formatting.FormatError):
            formatting.apply("x", 0, 1, "rainbow")


class CodeSpanTests(unittest.TestCase):
    def test_inline_code_wraps_with_a_single_backtick(self):
        result = formatting.apply("看变量", 0, 3, "code")
        self.assertEqual(result["text"], "`看变量`")
        self.assertEqual(result["start"], 1)
        self.assertEqual(result["end"], 4)

    def test_fence_length_grows_past_the_longest_run(self):
        result = formatting.apply("a``b", 0, 4, "code")
        self.assertEqual(result["text"], "```a``b```")

    def test_content_with_edge_spaces_is_padded(self):
        result = formatting.apply(" 前后 ", 0, 4, "code")
        self.assertEqual(result["text"], "`  前后  `")

    def test_code_span_round_trips_through_markdown_it(self):
        try:
            from markdown_it import MarkdownIt
        except ImportError:                      # pragma: no cover
            self.skipTest("markdown-it-py 未安装")
        engine = MarkdownIt("commonmark")
        for source in ["a`b", " 前后 ", "普通", "`", "a``b"]:
            result = formatting.apply(source, 0, len(source), "code")
            html = engine.render(result["text"])
            self.assertIn("<code>", html, result["text"])
            body = html.split("<code>", 1)[1].split("</code>", 1)[0]
            self.assertEqual(body, source, result["text"])


class LinkTests(unittest.TestCase):
    def test_selection_becomes_the_label(self):
        result = formatting.apply("点这里", 1, 3, "link")
        self.assertEqual(result["text"], "点[这里](https://)")
        self.assertEqual(result["text"][result["start"]:result["end"]], "这里")

    def test_a_selected_url_becomes_the_target(self):
        source = "https://example.com/a"
        result = formatting.apply(source, 0, len(source), "link")
        self.assertEqual(result["text"], "[说明](https://example.com/a)")

    def test_empty_selection_inserts_a_template_with_the_label_selected(self):
        result = formatting.apply("", 0, 0, "link")
        self.assertEqual(result["text"], "[说明](https://)")
        self.assertEqual(result["text"][result["start"]:result["end"]], "说明")

    def test_explicit_url_is_used(self):
        result = formatting.apply("标题", 0, 2, "link", url="https://a.b")
        self.assertEqual(result["text"], "[标题](https://a.b)")


class LineActionTests(unittest.TestCase):
    def test_heading_sets_and_clears(self):
        source = "标题\n正文\n"
        once = formatting.apply(source, 0, 0, "heading", level=2)
        self.assertEqual(once["text"], "## 标题\n正文\n")
        twice = formatting.apply(once["text"], once["start"], once["end"], "heading", level=2)
        self.assertEqual(twice["text"], source)

    def test_heading_replaces_a_different_level(self):
        result = formatting.apply("# 标题\n", 0, 0, "heading", level=3)
        self.assertEqual(result["text"], "### 标题\n")

    def test_heading_applies_to_every_selected_line(self):
        source = "一\n二\n三\n"
        result = formatting.apply(source, 0, len(source) - 1, "heading", level=1)
        self.assertEqual(result["text"], "# 一\n# 二\n# 三\n")

    def test_heading_level_range_is_enforced(self):
        for level in (0, 7):
            with self.assertRaises(formatting.FormatError):
                formatting.apply("x", 0, 0, "heading", level=level)

    def test_bullets_toggle_and_keep_indentation(self):
        source = "一\n  二\n"
        once = formatting.apply(source, 0, len(source) - 1, "bullets")
        self.assertEqual(once["text"], "- 一\n  - 二\n")
        twice = formatting.apply(once["text"], once["start"], once["end"], "bullets")
        self.assertEqual(twice["text"], source)

    def test_ordered_list_numbers_the_selected_lines(self):
        result = formatting.apply("甲\n乙\n", 0, 3, "ordered")
        self.assertEqual(result["text"], "1. 甲\n2. 乙\n")

    def test_quote_toggles(self):
        once = formatting.apply("引用我\n", 0, 3, "quote")
        self.assertEqual(once["text"], "> 引用我\n")
        twice = formatting.apply(once["text"], once["start"], once["end"], "quote")
        self.assertEqual(twice["text"], "引用我\n")

    def test_blank_lines_are_left_alone(self):
        source = "一\n\n二\n"
        result = formatting.apply(source, 0, len(source) - 1, "bullets")
        self.assertEqual(result["text"], "- 一\n\n- 二\n")

    def test_a_selection_ending_at_a_line_start_does_not_touch_that_line(self):
        source = "一\n二\n三\n"
        result = formatting.apply(source, 0, 2, "bullets")   # 只选中「一\n」
        self.assertEqual(result["text"], "- 一\n二\n三\n")

    def test_a_caret_on_an_empty_line_gets_a_template(self):
        source = "一\n\n二\n"
        result = formatting.apply(source, 2, 2, "quote")     # 光标停在中间那个空行
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertEqual(result["text"], "一\n> \n二\n")
        back = formatting.apply(result["text"], result["start"], result["end"], "quote")
        self.assertEqual(back["text"], source)


class CodeBlockTests(unittest.TestCase):
    def test_selection_becomes_a_fenced_block(self):
        result = formatting.apply("a = 1\nb = 2\n", 0, 12, "code_block", language="python")
        self.assertEqual(result["text"], "```python\na = 1\nb = 2\n```\n")

    def test_existing_fence_is_removed_again(self):
        once = formatting.apply("a = 1\n", 0, 6, "code_block")
        twice = formatting.apply(once["text"], 0, len(once["text"]), "code_block")
        self.assertEqual(twice["text"], "a = 1\n")

    def test_fence_grows_when_the_body_contains_backticks(self):
        result = formatting.apply("```\n内层\n", 0, 8, "code_block")
        self.assertTrue(result["text"].startswith("````\n"), result["text"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
