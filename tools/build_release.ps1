# ============================================================
#  MDReader 正式版打包（免安装 exe + 运行时 + 插件 + zip）
#
#  用法：在仓库根目录执行
#     powershell -ExecutionPolicy Bypass -File tools/build_release.ps1
#     powershell -ExecutionPolicy Bypass -File tools/build_release.ps1 -NoZip
#     powershell -ExecutionPolicy Bypass -File tools/build_release.ps1 -SkipRuntime  # 复用已有 build\runtime
#
#  产物：
#     build\release\MarkdownReader\MDReader.exe    免安装版（解压即用，无需装 Python）
#     release\MarkdownReader-<版本>-win64.zip      GitHub Release 上传包
#
#  需要的工具：Python 3.12（含 tkinter）、gcc/windres（MinGW-w64，可选但推荐）。
# ============================================================

[CmdletBinding()]
param(
    [switch]$NoZip,
    [switch]$SkipRuntime,
    [string]$RuntimeDir = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$appSource = Join-Path $root 'release\app'
$distRoot = Join-Path $root 'build\release'
$outDir = Join-Path $distRoot 'MarkdownReader'
$runtimeDir = if ($RuntimeDir) { $RuntimeDir } else { Join-Path $root 'build\runtime' }
$previousWorkspace = $env:MDREADER_HOME

function Write-Step($t) { Write-Host "  $t" -ForegroundColor Gray }
function Write-Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }

Push-Location $root
try {
    # ---------------------------------------------------------- 0. 版本号
    $coreText = Get-Content -LiteralPath (Join-Path $root 'mdreader\core.py') -Raw -Encoding UTF8
    $match = [regex]::Match($coreText, 'APP_VERSION = "([^"]+)"')
    if (-not $match.Success) { throw 'mdreader/core.py 里找不到 APP_VERSION。' }
    $version = $match.Groups[1].Value

    Write-Host ''
    Write-Host "  MDReader $version 打包" -ForegroundColor Cyan
    Write-Host '  ------------------------------ ' -ForegroundColor DarkGray

    # --------------------------------------------------- 1. 运行时
    if (-not $SkipRuntime) {
        Write-Step '准备免安装运行时（Python embeddable + tkinter + 插件依赖）...'
        & powershell -ExecutionPolicy Bypass -File (Join-Path $root 'tools\prepare_runtime.ps1') -OutDir $runtimeDir
        if ($LASTEXITCODE -ne 0) { throw '运行时准备失败。' }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $runtimeDir 'pythonw.exe'))) {
        throw "运行时不可用：$runtimeDir\pythonw.exe 不存在（先跑 tools\prepare_runtime.ps1）"
    }

    # --------------------------------------------------- 2. 启动器
    Write-Step '编译 MDReader.exe 启动器 ...'
    & powershell -ExecutionPolicy Bypass -File (Join-Path $root 'tools\build_launcher.ps1') -OutDir (Join-Path $root 'build\launcher')
    if ($LASTEXITCODE -ne 0) { throw '启动器编译失败。' }
    $launcher = Join-Path $root 'build\launcher\MDReader.exe'
    if (-not (Test-Path -LiteralPath $launcher)) { throw "找不到启动器：$launcher" }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'tools\test_install.ps1')
    if ($LASTEXITCODE -ne 0) { throw '安装回归测试失败。' }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'tools\test_install_dialog.ps1')
    if ($LASTEXITCODE -ne 0) { throw '安装目录选择窗口测试失败。' }
    & (Join-Path $runtimeDir 'python.exe') (Join-Path $root 'tools\test_launcher.py')
    if ($LASTEXITCODE -ne 0) { throw '启动器参数测试失败。' }

    # --------------------------------------------------- 3. 组装发布目录
    Write-Step '组装发布目录 ...'
    # 上一次验证可能留下仍在运行的副本（会锁住 native_bridge.dll），先收干净
    $active = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($distRoot + '\', [System.StringComparison]::OrdinalIgnoreCase) }
    if ($active) { throw '旧发布目录中的程序仍在运行，请保存并关闭后重新打包。' }
    if (Test-Path -LiteralPath $distRoot) {
        $resolved = (Resolve-Path -LiteralPath $distRoot).Path
        if ($resolved -ne (Join-Path $root 'build\release')) { throw '发布目录校验失败。' }
        Move-Item -LiteralPath $resolved -Destination ($resolved + '.backup-' + [guid]::NewGuid().ToString('N'))
    }
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    # 3.1 程序源码（用户可见、可改；exe 就是跑这份源码）
    foreach ($item in @('main.py', 'mdreader', 'webui', 'tests', 'tools', 'docs', 'assets')) {
        $source = Join-Path $root $item
        if (-not (Test-Path -LiteralPath $source)) { continue }
        Copy-Item -LiteralPath $source -Destination $outDir -Recurse -Force
    }
    Get-ChildItem -LiteralPath $outDir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $launcher -Destination (Join-Path $outDir 'MDReader.exe') -Force

    # 3.2 免安装运行时
    robocopy $runtimeDir (Join-Path $outDir 'runtime') /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw '运行时复制失败。' }

    # 3.3 官方离线插件：插件包（用户在工作区安装）+ 插件依赖（没装 Python 也能用）
    $pluginSource = Join-Path $root 'plugins'
    foreach ($name in @('packages', 'official', 'dependencies')) {
        $source = Join-Path $pluginSource $name
        if (-not (Test-Path -LiteralPath $source)) { continue }
        $destination = Join-Path $outDir "plugins\$name"
        if ($name -eq 'dependencies') {
            robocopy $source $destination /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -ge 8) { throw '插件依赖复制失败。' }
        } else {
            New-Item -ItemType Directory -Force -Path $destination | Out-Null
            Copy-Item -Path (Join-Path $source '*') -Destination $destination -Recurse -Force
        }
    }

    # 3.4 启动器、安装脚本、说明
    Copy-Item -Path (Join-Path $appSource '*') -Destination $outDir -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $root 'docs\USAGE.md') -Destination (Join-Path $outDir '使用说明.md') -Force
    Copy-Item -LiteralPath (Join-Path $root 'README.md') -Destination (Join-Path $outDir 'README.md') -Force
    Copy-Item -LiteralPath (Join-Path $root 'README.en.md') -Destination (Join-Path $outDir 'README.en.md') -Force
    Copy-Item -LiteralPath (Join-Path $root 'CONTRIBUTING.md') -Destination (Join-Path $outDir 'CONTRIBUTING.md') -Force
    Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination (Join-Path $outDir 'LICENSE') -Force
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    @"
MDReader $version
打包时间: $stamp
运行环境: Windows 10/11 x64（免安装，自带 Python 运行时）
工作区    : %USERPROFILE%\MDReader
入口      : MDReader.exe（等价于 runtime\pythonw.exe main.py）
"@ | Set-Content -Path (Join-Path $outDir 'VERSION.txt') -Encoding UTF8

    # --------------------------------------------------- 4. 冒烟验证
    Write-Step '在发布目录里验证 ...'
    $exe = Join-Path $outDir 'MDReader.exe'
    $env:MDREADER_HOME = Join-Path $outDir 'smoke-workspace'

    # 4.1 入口本身：用随包的解释器直接跑源码，输出能回来
    $entryOut = & (Join-Path $outDir 'runtime\python.exe') (Join-Path $outDir 'main.py') --version 2>&1
    if ($LASTEXITCODE -ne 0 -or "$entryOut" -notmatch [regex]::Escape($version)) {
        throw "入口自检失败：$entryOut"
    }
    Write-Ok "版本自检: $entryOut"

    # 4.2 真实 exe 外壳：双击时走的是 pythonw.exe，它不产生控制台输出（这是预期行为），
    #     所以这里只验证「能跑完并给出成功退出码」。窗口能不能真的渲染由 --selftest 判定。
    $selfProcess = Start-Process -FilePath $exe -ArgumentList '--console --selftest' -WindowStyle Hidden -Wait -PassThru
    if ($selfProcess.ExitCode -ne 0) { throw "MDReader.exe --selftest 失败，退出码：$($selfProcess.ExitCode)" }
    Write-Ok 'exe 外壳与窗口自检通过（内嵌视图能创建并渲染）'
    $diagnosticLog = Join-Path $outDir 'MDReader.log'
    if (Test-Path -LiteralPath $diagnosticLog) { Remove-Item -LiteralPath $diagnosticLog -Force }

    # 4.3 随包插件：装得上、启得来（启用会真的拉起一次插件工作进程做握手）
    $smoke = Join-Path $outDir 'tools\smoke_release.py'
    $smokeOut = & (Join-Path $outDir 'runtime\python.exe') $smoke 2>&1
    if ($LASTEXITCODE -ne 0) { throw "随包插件自检失败：`n$smokeOut" }
    Write-Ok '随包插件自检通过（安装 + 启用 + 命令注册）'

    Remove-Item -LiteralPath $env:MDREADER_HOME -Recurse -Force -ErrorAction SilentlyContinue
    $env:MDREADER_HOME = $null

    # 冒烟跑过一次，清掉缓存
    Get-ChildItem -LiteralPath $outDir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    # --------------------------------------------------- 5. 汇总 + 压缩
    $files = Get-ChildItem -LiteralPath $outDir -Recurse -File
    $size = ($files | Measure-Object -Property Length -Sum).Sum
    Write-Host ''
    Write-Host "  打包完成：$outDir" -ForegroundColor Green
    Write-Host ("  文件数: {0}   大小: {1:N1} MB" -f $files.Count, ($size / 1MB))

    if (-not $NoZip) {
        $zip = Join-Path $root "release\MarkdownReader-$version-win64.zip"
        if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
        Write-Step "压缩 -> $zip"
        Compress-Archive -Path (Join-Path $outDir '*') -DestinationPath $zip -CompressionLevel Optimal
        $zipSize = (Get-Item -LiteralPath $zip).Length
        Write-Ok ("压缩包 {0:N1} MB" -f ($zipSize / 1MB))
    }
    Write-Host ''
    Write-Host '  下一步：把 zip 上传到 GitHub Release，解压后双击 MDReader.exe 即可。' -ForegroundColor Cyan
    Write-Host ''
} finally {
    $env:MDREADER_HOME = $previousWorkspace
    Pop-Location
}
