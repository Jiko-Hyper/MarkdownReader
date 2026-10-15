# ============================================================
#  MDReader.exe 启动器构建（gcc + windres）
#
#  用法：在仓库根目录执行
#     powershell -ExecutionPolicy Bypass -File tools/build_launcher.ps1
#
#  产物：build/launcher/MDReader.exe
#        - 纯 C 小壳，只负责把参数交给 runtime\pythonw.exe main.py
#        - 图标、版本信息、DPI 感知清单都写进 exe 资源
#
#  依赖：MinGW-w64（gcc / windres）。没有编译器时，发布包仍然可以用
#        「启动 MDReader.bat」启动，只是没有 exe 外壳。
# ============================================================

[CmdletBinding()]
param(
    [string]$OutDir = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $root 'build\launcher' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$gcc = (Get-Command gcc -ErrorAction SilentlyContinue).Source
$windres = (Get-Command windres -ErrorAction SilentlyContinue).Source
if (-not $gcc) { throw '找不到 gcc（MinGW-w64）。请安装后重试，或改用「启动 MDReader.bat」。' }
if (-not $windres) { throw '找不到 windres，无法构建带正确名称和图标的启动器。' }

# ------------------------------------------------------------ 版本号
$coreText = Get-Content -LiteralPath (Join-Path $root 'mdreader\core.py') -Raw -Encoding UTF8
$match = [regex]::Match($coreText, 'APP_VERSION = "([^"]+)"')
if (-not $match.Success) { throw 'mdreader/core.py 里找不到 APP_VERSION。' }
$version = $match.Groups[1].Value
$parts = @($version.Split('.') | ForEach-Object { [int]($_ -replace '\D', '') })
while ($parts.Count -lt 4) { $parts += 0 }
$csv = ($parts[0..3] -join ', ')

# ------------------------------------------------------------ 资源脚本
$icon = Join-Path $root 'assets\icon.ico'
if (-not (Test-Path -LiteralPath $icon)) {
    Write-Host '  [i] 还没有 assets/icon.ico，先运行 tools/make_icon.py 生成。' -ForegroundColor Yellow
    & python (Join-Path $root 'tools\make_icon.py')
}
$iconRc = $icon.Replace('\', '/')

$manifest = Join-Path $OutDir 'MDReader.manifest'
@'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity type="win32" name="MDReader.Launcher" version="1.0.0.0"
                    processorArchitecture="amd64"/>
  <description>MDReader - Markdown 阅读与项目整理</description>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
</assembly>
'@ | Set-Content -LiteralPath $manifest -Encoding UTF8

# 资源脚本放在 tools/ 下：windres 需要能找到 winver.h（它内部调用的预处理器
# 不会给 -I 路径加引号，路径里有空格就会失败），这里改成「脚本所在目录」相对引用。
$rc = Join-Path $root 'tools\MDReader.rc'
@"
#include <winver.h>
#pragma code_page(65001)

1 ICON "$iconRc"
1 24 "$($manifest.Replace('\','/'))"

VS_VERSION_INFO VERSIONINFO
FILEVERSION    $csv
PRODUCTVERSION $csv
FILEFLAGSMASK  0x3fL
FILEFLAGS      0x0L
FILEOS         VOS_NT_WINDOWS32
FILETYPE       VFT_APP
FILESUBTYPE    VFT2_UNKNOWN
BEGIN
    BLOCK "StringFileInfo"
    BEGIN
        BLOCK "080404b0"
        BEGIN
            VALUE "CompanyName",      "MDReader"
            VALUE "FileDescription",  "MDReader - Markdown 阅读与项目整理"
            VALUE "FileVersion",      "$version"
            VALUE "InternalName",     "MDReader"
            VALUE "LegalCopyright",   "MIT License"
            VALUE "OriginalFilename", "MDReader.exe"
            VALUE "ProductName",      "MDReader"
            VALUE "ProductVersion",   "$version"
        END
    END
    BLOCK "VarFileInfo"
    BEGIN
        VALUE "Translation", 0x804, 1200
    END
END
"@ | Set-Content -LiteralPath $rc -Encoding UTF8

# ------------------------------------------------------------ 编译
$res = Join-Path $OutDir 'MDReader.res'
$exe = Join-Path $OutDir 'MDReader.exe'
$source = Join-Path $root 'tools\launcher.c'
$resourceTool = if ($windres) { $windres } else { $null }
if ($resourceTool) {
    Push-Location (Join-Path $root 'tools')
    try {
        & $resourceTool 'MDReader.rc' --codepage=65001 -O coff -o $res
        if ($LASTEXITCODE -ne 0) { throw 'windres 编译资源失败。' }
    } finally { Pop-Location }
} else {
    Write-Host '  [!] 找不到 windres，图标与版本信息将不会写入 exe。' -ForegroundColor Yellow
}

$gccArgs = @('-O2', '-municode', '-mwindows', '-static-libgcc', '-o', $exe, $source)
if ($resourceTool) { $gccArgs += $res }
$gccArgs += @('-lcomctl32', '-lshell32', '-lole32', '-luuid')
& $gcc @gccArgs
if ($LASTEXITCODE -ne 0) { throw 'gcc 编译启动器失败。' }

# A native uninstall entry adds only a few KB and uses Windows PowerShell.
$uninstallRc = Join-Path $root 'tools\Uninstall.rc'
$uninstallResource = Join-Path $OutDir 'Uninstall.res'
$uninstallExe = Join-Path $OutDir '卸载.exe'
$uninstallText = (Get-Content -LiteralPath $rc -Raw -Encoding UTF8).Replace('MDReader.exe','卸载.exe').Replace('MDReader - Markdown 阅读与项目整理','MDReader 完整卸载')
[IO.File]::WriteAllText($uninstallRc,$uninstallText,[Text.UTF8Encoding]::new($true))
Push-Location (Join-Path $root 'tools')
try {
    & $windres 'Uninstall.rc' --codepage=65001 -O coff -o $uninstallResource
    if ($LASTEXITCODE -ne 0) { throw '卸载程序资源编译失败。' }
} finally { Pop-Location }
& $gcc '-Os' '-s' '-municode' '-mwindows' '-static-libgcc' '-finput-charset=UTF-8' '-o' $uninstallExe (Join-Path $root 'tools\uninstaller.c') $uninstallResource '-lshell32' '-luser32'
if ($LASTEXITCODE -ne 0) { throw '卸载程序编译失败。' }
Write-Host ("  卸载 EXE: {0:N0} KB" -f ((Get-Item -LiteralPath $uninstallExe).Length / 1KB))

$installRc = Join-Path $root 'tools\Install.rc'
$installResource = Join-Path $OutDir 'Install.res'
$installExe = Join-Path $OutDir '安装.exe'
$installText = (Get-Content -LiteralPath $rc -Raw -Encoding UTF8).Replace('MDReader.exe','安装.exe').Replace('MDReader - Markdown 阅读与项目整理','安装 MDReader')
[IO.File]::WriteAllText($installRc,$installText,[Text.UTF8Encoding]::new($true))
Push-Location (Join-Path $root 'tools')
try {
    & $windres 'Install.rc' --codepage=65001 -O coff -o $installResource
    if ($LASTEXITCODE -ne 0) { throw '安装程序资源编译失败。' }
} finally { Pop-Location }
& $gcc '-Os' '-s' '-municode' '-mwindows' '-static-libgcc' '-finput-charset=UTF-8' '-o' $installExe (Join-Path $root 'tools\installer.c') $installResource '-lshell32' '-luser32'
if ($LASTEXITCODE -ne 0) { throw '安装程序编译失败。' }
Write-Host ("  安装 EXE: {0:N0} KB" -f ((Get-Item -LiteralPath $installExe).Length / 1KB))

$size = (Get-Item -LiteralPath $exe).Length
Write-Host ''
Write-Host "  已生成 $exe" -ForegroundColor Green
Write-Host ("  版本: {0}   大小: {1:N0} KB" -f $version, ($size / 1KB))
Write-Host ''
