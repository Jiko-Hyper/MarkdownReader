"""Physical Windows key events against real Tk widgets and isolated documents."""
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch
from mdreader import winui
from tests import test_native_tabs, test_project_delete


class ShortcutTests(unittest.TestCase):
    setUp = test_native_tabs.NativeTabsTests.setUp
    destroy_window = staticmethod(test_native_tabs.NativeTabsTests.destroy_window)

    def key(self, target, letter=None, *, shift=False, caps=False, control=True, sequence=None):
        self.win.root.deiconify(); self.win.root.update()
        target.focus_force(); self.win.root.update()
        if sequence:
            target.event_generate(sequence)
        else:
            target.event_generate('<KeyPress>', keycode=ord(letter.upper()),
                                  state=(4 if control else 0) | (1 if shift else 0) | (2 if caps else 0))
        self.win.root.update()

    def source(self):
        self.win.open_local_files(self.files[:1]); self.win.show_source()
        return self.win.editor

    def test_global_shortcuts_across_focus_caps_and_shift_without_edit_side_effects(self):
        w=self.win; self.source()
        cases=[('s',False,'save_doc'),('s',True,'save_as'),('n',False,'new_loose_draft'),
               ('o',False,'open_local_files'),('w',False,'close_active_tab'),('e',False,'toggle_mode'),
               ('b',False,'toggle_sidebar'),('l',False,'show_outline'),('t',True,'insert_table_dialog'),
               ('v',True,'insert_clipboard_image'),('m',True,'format_selection')]
        for target in (w.root,w.editor,w.preview,w.recent_search,w.tree,w.root_tree,w.recent_list,w.cmb):
            if target is w.preview: w.mode='preview';w.render()
            elif target is w.editor: w.mode='source';w.show_source()
            with ExitStack() as stack:
                actions={name:stack.enter_context(patch.object(w,name)) for _,_,name in cases}
                w._bind_keys()
                for caps in (False,True):
                    for letter,shift,name in cases:
                        with self.subTest(focus=target.winfo_class(),letter=letter,shift=shift,caps=caps):
                            for action in actions.values(): action.reset_mock()
                            before=w.get_text(); cursor=w.editor.index('insert')
                            self.key(target,letter,shift=shift,caps=caps)
                            self.assertEqual(actions[name].call_count,1)
                            self.assertEqual([n for n,a in actions.items() if a.called],[name])
                            self.assertEqual(w.get_text(),before)
                            self.assertEqual(w.editor.index('insert'),cursor)
        # 8 focus targets x 11 shortcuts x 2 Caps Lock states = 176 cases.

    def test_open_cancel_and_new_file_do_not_change_original_buffer(self):
        w=self.win; editor=self.source(); before=w.get_text(); tab=w.active_tab
        editor.mark_set('insert','2.0')
        for caps in (False,True):
            with patch.object(w,'_ask_open_files',return_value=[]): self.key(editor,'o',caps=caps)
            self.assertEqual(w.get_text(),before);self.assertFalse(w.dirty)
            self.assertEqual(editor.index('insert'),'2.0')
        with patch.object(w,'_ask_open_files',return_value=self.files[1:2]):self.key(editor,'o')
        self.assertEqual(len(w.tabs),2)
        w.activate_tab(tab);self.assertEqual(w.get_text(),before)
        self.assertEqual(Path(self.files[0]).read_text('utf-8'),before)

    def test_save_save_as_new_close_next_and_reload(self):
        w=self.win; self.key(w.root,'n');self.assertEqual(len(w.tabs),1)
        w.editor.insert('1.0','saved by shortcut');w.root.update()
        first=Path(self.temp.name)/'save.md'; second=Path(self.temp.name)/'save-as.md'
        with patch.object(w,'_ask_save_path',return_value=str(first)):self.key(w.editor,'s',caps=True)
        self.assertEqual(first.read_text('utf-8'),'saved by shortcut')
        with patch.object(w,'_ask_save_path',return_value=str(second)):self.key(w.editor,'s',shift=True,caps=True)
        self.assertEqual(second.read_text('utf-8'),first.read_text('utf-8'))
        second.write_text('changed externally','utf-8');self.key(w.editor,sequence='<F5>')
        self.assertEqual(w.get_text(),'changed externally')
        tab=w.active_tab;self.key(w.text,'n');self.assertEqual(len(w.tabs),2)
        self.key(w.text,sequence='<Control-Tab>');self.assertIs(w.active_tab,tab)
        self.key(w.text,'w');self.assertEqual(len(w.tabs),1)

    def test_undo_redo_and_plain_edit_keys_stay_in_editor(self):
        w=self.win;editor=self.source();before=w.get_text()
        editor.edit_separator();editor.insert('end-1c',' added');editor.edit_separator();w.root.update()
        for caps in (False,True):
            self.key(editor,'z',caps=caps);self.assertEqual(w.get_text(),before)
            self.key(editor,'y',caps=caps);self.assertEqual(w.get_text(),before+' added')
        editor.mark_set('insert','1.0');self.key(editor,sequence='<Delete>')
        self.assertEqual(w.get_text(),(before+' added')[1:])

    def test_modified_navigation_keeps_selection_and_does_not_open_find(self):
        w=self.win;editor=self.source();before=w.get_text()
        editor.mark_set('insert','end-1c')
        self.key(editor,sequence='<Control-Shift-Home>')
        self.assertEqual(editor.get('sel.first','sel.last'),before)
        editor.tag_remove('sel','1.0','end');editor.mark_set('insert','1.0')
        self.key(editor,sequence='<Control-Shift-End>')
        self.assertEqual(editor.get('sel.first','sel.last'),before)
        with patch.object(w,'toggle_find') as find:
            w._bind_navigation(editor);w._bind_keys()
            self.key(editor,'f',shift=True)
            self.key(editor,sequence='<Control-Alt-f>')
            find.assert_not_called()

    def test_find_enter_shift_enter_escape_and_search_clear(self):
        w=self.win;editor=self.source();editor.insert('end-1c','\nmatch match match')
        self.key(editor,'f',caps=True);finder=w._find_dialog
        finder.query.set('match');finder.refresh()
        self.assertEqual(len(finder.matches),3)
        initial=finder.current;self.key(finder.entry,sequence='<Return>')
        self.assertEqual(finder.current,(initial+1)%3)
        self.key(finder.entry,sequence='<Shift-Return>');self.assertEqual(finder.current,initial)
        self.key(finder.entry,sequence='<Escape>');self.assertTrue(finder.closed)
        w.recent_query.set('something');self.key(w.recent_search,sequence='<Escape>')
        self.assertEqual(w.recent_query.get(),'')

    def test_outline_escape_mode_sidebar_and_document_edges(self):
        w=self.win;editor=self.source();self.key(editor,'l',caps=True)
        dialog=next(c for c in w.root.winfo_children() if isinstance(c,w._tk.Toplevel) and c.title()=='标题导航')
        self.key(dialog.winfo_children()[0],sequence='<Escape>');self.assertFalse(dialog.winfo_exists())
        for caps in (False,True):
            self.key(w.text,'e',caps=caps);self.assertEqual(w.mode,'preview')
            self.key(w.text,'e',caps=caps);self.assertEqual(w.mode,'source')
        before=w.side.winfo_manager();self.key(w.text,'b');self.assertNotEqual(w.side.winfo_manager(),before)
        self.key(w.text,'b');self.assertEqual(w.side.winfo_manager(),before)
        for sequence,index in [('<Control-End>','end-1c'),('<Control-Home>','1.0')]:
            self.key(w.text,sequence=sequence);self.assertEqual(w.text.index('insert'),w.text.index(index))
        before=w.base_size;w.text.event_generate('<Control-MouseWheel>',delta=120);w.root.update()
        self.assertEqual(w.base_size,min(20,before+1))

    def test_table_formula_and_undo_through_keys(self):
        w=self.win;editor=self.source();before=w.get_text()
        choice={'columns':2,'rows':1,'has_header':True,'header':['A','B'],
                'aligns':['left','right'],'fills':[['A','B'],['1','2']]}
        with patch.object(winui.TableDialog,'show',return_value=choice):self.key(editor,'t',shift=True,caps=True)
        self.assertIn('| A | B |',w.get_text());self.key(editor,'z');self.assertEqual(w.get_text(),before)
        self.key(editor,'m',shift=True,caps=True);self.assertIn('$',w.get_text())
        self.key(editor,'z');self.assertEqual(w.get_text(),before)
        self.key(w.text,'e',shift=True);self.assertEqual(w.mode,'preview')
        self.key(w.text,'e',shift=True,caps=True);self.assertEqual(w.mode,'source')

    def test_dialog_shortcuts_do_not_operate_on_document_behind_it(self):
        w=self.win; self.source();dialog=w._tk.Toplevel(w.root);entry=w._tk.Entry(dialog);entry.pack();w.root.update()
        with patch.object(w,'save_doc') as save,patch.object(w,'new_loose_draft') as new:
            w._bind_keys();self.key(entry,'s');self.key(entry,'n')
            save.assert_not_called();new.assert_not_called()
        self.assertNotIn(w._shortcut_tag,entry.bindtags());dialog.destroy()

    def test_clipboard_shortcut_uses_image_flow_without_pasting_text(self):
        from mdreader import media, media_ui
        from tests import test_image_insert_ui as image_tests
        w=self.win; editor=self.source()
        original=Path(self.temp.name)/'clipboard.png';original.write_bytes(image_tests.png_bytes())
        bridge=image_tests.FakeBridge({'markdown':'![image](assets/clipboard.png)\n'})
        command={'plugin':'mdreader.image-insert','command':'mdreader.image-insert.insert'}
        with patch.object(w,'plugin_commands',return_value=[command]), \
             patch.object(media,'clipboard_image',return_value=str(original)), \
             patch.object(media_ui,'choose_width',return_value=480), \
             patch.object(w,'plugin_bridge',return_value=bridge):
            self.key(editor,'v',shift=True,caps=True)
        self.assertEqual(bridge.calls[0][0],'/api/plugins/insert')
        self.assertEqual(bridge.calls[0][1]['options'],{'width':480})
        self.assertIn('![image](assets/clipboard.png)',w.get_text())
        self.assertTrue(original.exists())

    def test_ai_send_and_escape_are_local_to_assistant(self):
        from mdreader import core
        w=self.win;self.source()
        w.connection_api=core.Api(w.ws,str(Path(core.__file__).resolve().parents[1]/'webui'))
        w.show_ai_connection();dialog=w._assistant_dialog
        dialog.prompt.insert('1.0','test request')
        with patch.object(dialog,'send') as send:
            self.key(dialog.prompt,sequence='<Control-Return>')
            send.assert_called_once()
            self.assertEqual(dialog.prompt.get('1.0','end-1c'),'test request')
        self.key(dialog.prompt,sequence='<Escape>');self.assertTrue(dialog.closed)

    def test_navigation_accelerator_locates_selection_and_wheel_zoom_works_in_both_views(self):
        """说明里承诺的是"切换并定位选中文字"，这里断言真的定位到同一段。"""
        from mdreader.navigation import widget_index, widget_text
        w = self.win
        page = Path(self.temp.name) / '定位.md'
        text = '# 标题\n\n第一段保持原样。\n\n第二段要被改。\n\n第三段保持原样。\n'
        page.write_text(text, encoding='utf-8')
        w.open_local_files([str(page)])
        w.root.update()
        self.assertEqual(w.mode, 'preview')
        phrase = '第二段要被改。'
        rendered = widget_text(w.preview)
        at = rendered.index(phrase)
        w.preview.tag_add('sel', widget_index(w.preview, rendered, at),
                          widget_index(w.preview, rendered, at + len(phrase)))
        self.key(w.preview, sequence='<Control-Shift-E>')
        self.assertEqual(w.mode, 'source')
        self.assertEqual(w.editor.get('sel.first', 'sel.last'), phrase)
        start = text.index(phrase)
        w.editor.tag_add('sel', '1.0+%dc' % start, '1.0+%dc' % (start + len(phrase)))
        self.key(w.editor, sequence='<Control-Shift-E>')
        self.assertEqual(w.mode, 'preview')
        self.assertEqual(w.preview.get('sel.first', 'sel.last'), phrase)
        for widget in (w.preview, w.editor):
            before = w.base_size
            widget.event_generate('<Control-MouseWheel>', delta=120)
            w.root.update()
            self.assertEqual(w.base_size, min(20, before + 1))
            widget.event_generate('<Control-MouseWheel>', delta=-120)
            w.root.update()
            self.assertEqual(w.base_size, before)

    def test_scan_page_prompt_enter_escape_and_invalid_page(self):
        from mdreader.scan_ui import choose_pages
        w=self.win
        for value,expected in [('2,4-6','2,4-6'),('bad',None),(None,None)]:
            errors=[]
            def act():
                dialog=next(c for c in w.root.winfo_children() if isinstance(c,w._tk.Toplevel) and c.title()=='扫描 PDF 识别')
                try:
                    entry=next(c for c in dialog.winfo_children() if isinstance(c,w._tk.Entry))
                    if value is not None:
                        entry.delete(0,'end');entry.insert(0,value);self.key(entry,sequence='<Return>')
                    if expected is None:
                        self.assertTrue(dialog.winfo_exists());self.key(entry,sequence='<Escape>')
                except Exception as exc:
                    errors.append(exc)
                    if dialog.winfo_exists():dialog.destroy()
            w.root.after(50,act)
            self.assertEqual(choose_pages(w),expected)
            if errors:raise errors[0]

    def test_pdf_preview_arrows_and_escape(self):
        from mdreader.pdf_preview import Preview
        from PIL import Image
        path=Path(self.temp.name)/'pages.pdf'
        with Image.new('RGB',(300,400),'white') as first, Image.new('RGB',(300,400),'gray') as second:
            first.save(path,save_all=True,append_images=[second])
        preview=Preview(self.win,str(path));preview.dialog.deiconify()
        self.key(preview.canvas,sequence='<Right>');self.assertEqual(preview.number,1)
        self.key(preview.canvas,sequence='<Left>');self.assertEqual(preview.number,0)
        self.key(preview.canvas,sequence='<Escape>');self.assertTrue(preview.closed)
        self.assertFalse(preview.result)


class ProjectShortcutTests(unittest.TestCase):
    setUp = ShortcutTests.setUp
    destroy_window = staticmethod(ShortcutTests.destroy_window)
    key = ShortcutTests.key
    prepare=test_project_delete.ProjectDeleteTests.prepare
    select=test_project_delete.ProjectDeleteTests.select
    recycle=test_project_delete.ProjectDeleteTests.recycle

    def test_project_rename_address_delete_and_recent_record_preserve_scope(self):
        w=self.prepare();old=self.pdir/'docs/子目录/文档.md';self.select('docs/子目录/文档.md')
        with patch.object(winui.RenamePrompt,'show',return_value='renamed'):self.key(w.tree,'m',caps=True)
        new=old.with_name('renamed.md');self.assertTrue(new.exists());self.assertFalse(old.exists())
        self.select('docs/子目录/renamed.md')
        self.key(w.tree,sequence='<Return>')
        self.assertEqual(w.cur_doc['id'],'docs/子目录/renamed.md')
        with patch.object(winui.PathDialog,'show',return_value=None) as shown:self.key(w.tree,'y',caps=True)
        shown.assert_called_once()
        with patch.object(winui.SavePrompt,'show',return_value=None),patch('mdreader.core._send_to_recycle_bin') as recycle:
            self.key(w.tree,sequence='<Delete>');recycle.assert_not_called()
        self.assertTrue(new.exists())
        with patch.object(winui.SavePrompt,'show',return_value=True),patch('mdreader.core._send_to_recycle_bin',side_effect=self.recycle):
            self.key(w.tree,sequence='<Delete>')
        self.assertFalse(new.exists());self.assertTrue((Path(self.temp.name)/'recycled').exists())
        w.open_local_files(self.files[:1]);w.recent_list.selection_set(0)
        count=w.recent_list.size();self.key(w.recent_list,sequence='<Return>')
        self.assertEqual(w.cur_loose['path'],self.files[0])
        self.key(w.recent_list,sequence='<Delete>');self.assertTrue(Path(self.files[0]).exists())
        self.assertEqual(w.recent_list.size(),count-1)

if __name__=='__main__': unittest.main()
