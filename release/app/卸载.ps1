# ============================================================
#  MDReader - 卸载
#
#  用法：右键本文件 -> 使用 PowerShell 运行
#     .\卸载.ps1                  删除程序目录 + 桌面快捷方式，保留文档
#     .\卸载.ps1 -Purge           连 %USERPROFILE%\MDReader（文档与项目）一起删
#     .\卸载.ps1 -InstallDir DIR  指定当初的安装目录
#
#  默认绝不碰用户文档；-Purge 会二次确认后才删除。
# ============================================================

[CmdletBinding()]
param(
    [string]$InstallDir = $PSScriptRoot,
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'
$APPNAME = 'MDReader'
$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$ws = Join-Path $env:USERPROFILE $APPNAME
$configPath = Join-Path $InstallDir 'installation.json'
if (Test-Path -LiteralPath $configPath) {
    $ws = (Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json).workspace
}
if (-not $ws -or -not [IO.Path]::IsPathRooted($ws)) { throw '数据目录配置无效，已取消卸载。' }
$ws = [IO.Path]::GetFullPath($ws).TrimEnd('\')
if ($InstallDir -eq [IO.Path]::GetPathRoot($InstallDir).TrimEnd('\') -or
    $ws -eq $InstallDir -or $ws.StartsWith($InstallDir + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw '安装目录包含数据或路径不安全，已取消卸载。'
}
if ($Purge -and (Test-Path -LiteralPath $configPath)) { throw '自定义数据目录请自行备份后手动清理；卸载程序不会删除它。' }

function Write-Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Write-Warn2($t){ Write-Host "  [!] $t"  -ForegroundColor Yellow }

Write-Host ""
Write-Host "  $APPNAME 卸载" -ForegroundColor Cyan
Write-Host "  ------------------------------" -ForegroundColor DarkGray

# 运行中的程序会把 exe 锁住，先提醒
$running = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallDir + '\', [StringComparison]::OrdinalIgnoreCase)
}
if ($running) {
    Write-Warn2 "检测到 $APPNAME 正在运行，请先关闭窗口再卸载。"
    exit 1
}

# ------------------------------------------------------------- 桌面快捷方式
$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop) { $desktop = Join-Path $env:USERPROFILE 'Desktop' }
$lnk = Join-Path $desktop "$APPNAME.lnk"
if ((Test-Path -LiteralPath $lnk) -and
    ((New-Object -ComObject WScript.Shell).CreateShortcut($lnk).TargetPath -eq (Join-Path $InstallDir 'MDReader.exe'))) {
    Remove-Item -LiteralPath $lnk -Force
    Write-Ok "已删除桌面快捷方式"
}
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$smLnk = Join-Path $startMenu "$APPNAME.lnk"
if ((Test-Path -LiteralPath $smLnk) -and
    ((New-Object -ComObject WScript.Shell).CreateShortcut($smLnk).TargetPath -eq (Join-Path $InstallDir 'MDReader.exe'))) {
    Remove-Item -LiteralPath $smLnk -Force
    Write-Ok "已删除开始菜单快捷方式"
}

# ------------------------------------------------------------------ 程序目录
if (Test-Path -LiteralPath $InstallDir) {
    $exe = Join-Path $InstallDir "$APPNAME.exe"
    if (Test-Path -LiteralPath $exe) {
        Remove-Item -LiteralPath $InstallDir -Recurse -Force
        Write-Ok "已删除程序目录: $InstallDir"
    } else {
        Write-Warn2 "$InstallDir 里没有 $APPNAME.exe，已跳过（避免误删别的目录）"
    }
} else {
    Write-Warn2 "程序目录不存在: $InstallDir"
}

# --------------------------------------------------------- 注册表（仅本次安装）
$key = 'HKCU:\Software\Classes\MDReader.md'
if ((Test-Path "$key\shell\open\command") -and
    ((Get-Item "$key\shell\open\command").GetValue('') -eq ('"{0}" --open "%1"' -f (Join-Path $InstallDir 'MDReader.exe')))) {
    Remove-Item -Path $key -Recurse -Force
    Write-Ok "已清除 .md 打开方式登记"
}

# ------------------------------------------------------------------ 用户文档
if ($Purge) {
    if (Test-Path -LiteralPath $ws) {
        Write-Host ""
        Write-Host "  即将删除你的文档目录：$ws" -ForegroundColor Yellow
        $answer = Read-Host "  确认删除请输入 DELETE"
        if ($answer -eq 'DELETE') {
            Remove-Item -LiteralPath $ws -Recurse -Force
            Write-Ok "已删除文档目录"
        } else {
            Write-Warn2 "已取消，文档目录保留"
        }
    }
} else {
    Write-Host ""
    Write-Host "  文档目录保留在: $ws" -ForegroundColor Cyan
    Write-Host "  （如要一并删除，请运行 .\卸载.ps1 -Purge）"
}

Write-Host ""
Write-Host "  卸载完成。" -ForegroundColor Green
Write-Host ""
