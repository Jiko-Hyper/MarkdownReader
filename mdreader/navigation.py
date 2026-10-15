"""Align source and rendered text without relying on the first matching word."""
import difflib
import html
import re
from array import array
from bisect import bisect_left, bisect_right


class _Projection:
    """Visible characters and the original source range of each character."""

    def __init__(self):
        self.parts = []
        self.starts = array('I')
        self.ends = array('I')

    @property
    def text(self):
        return ''.join(self.parts)

    def literal(self, text, start, end):
        self.parts.append(text)
        self.starts.extend([start] * len(text))
        self.ends.extend([end] * len(text))

    def raw(self, text, start):
        self.parts.append(text)
        self.starts.extend(range(start, start + len(text)))
        self.ends.extend(range(start + 1, start + len(text) + 1))

    def extend(self, other):
        self.parts.extend(other.parts)
        self.starts.extend(other.starts)
        self.ends.extend(other.ends)

    def slice(self, first, last):
        result = _Projection()
        result.parts = [self.text[first:last]]
        result.starts = self.starts[first:last]
        result.ends = self.ends[first:last]
        return result


def _decoded(projected):
    """An entity maps back to its entire spelling, including the semicolon."""
    result, text, offset = _Projection(), projected.text, 0
    for match in re.finditer(r'&(?:#\d+|#[xX][\da-fA-F]+|[A-Za-z][A-Za-z0-9]*);', text):
        result.extend(projected.slice(offset, match.start()))
        result.literal(html.unescape(match[0]), projected.starts[match.start()],
                       projected.ends[match.end() - 1])
        offset = match.end()
    result.extend(projected.slice(offset, len(text)))
    return result


# These are the native tokenizer's marker boundaries. Its output is checked
# below before hiding syntax, so unsupported/malformed markup stays literal.
_INLINE_RULES = (
    ('code', re.compile(r'(`+)(.+?)\1', re.S)),
    ('formula', re.compile(r'(?<!\\)\$(?!\$)([^\n$]+?)(?<!\\)\$(?!\$)')),
    ('angle', re.compile(r'<[^<>\n]{0,400}>')),
    ('wiki_image', re.compile(r'!\[\[([^\]|]+)(?:\|([^\]]*))?\]\]')),
    ('image', re.compile(r'!\[([^\]]*)\]\(([^)\s]*)(?:\s+"[^"]*")?\)')),
    ('wiki', re.compile(r'\[\[([^\]|]+)(?:\|([^\]]*))?\]\]')),
    ('link', re.compile(r'\[([^\]\n]*(?:\[[^\]\n]*\][^\]\n]*)*)\]\(([^)\s]*)(?:\s+"[^"]*")?\)')),
    ('emphasis', re.compile(r'\*{3}(?=\S)(.+?)(?<=\S)\*{3}', re.S)),
    ('emphasis', re.compile(r'\*{2}(?=\S)(.+?)(?<=\S)\*{2}', re.S)),
    ('emphasis', re.compile(r'\*(?=[^\s*])(.+?)(?<=[^\s*])\*', re.S)),
    ('emphasis', re.compile(r'_{2}(?=\S)(.+?)(?<=\S)_{2}(?![A-Za-z0-9])', re.S)),
    ('emphasis', re.compile(r'_(?=\S)(.+?)(?<=\S)_(?![A-Za-z0-9])', re.S)),
    ('emphasis', re.compile(r'~~(?=\S)(.+?)(?<=\S)~~', re.S)),
    ('emphasis', re.compile(r'==(?=\S)(.+?)(?<=\S)==', re.S)),
    ('entity', re.compile(r'&(?:#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});')),
)


def _inline_projection(projected, rendered):
    from .winui import _Parser, tokenize_inline
    result, text, position = _Projection(), projected.text, 0
    parser = _Parser('')

    def group(match, number, strip=False):
        first, last = match.span(number)
        if strip:
            value = text[first:last]
            first += len(value) - len(value.lstrip())
            last -= len(value) - len(value.rstrip())
        return projected.slice(first, max(first, last))

    while position < len(text):
        if text[position] not in '*_`[!<~=&$' or (
                text[position] == '_' and position and text[position - 1].isalnum()):
            last = position + 1
            while last < len(text) and text[last] not in '*_`[!<~=&$':
                last += 1
            result.extend(projected.slice(position, last))
            position = last
            continue
        for kind, pattern in _INLINE_RULES:
            match = pattern.match(text, position)
            if match is None:
                continue
            raw = match[0]
            native = ''.join(value for value, _tags in tokenize_inline(raw, parser))
            # An empty code/formula or an unsupported HTML tag remains literal
            # in the native parser; never invent visibility for that syntax.
            if native == raw and kind not in ('formula', 'entity'):
                continue
            first, last = projected.starts[position], projected.ends[match.end() - 1]
            if kind == 'code':
                result.literal(' ', first, first)
                result.extend(_decoded(group(match, 2, strip=True)))
                result.literal(' ', last, last)
            elif kind == 'formula':
                if not match[1].strip() or match[1][:1].isspace() or match[1][-1:].isspace():
                    continue
                if raw in rendered:
                    result.extend(projected.slice(position, match.end()))
                else:
                    result.literal('\ufffc', first, last)
            elif kind == 'angle':
                result.extend(projected.slice(position + 1, match.end() - 1))
            elif kind in ('link', 'wiki'):
                number = (2 if match[2] else 1) if kind == 'wiki' else (1 if match[1].strip() else 2)
                result.extend(_decoded(group(match, number, strip=True)))
            elif kind in ('image', 'wiki_image'):
                number = (2 if match[2] else 1) if kind == 'wiki_image' else 1
                label = group(match, number, strip=kind == 'image')
                result.literal('\U0001f5bc ', first, first)
                if label.text:
                    result.extend(_decoded(label) if kind == 'image' else label)
                elif kind == 'image':
                    result.literal('\u56fe\u7247', first, last)
                if kind == 'image':
                    boundary = label.ends[-1] if label.ends else first
                    result.literal(' ', boundary, boundary)
                target = group(match, 2 if kind == 'image' else 1)
                path = ('\u2039%s\u203a' if kind == 'image' else '(%s)') % target.text
                if path in rendered:
                    boundary = target.starts[0] if target.starts else last
                    result.literal(path[0], boundary, boundary)
                    result.extend(target)
                    result.literal(path[-1], last, last)
                else:
                    result.literal('\ufffc', target.starts[0] if target.starts else last, last)
            else:
                result.extend(_decoded(group(match, 1)) if kind == 'emphasis'
                              else _decoded(projected.slice(position, match.end())))
            position = match.end()
            break
        else:
            result.extend(projected.slice(position, position + 1))
            position += 1
    return result


def _markdown_projection(source, rendered):
    """Project native Markdown before aligning it with the actual widget.

    The projection removes destinations and formatting *before* matching, so
    repeated words in invisible URLs cannot steal a visible word's anchor.
    Code and embedded objects remain separate from prose transformations.
    """
    from .winui import (_ATX_RE, _FENCE_RE, _HR_RE, _LI_RE, _TABLE_SEP_RE,
                        _TASK_RE, BULLETS, CHECK_OFF, CHECK_ON, GUTTER, RAY)
    result, prose = _Projection(), _Projection()
    lines = []
    offset = 0
    for line in source.splitlines(keepends=True):
        body = line.rstrip('\r\n')
        lines.append((body, offset, offset + len(line)))
        offset += len(line)
    if not lines:
        return result

    def flush():
        nonlocal prose
        if prose.starts:
            result.extend(_inline_projection(prose, rendered))
            prose = _Projection()

    def raw_line(index, first=0, last=None, inline=True):
        body, start, end = lines[index]
        last = len(body) if last is None else last
        output = _Projection()
        output.raw(body[first:last], start + first)
        if inline:
            prose.extend(output)
            prose.literal('\n', start + len(body), end)
        else:
            result.extend(output)
            result.literal('\n', start + len(body), end)

    index = 0
    # Front matter is rendered as plain key/value rows, without YAML quotes.
    fm = re.match(r'^\ufeff?(?:---|\+\+\+)[ \t]*\r?\n(.*?)\r?\n(?:---|\+\+\+)[ \t]*(?:\r?\n|$)', source, re.S)
    if fm:
        from .render import split_front_matter
        metadata, _body = split_front_matter(source)
        if metadata:
            for key, value in metadata.items():
                line = next(((body, start) for body, start, end in lines
                             if start < fm.end() and body.partition(':')[0].strip() == key), None)
                if line:
                    body, start = line
                    key_at, value_at = body.find(key), body.find(value, body.find(':') + 1)
                    result.raw(key, start + key_at)
                    result.literal(': ', start + body.find(':'), start + body.find(':') + 1)
                    result.raw(value, start + max(0, value_at))
                    result.literal('\n', start + len(body), start + len(body))
            closing = source.rfind('\n', 0, max(0, fm.end() - 1))
            result.literal(RAY * 78 + '\n', max(0, closing), fm.end())
            index = next((i for i, (_body, start, _end) in enumerate(lines)
                          if start >= fm.end()), len(lines))

    while index < len(lines):
        body, start, end = lines[index]
        fence = _FENCE_RE.match(body)
        if fence:
            flush()
            close, index = fence[2][0] * len(fence[2]), index + 1
            while index < len(lines):
                code, code_start, code_end = lines[index]
                if code.strip().startswith(close) and set(code.strip()) <= {close[0], ' ', '\t'}:
                    index += 1
                    break
                # expandtabs changes display width, but each expanded space
                # still belongs to its original source tab.
                column = 0
                for position, character in enumerate(code):
                    visible = ' ' * (4 - column % 4) if character == '\t' else character
                    result.literal(visible, code_start + position, code_start + position + 1)
                    column += len(visible)
                result.literal('\n', code_start + len(code), code_end)
                index += 1
            continue
        if body.lstrip().startswith('$$'):
            stripped, last = body.strip(), index + 1
            closed = stripped[2:].endswith('$$') and len(stripped[2:]) >= 2
            while not closed and last < len(lines):
                closed = lines[last][0].strip().endswith('$$')
                last += 1
            if closed:
                flush()
                raw = source[start:lines[last - 1][2]].strip()
                if raw in rendered:
                    result.raw(raw, start + len(body) - len(body.lstrip()))
                else:
                    result.literal('\ufffc', start, lines[last - 1][2])
                result.literal('\n', lines[last - 1][2], lines[last - 1][2])
                index = last
                continue
        if (index + 1 < len(lines) and '|' in body
                and '|' in lines[index + 1][0] and '-' in lines[index + 1][0]
                and _TABLE_SEP_RE.match(lines[index + 1][0].strip())):
            flush()
            last = index + 2
            while last < len(lines) and lines[last][0].strip() and '|' in lines[last][0] and last - index < 302:
                last += 1
            result.literal('\ufffc', start, lines[last - 1][2])
            result.literal('\n', lines[last - 1][2], lines[last - 1][2])
            index = last
            continue
        heading = _ATX_RE.match(body)
        setext = lines[index + 1][0].strip() if index + 1 < len(lines) else ''
        if heading or (len(setext) >= 2 and len(set(setext)) == 1 and setext[0] in '=-'):
            flush()
            first, last = heading.span(2) if heading else (0, len(body))
            raw_line(index, first, last)
            flush()
            index += 1 if heading else 2
            continue
        if _HR_RE.match(body):
            flush()
            result.literal(RAY * 78 + '\n', start, end)
        elif not body.strip():
            flush()
            result.literal('\n', start, end)
        elif re.match(r'^ {4,}\S', body) and index + 1 < len(lines):
            flush()
            raw_line(index, 4, len(body.rstrip()), inline=False)
        elif body.lstrip().startswith('>') and len(body) - len(body.lstrip()) <= 3:
            flush()
            first = body.index('>') + 1
            if body[first:first + 1] == ' ':
                first += 1
            result.literal(GUTTER, start, start + first)
            raw_line(index, first, len(body.rstrip()))
            flush()
        elif (item := _LI_RE.match(body[:160])):
            flush()
            first, last = item.span(4)
            marker = item[2]
            if marker[0] in '-*+':
                task = _TASK_RE.match(item[4])
                if task:
                    bullet = CHECK_ON if task[1].lower() == 'x' else CHECK_OFF
                    first += task.end()
                else:
                    bullet = BULLETS[0]
            else:
                bullet = marker if marker.endswith('.') else marker + '.'
            result.literal(bullet + '\t', start, start + first)
            raw_line(index, first, last)
            flush()
        else:
            first = len(body) - len(body.lstrip())
            raw_line(index, first, len(body.rstrip()))
        index += 1
    flush()
    return result


def map_markdown_selection(source, rendered, start, end, *, from_preview=True):
    """Map Python character offsets between native Markdown and its preview."""
    projected = _markdown_projection(source, rendered)
    text = projected.text
    if not text:
        return 0, 0
    if from_preview:
        first, last = map_selection(rendered, text, start, end)
        if first == last:
            if first >= len(text):
                return projected.ends[-1], projected.ends[-1]
            return projected.starts[first], projected.starts[first]
        return projected.starts[first], projected.ends[min(last, len(text)) - 1]
    # Source spans are ordered, including decoded entities and object ranges.
    # A hidden marker snaps to the nearest visible boundary in its own block.
    first = bisect_right(projected.ends, max(0, start))
    last = bisect_left(projected.starts, max(start, end))
    first = min(first, len(text))
    last = min(max(first, last), len(text))
    if start == end:
        last = first
    return map_selection(text, rendered, first, last)


def map_selection(origin, target, start, end):
    """Return a target range, using surrounding document order for duplicates.

    Markdown punctuation becomes separate tokens, so headings, links and inline
    emphasis do not shift all subsequent matches. Non-rendered syntax falls
    back to the closest visible token instead of an unrelated matching word.
    """
    left = list(re.finditer(r'[A-Za-z0-9_]+|[^\s]', origin))
    right = list(re.finditer(r'[A-Za-z0-9_]+|[^\s]', target))
    left_words, right_words = [m[0] for m in left], [m[0] for m in right]
    matcher = difflib.SequenceMatcher(None, left_words, right_words,
                                    autojunk=max(len(left), len(right)) > 4000)
    anchors = []
    blocks = []
    for tag, a, z, b, y in matcher.get_opcodes():
        if tag == 'equal':
            blocks.append((a, b, z-a))
        elif tag == 'replace' and max(z-a, y-b) <= 4000:
            # Popular Chinese characters omitted by the coarse pass still
            # need matching inside the gaps between uncommon words/numbers.
            local = difflib.SequenceMatcher(None, left_words[a:z], right_words[b:y], autojunk=False)
            blocks.extend((a+i, b+j, size) for i, j, size in local.get_matching_blocks())
    for a, b, size in blocks:
        for i in range(size):
            anchors.append((left[a+i].start(), left[a+i].end(),
                            right[b+i].start(), right[b+i].end()))
    # Emphasis may split a Latin word in source ("read**ing**"), while the
    # preview contains one token. Refine just the selected gap by characters.
    if not any(a <= start < z for a, z, _, _ in anchors):
        before = next((row for row in reversed(anchors) if row[1] <= start), None)
        after = next((row for row in anchors if row[0] >= end), None)
        a, b = (before[1], before[3]) if before else (0, 0)
        z, y = (after[0], after[2]) if after else (len(origin), len(target))
        if max(z-a, y-b) <= 4000:
            local = difflib.SequenceMatcher(None, origin[a:z], target[b:y], autojunk=False)
            anchors.extend((a+i, a+i+size, b+j, b+j+size)
                           for i, j, size in local.get_matching_blocks() if size)
            anchors.sort()
    # Entirely repetitive long passages may have no uncommon anchors. Exact
    # occurrences retain their order; use their ordinal, never the first hit.
    if not any(a <= start < z for a, z, _, _ in anchors):
        needle = origin[start:end].strip()
        if needle:
            matches = list(re.finditer(re.escape(needle), target))
            if matches:
                ordinal = len(list(re.finditer(re.escape(needle), origin[:start])))
                hit = matches[min(ordinal, len(matches)-1)]
                return hit.start(), hit.end()
    if not anchors:
        return 0, 0

    def locate(offset, ending=False):
        for a, z, b, y in anchors:
            contains = a < offset <= z if ending else a <= offset < z
            if contains:
                return b + offset - a
        a, z, b, y = min(anchors, key=lambda row: min(abs(offset-row[0]), abs(offset-row[1])))
        return y if offset >= z else b

    first, last = locate(start), locate(end, True)
    return min(first, last), max(first, last)


def widget_text(widget, end='end-1c'):
    """Include embedded objects so text offsets stay aligned with Tk indices."""
    return ''.join(value if kind == 'text' else '\ufffc'
                   for kind, value, _ in widget.dump('1.0', end, text=True, window=True, image=True))


def widget_index(widget, text, offset):
    # Tcl counts non-BMP characters as two code units in a Text line index.
    prefix = text[:offset]
    line = prefix.count('\n') + 1
    column = len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2
    return widget.index('%d.%d' % (line, column))
