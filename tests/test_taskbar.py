"""Real Windows Shell regression checks, without changing the user's pins."""
import ctypes as C
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from mdreader import taskbar
from mdreader.appicon import apply_window_icon

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.name == 'nt', 'Windows taskbar integration')
class TaskbarTests(unittest.TestCase):
    def test_outer_window_has_complete_packaged_relaunch_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / '中文 安装目录'
            folder.mkdir()
            (folder / 'MDReader.exe').write_bytes(b'fixture')
            window = tk.Tk()
            window.withdraw()
            try:
                hwnd = taskbar.configure_window(window, folder)
                self.assertNotEqual(hwnd, window.winfo_id())
                values = taskbar.read_window_properties(hwnd)
                self.assertEqual(values[5], taskbar.APP_ID)
                self.assertEqual(values[2], '"' + str(folder / 'MDReader.exe') + '"')
                self.assertEqual(values[3], str(folder / 'MDReader.exe') + ',0')
                self.assertEqual(values[4], 'MDReader')
                taskbar.clear_window(hwnd)
                self.assertEqual(taskbar.read_window_properties(hwnd), dict.fromkeys((2, 3, 4, 5)))
                taskbar.configure_window(window, folder)
            finally:
                window.destroy()

    def test_real_icon_entry_point_sets_identity_and_keeps_purple_icon(self):
        window = tk.Tk()
        window.withdraw()
        try:
            self.assertTrue(apply_window_icon(window))
            window.deiconify()
            window.update()
            self.assertTrue(window._mdreader_icon.width() > 0)
            user = C.WinDLL('user32')
            user.GetAncestor.argtypes = [C.c_void_p, C.c_uint]
            user.GetAncestor.restype = C.c_void_p
            values = taskbar.read_window_properties(user.GetAncestor(window.winfo_id(), 2))
            self.assertEqual(values[5], taskbar.DEV_ID)
            self.assertIn('main.py', values[2])
            self.assertIn('icon.ico', values[3])
        finally:
            window.destroy()

    def test_process_identity_is_set_in_a_fresh_process(self):
        code = '''import ctypes as C
from mdreader.taskbar import set_process_identity, DEV_ID
assert set_process_identity()
shell=C.WinDLL('shell32'); value=C.c_void_p()
shell.GetCurrentProcessExplicitAppUserModelID.argtypes=[C.POINTER(C.c_void_p)]
assert shell.GetCurrentProcessExplicitAppUserModelID(C.byref(value)) == 0
assert C.wstring_at(value) == DEV_ID
ole=C.OleDLL('ole32'); ole.CoTaskMemFree.argtypes=[C.c_void_p]; ole.CoTaskMemFree(value)
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installer_shortcut_identity_persists_and_target_is_unchanged(self):
        launcher = ROOT / 'build/launcher/MDReader.exe'
        if not launcher.exists():
            self.skipTest('Build launcher first to test persisted shortcut properties')
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / '中文 空间'
            folder.mkdir()
            exe = folder / 'MDReader.exe'
            shutil.copyfile(launcher, exe)
            env = dict(os.environ, MDREADER_SHORTCUT_TEST=str(folder))
            script = '''$dir=$env:MDREADER_SHORTCUT_TEST
$shell=New-Object -ComObject WScript.Shell
$link=$shell.CreateShortcut((Join-Path $dir 'MDReader.lnk'))
$link.TargetPath=Join-Path $dir 'MDReader.exe'
$link.IconLocation=$link.TargetPath+',0'
$link.Save()
'''
            subprocess.run(['powershell','-NoProfile','-Command',script],env=env,check=True,capture_output=True)
            link=folder/'MDReader.lnk'
            result=subprocess.run([str(exe),'--register-shortcut',str(link)],timeout=15)
            self.assertEqual(result.returncode,0)
            script='''$dir=$env:MDREADER_SHORTCUT_TEST
$shell=New-Object -ComObject Shell.Application
$item=$shell.NameSpace($dir).ParseName('MDReader.lnk')
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$link=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $dir 'MDReader.lnk'))
@{id=$item.ExtendedProperty('System.AppUserModel.ID');target=$link.TargetPath;icon=$link.IconLocation;arguments=$link.Arguments}|ConvertTo-Json
'''
            result=subprocess.run(['powershell','-NoProfile','-Command',script],env=env,check=True,capture_output=True,text=True,encoding='utf-8')
            values=json.loads(result.stdout)
            self.assertEqual(values['id'],taskbar.APP_ID)
            self.assertEqual(values['target'],str(exe))
            self.assertEqual(values['icon'],str(exe)+',0')
            self.assertEqual(values['arguments'],'')
            self.assertFalse((folder/'MDReader.log').exists())
            result=subprocess.run([str(exe),'--register-shortcut',str(folder/'absent.lnk')],timeout=15)
            self.assertNotEqual(result.returncode,0)
