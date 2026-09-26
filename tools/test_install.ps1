param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('mdreader-install-' + [guid]::NewGuid().ToString('N'))
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
    $installer = Join-Path $source '安装到桌面.ps1'
    Copy-Item -LiteralPath (Join-Path $root 'release\app\安装到桌面.ps1') -Destination $installer
    $wrapper = Join-Path $source '安装到桌面.cmd'
    Copy-Item -LiteralPath (Join-Path $root 'release\app\安装到桌面.cmd') -Destination $wrapper
    Push-Location $source
    try { '' | & $env:ComSpec /d /c 安装到桌面.cmd -InstallDir $target -WorkspaceDir $data -ShortcutDir $desktop }
    finally { Pop-Location }
    Assert ($LASTEXITCODE -eq 0) 'Double-click installer wrapper failed'
    Assert (Test-Path -LiteralPath (Join-Path $target 'main.py')) 'Wrapper did not install'
    Assert ((Get-Content (Join-Path $target 'installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json).workspace -eq $data) 'Custom data path not saved'
    & $installer -InstallDir $target -ShortcutDir $desktop -NoPause
    Assert (Test-Path -LiteralPath (Join-Path $target 'runtime\pythonw.exe')) 'Fresh install failed'
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $desktop 'MDReader.lnk'))
    $link.Arguments = 'old arguments'
    $link.Save()
    Set-Content -LiteralPath (Join-Path $target 'my-document.md') -Value 'keep me'
    & $installer -InstallDir $target -ShortcutDir $desktop -NoPause
    $link = $shell.CreateShortcut((Join-Path $desktop 'MDReader.lnk'))
    Assert ($link.Arguments -eq '') 'Stale shortcut arguments survived'
    Assert ((Get-Content -LiteralPath (Join-Path $target 'my-document.md')) -eq 'keep me') 'User document changed'
    Assert (@(Get-ChildItem -LiteralPath $scratch -Directory -Filter '*.backup-*').Count -eq 2) 'Upgrade backup missing'
    $unknown = Join-Path $scratch 'unrelated'
    New-Item -ItemType Directory -Path $unknown | Out-Null
    Set-Content -LiteralPath (Join-Path $unknown 'keep.txt') -Value 'untouched'
    $rejected = $false
    try { & $installer -InstallDir $unknown -WorkspaceDir $data -NoShortcut -NoPause } catch { $rejected = $true }
    Assert $rejected 'Unknown nonempty directory was accepted'
    Assert ((Get-Content -LiteralPath (Join-Path $unknown 'keep.txt')) -eq 'untouched') 'Unknown directory changed'
    $rejected = $false
    try { & $installer -InstallDir (Join-Path $source 'nested') -WorkspaceDir $data -NoShortcut -NoPause } catch { $rejected = $true }
    Assert $rejected 'Nested installation was accepted'
    $rejected = $false
    try { & $installer -InstallDir $target -WorkspaceDir (Join-Path $target 'data') -NoShortcut -NoPause } catch { $rejected = $true }
    Assert $rejected 'Data inside application accepted'
    Assert ((Get-Content (Join-Path $target 'installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json).workspace -eq $data) 'Upgrade lost custom data path'
    & $installer -DesktopOnly -WorkspaceDir $data -ShortcutDir $desktop -NoPause
    $link = $shell.CreateShortcut((Join-Path $desktop 'MDReader.lnk'))
    Assert ($link.TargetPath -eq (Join-Path $source 'MDReader.exe')) 'Portable shortcut target mismatch'
    Remove-Item -LiteralPath (Join-Path $source 'runtime\pythonw.exe')
    $rejected = $false
    try { & $installer -DesktopOnly -NoShortcut -NoPause } catch { $rejected = $true }
    Assert $rejected 'Incomplete package accepted'
    Write-Host 'PASS: install, upgrade backup, document preservation, shortcuts, unsafe paths, incomplete package.'
} finally {
    $resolved = (Resolve-Path -LiteralPath $scratch).Path
    if ($resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $resolved -Leaf).StartsWith('mdreader-install-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
