# -*- coding: utf-8 -*-
"""Content policy regressions: the same rules for reading, export and the page.

    python -m unittest tests.test_content_policy
"""
from __future__ import annotations

import unittest

from mdreader import content_policy as policy


class SafeUrlTests(unittest.TestCase):
    def test_supported_schemes_are_allowed(self):
        for value in ("https://example.com/a", "http://example.com", "mailto:a@b.c",
                      "images/a.png", "#anchor", "data:image/png;base64,AAAA"):
            self.assertTrue(policy.safe_url(value), value)

    def test_dangerous_schemes_are_refused(self):
        for value in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "  javascript:alert(1)",
                      "vbscript:msgbox", "data:text/html;base64,AAAA", "\\\\server\\share",
                      "\x01javascript:alert(1)"):
            self.assertFalse(policy.safe_url(value), value)


class SanitizeTests(unittest.TestCase):
    def test_scripts_and_handlers_are_removed(self):
        dirty = '<p onclick="steal()">text</p><script>alert(1)</script><img src=x onerror=alert(1)>'
        clean = policy.sanitize(dirty)
        self.assertNotIn("<script", clean)
        self.assertNotIn("onclick", clean)
        self.assertNotIn("onerror", clean)
        self.assertIn("text", clean)

    def test_dangerous_links_lose_their_target(self):
        clean = policy.sanitize('<a href="javascript:alert(1)">点我</a>')
        self.assertNotIn("javascript:", clean)
        self.assertIn("点我", clean)

    def test_remote_images_are_not_requested_automatically(self):
        clean = policy.sanitize('<img src="https://tracker.example/pixel.png" alt="图">')
        self.assertNotIn("<img", clean)
        self.assertIn("resource-warning", clean)
        self.assertIn("图", clean)

    def test_local_images_and_safe_attributes_survive(self):
        clean = policy.sanitize('<img src="images/a.png" alt="封面"><a href="https://e.com" title="t">x</a>')
        self.assertIn('src="images/a.png"', clean)
        self.assertIn('alt="封面"', clean)
        self.assertIn('rel="noopener noreferrer"', clean)

    def test_style_is_limited_to_alignment(self):
        self.assertIn("text-align:center", policy.sanitize('<p style="text-align:center">x</p>'))
        self.assertNotIn("expression", policy.sanitize('<p style="width:expression(alert(1))">x</p>'))

    def test_unknown_tags_are_dropped_but_their_text_stays(self):
        clean = policy.sanitize('<iframe src="http://x"></iframe><marquee>热闹</marquee>')
        self.assertNotIn("iframe", clean)
        self.assertNotIn("marquee", clean)
        self.assertIn("热闹", clean)

    def test_strikethrough_survives_from_both_renderers(self):
        """markdown-it emits <s>, the builtin renderer emits <del>."""
        self.assertIn("<s>删除</s>", policy.sanitize("<p><s>删除</s></p>"))
        self.assertIn("<del>删除</del>", policy.sanitize("<p><del>删除</del></p>"))
        self.assertIn("<mark>高亮</mark>", policy.sanitize("<p><mark>高亮</mark></p>"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
