"""Verify native install/update argument forwarding without opening user UI."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='mdreader-install-entry-') as temp:
        package = Path(temp)/'中文 source !'
        resources = package/'resources'
        resources.mkdir(parents=True)
        shutil.copy2(repo/'build/launcher/安装.exe', package)
        script = '''param([string]$InstallDir,[string]$WorkspaceDir,[string]$ShortcutDir,
[switch]$NoUI,[switch]$NoShortcut,[switch]$NoPause,[switch]$SetDefault,[string]$ResultFile)
@{install=$InstallDir;data=$WorkspaceDir;shortcut=$ShortcutDir;quiet=$NoUI.IsPresent;no_shortcut=$NoShortcut.IsPresent;no_pause=$NoPause.IsPresent;set_default=$SetDefault.IsPresent}|ConvertTo-Json|Set-Content -LiteralPath $ResultFile -Encoding UTF8
'''
        (resources/'安装到桌面.ps1').write_text(script, encoding='utf-8-sig')
        target, data, shortcuts = [str(Path(temp)/name) for name in ('安装 位置', '数据 !', '桌面')]
        result = Path(temp)/'result.json'
        command = [str(package/'安装.exe'), '--no-ui', '--no-shortcut', '--set-default', '--install-dir', target,
                   '--workspace-dir', data, '--shortcut-dir', shortcuts, '--result-file', str(result)]
        process = subprocess.run(command, timeout=30)
        assert process.returncode == 0, process.returncode
        record = json.loads(result.read_text(encoding='utf-8-sig'))
        assert record == {'install':target, 'data':data, 'shortcut':shortcuts, 'quiet':True,
                          'no_shortcut':True, 'no_pause':True, 'set_default':True}, record
        update = '''param([string]$InstallDir,[string]$Archive,[switch]$NoPause)
@{install=$InstallDir;archive=$Archive;no_pause=$NoPause.IsPresent}|ConvertTo-Json|Set-Content -LiteralPath ($Archive+'.json') -Encoding UTF8
'''
        (resources/'更新程序.ps1').write_text(update, encoding='utf-8-sig')
        archive = str(Path(temp)/'更新 ! 中文.zip')
        process = subprocess.run([str(package/'安装.exe'), '--update', '--no-ui', '--install-dir',target,
                                  '--archive',archive], timeout=30)
        assert process.returncode == 0
        assert json.loads(Path(archive+'.json').read_text(encoding='utf-8-sig')) == {
            'install':target, 'archive':archive, 'no_pause':True}
        setup = resources/'setup'
        setup.mkdir()
        for name in ('安装到桌面.ps1', '更新程序.ps1'):
            (resources/name).rename(setup/name)
        subprocess.run(command, timeout=30, check=True)
        assert json.loads(result.read_text(encoding='utf-8-sig')) == record
        subprocess.run([str(package/'安装.exe'), '--update', '--no-ui', '--install-dir',target,
                        '--archive',archive], timeout=30, check=True)
        # Exercise the first real window through the EXE, without installing.
        # SW_HIDE in STARTUPINFO makes ShowDialog block on an invisible form.
        visible_script = '''param([switch]$Gui,[switch]$NoPause,[string]$ResultFile,[string]$Archive)
if (-not $ResultFile) { $ResultFile = $Archive }
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class VisibilityProbe {
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr handle);
    [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
}
'@
$form = New-Object Windows.Forms.Form
$form.Text = 'MDReader installer visibility regression'
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 300
$timer.Add_Tick({
    $timer.Stop()
    @{visible=[VisibilityProbe]::IsWindowVisible($form.Handle);
      console=[VisibilityProbe]::GetConsoleWindow().ToInt64();
      apartment=[Threading.Thread]::CurrentThread.ApartmentState.ToString();
      gui=$Gui.IsPresent}|ConvertTo-Json|Set-Content -LiteralPath $ResultFile -Encoding UTF8
    $form.Close()
})
try { $timer.Start(); $form.ShowDialog() | Out-Null }
finally { $timer.Dispose(); $form.Dispose() }
'''
        for script_name, flags in [('安装到桌面.ps1', ['--result-file', str(result)]),
                                   ('更新程序.ps1', ['--update', '--archive', str(result)])]:
            (setup/script_name).write_text(visible_script, encoding='utf-8-sig')
            subprocess.run([str(package/'安装.exe'), *flags],
                           timeout=30, check=True)
            visibility = json.loads(result.read_text(encoding='utf-8-sig'))
            assert visibility == {'visible': True, 'console': 0, 'apartment': 'STA', 'gui': True}, visibility
    print('PASS: native install/update entries, visible dialogs, no console, STA, Unicode/spaces, arguments and result')


if __name__ == '__main__':
    main()
