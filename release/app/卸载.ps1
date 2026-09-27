# MDReader uninstall: keep workspace documents and settings.
[CmdletBinding()]
param(
    [string]$InstallDir = '',
    [switch]$Yes,
    [switch]$NoPause,
    [switch]$Purge,
    [string]$ShortcutDir = [Environment]::GetFolderPath('Desktop'),
    [string]$StartMenuDir = (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
)
$ErrorActionPreference = 'Stop'
try {
    if (-not $InstallDir) { $InstallDir = $PSScriptRoot }
    if ($Purge) { throw '卸载不会删除文档或配置。请备份后自行管理数据目录；不再支持 -Purge。' }
    if (-not [IO.Path]::IsPathRooted($InstallDir)) { throw '请提供完整的安装目录路径。' }
    $InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    if (-not (Test-Path -LiteralPath $InstallDir -PathType Container)) { throw '安装目录不存在，未执行删除。' }
    $resolved = (Resolve-Path -LiteralPath $InstallDir).ProviderPath.TrimEnd('\')
    if ($resolved -ne $InstallDir -or $InstallDir -eq [IO.Path]::GetPathRoot($InstallDir).TrimEnd('\')) {
        throw '安装路径不安全，已取消卸载。'
    }
    # Refuse junctions/symlinks in the target and its ancestors before recursion.
    $ancestor = Get-Item -LiteralPath $InstallDir -Force
    while ($ancestor) {
        if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '安装路径包含目录链接，已取消卸载。' }
        $ancestor = $ancestor.Parent
    }
    foreach ($required in @('MDReader.exe','main.py','mdreader\core.py','runtime\pythonw.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $InstallDir $required) -PathType Leaf)) {
            throw "无法确认这是完整的 MDReader 安装，缺少 $required；未执行删除。"
        }
    }
    $linked = Get-ChildItem -LiteralPath $InstallDir -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } | Select-Object -First 1
    if ($linked) { throw '安装目录内包含文件或目录链接，已取消卸载。' }
    $ws = Join-Path $env:USERPROFILE 'MDReader'
    $config = Join-Path $InstallDir 'installation.json'
    if (Test-Path -LiteralPath $config) { $ws = (Get-Content -LiteralPath $config -Raw -Encoding UTF8 | ConvertFrom-Json).workspace }
    if (-not $ws -or -not [IO.Path]::IsPathRooted($ws)) { throw '数据目录配置无效，已取消卸载。' }
    $ws = [IO.Path]::GetFullPath($ws).TrimEnd('\')
    if ($ws -eq $InstallDir -or $ws.StartsWith($InstallDir+'\',[StringComparison]::OrdinalIgnoreCase) -or $InstallDir.StartsWith($ws+'\',[StringComparison]::OrdinalIgnoreCase)) {
        throw '安装目录与数据目录重叠，已取消卸载。'
    }
    $running = Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallDir+'\',[StringComparison]::OrdinalIgnoreCase)
    }
    if ($running) { throw 'MDReader 正在运行。请先保存文档并关闭所有窗口，再卸载。' }
    Write-Host "即将卸载程序: $InstallDir"
    Write-Host "文档与配置保留在: $ws"
    if (-not $Yes -and (Read-Host '确认卸载请输入 YES；直接回车取消') -cne 'YES') {
        Write-Host '已取消卸载，未作修改。'
        return
    }
    # Only shortcuts pointing at this exact installation belong to this uninstall.
    $exe = Join-Path $InstallDir 'MDReader.exe'
    $shell = New-Object -ComObject WScript.Shell
    $shortcuts = @()
    foreach ($directory in @($ShortcutDir,$StartMenuDir)) {
        if ($directory -and (Test-Path -LiteralPath $directory)) {
            foreach ($link in (Get-ChildItem -LiteralPath $directory -Filter '*.lnk' -File)) {
                if ($shell.CreateShortcut($link.FullName).TargetPath -eq $exe) { $shortcuts += $link.FullName }
            }
        }
    }
    # Remove only known application items; keep unrelated top-level files.
    $appItems = @('MDReader.exe','main.py','mdreader','runtime','webui','assets','plugins','docs','tools','tests',
        'README.md','README.en.md','CONTRIBUTING.md','LICENSE','VERSION.txt','使用说明.md','安装到桌面.ps1',
        '安装到桌面.cmd','启动 MDReader.bat','卸载.ps1','卸载.cmd','installation.json','MDReader.log','__pycache__')
    foreach ($name in $appItems) {
        $item = [IO.Path]::GetFullPath((Join-Path $InstallDir $name))
        if (-not $item.StartsWith($InstallDir+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '程序文件路径越界。' }
        if (Test-Path -LiteralPath $item) { Remove-Item -LiteralPath $item -Recurse -Force }
    }
    if (@(Get-ChildItem -LiteralPath $InstallDir -Force).Count -eq 0) {
        Remove-Item -LiteralPath $InstallDir -Force
    } else {
        Write-Host "安装目录内的其他文件已保留: $InstallDir"
    }
    foreach ($link in $shortcuts) { Remove-Item -LiteralPath $link -Force }
    $key = 'HKCU:\Software\Classes\MDReader.md'
    $command = "$key\shell\open\command"
    if ((Test-Path -LiteralPath $command) -and ((Get-Item -LiteralPath $command).GetValue('') -eq ('"{0}" --open "%1"' -f $exe))) {
        Remove-Item -LiteralPath $key -Recurse -Force
        $openWith = 'HKCU:\Software\Classes\.md\OpenWithProgids'
        if (Test-Path -LiteralPath $openWith) {
            Remove-ItemProperty -LiteralPath $openWith -Name 'MDReader.md' -ErrorAction SilentlyContinue
        }
    }
    Write-Host '卸载完成。文档与配置已保留；已固定的任务栏图标请自行取消固定。' -ForegroundColor Green
} catch {
    Write-Host "卸载失败: $($_.Exception.Message)" -ForegroundColor Red
    throw
} finally {
    if (-not $NoPause) { Read-Host '按 Enter 关闭窗口' | Out-Null }
}
