# -*- coding: utf-8 -*-
import unittest

from mdreader import render, winui


SAMPLE = """| 组件 | 接口 | 参数 |
|---|---|---|
| 摄像头 | USB / V4L2 | `/dev/video0`, 30fps |
| 电池 | 12V | 11.6V–12.6V |
"""


class RenderQualityTests(unittest.TestCase):
    def test_html_table_is_semantic_and_wrapped(self):
        fragment, _, _ = render.render_markdown(SAMPLE)
        self.assertIn('<div class="table-wrap"><table>', fragment)
        self.assertIn("<th", fragment)
        self.assertIn("<td", fragment)

    def test_native_parser_preserves_table_cells_for_grid(self):
        segments, _, _, tables = winui._parse_document_for_widget(SAMPLE)
        self.assertEqual(len(tables), 1)
        table = next(iter(tables.values()))
        self.assertEqual(table["header"], ["组件", "接口", "参数"])
        self.assertEqual(table["rows"][0][0], "摄像头")
        self.assertTrue(any("__table_" in " ".join(tags) for _, tags in segments))


if __name__ == "__main__":
    unittest.main()
