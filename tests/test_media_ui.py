import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from mdreader import media, media_ui, content_policy
from tests import test_native_tabs


class MediaUITests(unittest.TestCase):
    setUp = test_native_tabs.NativeTabsTests.setUp
    destroy_window = staticmethod(test_native_tabs.NativeTabsTests.destroy_window)

    def test_preview_displays_image_resize_is_undoable_and_original_pixels_survive(self):
        picture = Path(self.temp.name, 'image.png')
        Image.new('RGB', (600, 300), '#6699aa').save(picture)
        document = Path(self.files[0])
        original = '![示例](image.png "width=240")\n'
        document.write_text(original, encoding='utf-8')
        w = self.win
        w.root.deiconify()
        w.root.geometry('1000x700')
        w.root.update()
        w.open_local_files([str(document)])
        w.root.update()
        images = [c for c in w.preview.winfo_children() if hasattr(c, 'image')]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].image.width(), 240)
        with patch.object(w, 'plugin_commands', return_value=[{'plugin': 'mdreader.image-insert'}]), \
             patch.object(media_ui, 'choose_width', return_value=480):
            media_ui.resize_image(w)
        self.assertIn('width=480', w.source)
        with Image.open(picture) as image:
            self.assertEqual(image.size, (600, 300))
        self.assertEqual(document.read_text(encoding='utf-8'), original)
        w.show_source()
        w.editor.edit_undo()
        self.assertEqual(w.get_text(), original)

    def test_code_examples_are_not_treated_as_missing_image_resources(self):
        text = '```markdown\n![code](missing.png)\n```\n`![inline](missing.png)`\n'
        self.assertEqual(media.image_matches(text), [])
        self.assertEqual(media.snapshot_images(text, self.files[0], self.temp.name), ([], {}))

    def test_web_width_is_bounded_and_other_styles_remain_filtered(self):
        output = content_policy.sanitize('<img src="a.png" title="width=240" style="position:fixed">')
        self.assertIn('width="240"', output)
        self.assertNotIn('position', output)

    def test_background_job_keeps_tk_callbacks_running(self):
        import time
        w = self.win
        fired = []
        w.root.after(10, lambda: fired.append(True))
        self.assertEqual(media_ui.run_job(w, lambda: (time.sleep(.1), 42)[1]), 42)
        self.assertTrue(fired)
