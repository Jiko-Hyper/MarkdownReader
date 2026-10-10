import random
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from mdreader import core, winui
from mdreader.ai_changes import Review


class ChangeTests(unittest.TestCase):
    def session(self,original,replacement):
        self.tab=object()
        self.text=original
        return Review({'tab':self.tab,'text':original},replacement)

    def snapshot(self):
        return {'tab':self.tab,'text':self.text}

    def apply(self,before,text):
        self.assertIs(before['tab'],self.tab)
        self.assertEqual(before['text'],self.text)
        self.text=text

    def test_only_second_change_is_accepted_and_offsets_remain_correct(self):
        original='old1\n\n重复\n\nold2😀\n\n重复\n\nold3\n'
        review=self.session(original,original.replace('old','new and longer'))
        self.assertEqual(len(review.changes),3)
        review.accept([1],self.snapshot,self.apply)
        self.assertEqual(self.text,original.replace('old2','new and longer2'))
        review.retain(0,self.snapshot)
        review.accept(review.pending(),self.snapshot,self.apply)
        self.assertEqual(self.text,original.replace('old2','new and longer2').replace('old3','new and longer3'))
        self.assertEqual(review.state(0),'保留原文')

    def test_insert_delete_crlf_and_final_newline_are_exact(self):
        original='标题\r\n\r\n删掉\r\n\r\n尾部'
        replacement='新内容😀\r\n标题\r\n\r\n\r\n尾部\r\n'
        review=self.session(original,replacement)
        review.accept(review.pending(),self.snapshot,self.apply)
        self.assertEqual(self.text,replacement)

    def test_undo_redo_reconcile_and_a_new_accept_branches_history(self):
        review=self.session('a\n\nb\n\nc\n','A\n\nB\n\nC\n')
        review.accept([0],self.snapshot,self.apply);first=self.text
        review.accept([1],self.snapshot,self.apply);second=self.text
        self.text=first;review.refresh(self.snapshot())
        self.assertEqual(review.accepted,{0})
        self.text=second;review.refresh(self.snapshot())
        self.assertEqual(review.accepted,{0,1})
        self.text=first;review.accept([2],self.snapshot,self.apply)
        self.assertEqual(self.text,'A\n\nb\n\nC\n')
        self.text=second
        with self.assertRaisesRegex(ValueError,'变化'):
            review.refresh(self.snapshot())

    def test_document_changes_switches_and_callback_failure_never_commit(self):
        review=self.session('old','new')
        with self.assertRaisesRegex(ValueError,'切换'):
            review.accept([0],lambda:{'tab':object(),'text':'old'},self.apply)
        self.text='hand edit'
        with self.assertRaisesRegex(ValueError,'变化'):
            review.accept([0],self.snapshot,self.apply)
        self.text='old'
        def fail(*_args):
            raise ValueError('请先完成输入法输入。')
        with self.assertRaisesRegex(ValueError,'输入法'):
            review.accept([0],self.snapshot,fail)
        self.assertEqual(review.accepted,set())
        self.assertEqual(review.expected,'old')

    def test_noop_repeated_accept_and_large_proposal(self):
        review=self.session('old','new')
        review.accept([0],self.snapshot,self.apply)
        with self.assertRaises(ValueError):
            review.accept([0],self.snapshot,self.apply)
        self.assertEqual(self.text,'new')
        review=self.session('a\n'*3001,'b\n'*3001)
        self.assertTrue(review.merged)
        self.assertEqual(len(review.changes),1)
        review.accept(review.pending(),self.snapshot,self.apply)
        self.assertEqual(self.text,'b\n'*3001)

    def test_arbitrary_repeated_lines_compose_the_exact_proposal(self):
        rng=random.Random(901)
        for _ in range(80):
            original=''.join(rng.choice(['重复\n','😀\n','\n','a\r\n','x']) for _ in range(30))
            replacement=''.join(rng.choice(['重复\n','😀\n','\n','a\r\n','y']) for _ in range(30))
            review=self.session(original,replacement)
            review.accept(review.pending(),self.snapshot,self.apply)
            self.assertEqual(self.text,replacement)

    def test_related_code_math_tables_and_references_are_reviewed_together(self):
        pairs=[
            ('```python\nold\n\nkeep\n```\n','~~~python\nnew\n\nkeep\n~~~\n'),
            ('$$\nold\n\nkeep\nold\n$$\n','$$\nnew\n\nkeep\nnew\n$$\n'),
            ('| old | n |\n| --- | --- |\n| old | 1 |\n','| new | n |\n| --- | --- |\n| new | 1 |\n'),
            ('[text][old]\n\n说明\n\n[old]: a.md\n','[text][new]\n\n说明\n\n[new]: b.md\n'),
            ('<div>\n\n内容\n\n</div>\n','<section>\n\n内容\n\n</section>\n')]
        for original,replacement in pairs:
            with self.subTest(original=original):
                review=self.session(original,replacement)
                self.assertEqual(len(review.changes),1)
                review.accept([0],self.snapshot,self.apply)
                self.assertEqual(self.text,replacement)


class ReviewWindowTests(unittest.TestCase):
    original='# 文稿\n\n第一处旧文字\n\n重复段落\n\n第二处旧文字😀\n\n重复段落\n\n第三处旧文字\n'

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ai-change-review-')
        self.addCleanup(self.temp.cleanup)
        self.ws=core.Workspace(self.temp.name)
        self.addCleanup(self.ws.plugins.shutdown)
        self.api=core.Api(self.ws,str(Path(core.__file__).resolve().parents[1]/'webui'))
        with patch.object(winui.MarkdownWindow,'enable_file_drop'):
            self.win=winui.MarkdownWindow(self.temp.name,connection_api=self.api)
        self.win.root.withdraw()
        self.addCleanup(self.cleanup)
        self.file=Path(self.temp.name)/'文稿.md'
        self.file.write_text(self.original,encoding='utf-8')
        self.win.open_local_files([str(self.file)])
        self.win.show_ai_connection()
        self.dialog=self.win._assistant_dialog
        self.dialog.window.withdraw()
        self.dialog.include_document.set(True)
        self.replacement=self.original.replace('旧文字','新文字，更详细')

    def cleanup(self):
        if not self.dialog.closed:
            self.dialog.close()
        for timer in self.win.root.tk.call('after','info'):
            self.win.root.after_cancel(timer)
        self.win.ws.plugins.shutdown()
        self.win.root.destroy()

    def wait(self):
        deadline=time.monotonic()+5
        while self.dialog.busy and time.monotonic()<deadline:
            self.win.root.update()
            time.sleep(.01)
        self.assertFalse(self.dialog.busy)

    def propose(self):
        self.dialog.prompt.insert('1.0','改三处文字')
        with patch.object(self.dialog.store,'chat',return_value={'text':'三处建议','document':self.replacement}):
            self.dialog.send()
            self.wait()
        self.assertIsNotNone(self.dialog.proposals[self.dialog.name])
        self.dialog.review_button.invoke()
        self.wait()
        return self.dialog.review_window

    def select(self,review,index):
        review.list.selection_clear(0,'end');review.list.selection_set(index);review.show_change()

    def test_actual_request_partial_accept_keep_all_and_undo(self):
        self.win.show_source()
        review=self.propose()
        self.select(review,1);review.accept_button.invoke()
        only_second=self.original.replace('第二处旧文字','第二处新文字，更详细')
        self.assertEqual(self.win.get_text(),only_second)
        self.assertEqual(self.file.read_text(encoding='utf-8'),self.original)
        review.close()
        self.win.editor.edit_undo()
        self.win.root.update()
        self.assertEqual(self.win.get_text(),self.original)
        self.dialog.review_button.invoke();review=self.dialog.review_window
        self.assertEqual(review.review.accepted,set())
        self.select(review,0);review.keep_button.invoke()
        self.select(review,2);review.accept_button.invoke()
        before_all=self.win.get_text()
        review.close()
        self.dialog.apply_button.invoke()
        self.assertEqual(self.win.get_text(),self.replacement.replace('第一处新文字，更详细','第一处旧文字'))
        self.win.editor.edit_undo();self.win.root.update()
        self.assertEqual(self.win.get_text(),before_all)
        self.dialog.review_button.invoke();review=self.dialog.review_window
        self.assertEqual(review.review.accepted,{2})
        self.assertEqual(review.review.state(0),'保留原文')
        self.assertEqual(self.file.read_text(encoding='utf-8'),self.original)

    def test_external_edits_and_switching_tabs_disable_old_suggestions(self):
        self.win.show_source();review=self.propose()
        self.win.editor.insert('end-1c','手动补充')
        text=self.win.get_text();review.perform('all')
        self.assertEqual(self.win.get_text(),text)
        self.assertIn('变化',review.notice.cget('text'))
        self.assertEqual(review.all_button.cget('state'),'disabled')
        other=Path(self.temp.name)/'另一篇.md';other.write_text('另一篇原文',encoding='utf-8')
        self.win.open_local_files([str(other)])
        review.perform('accept')
        self.assertEqual(self.win.get_text(),'另一篇原文')
        self.assertIn('切换',review.notice.cget('text'))
        self.assertEqual(self.file.read_text(encoding='utf-8'),self.original)

    def test_three_themes_details_preview_mode_and_close_keep_data(self):
        self.replacement=self.original.replace('第二处旧文字😀','`code=42` [链接](new.md)\n| 名称 | 数值 |\n| --- | --- |\n| 示例 | 100 |')
        review=self.propose()
        for theme in core.THEME_CHOICES:
            self.win.choose_theme(theme)
            self.assertEqual(review.window.cget('bg'),self.win.pal['bg'])
            self.assertEqual(review.texts[1].cget('bg'),self.win.pal['side'])
        self.assertIn('code=42',review.texts[1].get('1.0','end'))
        self.assertIn('new.md',review.texts[1].get('1.0','end'))
        self.assertEqual(self.win.mode,'preview')
        review.accept_button.invoke()
        self.assertEqual(self.win.mode,'preview')
        self.assertEqual(self.win.get_text(),self.replacement)
        self.assertIn('100',self.win.preview.get('1.0','end'))
        review.close()
        self.win.show_source();self.win.editor.edit_undo();self.win.root.update()
        self.assertEqual(self.win.get_text(),self.original)
        self.assertEqual(self.file.read_text(encoding='utf-8'),self.original)

    def test_input_method_failure_and_close_do_not_mark_changes_applied(self):
        review=self.propose()
        with patch.object(self.win,'composing',return_value=True):
            review.perform('all')
        self.assertIn('输入法',review.notice.cget('text'))
        self.assertEqual(review.review.accepted,set())
        self.assertEqual(self.win.get_text(),self.original)
        self.dialog.close()
        self.assertTrue(review.closed)
        self.assertEqual(self.win.get_text(),self.original)
