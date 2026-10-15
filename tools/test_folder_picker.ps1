$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'release\app\Select-Folder.ps1') -LoadOnly
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FolderPickerDpiProbe {
    [DllImport("user32.dll")] public static extern IntPtr GetThreadDpiAwarenessContext();
    [DllImport("user32.dll")] public static extern bool AreDpiAwarenessContextsEqual(IntPtr a, IntPtr b);
}
'@
$before = [FolderPickerDpiProbe]::GetThreadDpiAwarenessContext()
$previous = [MDReader.NativeFolderPicker]::EnterDpiContext()
try {
    if (-not [FolderPickerDpiProbe]::AreDpiAwarenessContextsEqual(
        [FolderPickerDpiProbe]::GetThreadDpiAwarenessContext(), [IntPtr](-4))) {
        throw 'Folder picker must use PerMonitorV2 DPI awareness'
    }
} finally { [MDReader.NativeFolderPicker]::RestoreDpiContext($previous) }
if (-not [FolderPickerDpiProbe]::AreDpiAwarenessContextsEqual(
    [FolderPickerDpiProbe]::GetThreadDpiAwarenessContext(), $before)) {
    throw 'Original DPI context was not restored'
}
$nonexistent = Join-Path $repo 'not-yet-created\child'
if ([MDReader.NativeFolderPicker]::ExistingDirectory($nonexistent) -ne $repo) {
    throw 'Nonexistent initial folder must resolve to the nearest existing parent'
}
if ([MDReader.NativeFolderPicker]::ExistingDirectory('') -or
    [MDReader.NativeFolderPicker]::ExistingDirectory('relative\folder')) {
    throw 'Invalid initial paths must not override native navigation'
}
$folderOnly = [MDReader.NativeFolderPicker]::DialogOptions(0x1000, $false)
$browser = [MDReader.NativeFolderPicker]::DialogOptions(0x3220, $true)
if (($folderOnly -band 0x20) -ne 0x20 -or ($browser -band 0x3220) -ne 0) {
    throw 'File browsing must show files and confirm empty folders; folder-only selection must stay supported'
}
if (($browser -band 0x848) -ne 0x848) {
    throw 'File browsing must still require a real filesystem directory and keep the process working directory'
}
if (($browser -band 0x2000000) -ne 0x2000000) {
    throw 'Directory browsing must not add placeholder filenames to recent files'
}
$helper = Join-Path $repo 'release\app\Select-Folder.ps1'
$bytes = [IO.File]::ReadAllBytes($helper)
if ($bytes[0] -ne 239 -or $bytes[1] -ne 187 -or $bytes[2] -ne 191) {
    throw 'Windows PowerShell 5.1 requires UTF-8 BOM for Chinese labels'
}
Write-Host 'PASS: native component compilation, PerMonitorV2, DPI restoration, initial folder fallback, file browsing, folder-only selection, UTF-8 BOM.'
