$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$path = Join-Path (Split-Path -Parent $PSScriptRoot) 'release\app\安装到桌面.ps1'
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
        $boxes[0].Text = 'D:\Apps\Reader'
        $boxes[1].Text = 'D:\Data\Reader'
        $form.AcceptButton.PerformClick()
    } catch { $script:dialogError = $_; $form.Close() }
})
try {
    $timer.Start()
    $result = Select-InstallFolders 'D:\Initial' 'D:\Documents'
    if ($script:dialogError) { throw $script:dialogError }
    if ($result.Count -ne 2 -or $result[0] -ne 'D:\Apps\Reader' -or $result[1] -ne 'D:\Data\Reader') { throw 'Dialog did not return edited paths' }
} finally { $timer.Dispose() }
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 200
$timer.Add_Tick({ $timer.Stop(); [Windows.Forms.Application]::OpenForms[0].CancelButton.PerformClick() })
try {
    $timer.Start()
    $result = Select-InstallFolders 'D:\Initial' 'D:\Documents'
    if ($result) { throw 'Cancelled dialog returned install paths' }
} finally { $timer.Dispose() }
Write-Host 'PASS: two folder fields/buttons, edited paths, install and cancel.'
