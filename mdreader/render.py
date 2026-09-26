# -*- coding: utf-8 -*-
"""
Markdown -> HTML rendering for MDReader.

Two engines:
  * "markdown-it-py"  : used when the package is importable (higher fidelity)
  * "builtin"         : pure-stdlib fallback, always available

Both produce a fragment that is wrapped by build_page() together with an
offline stylesheet (no CDN, no external fonts) so the app works with no network.
"""

from __future__ import annotations

import html
import re
from .content_policy import sanitize

try:  # optional dependency
    from markdown_it import MarkdownIt

    _MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    _MD.enable("table")
    _MD.enable("strikethrough")
    def _render_text(tokens, idx, options, env):
        value = html.escape(tokens[idx].content, quote=False)
        return re.sub(r'==(?=\S)(.+?)(?<=\S)==', r'<mark>\1</mark>', value)
    _MD.renderer.rules['text'] = _render_text
    ENGINE_NAME = "markdown-it-py"
except Exception:  # pragma: no cover - fallback path
    _MD = None
    ENGINE_NAME = "builtin"


# --------------------------------------------------------------------------
# front matter
# --------------------------------------------------------------------------

_FM_RE = re.compile(r"^\ufeff?(?:---|\+\+\+)[ \t]*\r?\n(.*?)\r?\n(?:---|\+\+\+)[ \t]*(?:\r?\n|$)", re.S)


def split_front_matter(text: str):
    """Return (meta_dict, body). Front matter is split off so it never shows up
    as raw text in the reading view, but is displayed in a small panel."""
    m = _FM_RE.match(text or "")
    if not m:
        return {}, text or ""
    raw = m.group(1)
    meta = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, text[m.end():]


# --------------------------------------------------------------------------
# builtin fallback renderer
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})[ \t]*([^\s`]*)[^\n]*$")

_ESCAPE_MAP = (("\\", "&#92;"),)
_INLINE_SPECIALS = "\\`*_{}[]()#+-.!|~"


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _inline_fallback(text: str) -> str:
    """Inline markdown for the builtin engine. Code spans are protected first."""
    placeholders = []

    def stash(html_fragment: str) -> str:
        placeholders.append(html_fragment)
        return "\x00%d\x00" % (len(placeholders) - 1)

    # 1. code spans
    def _code(m):
        return stash("<code>" + _esc(m.group(2)) + "</code>")

    text = re.sub(r"(`+)(.+?)\1", _code, text, flags=re.S)

    # 2. images then links (the optional "title" is kept, like markdown-it does)
    def _img(m):
        alt, url, title = m.group(1), m.group(2), m.group(3)
        extra = ' title="%s"' % html.escape(title, quote=True) if title else ""
        return stash(
            '<img src="%s" alt="%s"%s loading="lazy">'
            % (html.escape(url, quote=True), html.escape(alt, quote=True), extra)
        )

    def _link(m):
        label, url, title = m.group(1), m.group(2), m.group(3)
        ext = ""
        if re.match(r"^https?://", url, re.I):
            ext = ' target="_blank" rel="noopener"'
        extra = ' title="%s"' % html.escape(title, quote=True) if title else ""
        return stash('<a href="%s"%s%s>%s</a>' % (html.escape(url, quote=True), extra, ext, label))

    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)", _img, text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)", _link, text)

    # 3. autolinks
    text = re.sub(
        r"(?<![\"'>=])\b(https?://[^\s<>\)\]]+)",
        lambda m: stash('<a href="%s" target="_blank" rel="noopener">%s</a>' % (m.group(1), m.group(1))),
        text,
    )

    # 4. escape what is left, then apply emphasis on the escaped text
    text = _esc(text)
    text = text.replace("&amp;#92;", "\\")

    text = re.sub(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", r"<strong><em>\1</em></strong>", text, flags=re.S)
    text = re.sub(r"___(?=\S)(.+?)(?<=\S)___", r"<strong><em>\1</em></strong>", text, flags=re.S)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text, flags=re.S)
    text = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<strong>\1</strong>", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", text, flags=re.S)
    text = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", r"<em>\1</em>", text, flags=re.S)
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", text, flags=re.S)
    text = re.sub(r"==(?=\S)(.+?)(?<=\S)==", r"<mark>\1</mark>", text, flags=re.S)

    # 5. hard breaks
    text = text.replace("  \n", "<br>\n")

    def unstash(m):
        return placeholders[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", unstash, text)


def render_fallback(src: str) -> str:
    lines = (src or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    i = 0
    n = len(lines)

    def is_blank(k):
        return k >= n or not lines[k].strip()

    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        # fenced code
        fm = _FENCE_RE.match(line)
        if fm:
            fence = fm.group(2)
            lang = (fm.group(3) or "").strip().lower()
            i += 1
            buf = []
            while i < n and not re.match(r"^\s*" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$", lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1
            code = "\n".join(buf)
            cls = ' class="language-%s"' % html.escape(lang, quote=True) if lang else ""
            out.append(
                '<div class="code-wrap"><div class="code-head"><span class="code-lang">%s</span>'
                '<button class="copy-btn" type="button" data-copy>复制</button></div>'
                "<pre><code%s>%s</code></pre></div>" % (html.escape(lang or "text"), cls, _esc(code))
            )
            continue

        # indented code block: four spaces or a tab, and it cannot interrupt a
        # paragraph (CommonMark) — the paragraph branch below consumes those.
        if re.match(r"^(?: {4}|\t)", line):
            buf = []
            while i < n and (not lines[i].strip() or re.match(r"^(?: {4}|\t)", lines[i])):
                buf.append(re.sub(r"^(?: {4}|\t)", "", lines[i]))
                i += 1
            while buf and not buf[-1].strip():
                buf.pop()
            code = "\n".join(buf)
            out.append(
                '<div class="code-wrap"><div class="code-head"><span class="code-lang">text</span>'
                '<button class="copy-btn" type="button" data-copy>复制</button></div>'
                "<pre><code>%s</code></pre></div>" % _esc(code)
            )
            continue

        # heading
        hm = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if hm:
            lvl = len(hm.group(1))
            body = _inline_fallback(hm.group(2))
            slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", hm.group(2).lower()).strip("-")[:64]
            out.append('<h%d id="%s">%s</h%d>' % (lvl, slug or "h", body, lvl))
            i += 1
            continue

        # setext heading
        if i + 1 < n and re.match(r"^\s*(=+|-{2,})\s*$", lines[i + 1]) and line.strip():
            lvl = 1 if lines[i + 1].strip().startswith("=") else 2
            out.append("<h%d>%s</h%d>" % (lvl, _inline_fallback(line.strip()), lvl))
            i += 2
            continue

        # horizontal rule
        if re.match(r"^\s*([-*_])(?:\s*\1){2,}\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # table
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]) and "-" in lines[i + 1]:
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            aligns = []
            for c in lines[i + 1].strip().strip("|").split("|"):
                c = c.strip()
                if c.startswith(":") and c.endswith(":"):
                    aligns.append("center")
                elif c.endswith(":"):
                    aligns.append("right")
                else:
                    aligns.append("left")
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(
                '<th style="text-align:%s">%s</th>' % (aligns[k] if k < len(aligns) else "left", _inline_fallback(c))
                for k, c in enumerate(header)
            )
            trs = []
            for r in rows:
                tds = "".join(
                    '<td style="text-align:%s">%s</td>' % (aligns[k] if k < len(aligns) else "left", _inline_fallback(c))
                    for k, c in enumerate(r)
                )
                trs.append("<tr>%s</tr>" % tds)
            out.append(
                '<div class="table-wrap"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
                % (th, "".join(trs))
            )
            continue

        # blockquote
        if re.match(r"^\s*>", line):
            buf = []
            while i < n and (re.match(r"^\s*>", lines[i]) or (lines[i].strip() and buf and not is_blank(i))):
                if not re.match(r"^\s*>", lines[i]) and not lines[i].strip():
                    break
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>%s</blockquote>" % render_fallback("\n".join(buf)))
            continue

        # lists (with task checkboxes)
        lm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if lm:
            ordered = not lm.group(2) in ("-", "*", "+")
            base_indent = len(lm.group(1).expandtabs(4))
            items = []
            while i < n:
                m2 = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", lines[i])
                if not m2:
                    break
                if len(m2.group(1).expandtabs(4)) < base_indent:
                    break
                # A different marker type starts a new list, not a new item of
                # this one (CommonMark), so `<ol>` never absorbs a `<ul>`.
                if (m2.group(2) in ("-", "*", "+")) != (not ordered):
                    break
                content = [m2.group(3)]
                i += 1
                # continuation lines (deeper indent or lazily continued text)
                while i < n and lines[i].strip():
                    m3 = re.match(r"^(\s*)([-*+]|\d+[.)])\s+", lines[i])
                    if m3 and len(m3.group(1).expandtabs(4)) <= base_indent:
                        break
                    if _FENCE_RE.match(lines[i]):
                        break
                    content.append(lines[i][min(len(lines[i])-len(lines[i].lstrip()), base_indent+2):])
                    i += 1
                items.append("\n".join(content))
                while i < n and not lines[i].strip():
                    # blank line: list continues with the same kind of marker, or
                    # with a paragraph indented into the item's content column
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j >= n:
                        break
                    nxt = lines[j]
                    m4 = re.match(r"^(\s*)([-*+]|\d+[.)])\s+", nxt)
                    if (m4 and (m4.group(2) in ("-", "*", "+")) == (not ordered)
                            and len(m4.group(1).expandtabs(4)) >= base_indent):
                        i = j
                        break
                    content_column = base_indent + 2
                    if m4 or len(nxt) - len(nxt.lstrip()) < content_column:
                        break
                    # Loose continuation of the item just added (CommonMark keeps
                    # it in the list; treating it as a code block was wrong).
                    while j < n and lines[j].strip() and not re.match(
                            r"^(\s*)([-*+]|\d+[.)])\s+", lines[j]):
                        indent = len(lines[j]) - len(lines[j].lstrip())
                        if indent < content_column:
                            break
                        items[-1] += "\n\n" + lines[j][content_column:]
                        j += 1
                    i = j
                    break
            rendered = []
            for it in items:
                tm = re.match(r"^\[([ xX])\]\s+(.*)$", it, re.S)
                if tm:
                    checked = " checked" if tm.group(1).lower() == "x" else ""
                    body = re.sub(r"^<p>(.*?)</p>", r"\1",
                                  render_fallback(tm.group(2)), count=1, flags=re.S)
                    rendered.append(
                        '<li class="task"><input type="checkbox" disabled%s> <span>%s</span></li>'
                        % (checked, body)
                    )
                else:
                    inner = render_fallback(it)
                    if "\n\n" not in it:
                        # Tight list items carry no paragraph wrapper in CommonMark;
                        # without this the fallback renders airier lists than the
                        # markdown-it engine for the very same source.
                        inner = re.sub(r"^<p>(.*?)</p>", r"\1", inner, count=1, flags=re.S)
                    rendered.append("<li>%s</li>" % inner)
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join(rendered), tag))
            continue

        # paragraph
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(
            r"^\s*(#{1,6}\s|>|[-*+]\s|\d+[.)]\s|([-*_])(\s*\2){2,}\s*$)", lines[i]
        ) and not _FENCE_RE.match(lines[i]):
            buf.append(lines[i])
            i += 1
        out.append("<p>%s</p>" % _inline_fallback("\n".join(buf)))

    return "\n".join(out)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

#: `- [x] done` / `- [ ] todo` -> inline checkbox HTML that CommonMark passes through
_TASK_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)\[([ xX])\]\s+", re.M)


def _preprocess(src: str) -> str:
    """Light conveniences on top of CommonMark: ==highlight==, [[wikilinks]] and
    GitHub-style task list checkboxes."""
    src = re.sub(r"==(?=\S)(.+?)(?<=\S)==", lambda m: "<mark>%s</mark>" % m.group(1), src)
    src = _TASK_RE.sub(
        lambda m: '%s<input type="checkbox" class="task" disabled%s> '
                  % (m.group(1), " checked" if m.group(2).lower() == "x" else ""),
        src,
    )
    return src


def render_markdown(src: str, formula=None):
    """Return (html_fragment, meta_dict, engine_used).

    ``formula`` 是可选解析器：``callable(tex, display) -> {"ok", "src", "width",
    "height", "reason"}``。给了它就先把 ``$…$`` / ``$$…$$`` 换成哨兵，渲染完再
    把哨兵换成图片标签（F05）。

    为什么走哨兵而不是渲染后正则替换整页 HTML：公式是**结构化节点**，正文里的
    ``$`` 与代码块里的 ``$`` 必须先按 markdown 的规则区分开；渲染后再去猜哪段是
    公式，就等于让正文里的任意文本有机会变成 HTML。这里正文照旧过内容白名单，
    公式图片则走一条单独的、只由我们自己生成的通道。
    """
    meta, body = split_front_matter(src)
    entries: list = []
    if formula is not None:
        body, entries = _protect_formulas(body)
    if _MD is not None:
        frag = _MD.render(body)
        frag = re.sub(r'(<li>(?:\s*<p>)?)\[([ xX])\]\s+',
                      lambda m: m[1]+'<input type="checkbox" disabled'+(' checked' if m[2].lower()=='x' else '')+'> ', frag)
        # markdown-it emits no copy buttons; add them for fenced code blocks
        frag = _decorate_code_blocks(frag)
        frag = _decorate_tables(frag)
    else:
        # The builtin renderer already handles tasks/highlights itself.
        # Preprocessing would turn these into escaped literal HTML.
        frag = render_fallback(body)
    frag = sanitize(frag)
    if entries:
        frag = _substitute_formulas(frag, entries, formula)
    seen = set()
    counts = {}
    def heading(match):
        level, content = match[1], match[2]
        plain = html.unescape(re.sub('<[^>]+>', '', content))
        base = re.sub(r'[^\w\u4e00-\u9fff-]+', '-', plain.lower()).strip('-')[:64] or 'h'
        # Count per base name instead of probing "-2", "-3", ... one by one:
        # a generated document can repeat a heading thousands of times, and the
        # probing loop made that quadratic.
        suffix = counts.get(base, 0)
        slug = base if not suffix else '%s-%d' % (base, suffix + 1)
        while slug in seen:                     # only on a rare cross-base clash
            suffix += 1
            slug = '%s-%d' % (base, suffix + 1)
        counts[base] = suffix + 1
        seen.add(slug)
        return '<h%s id="%s">%s</h%s>' % (level, slug, content, level)
    frag = re.sub(r'<h([1-6])(?:\s[^>]*)?>(.*?)</h\1>', heading, frag, flags=re.S)
    return frag, meta, ENGINE_NAME


# --------------------------------------------------------------------------
# 公式（F05）
# --------------------------------------------------------------------------

#: 哨兵：正文里绝不会出现的纯文本标记。真出现了就换一个前缀（见 :func:`_protect_formulas`）。
_FORMULA_TOKEN = "@@MDFORMULA%d@@"
_FORMULA_PREFIX = "@@MDFORMULA"


def _token_prefix(body: str) -> str:
    prefix = _FORMULA_PREFIX
    while prefix in body:
        prefix += "X"
    return prefix


def _protect_formulas(body: str):
    """把公式换成哨兵，返回 ``(新正文, [(哨兵, 公式信息)])``。

    识别不了的公式（未闭合、内容为空、首尾带空白）**不动原文**——界面原样显示源码。
    """
    from . import formula as FX
    entries: list[dict] = []
    prefix = _token_prefix(body)
    out, cursor = [], 0
    for index, entry in enumerate(FX.scan(body)):
        if entry.get("error") or not entry.get("tex"):
            continue
        token = ("%s%d@@" % (prefix, index))
        out.append(body[cursor:entry["start"]])
        out.append(token)
        cursor = entry["end"]
        entries.append({"token": token, "tex": entry["tex"], "display": bool(entry["display"])})
    if not entries:
        return body, []
    out.append(body[cursor:])
    return "".join(out), entries


def _substitute_formulas(frag: str, entries, formula) -> str:
    """把哨兵换成图片标签；解析器说画不出来时就显示原始表达式与原因。"""
    for entry in entries:
        token = entry["token"]
        if token not in frag:
            continue
        try:
            resolved = formula(entry["tex"], entry["display"]) or {}
        except Exception as error:                      # 渲染器出错不能带垮整篇
            resolved = {"ok": False, "reason": "公式渲染出错：%s" % error}
        frag = frag.replace(token, _formula_tag(entry, resolved))
    return frag


def _formula_tag(entry, resolved: dict) -> str:
    source = entry["tex"]
    if resolved.get("ok"):
        img = ('<img class="formula-img" src="%s" alt="%s" width="%d" height="%d">'
               % (html.escape(resolved.get("src", ""), quote=True),
                  html.escape("$%s$" % source if not entry["display"] else "$$%s$$" % source,
                              quote=True),
                  int(resolved.get("width") or 0), int(resolved.get("height") or 0)))
        if entry["display"]:
            return '<div class="formula-block">%s</div>' % img
        return '<span class="formula-inline">%s</span>' % img
    reason = resolved.get("reason") or "这个公式暂时画不出来"
    return ('<span class="formula-error" title="%s">%s</span>'
            % (html.escape(reason, quote=True),
               html.escape("$%s$" % source if not entry["display"] else "$$%s$$" % source)))


_CODE_BLOCK_RE = re.compile(
    r'<pre><code(?: class="language-([^"]*)")?>(.*?)</code></pre>', re.S
)


def _decorate_code_blocks(frag: str) -> str:
    def repl(m):
        lang = (m.group(1) or "").strip()
        code = m.group(2)
        cls = ' class="language-%s"' % lang if lang else ""
        return (
            '<div class="code-wrap"><div class="code-head"><span class="code-lang">%s</span>'
            '<button class="copy-btn" type="button" data-copy>复制</button></div>'
            "<pre><code%s>%s</code></pre></div>" % (html.escape(lang or "text"), cls, code)
        )

    return _CODE_BLOCK_RE.sub(repl, frag)


_TABLE_BLOCK_RE = re.compile(r"(<table(?:\s[^>]*)?>.*?</table>)", re.S | re.I)


def _decorate_tables(frag: str) -> str:
    """Give markdown-it tables a scroll container like the builtin engine."""
    return _TABLE_BLOCK_RE.sub(r'<div class="table-wrap">\1</div>', frag)


# --------------------------------------------------------------------------
# page shell
# --------------------------------------------------------------------------

def build_page(*, title: str, body_html: str, meta: dict | None = None, subtitle: str = "",
               doc_path: str = "", engine: str = "", standalone: bool = False) -> str:
    meta = meta or {}
    body_html = sanitize(body_html)
    chips = []
    for k, v in list(meta.items())[:8]:
        chips.append(
            '<span class="chip"><b>%s</b>%s</span>' % (html.escape(str(k)), html.escape(str(v)))
        )
    chip_html = ('<div class="meta-chips">%s</div>' % "".join(chips)) if chips else ""
    sub = ('<div class="doc-sub">%s</div>' % html.escape(subtitle)) if subtitle else ""
    foot = ""
    if not standalone:
        foot = (
            '<footer class="doc-foot"><span>%s</span><span>渲染引擎 %s</span></footer>'
            % (html.escape(doc_path), html.escape(engine or ENGINE_NAME))
        )
    standalone_note = ""
    if standalone:
        standalone_note = (
            '<div class="standalone-note">由 MDReader 导出的离线单文件，可直接分享或打印。</div>'
        )
    return (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s</title>\n<style>\n%s\n</style>\n</head>\n"
        '<body class="doc-page%s">\n<article class="markdown-body">\n'
        "%s%s<h1 class=\"doc-title\">%s</h1>\n%s\n%s\n</article>\n"
        "%s\n</body>\n</html>"
        % (
            html.escape(title),
            STYLESHEET,
            " standalone" if standalone else "",
            standalone_note,
            chip_html,
            html.escape(title),
            sub,
            body_html,
            foot,
        )
    )


STYLESHEET = r"""
:root{
  --bg:#ffffff; --bg-soft:#f6f7f9; --fg:#1f2328; --fg-muted:#656d76;
  --border:#d8dee4; --accent:#0969da; --accent-soft:#ddf4ff;
  --code-bg:#f6f8fa; --quote-bg:#f6f8fa; --mark-bg:#fff8c5;
  --font-sans:-apple-system,"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",Roboto,"Helvetica Neue",Arial,sans-serif;
  --font-mono:"Cascadia Mono",Consolas,"JetBrains Mono","Courier New",monospace;
  --maxw:860px;
}
html[data-theme="dark"]{
  color-scheme:dark;
  --bg:#22262d; --bg-soft:#2b313a; --fg:#dce1e8; --fg-muted:#a8b1be;
  --border:#3b434f; --accent:#9bbde3; --accent-soft:#3b5068;
  --code-bg:#2b313a; --quote-bg:#29313b; --mark-bg:#514833;
}
html[data-theme="eye"]{
  color-scheme:light;
  --bg:#f3efdf; --bg-soft:#e8ecdc; --fg:#394538; --fg-muted:#63705d;
  --border:#cbd0b9; --accent:#4c6b43; --accent-soft:#d1dec1;
  --code-bg:#e7e7d6; --quote-bg:#e8ecdc; --mark-bg:#e5dbad;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body.doc-page{
  background:var(--bg); color:var(--fg); font-family:var(--font-sans);
  font-size:16px; line-height:1.75; -webkit-font-smoothing:auto; text-rendering:optimizeLegibility;
  padding:0 0 64px;
}
.standalone-note{
  background:var(--accent-soft); color:var(--fg-muted); font-size:12.5px;
  padding:8px 16px; border-bottom:1px solid var(--border); text-align:center;
}
.markdown-body{max-width:var(--maxw); margin:0 auto; padding:32px 28px 0; word-wrap:break-word}
.doc-title{font-size:2em; line-height:1.25; margin:.2em 0 .1em; padding-bottom:.3em; border-bottom:2px solid var(--border)}
.doc-sub{color:var(--fg-muted); font-size:.92em; margin-bottom:1.2em; font-family:var(--font-mono); word-break:break-all}
.meta-chips{display:flex; flex-wrap:wrap; gap:8px; margin:12px 0 8px}
.chip{background:var(--bg-soft); border:1px solid var(--border); border-radius:999px; padding:3px 12px; font-size:12.5px; color:var(--fg-muted)}
.chip b{color:var(--fg); margin-right:6px; font-weight:600}
h1,h2,h3,h4,h5,h6{margin:1.6em 0 .6em; line-height:1.3; font-weight:650}
h1{font-size:1.9em} h2{font-size:1.5em; padding-bottom:.3em; border-bottom:1px solid var(--border)}
h3{font-size:1.25em} h4{font-size:1.05em} h5,h6{font-size:.95em; color:var(--fg-muted)}
h1:first-child,h2:first-child,h3:first-child{margin-top:.4em}
p{margin:0 0 1em}
a{color:var(--accent); text-decoration:none}
a:hover{text-decoration:underline}
strong{font-weight:650}
mark{background:var(--mark-bg); color:inherit; padding:.1em .25em; border-radius:3px}
del{color:var(--fg-muted)}
ul,ol{margin:0 0 1em; padding-left:1.9em}
li{margin:.35em 0}
li.task{list-style:none; margin-left:-1.5em; display:flex; gap:.6em; align-items:flex-start}
li.task input{margin-top:.45em; flex:0 0 auto}
blockquote{
  margin:0 0 1em; padding:.5em 1.1em; color:var(--fg-muted);
  background:var(--quote-bg); border-left:4px solid var(--border); border-radius:0 6px 6px 0;
}
blockquote>p:last-child{margin-bottom:0}
hr{border:0; border-top:1px solid var(--border); margin:2em 0}
img{max-width:100%; height:auto; border-radius:6px; background:var(--bg-soft)}
code{
  font-family:var(--font-mono); font-size:.875em; background:var(--code-bg);
  padding:.18em .4em; border-radius:5px; border:1px solid var(--border);
}
.code-wrap{margin:0 0 1.2em; border:1px solid var(--border); border-radius:8px; overflow:hidden; background:var(--code-bg)}
.code-head{
  display:flex; justify-content:space-between; align-items:center; gap:8px;
  padding:6px 10px; border-bottom:1px solid var(--border);
  font-size:12px; color:var(--fg-muted); font-family:var(--font-mono);
}
.copy-btn{
  border:1px solid var(--border); background:var(--bg); color:var(--fg-muted);
  border-radius:5px; padding:2px 10px; font-size:12px; cursor:pointer; font-family:var(--font-sans);
}
.copy-btn:hover{color:var(--accent); border-color:var(--accent)}
.code-wrap pre{margin:0; padding:12px 14px; overflow:auto; font-size:13.5px; line-height:1.6}
.code-wrap pre code{background:none; border:0; padding:0; font-size:inherit}
.table-wrap{overflow:auto; margin:1.1em 0 1.4em; border:1px solid var(--border); border-radius:9px}
table{border-collapse:separate; border-spacing:0; width:100%; font-size:.94em}
th,td{border:0; border-right:1px solid var(--border); border-bottom:1px solid var(--border); padding:9px 12px; vertical-align:top; overflow-wrap:anywhere}
th:last-child,td:last-child{border-right:0} tbody tr:last-child td{border-bottom:0}
th{background:var(--bg-soft); font-weight:700; white-space:nowrap}
tbody tr:nth-child(2n){background:color-mix(in srgb, var(--bg-soft) 65%, transparent)}
tbody tr:hover{background:var(--accent-soft)}
kbd{border:1px solid var(--border); border-bottom-width:2px; border-radius:5px; padding:.1em .45em; font-family:var(--font-mono); font-size:.85em; background:var(--bg-soft)}
/* 公式（F05）：图片由本机渲染，尺寸就是最终显示尺寸；长公式允许横向滚动 */
.formula-inline{display:inline-block; max-width:100%; vertical-align:middle}
.formula-img{max-width:100%; height:auto; vertical-align:middle}
.formula-block{overflow-x:auto; overflow-y:hidden; margin:1.1em 0 1.3em; text-align:center}
.formula-block .formula-img{margin:0 auto}
.formula-error{background:var(--bg-soft); border-bottom:1px dashed var(--danger); color:var(--danger);
  font-family:var(--font-mono); font-size:.92em; padding:0 .2em; cursor:help}
input.task[type="checkbox"]{appearance:none; -webkit-appearance:none; width:1em; height:1em;
  border:1.5px solid var(--fg-muted); border-radius:4px; vertical-align:-.12em; margin-right:.15em; position:relative}
input.task[type="checkbox"]:checked{background:var(--accent); border-color:var(--accent)}
input.task[type="checkbox"]:checked::after{content:""; position:absolute; left:.26em; top:.06em;
  width:.26em; height:.52em; border:solid #fff; border-width:0 2px 2px 0; transform:rotate(45deg)}
li:has(> input.task[type="checkbox"]:checked){color:var(--fg-muted)}
details{margin:0 0 1em; border:1px solid var(--border); border-radius:8px; padding:.6em 1em; background:var(--bg-soft)}
summary{cursor:pointer; font-weight:600}
.doc-foot{
  max-width:var(--maxw); margin:48px auto 0; padding:14px 28px 0; border-top:1px solid var(--border);
  display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap;
  color:var(--fg-muted); font-size:12.5px; font-family:var(--font-mono);
}
/* syntax highlighting (builtin, offline) */
.tok-key{color:#cf222e} .tok-str{color:#0a3069} .tok-num{color:#0550ae}
.tok-com{color:#6e7781; font-style:italic} .tok-fn{color:#8250df} .tok-op{color:#0550ae}
html[data-theme="dark"] .tok-key{color:#ff7b72} html[data-theme="dark"] .tok-str{color:#a5d6ff}
html[data-theme="dark"] .tok-num{color:#79c0ff} html[data-theme="dark"] .tok-com{color:#8b949e}
html[data-theme="dark"] .tok-fn{color:#d2a8ff} html[data-theme="dark"] .tok-op{color:#79c0ff}
@media print{
  .doc-foot,.standalone-note{display:none}
  body.doc-page{font-size:12pt; padding:0}
  .markdown-body{max-width:none; padding:0}
  .code-wrap,.table-wrap,blockquote{break-inside:avoid}
  a{color:inherit}
}
@media (max-width:700px){ .markdown-body{padding:20px 16px 0} }
"""
