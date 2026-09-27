import os
import unittest

from mdreader import formula as f


LONG_FORMULA = (r"V_{ce} = f(i_c, T_j) \rightarrow P_{cond} = f(i_c, V_{ce}, T_j),\ "
                r"P_{sw} = f(f_{sw}, E_{on}, E_{off}, T_j) \rightarrow "
                r"T_j = f(P_{loss}, Z_{thjc}, T_c)")


@unittest.skipUnless(os.name == 'nt', 'Windows GDI renderer')
class FormulaLayoutTests(unittest.TestCase):
    def test_text_extents_stay_inside_row(self):
        for tex in (LONG_FORMULA, 'a,b,c,d,e,f', 'x=y+z', r'x_{a,b}^{c+d}',
                    r'\frac{a,b,c}{x=y}', r'\sqrt{x_i^2+y_j^2}'):
            for size in (12, 20, 32):
                with self.subTest(tex=tex, size=size), f._gdi_lock:
                    box = f._row_box(f._Parser(tex).parse(), size)
                    for op in box.ops:
                        if op[0] != 'text':
                            continue
                        _, x, y, text, family, pixels, weight = op
                        glyph = f._plain_text_box(family, text, pixels, weight)
                        self.assertLessEqual(x + glyph.width, box.width + 0.01)
                        self.assertLessEqual(glyph.height - y, box.height + 0.01)
                        self.assertLessEqual(y + glyph.depth, box.depth + 0.01)

    def test_operator_spacing_on_both_sides(self):
        with f._gdi_lock:
            box = f._row_box(f._Parser('x=y').parse(), 20)
            x, equals, y = box.ops
            xwidth = f._plain_text_box(x[4], x[3], x[5]).width
            ewidth = f._plain_text_box(equals[4], equals[3], equals[5]).width
            self.assertAlmostEqual(equals[1] - x[1] - xwidth, 5.6)
            self.assertAlmostEqual(y[1] - equals[1] - ewidth, 5.6)

    def test_subscripts_share_a_compact_baseline(self):
        with f._gdi_lock:
            for tex in ('T_j', 'P_{cond}', 'Z_{thjc}'):
                box = f._row_box(f._Parser(tex).parse(), 20)
                offsets = [op[2] for op in box.ops if op[0] == 'text'][1:]
                self.assertTrue(all(abs(y - 5.6) < .01 for y in offsets))

    def test_long_formula_renders_in_all_themes(self):
        for theme in ('light', 'dark', 'eye'):
            fg, bg = f.colors_for(theme)
            result = f.to_png(LONG_FORMULA, size=20, display=True, color=fg, background=bg)
            self.assertTrue(result['ok'], result.get('reason'))
