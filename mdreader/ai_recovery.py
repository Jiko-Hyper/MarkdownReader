"""Window-local drafts and explicitly retried AI requests; nothing is persisted."""
from dataclasses import dataclass
import tkinter as tk
from .ai_providers import PROVIDERS
from .selection import stale_reason


@dataclass
class Attempt:
    provider: str
    call: object
    complete: object
    prompt: str
    kind: str
    before: object
    scope: str
    attached: bool


class Recovery:
    def __init__(self, owner, parent, before_widget):
        self.owner = owner
        self.shown = None
        self.drafts = {name: ('', False, '') for name in PROVIDERS}
        self.failed = {name: None for name in PROVIDERS}
        self.before_widget = before_widget
        self.frame = tk.Frame(parent, bg=owner.pal['bg'])
        self.button = owner.button(self.frame, '重试上次请求', self.retry)
        self.button.pack(side='left')
        owner.label(self.frame, '重试会重新发送请求，可能再次计费；不会自动重试。').pack(side='left', padx=8)

    def text(self):
        return self.owner.prompt.get('1.0', 'end-1c')

    def scope(self):
        return self.owner.scope.get() if self.owner.scope_box is not None else ''

    def switch(self):
        owner = self.owner
        if self.shown is not None:
            self.drafts[self.shown] = self.text(), owner.include_document.get(), self.scope()
        self.shown = owner.name
        text, attached, scope = self.drafts[self.shown]
        owner.prompt.delete('1.0', 'end')
        owner.prompt.insert('1.0', text)
        owner.include_document.set(attached)
        if scope and owner.scope_box is not None:
            owner.scope.set(scope)
        elif owner.scope_box is not None:
            from .selection import SCOPE_LABELS
            owner.scope.set(SCOPE_LABELS['selection'])
        self.refresh()

    def refresh(self):
        available = self.failed[self.owner.name] is not None
        self.button.configure(state='normal' if available and not self.owner.busy else 'disabled')
        if available:
            self.frame.pack(fill='x', pady=4, before=self.before_widget)
        else:
            self.frame.pack_forget()

    def start(self, call, complete, kind, before=None):
        owner = self.owner
        attempt = Attempt(owner.name, call, complete, self.text(), kind, before,
                          self.scope(), owner.include_document.get())
        self.failed[owner.name] = None
        self.submit(attempt)

    def submit(self, attempt):
        self.owner.work(attempt.call, lambda result: self.succeeded(attempt, result),
                        lambda error: self.failure(attempt, error))

    def failure(self, attempt, error):
        owner = self.owner
        self.failed[attempt.provider] = attempt
        self.refresh()
        scope = attempt.scope if attempt.kind == 'selection' else '附上原文档' if attempt.attached else '仅对话'
        owner.feedback[attempt.provider] = ('请求失败：%s\n本次范围：%s。输入已保留。\n'
            '可修改连接设置后点「重试上次请求」；修改输入或发送范围后，请重新发送或重新选择选区改法。' % (error, scope))
        owner.render_history()
        owner.status_label.configure(text=error)

    def succeeded(self, attempt, result):
        self.failed[attempt.provider] = None
        self.refresh()
        attempt.complete(result)

    def retry(self):
        owner = self.owner
        if owner is None or owner.closed or owner.busy:
            return
        attempt = self.failed[owner.name]
        if attempt is None:
            return
        try:
            if self.text() != attempt.prompt:
                raise ValueError('输入已修改，请点击发送；选区修改请重新选择修改方式。')
            if attempt.kind == 'selection' and self.scope() != attempt.scope:
                raise ValueError('发送范围已修改，请重新选择选区修改方式。')
            if attempt.kind == 'chat' and owner.include_document.get() != attempt.attached:
                raise ValueError('附上文档的选择已修改，请点击发送以发起新请求。')
            if attempt.before is not None:
                if owner.get_snapshot is None:
                    raise ValueError('无法确认原文档身份，请重新发送。')
                snapshot = owner.get_snapshot()
                reason = stale_reason(attempt.before, snapshot['tab'], snapshot['text'])
                if reason:
                    raise ValueError(reason)
                if attempt.kind == 'selection':
                    current = owner.get_selection()
                    reason = stale_reason(attempt.before, current['tab'], current['text'])
                    if reason:
                        raise ValueError(reason)
                    if (current['start'], current['end']) != (attempt.before['start'], attempt.before['end']):
                        raise ValueError('选区已变化，请重新选择修改方式。')
            elif attempt.attached:
                raise ValueError('无法确认原文档身份，请点击发送以发起新请求。')
        except ValueError as exc:
            owner.status_label.configure(text=str(exc))
            return
        if owner.save():
            self.submit(attempt)
            owner.status_label.configure(text='正在重试上次请求，请稍候…')

    def clear(self):
        self.failed[self.owner.name] = None
        self.refresh()

    def close(self):
        self.failed.clear()
        self.drafts.clear()
        # Break the owner/controller cycle while still on the UI thread.
        self.owner = None
        self.before_widget = self.button = self.frame = None
