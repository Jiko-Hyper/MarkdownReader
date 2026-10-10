import gc
import tempfile
import threading
import time
import tkinter as tk
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch
from mdreader import core, winui, selection
from mdreader.ai_assistant_ui import AssistantDialog
from mdreader.ai_providers import PROVIDERS, ProviderStore
from tests import test_ai_changes as fixtures


class RecoveryWindowTests(unittest.TestCase):
    original = fixtures.ReviewWindowTests.original
    setUp = fixtures.ReviewWindowTests.setUp
    cleanup = fixtures.ReviewWindowTests.cleanup
    wait = fixtures.ReviewWindowTests.wait

    def input(self, text):
        self.dialog.prompt.delete('1.0', 'end')
        self.dialog.prompt.insert('1.0', text)

    def fail_request(self):
        self.input('请修改这段文字')
        with patch.object(self.dialog.store, 'chat', side_effect=OSError('连接超时')) as call:
            self.dialog.send(); self.wait()
            self.assertEqual(call.call_count, 1)
        return self.dialog.recovery.failed[self.dialog.name]

    def test_manual_retry_preserves_input_and_does_not_duplicate_history(self):
        self.dialog.include_document.set(False)
        self.input('保留的问题😀')
        with patch.object(self.dialog.store, 'chat', side_effect=[OSError('连接超时'), {'text':'成功回复'}]) as call:
            self.dialog.send(); self.wait()
            self.assertEqual(call.call_count, 1)
            self.assertEqual(self.dialog.prompt.get('1.0','end-1c'), '保留的问题😀')
            self.assertEqual(self.dialog.history[self.dialog.name], [])
            self.assertEqual(self.dialog.recovery.button.cget('bg'),self.win.pal['button'])
            self.dialog.recovery.button.invoke()
            self.dialog.recovery.retry()  # rapid repeated action cannot send twice
            self.wait()
            self.assertEqual(call.call_count, 2)
            self.assertEqual(call.call_args_list[0], call.call_args_list[1])
        self.assertEqual(len(self.dialog.history[self.dialog.name]), 2)
        self.assertIsNone(self.dialog.recovery.failed[self.dialog.name])
        self.assertEqual(self.dialog.prompt.get('1.0','end-1c'), '')
        self.assertEqual(self.file.read_text(encoding='utf-8'), self.original)

    def test_edited_input_requires_a_new_send_and_keeps_the_new_draft(self):
        self.fail_request()
        self.input('改成新的要求')
        with patch.object(self.dialog.store, 'chat', return_value={'text':'新回复'}) as call:
            self.dialog.recovery.retry()
            call.assert_not_called()
            self.assertIn('输入已修改', self.dialog.status_label.cget('text'))
            self.dialog.send(); self.wait()
            self.assertEqual(call.call_args.args[0]['messages'][-1]['content'], '改成新的要求')

    def test_changed_document_and_tab_are_not_sent_again(self):
        self.win.show_source(); self.fail_request()
        self.win.editor.insert('end-1c','手工编辑')
        with patch.object(self.dialog.store,'chat') as call:
            self.dialog.recovery.retry()
            call.assert_not_called()
            self.assertIn('变化',self.dialog.status_label.cget('text'))
            other=Path(self.temp.name)/'其他.md';other.write_text('其他正文',encoding='utf-8')
            self.win.open_local_files([str(other)])
            self.dialog.recovery.retry()
            call.assert_not_called()
            self.assertIn('切换',self.dialog.status_label.cget('text'))
        self.assertEqual(other.read_text(encoding='utf-8'),'其他正文')

    def test_attachment_choice_change_is_not_silently_ignored(self):
        self.fail_request()
        self.dialog.include_document.set(False)
        with patch.object(self.dialog.store,'chat') as call:
            self.dialog.recovery.retry()
            call.assert_not_called()
        self.assertIn('附上文档',self.dialog.status_label.cget('text'))

    def test_provider_drafts_failures_and_themes_are_isolated(self):
        self.win.root.deiconify()
        self.dialog.window.deiconify()
        self.dialog.include_document.set(False)
        attempt=self.fail_request()
        self.win.root.update()
        for widget in (self.dialog.recovery.button,self.dialog.send_button,self.dialog.prompt):
            self.assertTrue(widget.winfo_viewable())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),
                                 self.dialog.window.winfo_rooty()+self.dialog.window.winfo_height())
        first=self.dialog.name
        other=next(name for name in PROVIDERS if name!=first)
        self.dialog.provider.set(PROVIDERS[other]['label']);self.dialog.select_provider()
        self.assertEqual(self.dialog.prompt.get('1.0','end-1c'),'')
        self.assertIsNone(self.dialog.recovery.failed[other])
        self.input('另一个服务商的草稿')
        self.dialog.provider.set(PROVIDERS[first]['label']);self.dialog.select_provider()
        self.assertEqual(self.dialog.prompt.get('1.0','end-1c'), '请修改这段文字')
        self.assertIs(self.dialog.recovery.failed[first],attempt)
        for theme in core.THEME_CHOICES:
            self.win.choose_theme(theme)
            self.assertEqual(self.dialog.recovery.frame.cget('bg'),self.win.pal['bg'])
        self.dialog.clear_chat()
        self.assertIsNone(self.dialog.recovery.failed[first])
        self.assertEqual(self.dialog.prompt.get('1.0','end-1c'),'请修改这段文字')
        self.dialog.provider.set(PROVIDERS[other]['label']);self.dialog.select_provider()
        self.assertEqual(self.dialog.prompt.get('1.0','end-1c'),'另一个服务商的草稿')

    def test_selection_retry_checks_scope_and_range_then_applies_once(self):
        self.win.show_source()
        self.win.editor.tag_add('sel','7.0','7.end')
        self.input('精简这里')
        with patch.object(self.dialog.store,'chat',side_effect=OSError('超时')):
            self.dialog.request_selection('custom');self.wait()
        attempt=self.dialog.recovery.failed[self.dialog.name]
        self.assertIsNotNone(attempt)
        with patch.object(self.dialog.store,'chat',return_value={'text':'建议','replacement':'精简后😀'}) as call:
            self.dialog.scope.set(selection.SCOPE_LABELS['document'])
            self.dialog.recovery.retry();call.assert_not_called()
            self.assertIn('范围',self.dialog.status_label.cget('text'))
            self.dialog.scope.set(selection.SCOPE_LABELS['selection'])
            self.win.editor.tag_remove('sel','1.0','end');self.win.editor.tag_add('sel','3.0','3.end')
            self.dialog.recovery.retry();call.assert_not_called()
            self.assertIn('选区',self.dialog.status_label.cget('text'))
            self.win.editor.tag_remove('sel','1.0','end');self.win.editor.tag_add('sel','7.0','7.end')
            self.dialog.recovery.retry();self.wait()
            self.assertEqual(call.call_count,1)
            self.assertEqual(call.call_args.args[0]['selection']['outgoing'],'第二处旧文字😀')
        self.dialog.apply_selection_proposal()
        self.assertEqual(self.win.get_text(),self.original.replace('第二处旧文字😀','精简后😀'))
        self.win.editor.edit_undo();self.win.root.update()
        self.assertEqual(self.win.get_text(),self.original)
        self.assertEqual(self.file.read_text(encoding='utf-8'),self.original)

    def test_connection_test_does_not_replace_failed_document_request(self):
        attempt=self.fail_request()
        with patch.object(self.dialog.store,'chat',return_value={'text':'OK'}):
            self.dialog.test();self.wait()
        self.assertIs(self.dialog.recovery.failed[self.dialog.name],attempt)
        self.assertEqual(self.dialog.recovery.button.cget('state'),'normal')

    def test_selection_after_emoji_does_not_shift_and_restores_selected_result(self):
        from mdreader.navigation import widget_index
        self.win.show_source()
        text='😀前😀 目标🚀 后面😀\n尾行'
        self.win.editor.delete('1.0','end');self.win.editor.insert('1.0',text)
        start=text.index('目标');end=start+len('目标🚀')
        self.win.editor.tag_add('sel',widget_index(self.win.editor,text,start),
                               widget_index(self.win.editor,text,end))
        before=self.dialog.get_selection()
        self.assertEqual((before['start'],before['end']),(start,end))
        self.dialog.apply_selection(before,'新😀')
        self.assertEqual(self.win.get_text(),text[:start]+'新😀'+text[end:])
        self.assertEqual(self.win.editor.get('sel.first','sel.last'),'新😀')
        self.win.editor.edit_undo();self.win.root.update()
        self.assertEqual(self.win.get_text(),text)


class RecoveryLifetimeTests(unittest.TestCase):
    def test_close_during_retry_releases_ui_and_does_not_report_cancellation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw()
            self.addCleanup(root.destroy)
            store=ProviderStore(folder)
            dialog=AssistantDialog(root,store,winui.LIGHT)
            dialog.prompt.insert('1.0','问题')
            with patch.object(store,'chat',side_effect=OSError('超时')):
                dialog.send()
                deadline=time.monotonic()+3
                while dialog.busy and time.monotonic()<deadline:
                    root.update();time.sleep(.01)
            started,release=threading.Event(),threading.Event()
            self.addCleanup(release.set)
            def delayed(*args,**kwargs):
                started.set();release.wait(3)
                return {'text':'远端完成'}
            with patch.object(store,'chat',side_effect=delayed):
                dialog.recovery.retry()
                self.assertTrue(started.wait(1))
                dialog.close()
                ref=weakref.ref(dialog)
                del dialog
                gc.collect()
                self.assertIsNone(ref())
                release.set()
