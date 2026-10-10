import tempfile
from pathlib import Path
import unittest
from unittest import mock
from mdreader import core, export_ui
from mdreader.pdf_preview import Pages, Preview
from tests import test_plugins as fixtures
from tests.test_export_entry import buttons
from tools.verify_delivery import ROOT, install_exports


class PreviewCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-preview-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.ws = core.Workspace(str(self.folder/'workspace'))
        self.addCleanup(self.ws.plugins.shutdown)
        install_exports(self.ws)
        self.api = core.Api(self.ws, str(ROOT/'webui'))
        self.doc = self.folder/'source.md'
        self.doc.write_text('# Preview\n\nOriginal snapshot.\n', encoding='utf-8')
        self.api.loose.open_path(str(self.doc))
        self.command = next(c['command'] for c in self.ws.plugins.commands() if c['extension']=='pdf')
        self.dest = self.folder/'result.pdf'

    def ready(self, **extra):
        request = {'command':self.command,'doc':str(self.doc),'dest':str(self.dest),
                   'defer_commit':True,'background':True}
        request.update(extra)
        first = self.api.post('/api/plugins/export', request)
        task_id = first['task']['id']
        self.ws.plugins.tasks.poll(task_id, wait=60)
        return self.api.post('/api/plugins/export', {'task':task_id})

    def test_preview_approval_commits_exact_bytes_and_is_not_reusable(self):
        self.dest.write_bytes(b'original-output')
        result = self.ready(overwrite=True, confirm=True)
        self.assertTrue(result['preview_ready'])
        self.assertEqual(self.dest.read_bytes(), b'original-output')
        artifact = Path(result['preview_path']); approved = artifact.read_bytes()
        self.doc.write_text('Later source edit',encoding='utf-8')
        self.api.post('/api/plugins/export', {'task':result['task']['id'],'commit':True})
        self.assertEqual(self.dest.read_bytes(), approved)
        self.assertEqual(self.doc.read_text(encoding='utf-8'), 'Later source edit')
        self.assertFalse(artifact.exists())
        with self.assertRaises(ValueError):
            self.api.post('/api/plugins/export', {'task':result['task']['id'],'commit':True})

    def test_cancelling_preview_removes_only_task_files(self):
        result = self.ready()
        artifact = Path(result['preview_path'])
        self.api.post('/api/plugins/task/cancel', {'id':result['task']['id']})
        self.assertFalse(self.dest.exists())
        self.assertFalse(artifact.exists())
        self.assertIn('Original snapshot',self.doc.read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            self.api.post('/api/plugins/export', {'task':result['task']['id'],'commit':True})

    def test_changed_target_is_not_overwritten_after_preview(self):
        result = self.ready()
        self.dest.write_bytes(b'new-external-output')
        committed = self.api.post('/api/plugins/export', {'task':result['task']['id'],'commit':True})
        self.assertTrue(committed['conflict'])
        self.assertEqual(self.dest.read_bytes(),b'new-external-output')

    def test_changed_preview_bytes_are_rejected(self):
        result = self.ready()
        Path(result['preview_path']).write_bytes(b'%PDF-changed')
        with self.assertRaisesRegex(ValueError,'预览文件已变化'):
            self.api.post('/api/plugins/export', {'task':result['task']['id'],'commit':True})
        self.assertFalse(self.dest.exists())

    def test_bytes_are_verified_again_inside_final_copy(self):
        result = self.ready()
        commit = self.ws.plugins.tasks.commit_export
        def changed(*args, **kwargs):
            Path(result['preview_path']).write_bytes(b'%PDF-changed-during-copy')
            return commit(*args, **kwargs)
        with mock.patch.object(self.ws.plugins.tasks, 'commit_export', side_effect=changed):
            with self.assertRaisesRegex(core.PL.StaleResult,'预览文件已变化'):
                self.api.post('/api/plugins/export', {'task':result['task']['id'],'commit':True})
        self.assertFalse(self.dest.exists())


class PreviewUiTests(fixtures.PluginTestCase):
    window = fixtures.PluginDesktopTests.window
    open_document = fixtures.PluginDesktopTests.open_document

    def pdf(self):
        import pypdfium2 as pdfium
        path = Path(self.base)/'three.pdf'
        with pdfium.PdfDocument.new() as document:
            for n in range(5):
                document.new_page(600,800).close()
            document.save(str(path))
        return path

    def test_render_cache_is_bounded_and_detached_from_pdf_handles(self):
        path = self.pdf(); original = path.read_bytes()
        pages = Pages(str(path))
        for n in range(5):
            image = pages.render(n,600)
            self.assertEqual(image.mode,'RGB')
            self.assertLessEqual(image.width,600)
        self.assertEqual(len(pages.cache),3)
        self.assertIs(pages.render(4,600),image)
        pages.close(); self.assertFalse(pages.cache)
        self.assertEqual(path.read_bytes(),original)

    def test_actual_page_controls_and_close_follow_all_themes(self):
        win = self.window(); path = self.pdf()
        for theme in core.THEME_CHOICES:
            win.choose_theme(theme)
            preview = Preview(win,str(path))
            self.assertEqual(preview.dialog.cget('bg'),win.pal['bg'])
            self.assertEqual(preview.base_width,min(800,preview.size[0]-60))
            preview.move(1); self.assertEqual(preview.number,1)
            preview.resize(.25); self.assertEqual(preview.zoom,1.25)
            self.assertIn('2 / 5',preview.counter.cget('text'))
            positions = []
            def dismiss():
                positions.append(preview.dialog.geometry())
                buttons(preview.dialog)['返回修改'].invoke()
            win.root.after(30, dismiss)
            self.assertFalse(preview.show())
            width,height=preview.size
            x=min(max(0,win.root.winfo_rootx()+(win.root.winfo_width()-width)//2),
                  max(0,win.root.winfo_screenwidth()-width))
            y=min(max(0,win.root.winfo_rooty()+(win.root.winfo_height()-height)//2),
                  max(0,win.root.winfo_screenheight()-height))
            self.assertTrue(positions[0].endswith('+%d+%d' % (x,y)),positions[0])
            self.assertFalse(preview.result)
            self.assertFalse(preview.pages.cache)

    def test_close_while_rendering_waits_for_the_worker_and_releases_cache(self):
        win = self.window(); preview = Preview(win,str(self.pdf()))
        win.root.after(0, lambda: preview.finish(False))
        preview.move(1)
        self.assertTrue(preview.closed)
        self.assertFalse(preview.show())
        self.assertFalse(preview.pages.cache)

    def test_desktop_preview_can_cancel_or_save_without_regenerating(self):
        win = self.window(); self.open_document()
        win.ws.plugins.trust = core.PL.TrustRegistry()
        export_ui.prepare_format(win.ws.plugins,'pdf')
        win._pdf_preview = win._tk.BooleanVar(win.root,True)
        target=Path(self.base)/'preview.pdf'
        for approval in (False,True):
            with mock.patch.object(core,'_dialog_save',return_value=str(target)), \
                 mock.patch('mdreader.pdf_preview.preview_pdf',return_value=approval) as viewed:
                export_ui.choose_format(win,'pdf')
            viewed.assert_called_once()
            self.assertEqual(target.exists(),approval)
        self.assertTrue(target.read_bytes().startswith(b'%PDF'))
