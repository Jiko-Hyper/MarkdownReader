# ============================================================
#  MDReader - 安装到本机（桌面快捷方式 + 可选 .md 关联）
#
#  用法：右键本文件 -> 使用 PowerShell 运行
#     .\安装到桌面.ps1                    复制到 %LOCALAPPDATA%\MDReader 并建桌面快捷方式
#     .\安装到桌面.ps1 -DesktopOnly       不复制，只为当前位置建快捷方式
#     .\安装到桌面.ps1 -InstallDir D:\App 指定安装目录
#     .\安装到桌面.ps1 -Associate         同时登记「打开方式」（不抢默认程序）
#
#  文档默认保存在 %USERPROFILE%\MDReader，完整卸载将删除所选程序和数据目录。
# ============================================================

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'MDReader'),
    [string]$WorkspaceDir = '',
    [switch]$NoUI,
    [switch]$DesktopOnly,
    [switch]$Associate,
    [switch]$NoShortcut,
    [switch]$NoPause,
    [string]$ShortcutDir = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$APPNAME = 'MDReader'

function Write-Step($t) { Write-Host "  $t" -ForegroundColor Gray }
function Write-Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Write-Warn2($t){ Write-Host "  [!] $t"  -ForegroundColor Yellow }
function Write-Err($t)  { Write-Host "  [X] $t"  -ForegroundColor Red }

function Confirm-Paths([string]$program, [string]$data) {
    foreach ($path in @($program, $data)) {
        if ([string]::IsNullOrWhiteSpace($path) -or -not [IO.Path]::IsPathRooted($path)) { throw '请选择完整的绝对路径。' }
        if (Test-Path -LiteralPath $path -PathType Leaf) { throw '所选路径是文件，请选择文件夹。' }
    }
    $p = [IO.Path]::GetFullPath($program).TrimEnd('\')
    $d = [IO.Path]::GetFullPath($data).TrimEnd('\')
    if ($p -eq $d -or $p.StartsWith($d + '\', [StringComparison]::OrdinalIgnoreCase) -or $d.StartsWith($p + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw '程序目录和配置与数据目录必须分开，不能互相包含。'
    }
    foreach ($candidate in @($p, $d)) {
        foreach ($special in @($env:USERPROFILE,$env:WINDIR,$env:LOCALAPPDATA,$env:APPDATA,$env:ProgramFiles,${env:ProgramFiles(x86)},$env:ProgramData,[Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('MyDocuments'),(Join-Path $env:USERPROFILE 'Downloads'))) {
            if ($special) {
                $special = [IO.Path]::GetFullPath($special).TrimEnd('\')
                if ($candidate -eq $special -or $special.StartsWith($candidate+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '请选择专用子文件夹，不要选择系统、桌面或用户公共目录。' }
            }
        }
    }
    if ($p -eq [IO.Path]::GetPathRoot($p).TrimEnd('\') -or $d -eq [IO.Path]::GetPathRoot($d).TrimEnd('\')) { throw '请选择专用子文件夹，不要选择磁盘根目录。' }
}

function Select-InstallFolders([string]$program, [string]$data) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()
    $form = New-Object Windows.Forms.Form
    $form.Text = '安装 MDReader'
    $form.ClientSize = New-Object Drawing.Size(640, 285)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.AutoScaleMode = 'Dpi'
    $form.Font = New-Object Drawing.Font('Microsoft YaHei UI', 10)
    $boxes = @()
    $labels = @('程序安装目录', '配置与数据目录（设置、历史记录、项目）')
    for ($i = 0; $i -lt 2; $i++) {
        $y = 22 + 82 * $i
        $label = New-Object Windows.Forms.Label
        $label.Text = $labels[$i]; $label.SetBounds(22, $y, 590, 25)
        $box = New-Object Windows.Forms.TextBox
        $box.SetBounds(22, ($y + 29), 467, 28)
        $box.Text = @($program, $data)[$i]
        $browse = New-Object Windows.Forms.Button
        $browse.Text = '浏览文件夹…'; $browse.SetBounds(499, ($y + 27), 119, 32)
        $browse.Tag = $box
        $browse.Add_Click({
            $dialog = New-Object Windows.Forms.FolderBrowserDialog
            $dialog.Description = '选择文件夹（可新建专用文件夹）'
            $dialog.SelectedPath = $this.Tag.Text
            try { if ($dialog.ShowDialog() -eq 'OK') { $this.Tag.Text = $dialog.SelectedPath } }
            finally { $dialog.Dispose() }
        })
        $form.Controls.AddRange(@($label, $box, $browse))
        $boxes += $box
    }
    $hint = New-Object Windows.Forms.Label
    $hint.Text = '请选择专用目录：完整卸载会删除所选两个目录内的全部内容。'
    $hint.SetBounds(22, 188, 596, 25)
    $install = New-Object Windows.Forms.Button
    $install.Text = '安装'; $install.SetBounds(398, 232, 105, 34)
    $install.BackColor = [Drawing.Color]::FromArgb(0, 102, 204)
    $install.ForeColor = [Drawing.Color]::White
    $install.Add_Click({
        try {
            Confirm-Paths $boxes[0].Text $boxes[1].Text
            $form.Tag = @($boxes[0].Text, $boxes[1].Text)
            $form.DialogResult = 'OK'
        } catch { [Windows.Forms.MessageBox]::Show($form, $_.Exception.Message, '请检查目录', 'OK', 'Warning') | Out-Null }
    })
    $cancel = New-Object Windows.Forms.Button
    $cancel.Text = '取消'; $cancel.SetBounds(513, 232, 105, 34); $cancel.DialogResult = 'Cancel'
    $form.Controls.AddRange(@($hint, $install, $cancel))
    $form.AcceptButton = $install; $form.CancelButton = $cancel
    try { if ($form.ShowDialog() -eq 'OK') { return $form.Tag } }
    finally { $form.Dispose() }
}

try {
Write-Host ""
Write-Host "  $APPNAME 安装程序" -ForegroundColor Cyan
Write-Host "  ------------------------------" -ForegroundColor DarkGray

$sourceExe = Join-Path $here "$APPNAME.exe"
if (-not (Test-Path -LiteralPath $sourceExe)) {
    throw "找不到 $APPNAME.exe，请先完整解压发布包，再运行本脚本。"
}
foreach ($required in @('main.py', 'runtime\pythonw.exe', 'runtime\python.exe', 'mdreader\core.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $here $required) -PathType Leaf)) {
        throw "发布包不完整，缺少 $required。请完整解压，不要直接在压缩包内运行。"
    }
}

# ------------------------------------------------------------ 1. 目标目录
if (-not $WorkspaceDir) {
    $existingConfig = Join-Path $InstallDir 'installation.json'
    if ($DesktopOnly) { $existingConfig = Join-Path $here 'installation.json' }
    if (Test-Path -LiteralPath $existingConfig) {
        $WorkspaceDir = (Get-Content -LiteralPath $existingConfig -Raw -Encoding UTF8 | ConvertFrom-Json).workspace
    }
    if (-not $WorkspaceDir) { $WorkspaceDir = Join-Path $env:USERPROFILE 'MDReader' }
}
if (-not $NoUI -and -not $DesktopOnly -and -not $PSBoundParameters.ContainsKey('InstallDir')) {
    $selection = Select-InstallFolders $InstallDir $WorkspaceDir
    if (-not $selection) { Write-Step '已取消安装，未做任何更改。'; return }
    $InstallDir, $WorkspaceDir = $selection
}
$programPath = if ($DesktopOnly) { $here } else { $InstallDir }
Confirm-Paths $programPath $WorkspaceDir
$WorkspaceDir = [IO.Path]::GetFullPath($WorkspaceDir)
$dataSource = [IO.Path]::GetFullPath($here).TrimEnd('\')
if ($WorkspaceDir.TrimEnd('\') -eq $dataSource -or $WorkspaceDir.StartsWith($dataSource + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw '配置与数据目录不能放在发布包目录内，请选择独立文件夹。'
}
$target = $InstallDir
if ($DesktopOnly) {
    $target = $here
    Write-Step "只建快捷方式，程序位置: $target"
} else {
    $samePlace = $false
    try { $samePlace = ([IO.Path]::GetFullPath($here) -eq [IO.Path]::GetFullPath($target)) } catch { $samePlace = $false }
    if ($samePlace) {
        Write-Step "已经在安装目录里，跳过复制"
    } else {
        $target = [IO.Path]::GetFullPath($target).TrimEnd('\')
        $sourceRoot = [IO.Path]::GetFullPath($here).TrimEnd('\')
        if ($sourceRoot.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $target.StartsWith($sourceRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $target -eq [IO.Path]::GetPathRoot($target).TrimEnd('\')) {
            throw '安装目录不能是磁盘根目录，也不能与发布目录互相包含。'
        }
        $running = Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase)
        }
        if ($running) { throw '目标目录中的程序正在运行，请保存文档并关闭后重试。' }
        Write-Step "复制程序到: $target"
        if (Test-Path -LiteralPath $target) {
            if (@(Get-ChildItem -LiteralPath $target -Force).Count) {
                if (-not (Test-Path -LiteralPath (Join-Path $target 'main.py')) -or
                    -not (Test-Path -LiteralPath (Join-Path $target 'mdreader\core.py'))) {
                    throw '目标目录非空且不是可识别的 MDReader 安装，请选择一个新的空目录。'
                }
                $backup = $target + '.backup-' + [guid]::NewGuid().ToString('N')
                Copy-Item -LiteralPath $target -Destination $backup -Recurse
                Write-Ok "旧安装已完整备份到: $backup"
            }
        } else {
            New-Item -ItemType Directory -Force -Path $target | Out-Null
        }
        Get-ChildItem -LiteralPath $here -Force |
            Where-Object { $_.Name -in @('MDReader.exe', 'main.py', 'mdreader', 'runtime', 'webui', 'assets', 'plugins', 'docs', 'tools', 'tests', 'README.md', 'README.en.md', 'CONTRIBUTING.md', 'LICENSE', 'VERSION.txt', '使用说明.md', '安装到桌面.ps1', '安装到桌面.cmd', '启动 MDReader.bat', '卸载.ps1', '卸载.cmd') } |
            ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $target -Recurse -Force }
        Write-Ok "程序已复制"
    }
}

$target = [IO.Path]::GetFullPath($target).TrimEnd('\')
$targetExe = Join-Path $target "$APPNAME.exe"
if (-not (Test-Path -LiteralPath $targetExe)) {
    throw "找不到 $targetExe"
}
New-Item -ItemType Directory -Force -Path $WorkspaceDir | Out-Null
$probe = Join-Path $WorkspaceDir ('.mdreader-write-test-' + [guid]::NewGuid().ToString('N'))
try { [IO.File]::WriteAllText($probe, '') } finally { if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe } }
$configPath = Join-Path $target 'installation.json'
$installationId = [guid]::NewGuid().ToString()
if (Test-Path -LiteralPath $configPath) {
    $old = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $oldId = [guid]::Empty
    if ($old.schema -eq 2 -and $old.install_dir -eq $target -and [guid]::TryParse([string]$old.installation_id, [ref]$oldId) -and $oldId -ne [guid]::Empty) { $installationId = $old.installation_id }
}
$markerPath = Join-Path $WorkspaceDir '.mdreader-installations.json'
$entries = @()
if (Test-Path -LiteralPath $markerPath) {
    $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($marker.schema -ne 1 -or -not $marker.installations) { throw '数据目录的安装关联记录损坏，请检查后重试。' }
    $entries = @($marker.installations | Where-Object { $_.id -ne $installationId -and $_.install_dir -ne $target })
}
$entries += @{ id = $installationId; install_dir = $target }
$markerJson = @{ schema = 1; installations = @($entries) } | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText($markerPath, $markerJson, [Text.UTF8Encoding]::new($false))
$config = @{ workspace = $WorkspaceDir.TrimEnd('\'); install_dir = $target; installation_id = $installationId; schema = 2 } | ConvertTo-Json
[IO.File]::WriteAllText($configPath, $config, [Text.UTF8Encoding]::new($false))

# --------------------------------------------------------- 2. 桌面快捷方式
if (-not $NoShortcut) {
    $desktop = $ShortcutDir
    if (-not $desktop) { $desktop = Join-Path $env:USERPROFILE 'Desktop' }
    $lnk = Join-Path $desktop "$APPNAME.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($lnk)
    $sc.TargetPath = $targetExe
    $sc.Arguments = ''
    $sc.WorkingDirectory = $target
    $sc.IconLocation = "$targetExe,0"
    $sc.Description = 'MDReader - Markdown 阅读与项目整理'
    $sc.Save()
    $identityProcess = Start-Process -FilePath $targetExe -ArgumentList ('--register-shortcut "{0}"' -f $lnk) -WindowStyle Hidden -Wait -PassThru
    if ($identityProcess.ExitCode -ne 0) { throw "任务栏快捷方式身份设置失败: $($identityProcess.ExitCode)" }
    if (Test-Path -LiteralPath $lnk) { Write-Ok "桌面快捷方式: $lnk" } else { Write-Warn2 "快捷方式创建失败" }
}

# ------------------------------------------------------- 3. 可选: .md 关联
if ($Associate) {
    try {
        $key = 'HKCU:\Software\Classes\MDReader.md'
        New-Item -Path $key -Force | Out-Null
        Set-ItemProperty -Path $key -Name '(default)' -Value 'Markdown 文档'
        New-Item -Path "$key\DefaultIcon" -Force | Out-Null
        Set-ItemProperty -Path "$key\DefaultIcon" -Name '(default)' -Value "$targetExe,0"
        New-Item -Path "$key\shell\open\command" -Force | Out-Null
        Set-ItemProperty -Path "$key\shell\open\command" -Name '(default)' -Value ('"{0}" --open "%1"' -f $targetExe)
        New-Item -Path 'HKCU:\Software\Classes\.md\OpenWithProgids' -Force | Out-Null
        New-ItemProperty -Path 'HKCU:\Software\Classes\.md\OpenWithProgids' -Name 'MDReader.md' `
            -PropertyType None -Value ([byte[]]@()) -Force | Out-Null
        Write-Ok "已登记到 .md 的「打开方式」（不会抢默认程序）"
    } catch {
        Write-Warn2 "登记 .md 关联失败: $_"
    }
}

# The extracted package remembers the destination without changing portable runtime settings.
if ([IO.Path]::GetFullPath($here).TrimEnd('\') -ne $target) {
    try { [IO.File]::WriteAllText((Join-Path $here 'installed-target.json'), $config, [Text.UTF8Encoding]::new($false)) }
    catch { Write-Warn2 '无法在下载包保存安装地址，请从已安装目录运行卸载入口。' }
}
# ------------------------------------------------------------------ 完成
$ws = $WorkspaceDir
Write-Host ""
Write-Host "  安装完成！" -ForegroundColor Green
Write-Host "  程序目录: $target"
Write-Host "  数据目录: $ws  （完整卸载会删除，请先备份）"
Write-Host ""
if (-not $NoShortcut) { Write-Host "  双击桌面上的 $APPNAME 图标即可启动。" -ForegroundColor Cyan }
Write-Host ""
} catch {
    Write-Err "安装失败: $($_.Exception.Message)"
    throw
} finally {
    if (-not $NoPause) { Read-Host '按 Enter 关闭窗口' | Out-Null }
}
