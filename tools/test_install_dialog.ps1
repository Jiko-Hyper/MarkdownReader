$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$path = Join-Path (Split-Path -Parent $PSScriptRoot) 'release\app\安装到桌面.ps1'
$here = Split-Path -Parent $path
. (Join-Path $here 'Select-Folder.ps1') -LoadOnly
$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('mdreader-dialog-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
$global:MDReaderInstallerPickerResults = New-Object Collections.Queue
$global:MDReaderInstallerPickerResults.Enqueue('D:\Picked Program\中文')
$global:MDReaderInstallerPickerResults.Enqueue($null)
$global:MDReaderInstallerPickerResults.Enqueue('D:\Picked Data')
$global:MDReaderInstallerPickerCalls = New-Object 'Collections.Generic.List[object]'
$pickerStub = @'
param([switch]$LoadOnly)
function Select-MDReaderFolder {
    param([string]$Title, [string]$InitialPath, [long]$OwnerHandle)
    $global:MDReaderInstallerPickerCalls.Add(@{Title=$Title; InitialPath=$InitialPath; OwnerHandle=$OwnerHandle})
    return $global:MDReaderInstallerPickerResults.Dequeue()
}
'@
[IO.File]::WriteAllText((Join-Path $fixtureRoot 'Select-Folder.ps1'), $pickerStub, [Text.UTF8Encoding]::new($true))
$here = $fixtureRoot
try {
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Installer syntax error' }
foreach ($name in @('Confirm-Paths','Select-InstallFolders')) {
    $function = $ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name}, $true)
    Invoke-Expression $function.Extent.Text
}
$script:dialogError = $null
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 200
$timer.Add_Tick({
    $timer.Stop()
    $form = [Windows.Forms.Application]::OpenForms[0]
    try {
        $buttons = @($form.Controls | Where-Object { $_ -is [Windows.Forms.Button] })
        $boxes = @($form.Controls | Where-Object { $_ -is [Windows.Forms.TextBox] })
        if ($buttons.Count -ne 4 -or $boxes.Count -ne 2) { throw 'Expected two browse buttons and two path fields' }
        $buttons[0].PerformClick()
        if ($boxes[0].Text -ne 'D:\Picked Program\中文') { throw 'Program browse result not applied' }
        $buttons[0].PerformClick()
        if ($boxes[0].Text -ne 'D:\Picked Program\中文') { throw 'Cancelled browse changed program path' }
        $buttons[1].PerformClick()
        if ($boxes[1].Text -ne 'D:\Picked Data') { throw 'Data browse result not applied' }
        $calls = $global:MDReaderInstallerPickerCalls
        if ($calls.Count -ne 3 -or $calls[0].InitialPath -ne 'D:\Initial' -or
            $calls[1].InitialPath -ne 'D:\Picked Program\中文' -or $calls[2].InitialPath -ne 'D:\Documents') {
            throw 'Browse did not preserve initial paths'
        }
        foreach ($call in $calls) {
            if (-not $call.Title -or $call.OwnerHandle -ne $form.Handle.ToInt64()) { throw 'Browse dialog lost its title or owner' }
        }
        $boxes[0].Text = 'D:\Apps\Reader'
        $boxes[1].Text = 'D:\Data\Reader'
        $choice = @($form.Controls | Where-Object { $_ -is [Windows.Forms.CheckBox] })
        if ($choice.Count -ne 1 -or $choice[0].Checked) { throw 'Default app must be an explicit opt-in' }
        $choice[0].Checked = $true
        $form.AcceptButton.PerformClick()
    } catch { $script:dialogError = $_; $form.Close() }
})
try {
    $timer.Start()
    $result = Select-InstallFolders 'D:\Initial' 'D:\Documents'
    if ($script:dialogError) { throw $script:dialogError }
    if ($result.Count -ne 3 -or $result[0] -ne 'D:\Apps\Reader' -or $result[1] -ne 'D:\Data\Reader' -or $result[2] -ne $true) { throw 'Dialog did not return edited paths and default app choice' }
} finally { $timer.Dispose() }
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 200
$timer.Add_Tick({ $timer.Stop(); [Windows.Forms.Application]::OpenForms[0].CancelButton.PerformClick() })
try {
    $timer.Start()
    $result = Select-InstallFolders 'D:\Initial' 'D:\Documents'
    if ($result) { throw 'Cancelled dialog returned install paths' }
} finally { $timer.Dispose() }
Write-Host 'PASS: two folder fields/buttons, modern browse integration, owner, initial paths, browse cancellation, edited paths, install and cancel.'
} finally {
    Remove-Variable MDReaderInstallerPickerResults,MDReaderInstallerPickerCalls -Scope Global -ErrorAction SilentlyContinue
    $resolved = (Resolve-Path -LiteralPath $fixtureRoot).Path
    if ($resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $resolved -Leaf).StartsWith('mdreader-dialog-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
