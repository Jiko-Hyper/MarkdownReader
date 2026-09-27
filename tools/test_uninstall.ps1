param()
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$scratch=Join-Path $root ('.testtmp\uninstall-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
$desktop=Join-Path $scratch 'desktop'
$startMenu=Join-Path $scratch 'start menu'
New-Item -ItemType Directory -Path $desktop,$startMenu | Out-Null
$shell=New-Object -ComObject WScript.Shell
$script=Join-Path $root 'release\app\卸载.ps1'
$script:count=0
function Assert($ok,$message) { if (-not $ok) { throw $message }; $script:count++ }
function Fixture($name) {
    $dir=Join-Path $scratch $name
    $data=Join-Path $scratch ($name+' data')
    New-Item -ItemType Directory -Path $dir,$data,(Join-Path $dir 'mdreader'),(Join-Path $dir 'runtime') | Out-Null
    foreach ($file in @('MDReader.exe','main.py','mdreader\core.py','runtime\pythonw.exe')) { Set-Content -LiteralPath (Join-Path $dir $file) 'fixture' }
    Set-Content -LiteralPath (Join-Path $data 'notes.md') 'keep document'
    Set-Content -LiteralPath (Join-Path $data 'ui-settings.json') '{"theme":"dark"}'
    $id=[guid]::NewGuid().ToString()
    @{workspace=$data;install_dir=$dir;installation_id=$id;schema=2}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $dir 'installation.json') -Encoding UTF8
    @{schema=1;installations=@(@{id=$id;install_dir=$dir})}|ConvertTo-Json -Depth 5|Set-Content -LiteralPath (Join-Path $data '.mdreader-installations.json') -Encoding UTF8
    Copy-Item -LiteralPath $script -Destination $dir
    Copy-Item -LiteralPath (Join-Path $root 'release\app\卸载.cmd') -Destination $dir
    return $dir
}
function Link($name,$target,$dir) {
    $path=Join-Path $dir $name
    $link=$shell.CreateShortcut($path);$link.TargetPath=$target;$link.Save()
    return $path
}
function Reject($dir,$extra=@{}) {
    $failed=$false
    try { & $script -InstallDir $dir -Yes -DeleteData -NoPause -ShortcutDir $desktop -StartMenuDir $startMenu @extra } catch { $failed=$true }
    Assert $failed "Unsafe uninstall accepted: $dir"
    Assert (Test-Path -LiteralPath $dir) 'Rejected uninstall changed the directory'
}
$process=$null
$previousTemp=$env:TEMP
$env:TEMP=$scratch
try {
    $dir=Fixture '中文 安装 !'
    $mine=Link 'MDReader.lnk' (Join-Path $dir 'MDReader.exe') $desktop
    $menu=Link 'MDReader.lnk' (Join-Path $dir 'MDReader.exe') $startMenu
    $other=Link 'MDReader 测试版.lnk' (Join-Path $scratch 'other\MDReader.exe') $desktop
    # Cancellation must preserve files, shortcut and data.
    '' | & powershell -NoProfile -ExecutionPolicy Bypass -File $script -InstallDir $dir -NoPause -ShortcutDir $desktop -StartMenuDir $startMenu
    Assert ($LASTEXITCODE -eq 0) 'Cancel failed'
    Assert ((Test-Path -LiteralPath $mine) -and (Test-Path -LiteralPath $dir)) 'Cancellation removed files'
    # Run the actual self-deleting CMD from its own installation directory.
    Push-Location $dir
    try { @('DELETE','') | & $env:ComSpec /d /c ('卸载.cmd -ShortcutDir "{0}" -StartMenuDir "{1}"' -f $desktop,$startMenu) }
    finally { Pop-Location }
    Assert ($LASTEXITCODE -eq 0) 'Double-click uninstall wrapper failed'
    Assert (-not (Test-Path -LiteralPath $dir)) 'Installation directory remains'
    Assert (-not (Test-Path -LiteralPath $mine)) 'Own desktop shortcut remains'
    Assert (-not (Test-Path -LiteralPath $menu)) 'Own Start menu shortcut remains'
    Assert (Test-Path -LiteralPath $other) 'Other installation shortcut deleted'
    Assert (-not (Test-Path -LiteralPath ($dir+' data'))) 'Data directory remains'
    $dir=Fixture 'personal files'
    Set-Content -LiteralPath (Join-Path $dir 'my-notes.md') 'personal'
    & $script -InstallDir $dir -Yes -DeleteData -NoPause -ShortcutDir $desktop -StartMenuDir $startMenu
    Assert (-not (Test-Path -LiteralPath $dir)) 'Full uninstall kept files inside program directory'
    Assert (-not (Test-Path -LiteralPath (Join-Path $dir 'MDReader.exe'))) 'Program still installed'
    # Damaged packages, invalid config, overlapping data and explicit purge all refuse.
    # Regression: launching from the download package must delete the recorded targets only.
    $dir=Fixture 'recorded destination'
    $source=Join-Path $scratch 'download package !'
    New-Item -ItemType Directory -Path $source | Out-Null
    Copy-Item -LiteralPath $script,(Join-Path $root 'release\app\卸载.cmd') -Destination $source
    Copy-Item -LiteralPath (Join-Path $dir 'installation.json') -Destination (Join-Path $source 'installed-target.json')
    Set-Content -LiteralPath (Join-Path $source 'outside.md') 'outside'
    Push-Location $source
    try { @('DELETE','') | & $env:ComSpec /d /c ('卸载.cmd -ShortcutDir "{0}" -StartMenuDir "{1}"' -f $desktop,$startMenu) }
    finally { Pop-Location }
    Assert ($LASTEXITCODE -eq 0) 'Source wrapper failed'
    Assert (-not (Test-Path -LiteralPath $dir)) 'Recorded program target remains'
    Assert (-not (Test-Path -LiteralPath ($dir+' data'))) 'Recorded data target remains'
    Assert ((Get-Content -LiteralPath (Join-Path $source 'outside.md')) -eq 'outside') 'Source package was deleted'
    $dir=Fixture 'old command'
    $failed=$false
    try { & $script -InstallDir $dir -Yes -NoPause } catch { $failed=$true }
    Assert $failed 'Legacy unattended command deleted data'
    Assert (Test-Path -LiteralPath ($dir+' data\notes.md')) 'Refusal deleted data'
    $dir=Fixture 'legacy record'
    @{schema=1;workspace=($dir+' data')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dir 'installation.json')
    Reject $dir
    $dir=Fixture 'wrong association'
    @{schema=1;installations=@(@{id=[guid]::NewGuid().ToString();install_dir=$dir})} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath ($dir+' data\.mdreader-installations.json')
    Reject $dir
    $dir=Fixture 'wrong program path'
    $record=Get-Content -LiteralPath (Join-Path $dir 'installation.json') -Raw | ConvertFrom-Json
    $record.install_dir=$source
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dir 'installation.json')
    Reject $dir
    $dir=Fixture 'incomplete'
    Remove-Item -LiteralPath (Join-Path $dir 'MDReader.exe')
    Reject $dir
    $dir=Fixture 'invalid config'
    Set-Content -LiteralPath (Join-Path $dir 'installation.json') '{broken'
    Reject $dir
    $dir=Fixture 'inside data'
    $record=Get-Content -LiteralPath (Join-Path $dir 'installation.json') -Raw | ConvertFrom-Json
    $record.workspace=Join-Path $dir 'notes'
    $record|ConvertTo-Json|Set-Content (Join-Path $dir 'installation.json')
    Reject $dir
    $dir=Fixture 'parent data'
    $record=Get-Content -LiteralPath (Join-Path $dir 'installation.json') -Raw | ConvertFrom-Json
    $record.workspace=$scratch
    $record|ConvertTo-Json|Set-Content (Join-Path $dir 'installation.json')
    Reject $dir
    $dir=Fixture 'purge refused'
    Reject $dir @{Purge=$true}
    $dir=Fixture 'running'
    Copy-Item -LiteralPath (Join-Path $env:WINDIR 'System32\ping.exe') -Destination (Join-Path $dir 'runtime\pythonw.exe') -Force
    $process=Start-Process -FilePath (Join-Path $dir 'runtime\pythonw.exe') -ArgumentList '-t 127.0.0.1' -WindowStyle Hidden -PassThru
    Reject $dir
    Stop-Process -Id $process.Id -Force; $process.WaitForExit(); $process=$null
    & $script -InstallDir $dir -Yes -DeleteData -NoPause -ShortcutDir $desktop -StartMenuDir $startMenu
    Assert (-not (Test-Path -LiteralPath $dir)) 'Closed application cannot uninstall'
    $failed=$false
    try { & $script -InstallDir ([IO.Path]::GetPathRoot($scratch)) -Yes -DeleteData -NoPause } catch { $failed=$true }
    Assert $failed 'Drive root accepted'
    # CMD must preserve failure status and keep incomplete install untouched.
    $dir=Fixture 'wrapper fails'
    Set-Content -LiteralPath (Join-Path $dir 'installation.json') '{broken'
    Push-Location $dir
    try { '' | & $env:ComSpec /d /c 卸载.cmd -Yes }
    finally { Pop-Location }
    Assert ($LASTEXITCODE -ne 0) 'CMD swallowed failure exit status'
    Assert (Test-Path -LiteralPath $dir) 'Failed wrapper removed directory'
    Write-Host "PASS: $count uninstall assertions (self-delete, cancellation, data, shortcuts, path/config guards, running process, failure status)."
} finally {
    $env:TEMP=$previousTemp
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force; $process.WaitForExit() }
    $resolved=(Resolve-Path -LiteralPath $scratch).Path
    $allowed=[IO.Path]::GetFullPath((Join-Path $root '.testtmp'))+'\'
    if ($resolved.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase) -and (Split-Path $resolved -Leaf).StartsWith('uninstall-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
