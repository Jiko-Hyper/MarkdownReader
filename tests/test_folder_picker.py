"""Folder picker integration: native helper, safe arguments and cancellation."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from mdreader import core, winui


class FolderPickerTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Tk owner")
    def test_owned_dialog_pumps_events_handles_fast_results_errors_and_owner_close(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with patch.object(core, "_run_ps", return_value=["D:\\中文"]):
            self.assertEqual(core._dialog_folder(parent=root), ["D:\\中文"])
        with patch.object(core, "_run_ps", side_effect=RuntimeError("dialog failure")):
            with self.assertRaisesRegex(RuntimeError, "dialog failure"):
                core._dialog_folder(parent=root)
        finished = threading.Event()
        def wait_for_event(*_args, **_kwargs):
            self.assertTrue(finished.wait(2), "Tk stopped processing events")
            return []
        def reenter():
            self.assertEqual(core._dialog_folder(parent=root), [])
            finished.set()
        root.after(20, reenter)
        with patch.object(core, "_run_ps", side_effect=wait_for_event) as run:
            self.assertEqual(core._dialog_folder(parent=root), [])
            self.assertEqual(run.call_count, 1)
        owner = tk.Toplevel(root)
        closed = threading.Event()
        def wait_for_close(*_args, cancel_event, **_kwargs):
            self.assertTrue(cancel_event.wait(2))
            closed.set()
            return []
        owner.after(20, owner.destroy)
        with patch.object(core, "_run_ps", side_effect=wait_for_close):
            self.assertEqual(core._dialog_folder(parent=owner), [])
        self.assertTrue(closed.wait(2))

    def test_source_and_packaged_helper_lookup_and_literal_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "release/app/Select-Folder.ps1"
            helper.parent.mkdir(parents=True)
            helper.touch()
            for packaged in ('source', 'legacy', 'setup'):
                if packaged == 'legacy':
                    helper = helper.rename(root / "Select-Folder.ps1")
                elif packaged == 'setup':
                    (root / 'setup').mkdir()
                    helper = helper.rename(root / 'setup/Select-Folder.ps1')
                with patch.object(core, "app_dir", return_value=tmp), \
                     patch.object(core, "_run_ps", return_value=[tmp]) as run:
                    paths = core._dialog_folder(title="选择 O'Brien 的文件夹",
                                                initialdir="D:\\中文 资料\\O'Brien;$folder")
                self.assertEqual(paths, [tmp])
                script = run.call_args.args[0]
                self.assertIn(str(helper).replace("'", "''"), script)
                self.assertIn("'D:\\中文 资料\\O''Brien;$folder'", script)
                self.assertIn("'选择 O''Brien 的文件夹'", script)
                self.assertEqual(run.call_args.kwargs, {"check": True})

    def test_cancel_and_helper_errors_are_distinct(self):
        with patch.object(core, "_run_ps", return_value=[]):
            self.assertEqual(core._dialog_folder(), [])
        with patch.object(core.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, stdout=b"", stderr="选择组件加载失败".encode("utf-8"))):
            with self.assertRaisesRegex(RuntimeError, "选择组件加载失败"):
                core._dialog_folder()

    def test_file_browser_mode_is_explicit_and_keeps_folder_only_default(self):
        with patch.object(core, "_run_ps", return_value=[]) as run:
            core._dialog_folder()
            self.assertNotIn("-ShowFiles", run.call_args.args[0])
            core._dialog_folder(browse_files=True)
            self.assertIn(" -ShowFiles", run.call_args.args[0])

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell")
    def test_owner_close_can_terminate_the_picker_process(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.2, cancelled.set)
        timer.start()
        try:
            self.assertEqual(core._run_ps("Start-Sleep -Seconds 30", check=True,
                                         cancel_event=cancelled), [])
        finally:
            timer.cancel()

    def test_missing_helper_reports_problem_instead_of_falling_back_to_blurry_picker(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(core, "app_dir", return_value=tmp):
            with self.assertRaises(FileNotFoundError):
                core._dialog_folder()

    @unittest.skipUnless(os.name == "nt", "Windows native dialog")
    def test_project_browse_keeps_original_on_cancel_and_applies_selected_folder(self):
        import tkinter as tk
        from mdreader.display import enable_high_dpi
        enable_high_dpi()
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        dialog = winui.RenamePrompt(root, "项目", winui.DARK, project_parent="D:\\原位置")
        self.addCleanup(lambda: dialog.window.destroy() if dialog.window.winfo_exists() else None)
        def browse_button(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Button) and child.cget("text") == "浏览…":
                    return child
                found = browse_button(child)
                if found is not None:
                    return found
        button = browse_button(dialog.window)
        with patch.object(core, "_dialog_folder", return_value=[]) as picker:
            button.invoke()
            self.assertEqual(dialog.location.get(), "D:\\原位置")
            self.assertIs(picker.call_args.kwargs["parent"], dialog.window)
            self.assertEqual(picker.call_args.kwargs["initialdir"], "D:\\原位置")
        with patch.object(core, "_dialog_folder", return_value=["D:\\中文 资料\\项目"]):
            button.invoke()
            self.assertEqual(dialog.location.get(), "D:\\中文 资料\\项目")
