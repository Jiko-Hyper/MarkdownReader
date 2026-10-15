param([string]$InstallDir='', [string]$Archive='', [switch]$NoPause, [switch]$Gui)
$ErrorActionPreference='Stop'
$dpiEntered=$false
$helperRoot=$PSScriptRoot
$packagePayload=$PSScriptRoot
if ((Split-Path -Leaf $packagePayload) -eq 'setup') { $packagePayload=Split-Path -Parent $packagePayload }
try {
    . (Join-Path $PSScriptRoot 'Select-Folder.ps1') -LoadOnly
    $dpiContext=[MDReader.NativeFolderPicker]::EnterDpiContext()
    $dpiEntered=$true
    Add-Type -AssemblyName System.Windows.Forms
    if (-not $InstallDir) {
        $InstallDir=Select-MDReaderFolder -Title '选择已安装的 MDReader 程序目录' -InitialPath (Join-Path $env:LOCALAPPDATA 'MDReader')
        if (-not $InstallDir) { return }
    }
    $InstallDir=(Resolve-Path -LiteralPath $InstallDir).Path.TrimEnd('\')
    $running=Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallDir+'\',[StringComparison]::OrdinalIgnoreCase)
    }
    if ($running) { throw '请保存文档并关闭此安装目录中的 MDReader，然后重新更新。' }
    if (-not $Archive) {
        $pick=New-Object System.Windows.Forms.OpenFileDialog
        $pick.Title='选择 MDReader 小体积更新包'
        $pick.Filter='MDReader 更新包|*-update.zip'
        if ($pick.ShowDialog() -ne 'OK') { return }
        $Archive=$pick.FileName
    }
    $payload=$InstallDir
    if (Test-Path -LiteralPath (Join-Path $InstallDir 'resources\runtime\python.exe')) { $payload=Join-Path $InstallDir 'resources' }
    & (Join-Path $payload 'runtime\python.exe') (Join-Path $packagePayload 'tools\apply_update.py') --install $InstallDir --archive $Archive
    if ($LASTEXITCODE -ne 0) { throw '更新未完成，请查看上方原因。' }
    if ($Gui) { [Windows.Forms.MessageBox]::Show('更新完成。请重新启动 MDReader。','更新 MDReader') | Out-Null }
} catch { Write-Host $_ -ForegroundColor Red; if ($Gui) { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'更新失败') | Out-Null }; throw }
finally {
    if ($dpiEntered) { [MDReader.NativeFolderPicker]::RestoreDpiContext($dpiContext) }
    if (-not $NoPause) { Read-Host '按 Enter 关闭窗口' | Out-Null }
}
