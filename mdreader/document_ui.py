"""Desktop document safety and navigation, separated from the main layout."""
import difflib
import json
import os
import re
import time
from . import documents as D
from .storage import atomic_write

class DocumentActions:
    def recovery_identity(self, tab):
        info = tab.get('loose') or tab.get('doc') or {}
        return info.get('path') or info.get('_abs') or str(tab['key'])

    def flush_recovery(self):
        self._recovery_job = None
        self._recovery_last = time.monotonic()
        self.stash_tab()
        for tab in self.tabs:
            if not tab.get('dirty'): continue
            info = tab.get('loose') or tab.get('doc') or {}
            try:
                self.ws.recovery.save(self.recovery_identity(tab), tab['source'],
                                      info.get('path') or info.get('_abs') or '', info.get('revision', 'missing'))
            except (OSError, ValueError) as exc:
                self.notice('恢复快照未写入：%s' % exc, error=True)

    def offer_recovery(self):
        # 插件任务运行时会**借用事件循环**（`media_ui.run_job` 用 wait_variable 让界面继续
        # 重绘）。这时弹一个模态的“要恢复吗”会把用户和任务一起卡在对话框后面，所以
        # 只要还有插件任务在跑，就把这次询问往后挪。
        if getattr(self, '_plugin_busy', False):
            self.root.after(3000, self.offer_recovery)
            return
        records = self.ws.recovery.list()
        if not records: return
        self.show_recovery()

    def show_recovery(self):
        from tkinter import messagebox
        records = self.ws.recovery.list()
        if not records:
            self.notice('没有待恢复快照'); return
        names = '\n'.join(r.get('path') or r.get('identity') or ('损坏快照：'+r['key']) for r in records)
        if not messagebox.askyesno('恢复未保存文档', '发现以下快照（原文件不会被覆盖）：\n'+names+'\n\n现在打开恢复？', parent=self.root): return
        for record in records:
            if 'error' in record:
                self.notice('快照损坏，已保留：'+record['key'], error=True); continue
            path = record.get('path')
            if path and os.path.isfile(path):
                self.open_local_files([path])
                self.cur_loose['revision'] = record.get('baseline', 'missing')
            else:
                self.new_loose_draft()
            self.mode = 'source'
            self.source = record['text']
            self.show_source(record['text'])    # the tab's own editor, not the preview
            self.set_dirty(True)

    def resolve_conflict(self, conflict):
        tk = self._tk
        dialog = tk.Toplevel(self.root); dialog.title('磁盘版本冲突 — 未覆盖原文件')
        dialog.transient(self.root); dialog.configure(bg=self.pal['bg'])
        local = self.source
        try: disk = D.snapshot(conflict.path)['text']
        except (OSError, ValueError): disk = '（文件不存在或无法读取）'
        view = tk.Text(dialog, width=80, height=20, bg=self.pal['bg'], fg=self.pal['fg'], bd=0)
        view.pack(fill='both', expand=True, padx=16, pady=16)
        view.insert('1.0', ''.join(difflib.unified_diff(disk.splitlines(True), local.splitlines(True),
                                                     fromfile='磁盘内容', tofile='当前编辑')) or '内容相同，磁盘编码或换行已改变。')
        view.configure(state='disabled')
        result = [None]
        def finish(value): result[0] = value; dialog.destroy()
        actions = tk.Frame(dialog, bg=self.pal['bg']); actions.pack(pady=12)
        choices = [('取消，保留编辑', None), ('另存为', 'saveas')]
        if conflict.current != 'missing': choices.append(('备份后覆盖磁盘版本', 'overwrite'))
        for label, choice in choices:
            tk.Button(actions, text=label, command=lambda c=choice: finish(c),
                      bg=self.pal['button'], fg=self.pal['fg'], relief='flat').pack(side='left', padx=8)
        dialog.bind('<Escape>', lambda _e: finish(None)); dialog.update_idletasks()
        dialog.geometry('+%d+%d' % (max(0,self.root.winfo_rootx()+80), max(0,self.root.winfo_rooty()+80)))
        dialog.grab_set(); self.root.wait_window(dialog)
        if result[0] == 'saveas': return self.save_as()
        if result[0] != 'overwrite': return False
        try:
            if self.cur_loose:
                saved = self.loose.save(self.cur_loose['id'], local, conflict.path,
                                        expected=self.cur_loose.get('revision'), overwrite=conflict.current)
                self.cur_loose = saved['info']
                self.active_tab['key'] = self._tab_key(loose=self.cur_loose)
            else:
                self.cur_doc = self.ws.save_doc(self.ws.require_project(self.cur_pid), self.cur_doc['id'], local,
                                                expected=self.cur_doc.get('revision'), overwrite=conflict.current)
        except D.ConflictError as again:
            return self.resolve_conflict(again)
        except Exception as exc:
            self.notice('保存失败，编辑已保留：'+str(exc), error=True); return False
        self.set_dirty(False); self.mark_baseline()
        self.draw_tabs(); self.refresh_recent()
        self.notice('已保存；覆盖前的磁盘版本保留在恢复目录 conflicts 中')
        return True

    def save_as(self):
        if not self.active_tab: return False
        self.stash_tab()
        path = self._ask_save_path((self.cur_loose or self.cur_doc).get('file', 'Untitled.md'))
        if not path: return False
        try:
            D.write_checked(path, self.source, 'missing')
        except D.ConflictError as exc:
            from tkinter import messagebox
            if not messagebox.askyesno('确认覆盖', '目标已存在。备份后覆盖？\n'+path, parent=self.root): return False
            try: D.write_checked(path, self.source, 'missing', exc.current, os.path.join(self.ws.root,'.recovery','conflicts'))
            except Exception as error:
                self.notice(str(error), error=True); return False
        except Exception as exc:
            self.notice(str(exc), error=True); return False
        identity = self.recovery_identity(self.active_tab)
        self.ws.recovery.discard(identity, self.source)
        self.cur_loose = self.loose.open_path(path); self.cur_doc = None
        self.active_tab['key'] = self._tab_key(loose=self.cur_loose)
        self.set_dirty(False); self.mark_baseline()
        self.draw_tabs(); self.refresh_recent()
        return True

    def find_in_document(self):
        tk = self._tk
        dialog = tk.Toplevel(self.root); dialog.title('文档内查找')
        dialog.transient(self.root); dialog.configure(bg=self.pal['bg'])
        query = tk.StringVar(); entry = tk.Entry(dialog, textvariable=query, bg=self.pal['bg'], fg=self.pal['fg'])
        entry.pack(fill='x', padx=12, pady=12)
        status = tk.Label(dialog, bg=self.pal['bg'], fg=self.pal['fg']); status.pack()
        position = ['1.0']
        def find(back=False):
            self.text.tag_remove('find_hit', '1.0','end')
            needle = query.get()
            if not needle: return
            start = self.text.index(position[0]+'-1c') if back else position[0]
            hit = self.text.search(needle, start, stopindex='1.0' if back else 'end', backwards=back, nocase=True)
            if not hit: hit = self.text.search(needle, 'end' if back else '1.0', backwards=back, nocase=True)
            if not hit: status.configure(text='无匹配结果'); return
            end = self.text.index('%s+%dc' % (hit,len(needle)))
            self.text.tag_configure('find_hit', background=self.pal['sel'], foreground=self.pal['fg'])
            self.text.tag_add('find_hit', hit, end); self.text.see(hit)
            position[0] = hit if back else end; status.configure(text='位置 '+hit)
        for label, backwards in [('上一处',True),('下一处',False)]:
            tk.Button(dialog,text=label,command=lambda b=backwards:find(b)).pack(side='left',padx=12,pady=12)
        entry.bind('<Return>',lambda _e:find()); entry.focus_set()
        def close(): self.text.tag_remove('find_hit','1.0','end'); dialog.destroy()
        dialog.protocol('WM_DELETE_WINDOW',close); dialog.bind('<Escape>',lambda _e:close())

    def show_outline(self):
        tk = self._tk; dialog=tk.Toplevel(self.root); dialog.title('标题导航')
        box=tk.Listbox(dialog,width=50,height=22,bg=self.pal['bg'],fg=self.pal['fg']); box.pack(fill='both',expand=True)
        headings=[]
        if self.mode == 'source':
            fence=False
            for line,text in enumerate(self.get_text().splitlines(),1):
                if re.match(r'^\s*(```|~~~)',text): fence=not fence
                match=re.match(r'^(#{1,6})\s+(.+)',text)
                if match and not fence: headings.append((str(line)+'.0',len(match[1]),match[2]))
        else:
            for level in range(1,7):
                ranges=self.text.tag_ranges('h'+str(level))
                for start,end in zip(ranges[::2],ranges[1::2]):
                    headings.append((str(start),level,self.text.get(start,end).strip()))
            headings.sort(key=lambda row:tuple(map(int,row[0].split('.'))))
        for _,level,title in headings: box.insert('end','  '*(level-1)+title)
        def jump(_e=None):
            if box.curselection(): self.text.see(headings[box.curselection()[0]][0])
        box.bind('<<ListboxSelect>>',jump)

    def save_reading_positions(self):
        self.stash_tab()
        positions={self.recovery_identity(t):t.get('scroll',0) for t in self.tabs}
        path=os.path.join(self.ws.root,'reading-positions.json')
        try:
            try:
                with open(path,encoding='utf-8') as stream: old=json.load(stream)
            except (OSError,ValueError): old={}
            old.update(positions); atomic_write(path,json.dumps(old,ensure_ascii=False))
        except OSError as exc: self.notice('阅读位置未保存：'+str(exc),error=True)

    def restore_reading_position(self):
        if not self.active_tab:return
        try:
            with open(os.path.join(self.ws.root,'reading-positions.json'),encoding='utf-8') as stream: positions=json.load(stream)
            value=float(positions.get(self.recovery_identity(self.active_tab),0))
            self.root.update_idletasks(); self.text.yview_moveto(max(0,min(1,value)))
        except (OSError,ValueError,TypeError): pass
