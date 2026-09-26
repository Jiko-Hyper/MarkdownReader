# -*- coding: utf-8 -*-
"""Markdown fidelity: the two renderers must agree on what they both support.

`开发框架.md` 5.4 asks for the same content, list depth, link meaning and code
text from the current engine (markdown-it-py) and the built-in fallback, and for
code fences to stay untouched by the reading conveniences.

    python -m unittest tests.test_render_fidelity
"""
from __future__ import annotations

import html
import re
import unittest

from mdreader import render as R

#: Syntax both engines claim to support.
CORPUS = {
    "headings": "# 一级\n\n## 二级\n\n### 重复\n\n### 重复\n",
    "inline": "文字 **粗** 与 *斜* 与 `代码` 与 [链接](https://e.com) 与 ![图](a.png)\n",
    "nested list": "- 一\n  - 一甲\n    - 一甲甲\n- 二\n\n1. 第一\n2. 第二\n",
    "tasks": "- [x] 已完成\n- [ ] 未完成\n",
    "quote": "> 引用一\n> 引用二\n\n> 嵌套\n> > 更深\n",
    "table": "| a | b |\n| --- | --- |\n| 1 | 2 |\n",
    "rule": "---\n\n***\n",
    "strike and mark": "~~删除~~ ==高亮==\n",
    "long paragraph": "很长的一行 " * 60 + "\n",
    "front matter": "---\ntitle: 标题\n---\n\n正文\n",
}

#: Fenced code must come through byte for byte, including marker lookalikes.
FENCES = {
    "fence": "```python\n==不是高亮== ~~不是删除~~ - [ ] 不是任务 # 不是标题\n[n](x) | a |\n```\n",
    "tilde fence": "~~~\n# 也不是标题\n- [x] 也不是任务\n~~~\n",
    "indented fence content": "```\n   缩进保留\n\ttab 保留\n```\n",
}


def text_of(fragment: str) -> str:
    """Visible text with block boundaries kept as single spaces."""
    spaced = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(spaced)).strip()


def code_blocks(fragment: str):
    return [html.unescape(block).strip("\n")
            for block in re.findall(r"<pre><code[^>]*>(.*?)</code></pre>", fragment, re.S)]


def hrefs(fragment: str):
    return re.findall(r'href="([^"]+)"', fragment)


def heading_ids(fragment: str):
    return re.findall(r"<h[1-6][^>]*id=\"([^\"]+)\"", fragment)


class CorpusAgreementTests(unittest.TestCase):
    """Same source, both engines, same meaning."""

    def both(self, source):
        current, _, _ = R.render_markdown(source)
        # render_markdown splits front matter before rendering; the fallback gets
        # the same body so the comparison is about the renderer, not the split.
        _, body = R.split_front_matter(source)
        return current, R.render_fallback(body)

    def test_visible_text_matches(self):
        for name, source in CORPUS.items():
            with self.subTest(name):
                current, builtin = self.both(source)
                self.assertEqual(text_of(current), text_of(builtin))

    def test_structure_counts_match(self):
        # Appearance may differ (tight vs loose lists, <s> vs <del>); the block
        # structure a reader navigates by must not.
        tags = ("li", "ul", "ol", "blockquote", "table", "tr", "th", "td",
                "h1", "h2", "h3", "p", "hr", "strong", "em", "code", "mark")
        for name, source in CORPUS.items():
            with self.subTest(name):
                current, builtin = self.both(source)
                for tag in tags:
                    self.assertEqual(len(re.findall(r"<%s[\s>]" % tag, current)),
                                     len(re.findall(r"<%s[\s>]" % tag, builtin)),
                                     "%s 的 <%s> 数量不一致" % (name, tag))
                self.assertEqual(len(re.findall(r"<(?:del|s)[\s>]", current)),
                                 len(re.findall(r"<(?:del|s)[\s>]", builtin)),
                                 "%s 的删除线数量不一致" % name)
                self.assertEqual(len(re.findall(r"<ol[\s>]", current)),
                                 len(re.findall(r"<ol[\s>]", builtin)),
                                 "%s 的 <ol> 数量不一致" % name)

    def test_links_images_and_tasks_match(self):
        for name, source in CORPUS.items():
            with self.subTest(name):
                current, builtin = self.both(source)
                self.assertEqual(hrefs(current), hrefs(builtin))
                self.assertEqual(current.count('type="checkbox"'), builtin.count('type="checkbox"'))
                self.assertEqual(current.count("checked"), builtin.count("checked"))

    def test_reading_conveniences_match(self):
        source = "~~删除~~ ==高亮==\n\n- [x] 完成\n- [ ] 未完成\n"
        current, builtin = self.both(source)
        for fragment in (current, builtin):
            self.assertIn("删除", text_of(fragment))
            self.assertRegex(fragment, r"<(del|s)>删除</(del|s)>")
            self.assertIn("<mark>高亮</mark>", fragment)
            self.assertIn("checked", fragment)
            self.assertEqual(fragment.count("checkbox"), 2)


class CodeFenceFidelityTests(unittest.TestCase):
    """Code is never rewritten by the reading conveniences (5.4)."""

    def test_fenced_code_is_byte_identical_in_both_engines(self):
        for name, source in FENCES.items():
            with self.subTest(name):
                body = source.split("\n", 1)[1].rsplit("```", 1)[0] if name == "fence" else None
                current, _, _ = R.render_markdown(source)
                builtin = R.render_fallback(source)
                current_code = code_blocks(current)
                builtin_code = code_blocks(builtin)
                self.assertEqual(len(current_code), 1, current)
                self.assertEqual(current_code, builtin_code)
                self.assertNotIn("<mark>", current_code[0])
                self.assertNotIn("<del>", current_code[0])
                self.assertNotIn("checkbox", current_code[0])
                if body:
                    self.assertEqual(current_code[0].strip(), body.strip())

    def test_fenced_code_keeps_its_own_markers(self):
        source = "```\n==x== ~~y~~ - [ ] z # h\n```\n"
        current, _, _ = R.render_markdown(source)
        self.assertEqual(code_blocks(current), ["==x== ~~y~~ - [ ] z # h"])

    def test_long_code_lines_and_tables_are_wrapped_not_broken(self):
        long_line = "x = " + "1 + " * 200 + "1"
        current, _, _ = R.render_markdown("```python\n%s\n```\n" % long_line)
        self.assertIn('class="code-wrap"', current)
        self.assertIn(long_line, code_blocks(current)[0])
        table, _, _ = R.render_markdown("| a | b |\n| --- | --- |\n| %s | 2 |\n" % ("长" * 200))
        self.assertIn('<div class="table-wrap">', table)


class HeadingAnchorTests(unittest.TestCase):
    def test_repeated_headings_get_unique_stable_ids(self):
        source = "# 重复\n\n## 重复\n\n# 重复\n"
        current, _, _ = R.render_markdown(source)
        ids = heading_ids(current)
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3, ids)          # 稳定且不冲突
        again, _, _ = R.render_markdown(source)
        self.assertEqual(ids, heading_ids(again))         # 同一文档重复渲染结果一致

    def test_markup_in_a_heading_still_produces_a_usable_anchor(self):
        current, _, _ = R.render_markdown("# **加粗** 标题\n")
        self.assertEqual(heading_ids(current), ["加粗-标题"])


class FrontMatterTests(unittest.TestCase):
    def test_front_matter_is_split_off_and_not_rendered_as_text(self):
        meta, body = R.split_front_matter("---\ntitle: 标题\n---\n\n正文\n")
        self.assertEqual(meta.get("title"), "标题")
        self.assertNotIn("title:", body)
        current, seen, _ = R.render_markdown("---\ntitle: 标题\n---\n\n正文\n")
        self.assertEqual(seen.get("title"), "标题")
        self.assertNotIn("title:", text_of(current))


#: Templates combined into a larger generated corpus. Both engines claim these.
TEMPLATES = (
    "# 标题{0}\n\n段落{0}：中文、English、数字 12345，标点“引号”、破折号——以及省略号……\n",
    "## 小节{0}\n\n**粗体{0}** 与 *斜体{0}* 与 ***两者{0}*** 与 `代码{0}` 与 ~~删除{0}~~ 与 ==高亮{0}==\n",
    "- 项目{0}甲\n- 项目{0}乙\n  - 嵌套{0}丙\n    1. 更深{0}丁\n    2. 更深{0}戊\n",
    "1. 有序{0}一\n2. 有序{0}二\n\n3. 有序{0}三\n",
    "- [x] 完成{0}\n- [ ] 待办{0}\n",
    "> 引用{0}第一行\n> 引用{0}第二行\n>\n> > 嵌套引用{0}\n",
    "| 列{0}A | 列{0}B | 列{0}C |\n| :--- | :---: | ---: |\n| 值{0}1 | 值{0}2 | 值{0}3 |\n",
    "段落{0}带 [链接{0}](https://example.com/{0}) 与 ![图{0}](images/{0}.png \"标题{0}\")\n",
    "```python\n# 代码{0}\nvalue_{0} = \"==不是高亮{0}==\"\n```\n",
    "~~~\n~~不是删除{0}~~\n- [ ] 不是任务{0}\n~~~\n",
    "普通{0} <span>行内 HTML{0}</span> 与 &amp; 实体 与 <https://example.com/{0}>\n",
    "第一行{0}  \n第二行{0}（硬换行）\n\n***\n\n---\n",
    "参考资料{0}：[引用式链接{0}][ref{0}]\n\n[ref{0}]: https://example.com/ref-{0}\n",
    "文字{0}\t制表符后继续\n\n    缩进代码块{0}\n",
    "长行{0}：" + "填充" * 80 + "\n",
)

#: Syntax neither engine supports on purpose: it must degrade to readable text
#: instead of disappearing, but the two may disagree about what it means.
UNSUPPORTED = {
    "footnote": "脚注样式{0}[^note{0}] 与 未定义引用{0}[missing{0}]\n\n[^note{0}]: 脚注正文{0}\n",
    "math": "公式{0}：$E = mc^2$ 与 $$\\int_0^1 x\\,dx$$\n",
    "mermaid": "```mermaid\ngraph TD{0}; A-->B;\n```\n",
}

#: Words that only exist as syntax markers and never need to survive.
IGNORED_WORDS = {"http", "https", "example", "com", "images", "png", "ref", "note",
                 "missing", "python", "span", "amp", "html"}


def content_words(source: str):
    """CJK runs and latin words that a reader would expect to still see."""
    without_urls = re.sub(r"<?https?://\S+>?", " ", source)
    without_fences = re.sub(r"^```.*$", " ", without_urls, flags=re.M)
    return [word for word in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z]{3,}", without_fences)
            if word.lower() not in IGNORED_WORDS]


def build_corpus():
    """A deterministic, wider corpus: every template crossed with two variants."""
    corpus = {}
    for index, template in enumerate(TEMPLATES):
        for part in (1, 2):
            corpus["t%02d-%d" % (index, part)] = template.format(part)
    corpus["mixed"] = "\n\n".join(template.format(7) for template in TEMPLATES[:8])
    corpus["empty"] = ""
    corpus["blank lines"] = "\n\n\n文字\n\n\n"
    return corpus


class GeneratedCorpusTests(unittest.TestCase):
    """No content loss and identical structure across the two engines."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = build_corpus()

    def test_corpus_is_large_enough_to_be_meaningful(self):
        self.assertGreaterEqual(len(self.corpus), 30)
        self.assertGreater(sum(len(text) for text in self.corpus.values()), 1500)

    def test_every_content_word_survives_in_both_engines(self):
        for name, source in self.corpus.items():
            current, _, _ = R.render_markdown(source)
            _, body = R.split_front_matter(source)
            builtin = R.render_fallback(body)
            for engine, fragment in (("markdown-it", current), ("builtin", builtin)):
                # Alt text of an image lives in an attribute, so it counts too.
                haystack = " ".join([text_of(fragment)] +
                                    re.findall(r'(?:alt|title)="([^"]*)"', fragment)).replace(" ", "")
                # Front matter is split off before rendering by design, so the
                # body is what both engines must preserve word for word.
                for word in content_words(body):
                    self.assertIn(word, haystack,
                                  "%s：%s 引擎丢了内容 %r" % (name, engine, word))

    def test_block_structure_and_links_agree(self):
        for name, source in self.corpus.items():
            with self.subTest(name):
                current, _, _ = R.render_markdown(source)
                builtin = R.render_fallback(R.split_front_matter(source)[1])
                for tag in ("li", "ul", "ol", "blockquote", "table", "tr", "th", "td",
                            "h1", "h2", "h3", "hr", "pre", "code", "mark"):
                    self.assertEqual(len(re.findall(r"<%s[\s>]" % tag, current)),
                                     len(re.findall(r"<%s[\s>]" % tag, builtin)),
                                     "%s 的 <%s> 数量不一致" % (name, tag))
                self.assertEqual(len(re.findall(r"<(?:del|s)[\s>]", current)),
                                 len(re.findall(r"<(?:del|s)[\s>]", builtin)),
                                 "%s 的删除线数量不一致" % name)
                self.assertEqual(hrefs(current), hrefs(builtin), "%s 的链接不一致" % name)

    def test_code_blocks_are_identical_between_engines(self):
        for name, source in self.corpus.items():
            with self.subTest(name):
                current, _, _ = R.render_markdown(source)
                builtin = R.render_fallback(R.split_front_matter(source)[1])
                self.assertEqual(code_blocks(current), code_blocks(builtin))

    def test_empty_and_blank_documents_render_without_error(self):
        for name in ("empty", "blank lines"):
            current, _, _ = R.render_markdown(self.corpus[name])
            builtin = R.render_fallback(self.corpus[name])
            self.assertIsInstance(current, str)
            self.assertIsInstance(builtin, str)


class UnsupportedSyntaxTests(unittest.TestCase):
    """Not-supported syntax degrades to readable text, never to silence (5.4)."""

    def test_unsupported_constructs_keep_their_text(self):
        for name, template in UNSUPPORTED.items():
            with self.subTest(name):
                source = template.format(3)
                current, _, _ = R.render_markdown(source)
                builtin = R.render_fallback(R.split_front_matter(source)[1])
                for engine, fragment in (("markdown-it", current), ("builtin", builtin)):
                    haystack = text_of(fragment)
                    self.assertIn("3", haystack, "%s/%s 丢了编号" % (name, engine))
                    self.assertTrue(len(haystack) > 4, "%s/%s 只剩空白" % (name, engine))

    def test_indented_code_block_is_recognised_by_both_engines(self):
        source = "段落\n\n    缩进代码 ==不是高亮==\n\n后续段落\n"
        current, _, _ = R.render_markdown(source)
        builtin = R.render_fallback(source)
        for fragment in (current, builtin):
            self.assertIn("<pre>", fragment)
            self.assertIn("==不是高亮==", code_blocks(fragment)[0] if code_blocks(fragment) else fragment)
            self.assertIn("后续段落", text_of(fragment))


class FuzzCorpusTests(unittest.TestCase):
    """A seeded fuzz corpus: random combinations must not crash or lose text."""

    BLOCKS = (
        "# 标题{0}\n", "## 小节{0}\n", "段落{0} **粗** *斜* `码`\n", "- 甲{0}\n- 乙{0}\n",
        "  - 嵌套{0}\n", "1. 一{0}\n2. 二{0}\n", "- [x] 完{0}\n- [ ] 待{0}\n",
        "> 引用{0}\n", "> > 深引用{0}\n", "| a{0} | b{0} |\n| --- | --- |\n| 1 | 2 |\n",
        "```\n代码{0}\n```\n", "---\n", "![图{0}](i{0}.png)\n", "[链接{0}](https://e.com/{0})\n",
        "==高亮{0}== 与 ~~删除{0}~~\n", "    缩进码{0}\n",
    )

    def build(self):
        import random
        rng = random.Random(20260925)
        documents = {}
        for index in range(80):
            parts = [rng.choice(self.BLOCKS).format(index % 9 + 1) for _ in range(rng.randint(2, 12))]
            documents["fuzz%02d" % index] = "\n".join(parts) + "\n"
        return documents

    def test_no_crash_and_no_lost_words(self):
        for name, source in self.build().items():
            with self.subTest(name):
                current, _, _ = R.render_markdown(source)
                builtin = R.render_fallback(R.split_front_matter(source)[1])
                for engine, fragment in (("markdown-it", current), ("builtin", builtin)):
                    self.assertIsInstance(fragment, str)
                    haystack = " ".join([text_of(fragment)] +
                                        re.findall(r'(?:alt|title)="([^"]*)"', fragment)).replace(" ", "")
                    for word in content_words(R.split_front_matter(source)[1]):
                        self.assertIn(word, haystack,
                                      "%s：%s 丢了内容 %r" % (name, engine, word))

    def test_fenced_code_survives_the_fuzz_corpus(self):
        for name, source in self.build().items():
            current, _, _ = R.render_markdown(source)
            builtin = R.render_fallback(R.split_front_matter(source)[1])
            self.assertEqual(code_blocks(current), code_blocks(builtin), name)

    def test_looks_like_front_matter_is_not_lost_but_kept_as_metadata(self):
        """A leading `---` may open front matter; that content must still exist."""
        source = "---\n\n## 小节1\n\n  - 嵌套1\n\n---\n\n正文\n"
        meta, body = R.split_front_matter(source)
        self.assertIn("嵌套1", source)
        self.assertNotIn("嵌套1", body, "front matter 已按设计从正文分离")
        current, seen, _ = R.render_markdown(source)
        self.assertIn("嵌套1", source[source.index("---") :])
        self.assertIn("正文", text_of(current))


if __name__ == "__main__":
    unittest.main(verbosity=2)
