"""Exact, independent line changes for reviewing an AI document proposal."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import re


def paragraph_breaks(lines):
    """Do not split a fenced code block or display math at its blank lines."""
    breaks, fence, math = set(), None, False
    for n, line in enumerate(lines):
        line = re.sub(r'^(?:[ ]{0,3}>[ ]?)+', '', line)
        marker = re.match(r'^[ ]{0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif marker and not math:
            fence = marker[1]
        elif line.strip() == '$$':
            math = not math
        elif not line.strip() and not math:
            breaks.add(n)
    return breaks


@dataclass(frozen=True)
class Change:
    start: int
    end: int
    original: str
    replacement: str
    line: int
    last_line: int


class Review:
    def __init__(self, before, replacement):
        if not isinstance(before, dict) or 'tab' not in before or not isinstance(before.get('text'), str):
            raise ValueError('原文信息已失效，请重新生成建议。')
        if not isinstance(replacement, str) or len(replacement) > 200000:
            raise ValueError('建议格式或长度无效，请重新生成。')
        self.tab = before['tab']
        self.original = before['text']
        self.replacement = replacement
        self.accepted = frozenset()
        self.retained = set()
        self.timeline = [(self.original, self.accepted)]
        self.cursor = 0
        self.merged = False
        self.note = ''
        a, b = self.original.splitlines(keepends=True), replacement.splitlines(keepends=True)
        if max(len(a), len(b)) > 3000 or len(self.original) > 200000:
            # Avoid quadratic matching on very large repetitive documents.
            self.merged = True
            self.note = '文档较长，本次合并为一个修改块，请完整核对后再采用。'
            self.changes = [] if self.original == replacement else [
                Change(0, len(self.original), self.original, replacement, 1, max(1, len(a)))]
        else:
            offsets = [0]
            for line in a:
                offsets.append(offsets[-1] + len(line))
            groups = []
            left_breaks, right_breaks = paragraph_breaks(a), paragraph_breaks(b)
            related = False
            for tag, i, j, x, y in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                if tag == 'equal':
                    continue
                changed = a[i:j] + b[x:y]
                if any(re.match(r'^\s{0,3}\[[^]]+\]:', line) or re.search(r'</?[A-Za-z][^>]*>', line)
                       for line in changed):
                    related = True
                if groups and (not any(n in left_breaks for n in range(groups[-1][1], i)) or
                               not any(n in right_breaks for n in range(groups[-1][3], x))):
                    groups[-1][1], groups[-1][3] = j, y
                else:
                    groups.append([i,j,x,y])
            self.changes = [Change(offsets[i], offsets[j], ''.join(a[i:j]), ''.join(b[x:y]),
                                   i+1, max(i+1, j)) for i,j,x,y in groups]
            if related and len(self.changes) > 1:
                self.note = '引用定义或 HTML 结构发生变化，本次合并核对，避免分开采用影响关联内容。'
                self.merged = True
                self.changes = [Change(0,len(self.original),self.original,replacement,1,max(1,len(a)))]
            if len(self.changes) > 200:
                self.merged = True
                self.note = '修改处较多，本次合并为一个修改块，请完整核对后再采用。'
                self.changes = [Change(0, len(self.original), self.original, replacement, 1, max(1, len(a)))]

    @property
    def expected(self):
        return self.timeline[self.cursor][0]

    def state(self, index):
        if index in self.accepted:
            return '已采用'
        return '保留原文' if index in self.retained else '待核对'

    def pending(self):
        return [n for n in range(len(self.changes)) if self.state(n) == '待核对']

    def refresh(self, snapshot):
        """Reconcile editor undo/redo only with exact states produced by us."""
        if snapshot.get('tab') is not self.tab:
            raise ValueError('当前文档已切换，请回到原文档再核对。')
        text = snapshot.get('text')
        if text == self.expected:
            return
        matches = [n for n, (value, _accepted) in enumerate(self.timeline) if value == text]
        if len(matches) != 1:
            raise ValueError('正文已变化，旧建议不能继续应用；请重新生成。')
        self.cursor = matches[0]
        self.accepted = self.timeline[self.cursor][1]
        self.retained.difference_update(self.accepted)

    def compose(self, accepted):
        parts, cursor = [], 0
        for n, change in enumerate(self.changes):
            parts.extend((self.original[cursor:change.start],
                          change.replacement if n in accepted else change.original))
            cursor = change.end
        parts.append(self.original[cursor:])
        return ''.join(parts)

    def accept(self, indices, get_snapshot, apply_edit):
        self.refresh(get_snapshot())
        indices = set(indices)
        if not indices.issubset(self.pending()):
            raise ValueError('只能采用尚未处理的修改块。')
        if not indices:
            return
        accepted = self.accepted | indices
        result = self.compose(accepted)
        # Existing host callback checks identity, exact text and IME again.
        apply_edit({'tab': self.tab, 'text': self.expected}, result)
        self.timeline = self.timeline[:self.cursor+1] + [(result, accepted)]
        self.cursor += 1
        self.accepted = accepted

    def retain(self, index, get_snapshot):
        self.refresh(get_snapshot())
        if index not in self.pending():
            raise ValueError('只能保留尚未处理的修改块。')
        self.retained.add(index)
