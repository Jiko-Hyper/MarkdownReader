"""Align source and rendered text without relying on the first matching word."""
import difflib
import re


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
