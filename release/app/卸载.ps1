# Full uninstall: use the two recorded paths, never infer targets from this script.
[CmdletBinding()]
param([string]$InstallDir='', [switch]$Yes, [switch]$DeleteData, [switch]$NoPause,
    [string]$ShortcutDir=[Environment]::GetFolderPath('Desktop'),
    [string]$StartMenuDir=(Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))
$ErrorActionPreference='Stop'
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
    if ($Yes -and -not $DeleteData) { throw '完整卸载会删除数据；自动化须同时指定 -Yes -DeleteData，不能沿用旧版 -Yes 命令。' }
    if ($InstallDir) { $requested=Full-Path $InstallDir; $recordPath=Join-Path $requested 'installation.json' }
    else {
        $recordPath=Join-Path $PSScriptRoot 'installation.json'
        if (-not (Test-Path -LiteralPath $recordPath)) { $recordPath=Join-Path $PSScriptRoot 'installed-target.json' }
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
    $actualPath=Join-Path $InstallDir 'installation.json'
    if (-not (Test-Path -LiteralPath $actualPath -PathType Leaf)) { throw '记录指向的程序目录不存在或已移动；不会改为删除当前文件夹。' }
    $actual=Get-Content -LiteralPath $actualPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($actual.schema -ne 2 -or $actual.installation_id -ne $record.installation_id -or (Full-Path $actual.install_dir) -ne $InstallDir -or (Full-Path $actual.workspace) -ne $ws) { throw '安装记录与目标程序不一致。' }
    foreach ($name in @('MDReader.exe','main.py','mdreader\core.py','runtime\pythonw.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $InstallDir $name) -PathType Leaf)) { throw "无法确认目标安装，缺少 $name。" }
    }
    if (Test-Path -LiteralPath $ws) {
        $markerPath=Join-Path $ws '.mdreader-installations.json'
        if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { throw '数据目录缺少安装关联记录，请先用新版安装器升级。' }
        $marker=Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $owned=@($marker.installations | Where-Object { $_.id -eq $record.installation_id -and (Full-Path $_.install_dir) -eq $InstallDir })
        if ($marker.schema -ne 1 -or $owned.Count -ne 1) { throw '数据目录与该安装记录不匹配。' }
        if (@($marker.installations).Count -gt 1) { Write-Host '注意：该数据目录还登记给其他安装，继续将一并清空共享数据。' -ForegroundColor Yellow }
    }
    $running=Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallDir+'\',[StringComparison]::OrdinalIgnoreCase) }
    if ($running) { throw '请先保存文档并关闭此安装的全部窗口，再完整卸载。' }
    Write-Host "将完整删除程序目录及全部内容: $InstallDir" -ForegroundColor Yellow
    Write-Host "将完整删除数据目录及全部内容: $ws" -ForegroundColor Yellow
    Write-Host '数据目录内的文档、项目、配置和密钥均会删除，请先备份。两个目录之外的原始文件、下载包和升级备份不受影响。' -ForegroundColor Yellow
    if (-not $Yes -and (Read-Host '确认永久删除上述两个目录请输入 DELETE；其他输入取消') -cne 'DELETE') { Write-Host '已取消，两个目录均未修改。'; return }
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
    # Recheck exact recorded absolute paths before recursive deletion.
    Check-Directory $InstallDir
    Check-Directory $ws
    if (Test-Path -LiteralPath $ws) { Remove-Item -LiteralPath $ws -Recurse -Force }
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
    foreach ($link in $shortcuts) { Remove-Item -LiteralPath $link -Force }
    $key='HKCU:\Software\Classes\MDReader.md'
    $command="$key\shell\open\command"
    if ((Test-Path -LiteralPath $command) -and ((Get-Item -LiteralPath $command).GetValue('') -eq ('"{0}" --open "%1"' -f $exe))) {
        Remove-Item -LiteralPath $key -Recurse -Force
        $openWith='HKCU:\Software\Classes\.md\OpenWithProgids'
        if (Test-Path -LiteralPath $openWith) { Remove-ItemProperty -LiteralPath $openWith -Name 'MDReader.md' -ErrorAction SilentlyContinue }
    }
    Write-Host '完整卸载完成：已删除记录中的程序与数据两个目录。旧任务栏图标请自行取消固定。' -ForegroundColor Green
} catch { Write-Host "卸载失败: $($_.Exception.Message)" -ForegroundColor Red; throw }
finally { if (-not $NoPause) { Read-Host '按 Enter 关闭窗口' | Out-Null } }
