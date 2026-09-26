# -*- coding: utf-8 -*-
"""当前文档的链接检查（F09）。

只做**当前这一篇**：列出找不到的本地图片/文档链接、越出文档目录的引用、写法本身
不可移植的引用（绝对路径、盘符），以及指向不存在锚点的站内跳转。不扫描全盘、不自动
改写任何文档——每条问题都给出**行号**，界面据此定位源码；要换文件也是用户自己选。

与导出的预检（`exporting.preflight`）共用同一套“相对路径 → 授权范围内的真实路径”
的读取器，所以“导出时说缺图、检查时说没问题”这种自相矛盾不会出现。
"""
from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlsplit

#: 问题的级别：error 是“点了也没用”，warn 是“大概率是笔误”。
LEVEL_ERROR = "error"
LEVEL_WARN = "warn"

_IMAGE = re.compile(r'!\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]*)')
_LINK = re.compile(r'(?<!!)\[([^\]\n]*(?:\[[^\]\n]*\][^\]\n]*)*)\]\(\s*(<[^>]*>|[^)\s]*)')
_HEADING = re.compile(r'^[ \t]{0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$')
_FENCE = re.compile(r'^[ \t]{0,3}(`{3,}|~{3,})')


def heading_anchors(markdown: str) -> set:
    """文档里可跳转的锚点。

    与 `render.render_markdown` 的规则保持一致（那里是唯一的渲染出口）；用例会拿
    渲染结果里的 ``id=`` 反过来核对这一份，防止两边慢慢漂移。
    """
    anchors, seen, counts = set(), set(), {}
    fence = None
    for line in (markdown or "").split("\n"):
        marker = _FENCE.match(line)
        if marker:
            token = marker.group(1)[0] * 3
            if fence is None:
                fence = token
            elif token == fence:
                fence = None
            continue
        if fence is not None:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        plain = re.sub(r'<[^>]+>', '', match.group(2))
        plain = re.sub(r'[*_`~]', '', plain)
        base = re.sub(r'[^\w\u4e00-\u9fff-]+', '-', plain.lower()).strip('-')[:64] or 'h'
        suffix = counts.get(base, 0)
        slug = base if not suffix else '%s-%d' % (base, suffix + 1)
        while slug in seen:
            suffix += 1
            slug = '%s-%d' % (base, suffix + 1)
        counts[base] = suffix + 1
        seen.add(slug)
        anchors.add(slug)
    return anchors


def _line_of(text: str, offset: int) -> tuple:
    prefix = text[:offset]
    return prefix.count("\n") + 1, offset - (prefix.rfind("\n") + 1)


def _is_external(source: str) -> bool:
    parsed = urlsplit(source or "")
    return bool(parsed.scheme or parsed.netloc) or (source or "").startswith("//")


def _looks_absolute(source: str) -> bool:
    raw = source or ""
    return bool(re.match(r'^[A-Za-z]:[\\/]', raw)) or raw.startswith(("/", "\\"))


def _skip_ranges(markdown: str):
    """围栏代码块与行内代码的范围（这两处的“链接”只是文本）。"""
    ranges = []
    position = 0
    fence = None
    for line in markdown.split("\n"):
        end = position + len(line)
        marker = _FENCE.match(line)
        if marker:
            token = marker.group(1)[0] * 3
            if fence is None:
                fence = token
                start = position
            elif token == fence:
                fence = None
                ranges.append((start, end))
        position = end + 1
    if fence is not None:
        ranges.append((start, len(markdown)))
    for match in re.finditer(r'(`+)(.+?)\1', markdown, re.S):
        ranges.append((match.start(), match.end()))
    return ranges


def _in_ranges(offset: int, ranges) -> bool:
    return any(start <= offset < end for start, end in ranges)


def check(markdown: str, *, identify=None, anchors=None) -> dict:
    """检查一篇文档里的引用。

    ``identify(source) -> path``：把相对路径解析成真实路径（越界抛 ``PermissionError``，
    不存在抛 ``FileNotFoundError``）。为 ``None`` 时只做“写法”层面的检查。
    """
    markdown = markdown or ""
    anchors = heading_anchors(markdown) if anchors is None else set(anchors)
    skip = _skip_ranges(markdown)
    issues: list[dict] = []
    checked = 0

    def add(kind, level, source, offset, message, *, action="locate", target=""):
        line, column = _line_of(markdown, offset)
        issues.append({"kind": kind, "level": level, "source": source, "line": line,
                       "column": column, "message": message, "action": action,
                       "target": target})

    for pattern, kind in ((_IMAGE, "image"), (_LINK, "link")):
        for match in pattern.finditer(markdown):
            if _in_ranges(match.start(), skip):
                continue
            source = match.group(2).strip("<>")
            if not source:
                add(kind, LEVEL_ERROR, source, match.start(), "引用是空的")
                continue
            if source.startswith("#"):
                checked += 1
                if source[1:] not in anchors:
                    add(kind, LEVEL_WARN, source, match.start(),
                        "跳转的标题在本文档里不存在：%s" % source)
                continue
            # 盘符（C:\…）在 urlsplit 眼里像个协议，所以先判“绝对路径”再判“外部地址”
            if _looks_absolute(source):
                checked += 1
                add(kind, LEVEL_ERROR, source, match.start(),
                    "这是绝对路径，换台电脑或换个目录就会失效")
                continue
            if _is_external(source):
                continue                        # 外部地址不检查、也不抓取
            checked += 1
            if identify is None:
                continue
            try:
                path = identify(source)
            except FileNotFoundError:
                add(kind, LEVEL_ERROR, source, match.start(),
                    "找不到这个文件：%s" % unquote(source),
                    action="choose" if kind == "image" else "locate")
                continue
            except PermissionError:
                add(kind, LEVEL_ERROR, source, match.start(),
                    "这个引用越出了文档所在目录：%s" % unquote(source))
                continue
            except Exception as error:
                add(kind, LEVEL_ERROR, source, match.start(),
                    "读不出这个引用（%s）：%s" % (type(error).__name__, error))
                continue
            if path and kind == "link" and os.path.splitext(path)[1].lower() not in (
                    ".md", ".markdown", ".mdown", ".mkd", ".txt"):
                add(kind, LEVEL_WARN, source, match.start(),
                    "指向的不是 Markdown 文档：%s" % unquote(source))

    errors = [item for item in issues if item["level"] == LEVEL_ERROR]
    warnings = [item for item in issues if item["level"] == LEVEL_WARN]
    return {"ok": not issues, "issues": issues, "errors": errors, "warnings": warnings,
            "checked": checked,
            "summary": ("没有发现问题" if not issues else
                        "有 %d 个必须处理的问题" % len(errors) if errors else
                        "有 %d 条提示" % len(warnings))}


def relative_target(document: str, chosen: str) -> str:
    """用户重新选择的文件 → 相对文档目录的引用写法（统一用 /）。"""
    rel = os.path.relpath(os.path.abspath(chosen), os.path.dirname(os.path.abspath(document)))
    return rel.replace(os.sep, "/")


def replace_reference(markdown: str, source: str, replacement: str) -> dict:
    """把某一处引用换成新的地址（只改正文，落盘由调用方按自己的保存流程做）。

    只替换**完全等于** ``source`` 的引用；同一地址出现多次时全部替换（它们本来就是
    同一个文件）。返回改了第几行，便于界面把光标放过去。
    """
    if not source or not replacement:
        return {"ok": False, "reason": "缺少要替换的引用"}
    escaped = re.escape(source)
    pattern = re.compile(r'(?<!\!)\[([^\]\n]*)\]\(\s*%s\s*\)|!\[([^\]]*)\]\(\s*%s\s*\)'
                         % (escaped, escaped))
    hits = []
    skip = _skip_ranges(markdown)

    def repl(match):
        if _in_ranges(match.start(), skip):
            return match.group(0)
        hits.append(match.start())
        if match.group(1) is not None:
            return "[%s](%s)" % (match.group(1), replacement)
        return "![%s](%s)" % (match.group(2), replacement)

    updated = pattern.sub(repl, markdown)
    if not hits:
        return {"ok": False, "reason": "正文里已经没有这个引用了：%s" % source}
    line = _line_of(updated, hits[0])[0]
    return {"ok": True, "markdown": updated, "replaced": len(hits), "line": line,
            "source": source, "target": replacement}
