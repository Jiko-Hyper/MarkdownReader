"""选区 AI 修改的纯逻辑：范围校验、按范围替换、过期判断、实际发送范围。

界面只负责取选区、显示和调用这里的函数；桌面与网页共用同一套规则。
这样"只改选中范围""过期结果不能覆盖正文"这类要求可以脱离窗口单独验证。
"""
from __future__ import annotations

MAX_CHARS = 200000                                  # 与 ai_providers 的上限一致

SCOPES = ('selection', 'block', 'document')
SCOPE_LABELS = {'selection': '仅选区', 'block': '选区所在段落', 'document': '整篇文档'}

#: 首批只提供这四种改法；自定义用输入框里的要求。
INTENTS = (
    ('polish', '润色', '把选中的文字润色：更通顺、更书面，不改动事实与结构。'),
    ('shorten', '精简', '精简选中的文字：删掉重复和冗词，保留全部关键信息。'),
    ('expand', '扩写', '扩写选中的文字：不编造事实，补充细节与过渡。'),
)


class SelectionError(ValueError):
    """选区不可用或替换结果不可应用；文字本身就是给用户看的原因。"""


def require_range(text, start, end):
    """校验选中范围并返回选中的文字。"""
    if not isinstance(text, str) or not isinstance(start, int) or not isinstance(end, int):
        raise SelectionError('无法读取当前选区，请重新选择要修改的文字。')
    if start < 0 or end < start or end > len(text):
        raise SelectionError('选区位置无效，请重新选择要修改的文字。')
    if start == end:
        raise SelectionError('请先选中要修改的文字，再使用选区修改。')
    return text[start:end]


def block_bounds(text, start, end):
    """选区所在的整段（前后以空行为界）。"""
    head = text.rfind('\n\n', 0, start)
    tail = text.find('\n\n', end)
    return (0 if head < 0 else head + 2, len(text) if tail < 0 else tail)


def outgoing(text, start, end, scope='selection'):
    """按发送范围返回 ``(要发送的文字, 范围说明)``。

    说明里带字数和字符区间：用户勾选"整篇文档"之前必须看得见自己发出去了多少。
    """
    if scope not in SCOPES:
        raise SelectionError('未知的发送范围：%s' % scope)
    require_range(text, start, end)
    if scope == 'document':
        return text, '整篇文档 %d 字（含未保存内容）' % len(text)
    if scope == 'block':
        head, tail = block_bounds(text, start, end)
        return text[head:tail], '选区所在段落 %d 字（第 %d–%d 字符）' % (tail - head, head + 1, tail)
    return text[start:end], '仅选区 %d 字（第 %d–%d 字符）' % (end - start, start + 1, end)


def check_replacement(replacement):
    """空结果、非文本、超长结果都不能成为可应用的新正文。"""
    if not isinstance(replacement, str) or not replacement.strip():
        raise SelectionError('模型没有返回可用的替换文字，文档未改动。')
    if len(replacement) > MAX_CHARS:
        raise SelectionError('替换文字超过 20 万字符，文档未改动。')
    return replacement


def replace_range(text, start, end, replacement):
    """只替换目标范围，范围外的文字原样保留；返回 ``(新正文, 新选区)``。"""
    require_range(text, start, end)
    check_replacement(replacement)
    return text[:start] + replacement + text[end:], start, start + len(replacement)


def request_before(tab, text, start, end):
    """记录一次选区请求的文档身份、正文版本与范围。"""
    require_range(text, start, end)
    return {'tab': tab, 'text': text, 'start': start, 'end': end}


def stale_reason(before, tab, current):
    """请求发出之后是否还能安全应用；不能就返回给用户看的原因。

    返回空字符串表示可以应用。文档切走了、正文变了都算过期：旧结果绝不能
    直接盖到另一篇文档或另一版正文上。
    """
    if not isinstance(before, dict) or 'tab' not in before:
        return '选区信息已失效，请重新选中文字后再试。'
    if before.get('tab') is not tab:
        return '当前文档已切换，请回到原文档后再应用。'
    if current != before.get('text'):
        return '文档在请求后已变化，请重新发送要求以生成最新修改。'
    return ''


def diff_of(original, replacement):
    """原文与建议的差异文本（界面展示用，判定权仍在用户手里）。"""
    import difflib
    return '\n'.join(difflib.unified_diff(original.splitlines(), replacement.splitlines(),
                                          fromfile='原文', tofile='建议', lineterm=''))
