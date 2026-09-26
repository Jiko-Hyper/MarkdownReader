# ============================================================
#  准备免安装运行时 runtime\（Python 3.12 embeddable + tkinter + 插件依赖）
#
#  用法：在仓库根目录执行
#     powershell -ExecutionPolicy Bypass -File tools/prepare_runtime.ps1
#     powershell -ExecutionPolicy Bypass -File tools/prepare_runtime.ps1 -Force
#
#  为什么不直接打包 Python：embed 版没有 tkinter，也没有插件要用的第三方库。
#  这个脚本把两者补齐，最后得到一个能直接跑源码目录的 runtime\：
#     runtime\pythonw.exe        <- 启动器调用它
#     runtime\tkinter\ tcl\ ...  <- 从本机 Python 3.12 复制（要求小版本一致）
#     runtime\PIL\ docx\ ...     <- markdown_it / Pillow / python-docx / reportlab
#
#  产物：build\runtime\（约 60 MB）。构建脚本会自动调用本脚本。
# ============================================================

[CmdletBinding()]
param(
    [string]$OutDir = '',
    [string]$CacheDir = '',
    [string]$TkSource = '',
    [string]$DepsSource = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $root 'build\runtime' }
if (-not $CacheDir) { $CacheDir = Join-Path $root 'build\dl' }
if (-not $DepsSource) { $DepsSource = Join-Path $root 'plugins\dependencies' }

function Write-Step($t) { Write-Host "  $t" -ForegroundColor Gray }
function Write-Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }

# ------------------------------------------------------------ Python 版本
$pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pyExe) { throw '找不到 python，无法确定要准备的运行时版本。' }
$version = (& python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
if (-not $version) { throw '无法读取本机 Python 版本。' }
$short = ($version -split '\.')[0..1] -join '.'
Write-Host ''
Write-Host "  准备免安装运行时 runtime\ (Python $version)" -ForegroundColor Cyan
Write-Host '  ------------------------------ ' -ForegroundColor DarkGray

# ------------------------------------------------------------ embed 包
$embedZip = Join-Path $CacheDir "python-$version-embed-amd64.zip"
if (-not (Test-Path -LiteralPath $embedZip)) {
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
    $url = "https://www.python.org/ftp/python/$version/python-$version-embed-amd64.zip"
    Write-Step "下载 $url"
    Invoke-WebRequest -Uri $url -OutFile $embedZip -UseBasicParsing
}
if ((Get-Item -LiteralPath $embedZip).Length -lt 5MB) { throw "embed 包不完整：$embedZip" }

if ($Force -and (Test-Path -LiteralPath $OutDir)) { Remove-Item -LiteralPath $OutDir -Recurse -Force }
if (Test-Path -LiteralPath $OutDir) { Remove-Item -LiteralPath $OutDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Expand-Archive -LiteralPath $embedZip -DestinationPath $OutDir -Force
Write-Ok "embed 包解压完成"

# ------------------------------------------------------------ tkinter
# 只有完整安装的 Python 才带 tkinter。本机这份来自 python.org 安装包，
# 目录形状是 <root>\python.exe + <root>\Lib\tkinter + <root>\DLLs\_tkinter.pyd。
function Find-TkRoot {
    $candidates = @()
    if ($TkSource) { $candidates += $TkSource }
    $exeDir = Split-Path -Parent $pyExe
    $candidates += @($exeDir, (Split-Path -Parent $exeDir), (Split-Path -Parent (Split-Path -Parent $exeDir)))
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$($short.Replace('.',''))") }
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles "Python$($short.Replace('.',''))") }
    $candidates += @("C:\Python$($short.Replace('.',''))", 'D:\APPLICATION\PYTHON\TOOLS')
    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        foreach ($probe in @($candidate, (Join-Path $candidate 'python'))) {
            if ((Test-Path -LiteralPath (Join-Path $probe 'Lib\tkinter')) -and
                (Test-Path -LiteralPath (Join-Path $probe 'DLLs\_tkinter.pyd'))) {
                return $probe
            }
        }
    }
    return $null
}

$tk = Find-TkRoot
if (-not $tk) {
    throw ("找不到 tkinter 源目录：需要与本机同一个 $version 的完整 Python 安装（安装时勾选 tcl/tk）。" +
           "也可以用 -TkSource <Python 安装目录> 手动指定。")
}
Write-Step "tkinter 源目录: $tk"
Copy-Item -LiteralPath (Join-Path $tk 'Lib\tkinter') -Destination (Join-Path $OutDir 'tkinter') -Recurse -Force
foreach ($dll in @('_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll', 'zlib1.dll')) {
    $src = Join-Path $tk "DLLs\$dll"
    if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $OutDir -Force }
}
# Tcl/Tk 的库目录名跟的是 Tcl 自己的版本（如 tcl8.6 / tk8.6），不是 Python 版本，
# 所以这里按名字去找，不拼版本号。
$tclRoot = Join-Path $tk 'tcl'
foreach ($source in @(Get-ChildItem -LiteralPath $tclRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'tcl[0-9]*' -or $_.Name -like 'tk[0-9]*' })) {
    Copy-Item -LiteralPath $source.FullName -Destination (Join-Path $OutDir "tcl\$($source.Name)") -Recurse -Force
}
if (-not (Test-Path -LiteralPath (Join-Path $OutDir 'tcl\tcl8.6\init.tcl'))) {
    throw "Tcl 库没有复制成功：$tclRoot 下找不到 tcl8.6\init.tcl"
}
Write-Ok 'tkinter / Tcl-Tk 已补齐'

# ------------------------------------------------------------ 插件依赖
$packages = @('PIL', 'markdown_it', 'mdurl', 'docx', 'lxml', 'reportlab',
              'charset_normalizer', 'typing_extensions.py')
$siteUser = & python -c "import site, os; print(os.path.dirname(site.getusersitepackages()))"
$siteUser = if ($siteUser) { $siteUser.Trim() } else { '' }
$searchRoots = @()
foreach ($candidate in @($siteUser, $DepsSource)) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { $searchRoots += $candidate }
}
if ($siteUser) {
    $lib = Join-Path (Split-Path -Parent $pyExe) 'Lib\site-packages'
    if (Test-Path -LiteralPath $lib) { $searchRoots += $lib }
}

function Find-PackageFolder($name) {
    foreach ($searchRoot in $searchRoots) {
        $path = Join-Path $searchRoot $name
        if (Test-Path -LiteralPath $path) { return $path }
    }
    return $null
}

foreach ($package in $packages) {
    $source = Find-PackageFolder $package
    if (-not $source) {
        Write-Host "  [!] 缺少 $package，插件可能无法工作（可从 plugins\dependencies 补齐）" -ForegroundColor Yellow
        continue
    }
    Copy-Item -LiteralPath $source -Destination $OutDir -Recurse -Force
}
# dist-info：importlib.metadata 需要（Pillow / python-docx 会查自己的版本）
foreach ($searchRoot in $searchRoots) {
    Get-ChildItem -LiteralPath $searchRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(PIL|pillow|markdown_it|mdurl|docx|python_docx|lxml|reportlab|charset_normalizer|typing_extensions)-' } |
        ForEach-Object {
            $destination = Join-Path $OutDir $_.Name
            if (-not (Test-Path -LiteralPath $destination)) {
                Copy-Item -LiteralPath $_.FullName -Destination $OutDir -Recurse -Force
            }
        }
}
Write-Ok '插件依赖已复制'

# ------------------------------------------------------------ 自检
Write-Step '自检运行时 ...'
# 自检代码写成临时文件再执行：多行代码直接当原生参数传，在旧版 PowerShell 下会走样。
# Tcl/Tk 的库位置由程序启动时自己设置（mdreader/appicon.ensure_tcl_environment），
# 所以探针从应用目录导入 mdreader 再验 tkinter，路径与实际运行完全一致。
$probeScript = Join-Path $root 'build\runtime-probe.py'
@'
import os
import sys

APP = os.environ["MDREADER_APP"]
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "runtime"))

from mdreader.appicon import ensure_tcl_environment
ensure_tcl_environment()

import tkinter, PIL, markdown_it, docx, typing_extensions
import lxml.etree
try:
    import reportlab
    rl = reportlab.Version
except Exception as exc:
    rl = "MISSING (%s)" % exc

root = tkinter.Tk()
root.withdraw()
print("tkinter %s / Pillow %s / markdown-it %s / lxml %s / reportlab %s"
      % (tkinter.TkVersion, PIL.__version__, markdown_it.__version__,
         lxml.etree.LXML_VERSION[0:3], rl))
root.destroy()
'@ | Set-Content -LiteralPath $probeScript -Encoding UTF8
$env:MDREADER_APP = $root
$probe = & (Join-Path $OutDir 'python.exe') $probeScript 2>&1
$probeCode = $LASTEXITCODE
$env:MDREADER_APP = $null
Remove-Item -LiteralPath $probeScript -Force -ErrorAction SilentlyContinue
if ($probeCode -ne 0) { throw "运行时自检失败：`n$probe" }
Write-Ok $probe

$size = (Get-ChildItem -LiteralPath $OutDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ''
Write-Host ("  运行时就绪: $OutDir  ({0:N1} MB)" -f ($size / 1MB)) -ForegroundColor Green
Write-Host ''
