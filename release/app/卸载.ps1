# Full uninstall: use the two recorded paths, never infer targets from this script.
[CmdletBinding()]
param([string]$InstallDir='', [switch]$Yes, [switch]$DeleteData, [switch]$NoPause,
    [string]$ShortcutDir=[Environment]::GetFolderPath('Desktop'),
    [string]$StartMenuDir=(Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),
    [switch]$Gui, [int]$LauncherProcessId=0, [switch]$TemporaryRunner, [string]$ResultFile='')
$ErrorActionPreference='Stop'
$uninstallStatus='failed'
$uninstallError=''
$planFile=$null
function Full-Path([string]$path) {
    if ([string]::IsNullOrWhiteSpace($path) -or -not [IO.Path]::IsPathRooted($path)) { throw '安装记录必须包含完整的绝对路径。' }
    return [IO.Path]::GetFullPath($path).TrimEnd('\')
}
function Check-Directory([string]$path) {
    $protected=@($env:USERPROFILE,$env:WINDIR,$env:LOCALAPPDATA,$env:APPDATA,$env:ProgramFiles,${env:ProgramFiles(x86)},$env:ProgramData,
        [Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('MyDocuments'),(Join-Path $env:USERPROFILE 'Downloads'))
    if ($path -eq [IO.Path]::GetPathRoot($path).TrimEnd('\')) { throw '拒绝删除磁盘根目录。' }
    foreach ($special in $protected) {
        if ($special) {
            $special=Full-Path $special
            if ($path -eq $special -or $special.StartsWith($path+'\',[StringComparison]::OrdinalIgnoreCase)) { throw "拒绝删除系统或用户公共目录: $path" }
        }
    }
    $existing=$path
    while (-not (Test-Path -LiteralPath $existing)) { $existing=Split-Path -Parent $existing; if (-not $existing) { throw '路径无效。' } }
    $ancestor=Get-Item -LiteralPath $existing -Force
    while ($ancestor) {
        if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "路径包含链接: $path" }
        $ancestor=$ancestor.Parent
    }
    if (Test-Path -LiteralPath $path) {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw '记录指向文件而不是目录。' }
        if ((Resolve-Path -LiteralPath $path).ProviderPath.TrimEnd('\') -ne $path) { throw '目录解析结果不匹配。' }
        if (Get-ChildItem -LiteralPath $path -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } | Select-Object -First 1) { throw "目录内包含链接: $path" }
    }
}
try {
    # The native entry point exits before we check processes or delete its EXE.
    if ($LauncherProcessId -gt 0) { Wait-Process -Id $LauncherProcessId -ErrorAction SilentlyContinue }
    if ($Gui) { Add-Type -AssemblyName System.Windows.Forms }
    if ($Yes -and -not $DeleteData) { throw '完整卸载会删除数据；自动化须同时指定 -Yes -DeleteData，不能沿用旧版 -Yes 命令。' }
    if ($InstallDir) {
        $requested=Full-Path $InstallDir; $recordPath=Join-Path $requested 'resources\installation.json'
        if (-not (Test-Path -LiteralPath $recordPath)) { $recordPath=Join-Path $requested 'installation.json' }
    }
    else {
        $recordRoot=$PSScriptRoot
        if ((Split-Path -Leaf $recordRoot) -eq 'setup') { $recordRoot=Split-Path -Parent $recordRoot }
        $recordPath=Join-Path $recordRoot 'installation.json'
        if (-not (Test-Path -LiteralPath $recordPath)) { $recordPath=Join-Path $recordRoot 'installed-target.json' }
    }
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) { throw '找不到安装记录。请使用已安装目录或原安装包中的卸载入口，或通过 -InstallDir 指定安装目录；不会删除脚本所在文件夹。' }
    $record=Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $parsedId=[guid]::Empty
    if ($record.schema -ne 2 -or -not [guid]::TryParse([string]$record.installation_id,[ref]$parsedId) -or $parsedId -eq [guid]::Empty) { throw '记录缺少可核对的双目录信息，请先用新版安装器升级；未执行删除。' }
    $InstallDir=Full-Path $record.install_dir
    $ws=Full-Path $record.workspace
    if ($requested -and $requested -ne $InstallDir) { throw '指定目录与安装记录不一致。' }
    if ($InstallDir -eq $ws -or $InstallDir.StartsWith($ws+'\',[StringComparison]::OrdinalIgnoreCase) -or $ws.StartsWith($InstallDir+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '两个目标目录重叠。' }
    Check-Directory $InstallDir
    Check-Directory $ws
    $payload=$InstallDir
    if (Test-Path -LiteralPath (Join-Path $InstallDir 'resources\installation.json') -PathType Leaf) { $payload=Join-Path $InstallDir 'resources' }
    $actualPath=Join-Path $payload 'installation.json'
    if (-not (Test-Path -LiteralPath $actualPath -PathType Leaf)) { throw '记录指向的程序目录不存在或已移动；不会改为删除当前文件夹。' }
    $actual=Get-Content -LiteralPath $actualPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($actual.schema -ne 2 -or $actual.installation_id -ne $record.installation_id -or (Full-Path $actual.install_dir) -ne $InstallDir -or (Full-Path $actual.workspace) -ne $ws) { throw '安装记录与目标程序不一致。' }
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'MDReader.exe') -PathType Leaf)) { throw '无法确认目标安装，缺少主程序。' }
    foreach ($name in @('main.py','mdreader\core.py','runtime\pythonw.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $payload $name) -PathType Leaf)) { throw "无法确认目标安装，缺少 $name。" }
    }
    if (Test-Path -LiteralPath $ws) {
        $markerPath=Join-Path $ws '.mdreader-installations.json'
        if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { throw '数据目录缺少安装关联记录，请先用新版安装器升级。' }
        $marker=Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $owned=@($marker.installations | Where-Object { $_.id -eq $record.installation_id -and (Full-Path $_.install_dir) -eq $InstallDir })
        if ($marker.schema -ne 1 -or $owned.Count -ne 1) { throw '数据目录与该安装记录不匹配。' }
        if (@($marker.installations).Count -gt 1) { Write-Host '注意：该目录由多个安装共享，本程序配置将清除，项目和用户文件保留。' -ForegroundColor Yellow }
    }
    $running=Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallDir+'\',[StringComparison]::OrdinalIgnoreCase) }
    if ($running) { throw '请先保存文档并关闭此安装的全部窗口，再完整卸载。' }
    $planner=Join-Path $payload 'mdreader\uninstalling.py'
    $python=Join-Path $payload 'runtime\python.exe'
    if (-not (Test-Path -LiteralPath $planner) -or -not (Test-Path -LiteralPath $python)) { throw '缺少保留用户文件的卸载组件，请先使用新版测试安装包升级。' }
    $planFile=[IO.Path]::GetTempFileName()
    & $python $planner --program $InstallDir --workspace $ws --out $planFile
    if ($LASTEXITCODE -ne 0) { throw '无法核对程序文件清单，未执行删除。' }
    $plan=Get-Content -LiteralPath $planFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($plan.schema -ne 1) { throw '卸载计划无效。' }
    Write-Host "将删除程序文件: $InstallDir" -ForegroundColor Yellow
    Write-Host "将清除配置、API 密钥、历史与缓存: $ws" -ForegroundColor Yellow
    Write-Host ("保留项目及用户自己的文件（{0} 个文件）；重装后必须重新配置 API。" -f $plan.retained_files) -ForegroundColor Green
    if (-not $Yes) {
        if ($Gui) {
            $message="将卸载程序并清除本程序的配置、API 密钥、历史记录、插件与缓存：`n`n程序：$InstallDir`n`n配置：$ws`n`n保留用户项目及自有文件。重装后必须重新配置 API。`n`n确认继续卸载？"
            $confirmed=[System.Windows.Forms.MessageBox]::Show($message,'MDReader 完整卸载',
                [System.Windows.Forms.MessageBoxButtons]::YesNo,[System.Windows.Forms.MessageBoxIcon]::Warning,
                [System.Windows.Forms.MessageBoxDefaultButton]::Button2) -eq [System.Windows.Forms.DialogResult]::Yes
        } else { $confirmed=(Read-Host '确认永久删除上述两个目录请输入 DELETE；其他输入取消') -ceq 'DELETE' }
        if (-not $confirmed) { $uninstallStatus='cancelled'; Write-Host '已取消，两个目录均未修改。'; return }
    }
    $exe=Join-Path $InstallDir 'MDReader.exe'
    $shell=New-Object -ComObject WScript.Shell
    $shortcuts=@()
    foreach ($directory in @($ShortcutDir,$StartMenuDir)) {
        if ($directory -and (Test-Path -LiteralPath $directory)) {
            foreach ($link in (Get-ChildItem -LiteralPath $directory -Filter '*.lnk' -File)) {
                if ($shell.CreateShortcut($link.FullName).TargetPath -eq $exe) { $shortcuts+=$link.FullName }
            }
        }
    }
    # Delete only inventoried program/state files, never recurse through user data.
    Check-Directory $InstallDir
    Check-Directory $ws
    foreach ($name in $plan.files) {
        $item=Full-Path $name
        if (-not ($item.StartsWith($InstallDir+'\',[StringComparison]::OrdinalIgnoreCase) -or $item.StartsWith($ws+'\',[StringComparison]::OrdinalIgnoreCase))) { throw '卸载计划越过安装目录。' }
        if (Test-Path -LiteralPath $item -PathType Leaf) { Remove-Item -LiteralPath $item -Force }
    }
    foreach ($name in $plan.directories) {
        $item=Full-Path $name
        if (-not ($item -eq $InstallDir -or $item -eq $ws -or $item.StartsWith($InstallDir+'\',[StringComparison]::OrdinalIgnoreCase) -or $item.StartsWith($ws+'\',[StringComparison]::OrdinalIgnoreCase))) { throw '卸载目录计划越界。' }
        if ((Test-Path -LiteralPath $item -PathType Container) -and @(Get-ChildItem -LiteralPath $item -Force).Count -eq 0) { Remove-Item -LiteralPath $item -Force }
    }
    foreach ($link in $shortcuts) { Remove-Item -LiteralPath $link -Force }
    $key='HKCU:\Software\Classes\MDReader.md'
    $command="$key\shell\open\command"
    if ((Test-Path -LiteralPath $command) -and ((Get-Item -LiteralPath $command).GetValue('') -eq ('"{0}" --open "%1"' -f $exe))) {
        Remove-Item -LiteralPath $key -Recurse -Force
        $openWith='HKCU:\Software\Classes\.md\OpenWithProgids'
        if (Test-Path -LiteralPath $openWith) { Remove-ItemProperty -LiteralPath $openWith -Name 'MDReader.md' -ErrorAction SilentlyContinue }
    }
    $appKey='HKCU:\Software\Classes\Applications\MDReader.exe'
    $appCommand="$appKey\shell\open\command"
    if ((Test-Path -LiteralPath $appCommand) -and ((Get-Item -LiteralPath $appCommand).GetValue('') -eq ('"{0}" --open "%1"' -f $exe))) {
        Remove-Item -LiteralPath $appKey -Recurse -Force
    }
    $capabilities='HKCU:\Software\MDReader\Capabilities'
    if ((Test-Path -LiteralPath $capabilities) -and ((Get-Item -LiteralPath $capabilities).GetValue('ApplicationIcon') -eq ('"{0}",0' -f $exe))) {
        Remove-Item -LiteralPath $capabilities -Recurse -Force
        $registeredApps='HKCU:\Software\RegisteredApplications'
        if ((Test-Path -LiteralPath $registeredApps) -and ((Get-Item -LiteralPath $registeredApps).GetValue('MDReader') -eq 'Software\MDReader\Capabilities')) {
            Remove-ItemProperty -LiteralPath $registeredApps -Name 'MDReader' -ErrorAction SilentlyContinue
        }
    }
    Write-Host '卸载完成：程序配置及 API 密钥已清除，项目和用户文件已保留。重装后请重新配置 API。' -ForegroundColor Green
    $uninstallStatus='removed'
    if ($Gui) { [System.Windows.Forms.MessageBox]::Show('卸载完成，程序配置和 API 密钥已清除。项目与用户文件已保留，重装后请重新配置 API。','MDReader 完整卸载') | Out-Null }
} catch {
    $uninstallError=$_.Exception.Message
    Write-Host "卸载失败: $uninstallError" -ForegroundColor Red
    if ($Gui) { [System.Windows.Forms.MessageBox]::Show("卸载失败：$uninstallError",'MDReader 完整卸载') | Out-Null }
    throw
} finally {
    if ($planFile -and (Test-Path -LiteralPath $planFile -PathType Leaf)) { Remove-Item -LiteralPath $planFile -Force }
    if ($ResultFile) {
        $result=@{status=$uninstallStatus;error=$uninstallError} | ConvertTo-Json -Compress
        [IO.File]::WriteAllText([IO.Path]::GetFullPath($ResultFile),$result,[Text.UTF8Encoding]::new($false))
    }
    if ($TemporaryRunner) {
        $runnerRoot=[IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
        $tempRoot=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
        if ((Split-Path -Parent $runnerRoot) -eq $tempRoot -and (Split-Path -Leaf $runnerRoot) -match '^MDReader-uninstall-[0-9a-f]{8}-[0-9a-f]{8}$') {
            # Only these two files belong to this runner. Never recurse through temp.
            Remove-Item -LiteralPath (Join-Path $runnerRoot 'installation.json'),(Join-Path $runnerRoot 'runner.ps1') -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $runnerRoot -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $NoPause) { Read-Host '按 Enter 关闭窗口' | Out-Null }
}
