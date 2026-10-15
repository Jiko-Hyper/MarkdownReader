param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('mdreader-install-' + [guid]::NewGuid().ToString('N'))
$associationSandbox = 'HKCU:\Software\MDReaderInstallTest-' + [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $scratch | Out-Null
function Assert($condition, $message) { if (-not $condition) { throw $message } }
try {
    $source = Join-Path $scratch '中文 source'
    $target = Join-Path $scratch '中文 installed'
    $desktop = Join-Path $scratch 'desktop'
    $data = Join-Path $scratch '中文 data'
    New-Item -ItemType Directory -Path $source,$desktop,(Join-Path $source 'runtime'),(Join-Path $source 'mdreader') | Out-Null
    foreach ($file in @('MDReader.exe','main.py','runtime\python.exe','runtime\pythonw.exe','mdreader\core.py')) {
        Set-Content -LiteralPath (Join-Path $source $file) -Value 'fixture'
    }
    Copy-Item -LiteralPath (Join-Path $root 'build/launcher/MDReader.exe') -Destination (Join-Path $source 'MDReader.exe') -Force
    Copy-Item -LiteralPath (Join-Path $root 'build/launcher/卸载.exe') -Destination $source
    Copy-Item -LiteralPath (Join-Path $root 'release/app/Register-FileAssociation.ps1') -Destination $source
    Copy-Item -LiteralPath (Join-Path $root 'release/app/Select-Folder.ps1') -Destination $source
    $installer = Join-Path $source '安装到桌面.ps1'
    Copy-Item -LiteralPath (Join-Path $root 'release\app\安装到桌面.ps1') -Destination $installer
    Copy-Item -LiteralPath (Join-Path $root 'release\app\卸载.ps1'),(Join-Path $root 'release\app\卸载.cmd') -Destination $source
    $wrapper = Join-Path $source '安装到桌面.cmd'
    Copy-Item -LiteralPath (Join-Path $root 'release\app\安装到桌面.cmd') -Destination $wrapper
    $owned=@(Get-ChildItem -LiteralPath $source -Recurse -File | ForEach-Object { $_.FullName.Substring($source.Length+1).Replace('\','/') })
    @{schema=1;files=$owned+@('program-files.json')}|ConvertTo-Json -Depth 4|Set-Content -LiteralPath (Join-Path $source 'program-files.json') -Encoding UTF8
    Push-Location $source
    try { '' | & $env:ComSpec /d /c 安装到桌面.cmd -InstallDir $target -WorkspaceDir $data -ShortcutDir $desktop -AssociationClassesRoot $associationSandbox }
    finally { Pop-Location }
    Assert ($LASTEXITCODE -eq 0) 'Double-click installer wrapper failed'
    Assert (Test-Path -LiteralPath (Join-Path $target 'main.py')) 'Wrapper did not install'
    Assert (Test-Path -LiteralPath (Join-Path $target '卸载.cmd')) 'Uninstall wrapper missing from installed app'
    Assert (Test-Path -LiteralPath (Join-Path $target '卸载.ps1')) 'Uninstall script missing from installed app'
    Assert (Test-Path -LiteralPath (Join-Path $target '卸载.exe')) 'Native uninstall entry missing from installed app'
    Assert (Test-Path -LiteralPath (Join-Path $target 'Select-Folder.ps1')) 'Modern folder picker missing from installed app'
    Assert ((Get-Content (Join-Path $target 'installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json).workspace -eq $data) 'Custom data path not saved'
    $record=Get-Content -LiteralPath (Join-Path $target 'installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert ($record.schema -eq 2 -and $record.install_dir -eq $target) 'Both installation paths must be recorded'
    $receipt=Get-Content -LiteralPath (Join-Path $source 'installed-target.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert ($receipt.installation_id -eq $record.installation_id -and $receipt.install_dir -eq $target -and $receipt.workspace -eq $data) 'Source receipt must point to chosen paths'
    $id=$record.installation_id
    & $installer -InstallDir $target -ShortcutDir $desktop -NoPause -AssociationClassesRoot $associationSandbox
    $record=Get-Content -LiteralPath (Join-Path $target 'installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert ($record.installation_id -eq $id) 'Upgrade changed installation identity'
    $marker=Get-Content -LiteralPath (Join-Path $data '.mdreader-installations.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert (@($marker.installations).Count -eq 1 -and $marker.installations[0].id -eq $id) 'Data association incorrect'

    Assert (Test-Path -LiteralPath (Join-Path $target 'runtime\pythonw.exe')) 'Fresh install failed'
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $desktop 'MDReader.lnk'))
    $link.Arguments = 'old arguments'
    $link.Save()
    Set-Content -LiteralPath (Join-Path $target 'my-document.md') -Value 'keep me'
    & $installer -InstallDir $target -ShortcutDir $desktop -NoPause -AssociationClassesRoot $associationSandbox
    $link = $shell.CreateShortcut((Join-Path $desktop 'MDReader.lnk'))
    Assert ($link.Arguments -eq '') 'Stale shortcut arguments survived'
    Assert ((Get-Content -LiteralPath (Join-Path $target 'my-document.md')) -eq 'keep me') 'User document changed'
    Assert (@(Get-ChildItem -LiteralPath $scratch -Directory -Filter '*.backup-*').Count -eq 2) 'Upgrade backup missing'
    $unknown = Join-Path $scratch 'unrelated'
    New-Item -ItemType Directory -Path $unknown | Out-Null
    Set-Content -LiteralPath (Join-Path $unknown 'keep.txt') -Value 'untouched'
    & $installer -InstallDir $unknown -WorkspaceDir $data -NoShortcut -NoPause -AssociationClassesRoot $associationSandbox
    Assert (Test-Path -LiteralPath (Join-Path $unknown 'MDReader.exe')) 'Reinstall beside retained user files failed'
    Assert ((Get-Content -LiteralPath (Join-Path $unknown 'keep.txt')) -eq 'untouched') 'Unknown directory changed'
    $collision=Join-Path $scratch 'user collision'
    New-Item -ItemType Directory -Path $collision | Out-Null
    Set-Content -LiteralPath (Join-Path $collision 'main.py') 'user content'
    $rejected=$false
    try { & $installer -InstallDir $collision -WorkspaceDir $data -NoShortcut -NoPause } catch { $rejected=$true }
    Assert $rejected 'Reinstall overwrote a same-name user file'
    Assert ((Get-Content -LiteralPath (Join-Path $collision 'main.py')) -eq 'user content') 'User code changed'
    $rejected = $false
    try { & $installer -InstallDir (Join-Path $source 'nested') -WorkspaceDir $data -NoShortcut -NoPause } catch { $rejected = $true }
    Assert $rejected 'Nested installation was accepted'
    $rejected = $false
    try { & $installer -InstallDir $target -WorkspaceDir (Join-Path $target 'data') -NoShortcut -NoPause } catch { $rejected = $true }
    Assert $rejected 'Data inside application accepted'
    Assert ((Get-Content (Join-Path $target 'installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json).workspace -eq $data) 'Upgrade lost custom data path'
    & $installer -DesktopOnly -WorkspaceDir $data -ShortcutDir $desktop -NoPause -AssociationClassesRoot $associationSandbox
    $link = $shell.CreateShortcut((Join-Path $desktop 'MDReader.lnk'))
    Assert ($link.TargetPath -eq (Join-Path $source 'MDReader.exe')) 'Portable shortcut target mismatch'
    Remove-Item -LiteralPath (Join-Path $source 'runtime\pythonw.exe')
    $rejected = $false
    try { & $installer -DesktopOnly -NoShortcut -NoPause } catch { $rejected = $true }
    Assert $rejected 'Incomplete package accepted'
    Write-Host 'PASS: install, upgrade backup, document preservation, shortcuts, unsafe paths, incomplete package.'
} finally {
    if (Test-Path -LiteralPath $associationSandbox) { Remove-Item -LiteralPath $associationSandbox -Recurse -Force }
    $resolved = (Resolve-Path -LiteralPath $scratch).Path
    if ($resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $resolved -Leaf).StartsWith('mdreader-install-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
