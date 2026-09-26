# -*- coding: utf-8 -*-
"""F04 表格模块的回归用例（纯逻辑，两端共用同一份规则）。

    python -m unittest tests.test_tables

用例的重点不是“函数能跑”，而是三件容易出错的事：

* **转义**：``|`` 与 ``\\`` 写进去、读回来必须一致，而且与 markdown-it 的解释一致；
* **拒绝**：列数对不上、竖线写法不统一、围栏代码块里的伪表格都要拒绝修改，
  不能“猜一个看起来对的结果”；
* **不静默错列**：TSV 列数不一致时补空单元格并给出提示，而不是把内容挤到别的列。
"""
from __future__ import annotations

import unittest

from mdreader import tables


SIMPLE = "| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n| 乙 | 2 |\n"


class SplitRowTests(unittest.TestCase):
    def test_padded_row_keeps_the_original_cell_text(self):
        cells, leading, trailing = tables.split_row("| a | b |")
        self.assertEqual(cells, [" a ", " b "])
        self.assertTrue(leading and trailing)

    def test_row_without_outer_pipes(self):
        cells, leading, trailing = tables.split_row("a | b")
        self.assertEqual(cells, ["a ", " b"])
        self.assertFalse(leading or trailing)

    def test_escaped_pipe_is_not_a_separator(self):
        cells, _l, trailing = tables.split_row(r"| a \| b | c |")
        self.assertEqual(cells, [r" a \| b ", " c "])
        self.assertTrue(trailing)
        self.assertEqual([tables.cell_text(cell).strip() for cell in cells], ["a | b", "c"])

    def test_trailing_escaped_pipe_belongs_to_the_cell(self):
        cells, leading, trailing = tables.split_row(r"| a \|")
        self.assertEqual(cells, [r" a \|"])
        self.assertTrue(leading)
        self.assertFalse(trailing)

    def test_backslash_escape_round_trip(self):
        for text in ["a | b", r"c\d", r"e\|f", "普通 | 中文", r"\\", "|"]:
            raw = tables.escape_cell(text)
            self.assertEqual(tables.cell_text(raw), text, text)

    def test_escape_cell_matches_what_markdown_it_renders(self):
        try:
            from markdown_it import MarkdownIt
        except ImportError:                      # pragma: no cover - 环境缺依赖时跳过
            self.skipTest("markdown-it-py 未安装，无法做交叉验证")
        engine = MarkdownIt("commonmark").enable("table")
        for text in ["a | b", r"c\d", "反斜杠 \\ 与竖线 |"]:
            source = "| 列 |\n| --- |\n| %s |\n" % tables.escape_cell(text)
            html = engine.render(source)
            self.assertIn("</table>", html, source)
            body = html.split("<td>", 1)[1].split("</td>", 1)[0]
            self.assertEqual(body.strip(), text, source)


class BlockTests(unittest.TestCase):
    def test_finds_a_plain_table(self):
        found = tables.blocks(SIMPLE)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["aligns"], ["left", "left"])
        self.assertEqual(tables.model(found[0])["rows"], [["甲", "1"], ["乙", "2"]])

    def test_alignment_specs_are_read(self):
        source = "| a | b | c |\n| :--- | :---: | ---: |\n| 1 | 2 | 3 |\n"
        self.assertEqual(tables.blocks(source)[0]["aligns"], ["left", "center", "right"])

    def test_table_inside_a_fence_is_not_a_table(self):
        source = "```\n| a | b |\n| --- | --- |\n| 1 | 2 |\n```\n"
        self.assertEqual(tables.blocks(source), [])
        self.assertFalse(tables.read(source, offset=source.index("| a"))["found"])

    def test_a_delimiter_row_is_required(self):
        source = "| a | b |\n| 1 | 2 |\n"
        self.assertEqual(tables.blocks(source), [])

    def test_cursor_position_decides_which_table(self):
        source = SIMPLE + "\n正文\n\n" + "| x |\n| --- |\n| 9 |\n"
        second = source.rindex("| 9 |")
        self.assertEqual(tables.read(source, offset=second)["table"]["header"], ["x"])
        # 光标在表格之外：明确说“不在表格里”，而不是猜最近的一张
        self.assertFalse(tables.read(source, offset=source.index("正文"))["found"])

    def test_render_then_read_is_stable(self):
        block = tables.blocks(SIMPLE)[0]
        rendered = tables.render(**tables.model(block))
        again = tables.model(tables.blocks(rendered + "\n")[0])
        self.assertEqual(again, tables.model(block))

    def test_crlf_documents_keep_their_newlines(self):
        source = "标题\r\n\r\n" + SIMPLE.replace("\n", "\r\n")
        result = tables.operate(source, "insert_row", line=2, values=["丙", "3"])
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertIn("\r\n", result["text"])
        self.assertNotIn("\n", result["text"].replace("\r\n", ""))
        self.assertIn("| 丙 | 3 |", result["text"])


class ReviewTests(unittest.TestCase):
    def test_ragged_rows_are_refused_not_padded(self):
        source = "| a | b |\n| --- | --- |\n| 1 |\n"
        info = tables.read(source, line=0)
        self.assertTrue(info["found"])
        self.assertFalse(info["ok"])
        self.assertIn("对不上", info["reason"])

    def test_mixed_pipe_styles_are_refused(self):
        source = "| a | b |\n| --- | --- |\n1 | 2\n"
        info = tables.read(source, line=0)
        self.assertFalse(info["ok"])
        self.assertIn("首尾竖线", info["reason"])

    def test_mixed_padding_in_the_header_is_refused(self):
        source = "| a |  b |\n| --- | --- |\n| 1 | 2 |\n"
        info = tables.read(source, line=0)
        self.assertFalse(info["ok"])
        self.assertIn("空白", info["reason"])

    def test_table_without_outer_pipes_is_still_editable(self):
        source = "a | b\n--- | ---\n1 | 2\n"
        info = tables.read(source, line=0)
        self.assertTrue(info["ok"], info["reason"])
        head = tables.operate(source, "set_cell", line=0, row=-1, column=1, value="改过")
        self.assertEqual(head["text"], "a | 改过\n--- | ---\n1 | 2\n")
        body = tables.operate(source, "set_cell", line=0, row=0, column=1, value="改过")
        self.assertEqual(body["text"], "a | b\n--- | ---\n1 | 改过\n")

    def test_too_many_columns_is_refused_with_a_reason(self):
        wide = "|" + "|".join("c%d" % n for n in range(tables.MAX_COLUMNS + 1)) + "|\n"
        source = wide + "|" + "|".join(["---"] * (tables.MAX_COLUMNS + 1)) + "|\n"
        info = tables.read(source, line=0)
        self.assertFalse(info["ok"])
        self.assertIn("上限", info["reason"])

    def test_operating_on_a_refused_table_returns_the_source_untouched(self):
        source = "| a | b |\n| --- | --- |\n| 1 |\n"
        result = tables.operate(source, "delete_row", line=0, index=0)
        self.assertFalse(result["ok"])
        self.assertNotIn("text", result)


class OperateTests(unittest.TestCase):
    def test_write_model_replaces_the_whole_table(self):
        result = tables.write_model(SIMPLE, ["项目", "数量"], [["甲", "1"], ["丙", "3"]],
                                    ["left", "right"], line=0)
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertEqual(result["text"],
                         "| 项目 | 数量 |\n| --- | ---: |\n| 甲 | 1 |\n| 丙 | 3 |\n")

    def test_write_model_refuses_when_the_cursor_is_not_in_a_table(self):
        result = tables.write_model("正文\n", ["a"], [["1"]], ["left"], line=0)
        self.assertFalse(result["ok"])
        self.assertNotIn("text", result)

    def test_write_model_refuses_a_multiline_cell(self):
        result = tables.write_model(SIMPLE, ["名称", "数量"], [["甲\n乙", "1"]], ["left", "left"],
                                    line=0)
        self.assertFalse(result["ok"])
        self.assertIn("一行文本", result["reason"])

    def test_set_table_operation_matches_write_model(self):
        result = tables.operate(SIMPLE, "set_table", line=0, header=["项目", "数量"],
                                rows=[["甲", "1"]], aligns=["center", "left"])
        self.assertEqual(result["text"],
                         "| 项目 | 数量 |\n| :---: | --- |\n| 甲 | 1 |\n")

    def test_insert_row_keeps_every_other_cell(self):
        result = tables.operate(SIMPLE, "insert_row", line=0, index=1, values=["丙", "3"])
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertEqual(result["text"],
                         "| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n| 丙 | 3 |\n| 乙 | 2 |\n")

    def test_delete_row_and_column(self):
        one = tables.operate(SIMPLE, "delete_row", line=0, index=0)
        self.assertIn("| 乙 | 2 |", one["text"])
        self.assertNotIn("| 甲 | 1 |", one["text"])
        two = tables.operate(SIMPLE, "delete_col", line=0, index=1)
        self.assertEqual(two["text"], "| 名称 |\n| --- |\n| 甲 |\n| 乙 |\n")

    def test_last_column_cannot_be_deleted(self):
        source = "| a |\n| --- |\n| 1 |\n"
        result = tables.operate(source, "delete_col", line=0, index=0)
        self.assertFalse(result["ok"])
        self.assertIn("至少要留一列", result["reason"])

    def test_insert_column_marks_only_the_new_column(self):
        result = tables.operate(SIMPLE, "insert_col", line=0, index=1, title="单位", value="-")
        self.assertEqual(result["text"],
                         "| 名称 | 单位 | 数量 |\n| --- | --- | --- |\n| 甲 | - | 1 |\n| 乙 | - | 2 |\n")

    def test_alignment_and_cells(self):
        moved = tables.operate(SIMPLE, "set_align", line=0, column=1, align="right")
        self.assertIn("| --- | ---: |", moved["text"])
        cell = tables.operate(SIMPLE, "set_cell", line=3, row=1, column=0, value="乙改")
        self.assertIn("| 乙改 | 2 |", cell["text"])
        head = tables.operate(SIMPLE, "set_cell", line=0, row=-1, column=0, value="项目")
        self.assertIn("| 项目 | 数量 |", head["text"])

    def test_cell_with_a_pipe_is_escaped_on_the_way_out(self):
        result = tables.operate(SIMPLE, "set_cell", line=0, row=0, column=0, value="a | b")
        self.assertIn(r"| a \| b | 1 |", result["text"])
        back = tables.read(result["text"], line=0)
        self.assertEqual(back["table"]["rows"][0][0], "a | b")

    def test_multiline_cell_is_refused(self):
        result = tables.operate(SIMPLE, "set_cell", line=0, row=0, column=0, value="第一行\n第二行")
        self.assertFalse(result["ok"])
        self.assertIn("一行文本", result["reason"])

    def test_out_of_range_row_or_column_is_refused(self):
        self.assertFalse(tables.operate(SIMPLE, "set_cell", line=0, row=9, column=0, value="x")["ok"])
        self.assertFalse(tables.operate(SIMPLE, "set_align", line=0, column=5, align="left")["ok"])
        self.assertFalse(tables.operate(SIMPLE, "set_align", line=0, column=0, align="middle")["ok"])
        self.assertFalse(tables.operate(SIMPLE, "explode", line=0)["ok"])

    def test_unknown_operation_changes_nothing(self):
        result = tables.operate(SIMPLE, "merge_cells", line=0, row=0, column=0)
        self.assertFalse(result["ok"])
        self.assertNotIn("text", result)


class InsertTests(unittest.TestCase):
    def test_new_table_has_a_delimiter_row_and_blank_lines(self):
        result = tables.insert("正文\n", columns=2, rows=2)
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertEqual(result["text"], "正文\n\n|  |  |\n| --- | --- |\n|  |  |\n|  |  |\n")

    def test_new_table_without_header_still_has_the_syntax_row(self):
        result = tables.insert("", columns=1, rows=1, header=False, fills=[["甲"]])
        self.assertEqual(result["text"], "|  |\n| --- |\n| 甲 |\n")

    def test_insert_lands_after_the_cursor_line(self):
        source = "第一行\n第二行\n"
        result = tables.insert(source, columns=1, rows=1, header=True, fills=[["h"], ["v"]],
                               offset=source.index("第一行"))
        self.assertEqual(result["text"], "第一行\n\n| h |\n| --- |\n| v |\n\n第二行\n")

    def test_limits_are_reported(self):
        self.assertIn("列数要在", tables.insert("", columns=0, rows=1)["reason"])
        self.assertIn("列数要在", tables.insert("", columns=tables.MAX_COLUMNS + 1, rows=1)["reason"])
        self.assertIn("行数要在", tables.insert("", columns=1, rows=-1)["reason"])
        self.assertIn("数字", tables.insert("", columns="二", rows=1)["reason"])


class TsvTests(unittest.TestCase):
    def test_rectangle_is_read_as_rows_and_columns(self):
        parsed = tables.parse_tsv("名称\t数量\n甲\t1\n乙\t2\n")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["columns"], 2)
        self.assertEqual(parsed["rows"][1], ["甲", "1"])
        self.assertEqual(parsed["warnings"], [])

    def test_ragged_rows_are_padded_and_reported(self):
        parsed = tables.parse_tsv("a\tb\tc\n1\t2\n")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["rows"][1], ["1", "2", ""])
        self.assertEqual(parsed["ragged"], [2])
        self.assertTrue(any("列数" in message for message in parsed["warnings"]))

    def test_quotes_and_embedded_newlines_are_reported(self):
        parsed = tables.parse_tsv('say "hi"\t1\n')
        self.assertTrue(any("引号" in message for message in parsed["warnings"]))
        blank = tables.parse_tsv("\t\n")
        self.assertTrue(blank["ok"])
        self.assertEqual(blank["columns"], 2)
        self.assertFalse(tables.parse_tsv("")["ok"])

    def test_too_wide_input_is_refused(self):
        parsed = tables.parse_tsv("\t".join(["c"] * (tables.MAX_COLUMNS + 1)))
        self.assertFalse(parsed["ok"])
        self.assertIn("上限", parsed["warnings"][0])

    def test_paste_replaces_the_selection(self):
        source = "开场\n\n旧内容\n占位\n\n结尾\n"
        start = source.index("旧内容")
        end = source.index("结尾")
        result = tables.paste_tsv(source, "h1\th2\nv1\tv2\n", start=start, end=end)
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertEqual(result["text"],
                         "开场\n\n| h1 | h2 |\n| --- | --- |\n| v1 | v2 |\n\n结尾\n")

    def test_paste_without_selection_goes_after_the_cursor_line(self):
        source = "第一行\n第二行\n"
        result = tables.paste_tsv(source, "h\nv\n", offset=source.index("第一行"))
        self.assertEqual(result["text"], "第一行\n\n| h |\n| --- |\n| v |\n\n第二行\n")

    def test_tsv_without_header_uses_an_empty_header_row(self):
        result = tables.paste_tsv("", "甲\t乙\n", offset=0, header=False)
        self.assertEqual(result["text"], "|  |  |\n| --- | --- |\n| 甲 | 乙 |\n")

    def test_paste_refuses_an_oversized_payload(self):
        payload = "\t".join(["x"] * (tables.MAX_COLUMNS + 1))
        result = tables.paste_tsv("正文\n", payload)
        self.assertFalse(result["ok"])
        self.assertNotIn("text", result)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
