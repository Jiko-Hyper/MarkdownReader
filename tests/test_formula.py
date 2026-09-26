# -*- coding: utf-8 -*-
"""F05 公式模块的回归用例。

    python -m unittest tests.test_formula

分三层：

* **识别**：``$…$`` / ``$$…$$``、转义、代码块、金额文本这些歧义输入必须有确定结论；
* **语法**：样本表里的每一条都要能排出版面；不支持或写错的表达式要**报错并保留源码**，
  不能悄悄画出半张图；
* **产物**：生成的 PNG 是真的（头部合法、尺寸与声明一致、有墨迹），缓存键按
  “表达式 + 字号 + 缩放 + 主题 + 渲染器版本”区分，缓存坏了能重画。
"""
from __future__ import annotations

import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from mdreader import formula


def decode_png(data: bytes):
    """不用第三方库读 PNG：返回 (宽, 高, 逐行 RGB)。"""
    offset, width, height, chunks = 8, 0, 0, b""
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        tag = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        if tag == b"IHDR":
            width, height = struct.unpack(">II", payload[:8])
        elif tag == b"IDAT":
            chunks += payload
        offset += 12 + length
    raw = zlib.decompress(chunks)
    stride = width * 3 + 1
    return width, height, [raw[row * stride + 1:(row + 1) * stride] for row in range(height)]


def ink_count(pixels, background=b"\xff\xff\xff"):
    return sum(1 for line in pixels for x in range(len(line) // 3)
               if line[x * 3:x * 3 + 3] != background)


class ScanTests(unittest.TestCase):
    def test_inline_and_display_forms(self):
        found = formula.scan("行内 $x^{2}$ 与块：\n\n$$\n\\frac{a}{b}\n$$\n")
        self.assertEqual([entry["tex"] for entry in found], ["x^{2}", "\\frac{a}{b}"])
        self.assertEqual([entry["display"] for entry in found], [False, True])
        self.assertTrue(all(not entry["error"] for entry in found))

    def test_escaped_dollar_is_plain_text(self):
        found = formula.scan(r"价格是 \$5 不是公式")
        self.assertEqual(found, [])

    def test_currency_text_is_not_a_formula(self):
        found = formula.scan("价格 $5 与 $6 元")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["error"], "首尾带空白的行内美元不该当成公式")

    def test_inline_formula_may_not_span_lines(self):
        found = formula.scan("$x +\ny$")
        self.assertEqual(len(found), 1)
        self.assertIn("没有找到配对", found[0]["error"])

    def test_display_formula_may_span_lines(self):
        found = formula.scan("$$\n\\sum_{i=1}^{n} i\n$$\n")
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["error"])

    def test_code_blocks_and_inline_code_are_skipped(self):
        source = "```\n$a + b$\n```\n\n行内代码 `$c$` 与真正的 $d$\n"
        found = formula.scan(source)
        self.assertEqual([entry["tex"] for entry in found], ["d"])

    def test_empty_formula_is_reported(self):
        found = formula.scan("$$\n\n$$")
        self.assertEqual(len(found), 1)
        self.assertIn("空", found[0]["error"])

    def test_unclosed_dollar_keeps_the_rest_as_source(self):
        found = formula.scan("开头 $x + y")
        self.assertEqual(len(found), 1)
        self.assertIn("没有找到配对", found[0]["error"])
        self.assertEqual(found[0]["tex"], "x + y")

    def test_counts_are_capped(self):
        found = formula.scan(" ".join("$x_{%d}$" % n for n in range(formula.MAX_EXPRESSIONS + 40)))
        self.assertEqual(len(found), formula.MAX_EXPRESSIONS)


class ParseTests(unittest.TestCase):
    def test_supported_samples_all_render(self):
        for tex, label in formula.SUPPORTED_SAMPLES:
            with self.subTest(label=label, tex=tex):
                result = formula.to_png(tex, size=18)
                self.assertTrue(result["ok"], "%s 渲染失败：%s" % (label, result.get("reason")))
                self.assertGreater(result["width"], 4)
                self.assertGreater(result["height"], 4)

    def test_unsupported_command_reports_and_keeps_the_source(self):
        result = formula.to_png(r"\begin{array}{cc} a & b \end{array}")
        self.assertFalse(result["ok"])
        self.assertIn("暂不支持的环境", result["reason"])
        self.assertEqual(result["tex"], r"\begin{array}{cc} a & b \end{array}")

    def test_unknown_command_is_named(self):
        result = formula.to_png(r"\foo{x}")
        self.assertFalse(result["ok"])
        self.assertIn("\\foo", result["reason"])

    def test_unbalanced_braces_report(self):
        self.assertFalse(formula.to_png(r"\frac{a}{b")["ok"])
        self.assertFalse(formula.to_png(r"{a + b")["ok"])

    def test_left_without_right_reports(self):
        result = formula.to_png(r"\left( \frac{a}{b}")
        self.assertFalse(result["ok"])
        self.assertIn("\\right", result["reason"])

    def test_environment_must_be_closed(self):
        result = formula.to_png(r"\begin{pmatrix} a & b")
        self.assertFalse(result["ok"])
        self.assertIn("没有 \\end", result["reason"])

    def test_environment_names_must_match(self):
        result = formula.to_png(r"\begin{pmatrix} a \end{bmatrix}")
        self.assertFalse(result["ok"])
        self.assertIn("不配对", result["reason"])

    def test_root_index_is_read(self):
        with_index = formula.to_png(r"\sqrt[3]{x}", size=24)
        plain = formula.to_png(r"\sqrt{x}", size=24)
        self.assertTrue(with_index["ok"] and plain["ok"])
        self.assertGreater(with_index["height"], plain["height"],
                           "带次数的根号要在根号上方多出一块，说明次数真的排上了")

    def test_empty_and_oversized_source_are_refused(self):
        self.assertFalse(formula.to_png("   ")["ok"])
        too_long = "x + " * formula.MAX_TEX_CHARS
        result = formula.to_png(too_long)
        self.assertFalse(result["ok"])
        self.assertIn("太长", result["reason"])

    def test_missing_glyph_falls_back_to_another_font(self):
        """字体里没有的符号要换字体画出来，而不是留空框。"""
        result = formula.to_png("α ∉ ℝ ∑ √ ✓", size=20)
        self.assertTrue(result["ok"])
        _width, _height, pixels = decode_png(result["png"])
        self.assertGreater(ink_count(pixels), 60)


class RenderTests(unittest.TestCase):
    def test_png_header_and_declared_size_agree(self):
        result = formula.to_png(r"\frac{a}{b}", size=20)
        width, height, pixels = decode_png(result["png"])
        self.assertEqual((width, height), (result["width"], result["height"]))
        self.assertEqual(formula.png_size(result["png"]), (width, height))
        self.assertGreater(ink_count(pixels), 20, "公式应当真的画出东西")

    def test_larger_size_makes_a_larger_image(self):
        small = formula.to_png(r"\sum_{i=1}^{n} i", size=14)
        large = formula.to_png(r"\sum_{i=1}^{n} i", size=28)
        self.assertLess(small["width"], large["width"])
        self.assertLess(small["height"], large["height"])

    def test_scale_multiplies_the_pixel_size(self):
        once = formula.to_png(r"x^{2}", size=20, scale=1.0)
        twice = formula.to_png(r"x^{2}", size=20, scale=2.0)
        self.assertAlmostEqual(twice["pixels"], once["pixels"] * 2, delta=1)

    def test_background_colour_is_respected(self):
        light = formula.to_png("x", size=20, background="#ffffff", color="#000000")
        dark = formula.to_png("x", size=20, background="#1f2430", color="#e5e7eb")
        _w, _h, light_pixels = decode_png(light["png"])
        _w, _h, dark_pixels = decode_png(dark["png"])
        self.assertEqual(light_pixels[0][:3], b"\xff\xff\xff")
        self.assertEqual(dark_pixels[0][:3], b"\x1f\x24\x30")

    def test_pixel_size_is_clamped(self):
        self.assertEqual(formula.to_png("x", size=4)["pixels"], formula.MIN_SIZE)
        self.assertEqual(formula.to_png("x", size=400)["pixels"], formula.MAX_SIZE)

    def test_oversized_formula_is_refused_instead_of_crashing(self):
        result = formula.to_png(r"x" * 900, size=96)
        self.assertFalse(result["ok"])
        self.assertTrue("太大" in result["reason"] or "太长" in result["reason"])


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-formula-")
        self.addCleanup(self.temp.cleanup)
        self.directory = str(Path(self.temp.name, "cache"))

    def test_key_depends_on_everything_that_changes_the_picture(self):
        base = formula.cache_key("x", size=16, theme="light", scale=1.0)
        self.assertNotEqual(base, formula.cache_key("y", size=16, theme="light", scale=1.0))
        self.assertNotEqual(base, formula.cache_key("x", size=18, theme="light", scale=1.0))
        self.assertNotEqual(base, formula.cache_key("x", size=16, theme="dark", scale=1.0))
        self.assertNotEqual(base, formula.cache_key("x", size=16, theme="light", scale=1.5))
        self.assertNotEqual(base, formula.cache_key("x", size=16, theme="light", scale=1.0,
                                                    weight=700))
        self.assertNotEqual(base, formula.cache_key("x", size=16, display=True))
        self.assertEqual(base, formula.cache_key("x", size=16, theme="light", scale=1.0))

    def test_first_call_writes_and_second_call_reads_the_same_file(self):
        first = formula.cached(self.directory, r"\frac{a}{b}", size=18, theme="light")
        self.assertTrue(first["ok"])
        self.assertFalse(first["cached"])
        self.assertTrue(os.path.isfile(first["path"]))
        second = formula.cached(self.directory, r"\frac{a}{b}", size=18, theme="light")
        self.assertTrue(second["cached"])
        self.assertEqual(second["path"], first["path"])
        self.assertEqual(second["width"], first["width"])
        with open(first["path"], "rb") as handle:
            self.assertEqual(handle.read(), first["png"])

    def test_a_damaged_cache_file_is_rebuilt(self):
        first = formula.cached(self.directory, "x^{2}", size=18)
        with open(first["path"], "wb") as handle:
            handle.write(b"not a png")
        again = formula.cached(self.directory, "x^{2}", size=18)
        self.assertTrue(again["ok"])
        self.assertFalse(again["cached"], "坏掉的缓存要重画，而不是把坏文件当结果")
        self.assertEqual(formula.png_size(again["png"]), (again["width"], again["height"]))

    def test_a_failed_cache_write_still_returns_the_picture(self):
        blocked = str(Path(self.temp.name, "文件占位"))
        Path(blocked).write_text("不是目录", encoding="utf-8")
        result = formula.cached(str(Path(blocked, "cache")), "x", size=18)
        self.assertTrue(result["ok"], "缓存写不进去也不能让公式消失")
        self.assertEqual(result["path"], "")
        self.assertGreater(result["width"], 0)

    def test_no_directory_means_no_write(self):
        result = formula.cached("", "x", size=18)
        self.assertTrue(result["ok"])
        self.assertEqual(result["path"], "")

    def test_errors_are_not_cached_as_pictures(self):
        result = formula.cached(self.directory, r"\foo{x}", size=18)
        self.assertFalse(result["ok"])
        self.assertEqual(sorted(os.listdir(self.directory)) if os.path.isdir(self.directory) else [],
                         [], "渲染失败时不应该留下任何文件")


class RenderIntegrationTests(unittest.TestCase):
    """公式接进渲染管线：图片走单独通道，正文照旧过内容白名单。"""

    def resolver(self, calls=None, ok=True):
        def resolve(tex, display):
            if calls is not None:
                calls.append((tex, display))
            if not ok:
                return {"ok": False, "reason": "测试里故意画不出来"}
            return {"ok": True, "src": "/api/formula?tex=%d" % len(calls or []),
                    "width": 40, "height": 12}
        return resolve

    def test_inline_and_display_formulas_become_images(self):
        from mdreader import render
        calls = []
        for engine_source in ("行内 $x^{2}$ 结束。\n",
                              "$$\n\\frac{a}{b}\n$$\n"):
            with self.subTest(source=engine_source):
                calls.clear()
                frag, _meta, _engine = render.render_markdown(engine_source,
                                                              formula=self.resolver(calls))
                self.assertIn("<img", frag)
                self.assertEqual(len(calls), 1)
                self.assertIn("alt=", frag, "替代文字要留着源码，方便搜索与复制")
                self.assertIn("$x^{2}$" if "x^" in engine_source else "$$", frag)

    def test_unsupported_formula_shows_the_source_and_the_reason(self):
        from mdreader import core, render
        with tempfile.TemporaryDirectory(prefix="mdreader-formula-") as root:
            frag, _meta, _engine = render.render_markdown(
                r"看这个 $\foo{x}$ 和这个 $\frac{a}{b}$。", formula=core.formula_resolver(root))
        self.assertIn("formula-error", frag)
        self.assertIn("$\\foo{x}$", frag)
        self.assertEqual(frag.count("<img"), 1, "能画的仍然画出来")

    def test_resolver_failure_is_visible_not_silent(self):
        from mdreader import render
        frag, _meta, _engine = render.render_markdown("$x$", formula=self.resolver(ok=False))
        self.assertIn("formula-error", frag)
        self.assertIn("画不出来", frag)
        self.assertNotIn("<img", frag)

    def test_error_text_and_reason_are_escaped(self):
        """错误提示里带的是用户写的表达式，必须转义后再放进 HTML。"""
        from mdreader import render
        frag, _meta, _engine = render.render_markdown(
            r"看 $\foo{<script>alert(1)</script>}$ 这里。", formula=self.resolver(ok=False))
        self.assertIn("formula-error", frag)
        self.assertNotIn("<script", frag)
        self.assertIn("&lt;script&gt;", frag)

    def test_code_spans_and_escaped_dollars_stay_text(self):
        from mdreader import render
        calls = []
        frag, _meta, _engine = render.render_markdown(
            "代码 `$x$`、转义 \\$5、金额 $5 与 $6 元，真正的 $y$。", formula=self.resolver(calls))
        self.assertEqual([tex for tex, _display in calls], ["y"])
        self.assertIn("$x$", frag)
        self.assertIn("$5 与 $6 元", frag)

    def test_the_document_keeps_passing_the_content_policy(self):
        """公式是可信产物，正文仍然按白名单过滤——不能因为公式就放行脚本。"""
        from mdreader import render
        frag, _meta, _engine = render.render_markdown(
            "正文 <script>alert(1)</script> 与 <img src=x onerror=alert(1)>，公式 $x$。",
            formula=self.resolver())
        self.assertNotIn("<script", frag)
        self.assertIn("&lt;script&gt;", frag, "文档里的标签应当被转义成文本")
        self.assertEqual(frag.count("<img"), 1, "页面里只应该有我们自己生成的公式图片")
        self.assertIn("formula-img", frag)

    def test_a_document_that_already_contains_the_sentinel_still_works(self):
        from mdreader import render
        calls = []
        source = "正文里就有 @@MDFORMULA0@@ 这样的字样，公式 $x$。"
        frag, _meta, _engine = render.render_markdown(source, formula=self.resolver(calls))
        self.assertEqual([tex for tex, _display in calls], ["x"])
        self.assertIn("@@MDFORMULA0@@", frag, "文档里原有的字样不能被当成公式占位符")
        self.assertEqual(frag.count("<img"), 1)

    def test_rendering_without_a_resolver_leaves_the_dollars_alone(self):
        from mdreader import render
        frag, _meta, _engine = render.render_markdown("行内 $x^{2}$ 结束。\n")
        self.assertNotIn("<img", frag)
        self.assertIn("$x^{2}$", frag)


class FormulaCacheDirTests(unittest.TestCase):
    def test_cache_directory_lives_in_the_workspace(self):
        from mdreader import core
        self.assertEqual(core.formula_cache_dir(r"C:\ws"),
                         os.path.join(r"C:\ws", core.FORMULA_DIRNAME))

    def test_resolver_returns_urls_for_reading_and_data_uris_for_export(self):
        from mdreader import core
        with tempfile.TemporaryDirectory(prefix="mdreader-formula-") as root:
            reading = core.formula_resolver(root, theme="light")
            exported = core.formula_resolver(root, theme="light", inline=True)
            online = reading("x^{2}", False)
            offline = exported("x^{2}", False)
            self.assertTrue(online["ok"] and offline["ok"])
            self.assertTrue(online["src"].startswith("/api/formula?"))
            self.assertIn("theme=light", online["src"])
            self.assertTrue(offline["src"].startswith("data:image/png;base64,"))
            self.assertGreater(online["width"], 0)

    def test_resolver_reports_a_broken_expression(self):
        from mdreader import core
        with tempfile.TemporaryDirectory(prefix="mdreader-formula-") as root:
            result = core.formula_resolver(root)(r"\foo{x}", False)
            self.assertFalse(result["ok"])
            self.assertIn("\\foo", result["reason"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
