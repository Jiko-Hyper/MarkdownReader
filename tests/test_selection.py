"""B01：选区修改的纯逻辑——定位、范围替换、过期判断都在窗口之外可验证。"""
import unittest

from mdreader import selection as SE


class RangeTests(unittest.TestCase):
    def test_a_missing_or_broken_selection_says_what_to_do(self):
        for text, start, end in [('正文', 1, 1), ('正文', 3, 1), ('正文', 0, 9), (None, 0, 1), ('正文', '0', 1)]:
            with self.subTest(start=start, end=end):
                with self.assertRaises(SE.SelectionError) as raised:
                    SE.require_range(text, start, end)
                self.assertIn('选', str(raised.exception))

    def test_outgoing_reports_the_exact_range_before_anything_is_sent(self):
        text = '# 标题\n\n第一段。\n\n第二段要改。\n\n第三段。\n'
        start, end = text.index('第二段要改。'), text.index('第二段要改。') + len('第二段要改。')
        sent, note = SE.outgoing(text, start, end, 'selection')
        self.assertEqual(sent, '第二段要改。')
        self.assertIn('仅选区', note)
        self.assertIn('第 %d–%d 字符' % (start + 1, end), note)
        sent, note = SE.outgoing(text, start, end, 'block')
        self.assertIn('第二段要改。', sent)
        self.assertNotIn('第一段。', sent, '段落范围只到空行为界')
        self.assertIn('选区所在段落', note)
        sent, note = SE.outgoing(text, start, end, 'document')
        self.assertEqual(sent, text)
        self.assertIn('整篇文档', note)
        with self.assertRaises(SE.SelectionError):
            SE.outgoing(text, start, end, 'everything')

    def test_replacing_a_range_never_touches_the_rest_of_the_document(self):
        text = '重复内容\n\n要改的地方\n\n重复内容\n\n重复内容\n'
        start = text.index('要改的地方')
        new_text, new_start, new_end = SE.replace_range(text, start, start + len('要改的地方'), '改好了')
        self.assertEqual(new_text, '重复内容\n\n改好了\n\n重复内容\n\n重复内容\n')
        self.assertEqual(new_text[new_start:new_end], '改好了')
        self.assertEqual(new_text.count('重复内容'), 3, '重复段落一处都不能少')

    def test_replacement_must_be_usable_text(self):
        for value in ('', '   ', '\n\t', None, 42, ['x']):
            with self.subTest(value=value):
                with self.assertRaises(SE.SelectionError):
                    SE.check_replacement(value)
        with self.assertRaises(SE.SelectionError):
            SE.check_replacement('字' * (SE.MAX_CHARS + 1))
        self.assertEqual(SE.check_replacement(' 好的 '), ' 好的 ')

    def test_emoji_and_multiline_selections_keep_their_offsets(self):
        text = '前言 🙂 中间\n跨行选区\n结尾'
        start, end = text.index('🙂'), text.index('结尾')
        replaced, new_start, new_end = SE.replace_range(text, start, end, '替换')
        self.assertEqual(replaced, '前言 替换结尾')
        self.assertEqual((new_start, new_end), (3, 5))


class StaleTests(unittest.TestCase):
    def test_a_stale_or_moved_result_must_not_be_applied(self):
        tab = {'id': 1}
        before = SE.request_before(tab, '原文', 0, 2)
        self.assertEqual(SE.stale_reason(before, tab, '原文'), '')
        self.assertIn('切换', SE.stale_reason(before, {'id': 2}, '原文'))
        self.assertIn('变化', SE.stale_reason(before, tab, '原文改过了'))
        self.assertIn('失效', SE.stale_reason({}, tab, '原文'))
        with self.assertRaises(SE.SelectionError):
            SE.request_before(tab, '原文', 2, 2)

    def test_diff_shows_both_sides(self):
        diff = SE.diff_of('原来的一句', '改好的一句')
        self.assertIn('-原来的一句', diff)
        self.assertIn('+改好的一句', diff)


if __name__ == '__main__':
    unittest.main()
