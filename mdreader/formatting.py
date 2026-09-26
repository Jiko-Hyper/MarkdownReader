# -*- coding: utf-8 -*-
"""常用格式工具栏背后的文本规则（桌面端与网页端共用）。

任务书 F04 要求“已有选区按规则包装，无选区插入模板”，并且**所有操作走同一条
编辑命令与撤销通道**。所以这里只做一件事：给定 ``(正文, 选区, 动作)`` 算出
``(新正文, 新选区)``；写回控件（一次撤销）、输入法组合期间不打扰用户，都由
两端界面负责。

规则是刻意“可预测”的，而不是聪明的：

* 包裹类（粗体、斜体、行内代码、链接）在选区上包裹；**再次执行同一动作会取消
  包裹**（切换），这样工具栏按钮不会越点越乱。
* 行级类（标题、列表、引用）作用于选区覆盖的每一行，跳过空行，保留行首缩进；
  再执行一次取消。
* 行内代码的围栏长度按内容里最长的反引号串决定，内容以反引号或空格开头/结尾
  时按 CommonMark 补一个空格，保证渲染回来还是原文。
* 任何动作都不会碰选区之外的字节。
"""

from __future__ import annotations

import re

_LINE_OF = re.compile(r"[^\n]*\n?")

HEADING_LEVELS = (1, 2, 3, 4, 5, 6)
ACTIONS = ("bold", "italic", "strike", "code", "code_block", "link",
           "formula_inline", "formula_block",
           "heading", "bullets", "ordered", "quote")

_HEADING_RE = re.compile(r"^([ \t]*)(#{1,6})[ \t]+")
_BULLET_RE = re.compile(r"^([ \t]*)[-*+][ \t]+")
_ORDERED_RE = re.compile(r"^([ \t]*)\d+[.)][ \t]+")
_QUOTE_RE = re.compile(r"^([ \t]*)>[ \t]?")
_URL_RE = re.compile(r"^(?:https?://|ftp://|www\.|mailto:)\S+$|^[^\s/]+\.[a-z]{2,}(?:/\S*)?$", re.I)


class FormatError(ValueError):
    """动作或参数不认识（属于编程错误，界面只允许传固定动作名）。"""


def line_span(text: str, start: int, end: int) -> tuple[int, int]:
    """选区覆盖的行范围 ``(首行, 末行)``（末行含）。

    选区正好停在行首时不算进末行，否则“选中一段”会顺手改到下面那一行。
    """
    start = max(0, min(len(text), int(start)))
    end = max(start, min(len(text), int(end)))
    first = text.count("\n", 0, start)
    if end > start and end <= len(text) and text[end - 1] == "\n":
        end -= 1
    last = text.count("\n", 0, end)
    return first, last


def _offset_of_line(text: str, line: int) -> int:
    if line <= 0:
        return 0
    position = -1
    for _ in range(line):
        position = text.find("\n", position + 1)
        if position < 0:
            return len(text)
    return position + 1


def _line_bounds(text: str, line: int) -> tuple[int, int]:
    """第 ``line`` 行的 ``[起, 止)``（不含换行符）。"""
    start = _offset_of_line(text, line)
    end = text.find("\n", start)
    return start, (len(text) if end < 0 else end)


def _replace_span(text: str, start: int, end: int, replacement: str) -> str:
    return text[:start] + replacement + text[end:]


# --------------------------------------------------------------------------
# 包裹类
# --------------------------------------------------------------------------

def _wrapped(text: str, start: int, end: int, marker: str) -> bool:
    """选区两侧（或选区本身）已经带着 ``marker``。"""
    if text[max(0, start - len(marker)):start] == marker and text[end:end + len(marker)] == marker:
        return True
    return text[start:end].startswith(marker) and text[start:end].endswith(marker) \
        and len(text[start:end]) >= 2 * len(marker)


def wrap(text: str, start: int, end: int, marker: str, placeholder: str = "") -> dict:
    """用 ``marker`` 包裹选区；已包裹则取消；空选区插入 ``marker+placeholder+marker``。"""
    start, end = max(0, min(len(text), start)), max(0, min(len(text), end))
    if _wrapped(text, start, end, marker):
        if text[max(0, start - len(marker)):start] == marker and text[end:end + len(marker)] == marker:
            inner = text[start:end]
            new = _replace_span(text, start, end + len(marker), inner)
            new = _replace_span(new, start - len(marker), start, "")
            return {"ok": True, "text": new, "start": start - len(marker), "end": end - len(marker),
                    "note": "已取消格式"}
        inner = text[start + len(marker):end - len(marker)]
        return {"ok": True, "text": _replace_span(text, start, end, inner),
                "start": start, "end": end - 2 * len(marker), "note": "已取消格式"}
    if start == end:
        body = marker + placeholder + marker
        return {"ok": True, "text": _replace_span(text, start, end, body),
                "start": start + len(marker), "end": start + len(marker) + len(placeholder),
                "note": "已插入格式模板"}
    return {"ok": True, "text": _replace_span(text, start, end, marker + text[start:end] + marker),
            "start": start + len(marker), "end": end + len(marker), "note": "已应用格式"}


def code_span(text: str, start: int, end: int) -> dict:
    """行内代码：围栏长度按内容里最长的反引号串决定，必要时补空格。"""
    start, end = max(0, min(len(text), start)), max(0, min(len(text), end))
    if start == end:
        return wrap(text, start, end, "`")
    inner = text[start:end]
    if _wrapped(text, start, end, "`"):
        return wrap(text, start, end, "`")
    fence = "`" * (max((len(run) for run in re.findall(r"`+", inner)), default=0) + 1)
    pad = " " if inner.startswith(("`", " ")) or inner.endswith(("`", " ")) else ""
    body = fence + pad + inner + pad + fence
    return {"ok": True, "text": _replace_span(text, start, end, body),
            "start": start + len(fence) + len(pad), "end": start + len(fence) + len(pad) + len(inner),
            "note": "已应用行内代码"}


def link(text: str, start: int, end: int, url: str = "") -> dict:
    """``[说明](地址)``；选中的是网址就把它放到地址位。"""
    start, end = max(0, min(len(text), start)), max(0, min(len(text), end))
    inner = text[start:end]
    if not inner and not url:
        body = "[说明](https://)"
        return {"ok": True, "text": _replace_span(text, start, end, body),
                "start": start + 1, "end": start + 3, "note": "已插入链接模板"}
    if not url and _URL_RE.match(inner.strip()):
        filled = "[说明](%s)" % inner.strip()
        return {"ok": True, "text": _replace_span(text, start, end, filled),
                "start": start + 1, "end": start + 3, "note": "已把选中的网址放进链接"}
    target = url.strip() if url.strip() else "https://"
    label = inner or "说明"
    body = "[%s](%s)" % (label, target)
    if inner:
        return {"ok": True, "text": _replace_span(text, start, end, body),
                "start": start + 1, "end": start + 1 + len(label), "note": "已应用链接"}
    return {"ok": True, "text": _replace_span(text, start, end, body),
            "start": start + 1, "end": start + 1 + len(label), "note": "已插入链接模板"}


def code_block(text: str, start: int, end: int, language: str = "") -> dict:
    """把选区变成围栏代码块（选区为空则插入一个空块）。"""
    first, last = line_span(text, start, end)
    begin, finish = _line_bounds(text, first)
    _last_begin, last_end = _line_bounds(text, last)
    body = text[begin:last_end]
    if body.startswith("```"):
        stripped = body.split("\n", 1)
        if stripped[0].strip() in ("```", "```" + language.strip()) and body.rstrip().endswith("```"):
            inner = body.split("\n", 1)[1].rsplit("```", 1)[0]
            if inner.endswith("\n"):
                inner = inner[:-1]
            return {"ok": True, "text": _replace_span(text, begin, last_end, inner),
                    "start": begin, "end": begin + len(inner), "note": "已取消代码块"}
    fence = "```"
    while fence in body:
        fence += "`"
    block = "%s%s\n%s\n%s" % (fence, language.strip(), body, fence)
    return {"ok": True, "text": _replace_span(text, begin, last_end, block),
            "start": begin + len(fence) + len(language.strip()) + 1, "end": begin + len(block), "note": "已应用代码块"}


def formula_inline(text: str, start: int, end: int) -> dict:
    """Wrap one expression, or insert a visible editable example."""
    if "\n" in text[start:end]:
        return {"ok": False, "reason": "行内公式不能跨行，请改用独立公式"}
    result = wrap(text, start, end, "$", "x" if start == end else "")
    result["note"] = "已插入行内公式；选中表达式可直接修改" if result["note"] != "已取消格式" else "已取消行内公式"
    return result


def formula_block(text: str, start: int, end: int) -> dict:
    """Insert a block with Markdown paragraph boundaries and select the TeX."""
    selected = text[start:end].strip("\n") or "\\frac{a}{b}"
    prefix = "" if start == 0 else ("\n" if text[:start].endswith("\n") else "\n\n")
    suffix = "" if end == len(text) else ("\n" if text[end:].startswith("\n") else "\n\n")
    block = prefix + "$$\n" + selected + "\n$$" + suffix
    at = start + len(prefix) + 3
    return {"ok": True, "text": _replace_span(text, start, end, block),
            "start": at, "end": at + len(selected),
            "note": "已插入独立公式；选中表达式可直接修改"}


# --------------------------------------------------------------------------
# 行级
# --------------------------------------------------------------------------

def _target_lines(text: str, start: int, end: int) -> list[int]:
    first, last = line_span(text, start, end)
    return list(range(first, last + 1))


def _line_texts(text: str, lines: list[int]) -> list[str]:
    out = []
    for line in lines:
        begin, finish = _line_bounds(text, line)
        out.append(text[begin:finish])
    return out


def _apply_prefix(text: str, lines: list[int], pattern: re.Pattern, prefix: str,
                  *, numbered: bool = False) -> dict:
    """给这些行加/去掉一种前缀；整段都已经带前缀时取消。

    选中的行全是空行时，仍然给第一行加前缀：在空行上按「列表」应当得到一个
    可以接着写的 ``- ``，而不是一句“没有可修改的内容”。
    """
    texts = _line_texts(text, lines)
    live = [index for index, value in enumerate(texts) if value.strip()]
    if not live:
        live = [0]
    already = all(pattern.match(texts[index]) for index in live)
    edits: list[tuple[int, int, str]] = []
    for index in live:
        line = lines[index]
        begin, finish = _line_bounds(text, line)
        value = texts[index]
        match = pattern.match(value)
        indent = match.group(1) if match else re.match(r"^([ \t]*)", value).group(1)
        if already:
            edits.append((begin, finish, indent + value[match.end():]))
        else:
            body = value[match.end():] if match else value[len(indent):]
            marker = "%d. " % (index - live[0] + 1) if numbered else prefix
            edits.append((begin, finish, indent + marker + body))
    new = text
    for begin, finish, replacement in reversed(edits):
        new = _replace_span(new, begin, finish, replacement)
    first, last = lines[0], lines[-1]
    new_begin, _ = _line_bounds(new, first)
    _last_begin, new_end = _line_bounds(new, last)
    return {"ok": True, "text": new, "start": new_begin, "end": new_end,
            "note": "已取消格式" if already else "已应用格式"}


def heading(text: str, start: int, end: int, level: int = 1) -> dict:
    """把选中的行设成第 ``level`` 级标题；整段已经是这一级时取消。"""
    level = int(level)
    if level not in HEADING_LEVELS:
        raise FormatError("标题级别只能是 1–6")
    lines = _target_lines(text, start, end)
    texts = _line_texts(text, lines)
    live = [index for index, value in enumerate(texts) if value.strip()]
    if not live:
        live = [0]
    marks = [_HEADING_RE.match(texts[index]) for index in live]
    applied = all(mark and len(mark.group(2)) == level for mark in marks)
    edits: list[tuple[int, int, str]] = []
    for index in live:
        begin, finish = _line_bounds(text, lines[index])
        value = texts[index]
        mark = _HEADING_RE.match(value)
        indent = mark.group(1) if mark else re.match(r"^([ \t]*)", value).group(1)
        body = value[mark.end():] if mark else value[len(indent):]
        replacement = indent + body if applied else indent + "#" * level + " " + body
        edits.append((begin, finish, replacement))
    new = text
    for begin, finish, replacement in reversed(edits):
        new = _replace_span(new, begin, finish, replacement)
    new_begin, _ = _line_bounds(new, lines[0])
    _last_begin, new_end = _line_bounds(new, lines[-1])
    return {"ok": True, "text": new, "start": new_begin, "end": new_end,
            "note": "已取消标题" if applied else "已应用标题"}


def bullets(text: str, start: int, end: int) -> dict:
    return _apply_prefix(text, _target_lines(text, start, end), _BULLET_RE, "- ")


def ordered(text: str, start: int, end: int) -> dict:
    return _apply_prefix(text, _target_lines(text, start, end), _ORDERED_RE, "1. ", numbered=True)


def quote(text: str, start: int, end: int) -> dict:
    return _apply_prefix(text, _target_lines(text, start, end), _QUOTE_RE, "> ")


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def apply(text: str, start: int, end: int, action: str, *, level: int = 1,
          url: str = "", language: str = "") -> dict:
    """执行一次格式动作；返回 ``{"ok", "text", "start", "end", "note"}``。"""
    if action not in ACTIONS:
        raise FormatError("不认识的格式动作：%s" % action)
    if text is None:
        text = ""
    start, end = max(0, min(len(text), int(start or 0))), max(0, min(len(text), int(end or 0)))
    if end < start:
        start, end = end, start
    if action == "bold":
        return wrap(text, start, end, "**")
    if action == "italic":
        return wrap(text, start, end, "*")
    if action == "strike":
        return wrap(text, start, end, "~~")
    if action == "code":
        return code_span(text, start, end)
    if action == "code_block":
        return code_block(text, start, end, language)
    if action == "formula_inline":
        return formula_inline(text, start, end)
    if action == "formula_block":
        return formula_block(text, start, end)
    if action == "link":
        return link(text, start, end, url)
    if action == "heading":
        return heading(text, start, end, level)
    if action == "bullets":
        return bullets(text, start, end)
    if action == "ordered":
        return ordered(text, start, end)
    return quote(text, start, end)
