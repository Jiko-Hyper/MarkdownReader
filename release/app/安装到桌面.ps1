# ============================================================
#  MDReader - 安装到本机（桌面快捷方式 + 可选 .md 关联）
#
#  用法：右键本文件 -> 使用 PowerShell 运行
#     .\安装到桌面.ps1                    复制到 %LOCALAPPDATA%\MDReader 并建桌面快捷方式
#     .\安装到桌面.ps1 -DesktopOnly       不复制，只为当前位置建快捷方式
#     .\安装到桌面.ps1 -InstallDir D:\App 指定安装目录
#     .\安装到桌面.ps1 -Associate         同时登记「打开方式」（不抢默认程序）
#
#  文档默认保存在 %USERPROFILE%\MDReader，卸载清理程序配置和 API，保留项目与用户文件。
# ============================================================

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'MDReader'),
    [string]$WorkspaceDir = '',
    [switch]$NoUI,
    [switch]$DesktopOnly,
    [switch]$Associate,
    [switch]$SetDefault,
    [string]$AssociationClassesRoot = 'HKCU:\Software\Classes',
    [switch]$NoShortcut,
    [switch]$NoPause,
    [string]$ShortcutDir = [Environment]::GetFolderPath('Desktop'),
    [switch]$Gui,
    [string]$ResultFile = ''
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
if ((Split-Path -Leaf $here) -eq 'setup') { $here = Split-Path -Parent $here }
$packageRoot = $here
$resourcePrefix = ''
if ((Split-Path -Leaf $here) -eq 'resources' -and (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $here) 'MDReader.exe'))) {
    $packageRoot = Split-Path -Parent $here
    $resourcePrefix = 'resources/'
}
$APPNAME = 'MDReader'
$installStatus = 'failed'
$installError = ''

function Get-PayloadRoot([string]$program) {
    if (Test-Path -LiteralPath (Join-Path $program 'resources\main.py') -PathType Leaf) { return (Join-Path $program 'resources') }
    return $program
}

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
    $picker = Join-Path $here 'setup\Select-Folder.ps1'
    if (-not (Test-Path -LiteralPath $picker)) { $picker = Join-Path $here 'Select-Folder.ps1' }
    . $picker -LoadOnly
    $dpiContext = [MDReader.NativeFolderPicker]::EnterDpiContext()
    $form = $null
    try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()
    $form = New-Object Windows.Forms.Form
    $form.Text = '安装 MDReader'
    $form.ClientSize = New-Object Drawing.Size(640, 345)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.AutoScaleDimensions = New-Object Drawing.SizeF(96, 96)
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
            $selected = Select-MDReaderFolder -Title '选择文件夹（可新建专用文件夹）' -InitialPath $this.Tag.Text -OwnerHandle $form.Handle.ToInt64()
            if ($selected) { $this.Tag.Text = $selected }
        })
        $form.Controls.AddRange(@($label, $box, $browse))
        $boxes += $box
    }
    $hint = New-Object Windows.Forms.Label
    $hint.Text = '请选择专用目录：卸载清除 API 和程序配置，保留项目与用户文件。'
    $hint.SetBounds(22, 188, 596, 25)
    $defaultApp = New-Object Windows.Forms.CheckBox
    $defaultApp.Text = '安装后设置 MDReader 为默认 .md 打开器'
    $defaultApp.Checked = $SetDefault.IsPresent
    $defaultApp.SetBounds(22, 218, 596, 28)
    $defaultHint = New-Object Windows.Forms.Label
    $defaultHint.Text = '勾选后打开 Windows 默认应用设置，由你确认 .md 的默认应用。'
    $defaultHint.SetBounds(22, 251, 596, 25)
    $install = New-Object Windows.Forms.Button
    $install.Text = '安装'; $install.SetBounds(398, 292, 105, 34)
    $install.BackColor = [Drawing.Color]::FromArgb(0, 102, 204)
    $install.ForeColor = [Drawing.Color]::White
    $install.Add_Click({
        try {
            Confirm-Paths $boxes[0].Text $boxes[1].Text
            $form.Tag = @($boxes[0].Text, $boxes[1].Text, $defaultApp.Checked)
            $form.DialogResult = 'OK'
        } catch { [Windows.Forms.MessageBox]::Show($form, $_.Exception.Message, '请检查目录', 'OK', 'Warning') | Out-Null }
    })
    $cancel = New-Object Windows.Forms.Button
    $cancel.Text = '取消'; $cancel.SetBounds(513, 292, 105, 34); $cancel.DialogResult = 'Cancel'
    $form.Controls.AddRange(@($hint, $defaultApp, $defaultHint, $install, $cancel))
    $form.AcceptButton = $install; $form.CancelButton = $cancel
    if ($form.ShowDialog() -eq 'OK') { return $form.Tag }
    } finally {
        if ($form) { $form.Dispose() }
        [MDReader.NativeFolderPicker]::RestoreDpiContext($dpiContext)
    }
}

try {
Write-Host ""
Write-Host "  $APPNAME 安装程序" -ForegroundColor Cyan
Write-Host "  ------------------------------" -ForegroundColor DarkGray

$sourceExe = Join-Path $packageRoot "$APPNAME.exe"
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
    $existingConfig = Join-Path (Get-PayloadRoot $InstallDir) 'installation.json'
    if ($DesktopOnly) { $existingConfig = Join-Path $here 'installation.json' }
    if (Test-Path -LiteralPath $existingConfig) {
        $WorkspaceDir = (Get-Content -LiteralPath $existingConfig -Raw -Encoding UTF8 | ConvertFrom-Json).workspace
    }
    if (-not $WorkspaceDir) { $WorkspaceDir = Join-Path $env:USERPROFILE 'MDReader' }
}
if (-not $NoUI -and -not $DesktopOnly -and -not $PSBoundParameters.ContainsKey('InstallDir')) {
    $selection = Select-InstallFolders $InstallDir $WorkspaceDir
    if (-not $selection) { $installStatus = 'cancelled'; Write-Step '已取消安装，未做任何更改。'; return }
    $InstallDir, $WorkspaceDir, $SetDefault = $selection
}
$programPath = if ($DesktopOnly) { $packageRoot } else { $InstallDir }
Confirm-Paths $programPath $WorkspaceDir
$WorkspaceDir = [IO.Path]::GetFullPath($WorkspaceDir)
$dataSource = [IO.Path]::GetFullPath($packageRoot).TrimEnd('\')
if ($WorkspaceDir.TrimEnd('\') -eq $dataSource -or $WorkspaceDir.StartsWith($dataSource + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw '配置与数据目录不能放在发布包目录内，请选择独立文件夹。'
}
$target = $InstallDir
$packageManifest=Join-Path $here 'program-files.json'
if (-not (Test-Path -LiteralPath $packageManifest -PathType Leaf)) { throw '安装包缺少程序文件清单，请完整解压新版安装包。' }
$packageInventory=Get-Content -LiteralPath $packageManifest -Raw -Encoding UTF8 | ConvertFrom-Json
if ($packageInventory.schema -ne 1 -or -not $packageInventory.files) { throw '程序文件清单无效。' }
$oldRecord = $null
$oldConfigPath = Join-Path (Get-PayloadRoot $programPath) 'installation.json'
if (Test-Path -LiteralPath $oldConfigPath -PathType Leaf) { $oldRecord = Get-Content -LiteralPath $oldConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json }
$migratedFiles = @()
if ($DesktopOnly) {
    $target = $packageRoot
    Write-Step "只建快捷方式，程序位置: $target"
} else {
    $samePlace = $false
    try { $samePlace = ([IO.Path]::GetFullPath($packageRoot) -eq [IO.Path]::GetFullPath($target)) } catch { $samePlace = $false }
    if ($samePlace) {
        Write-Step "已经在安装目录里，跳过复制"
    } else {
        $target = [IO.Path]::GetFullPath($target).TrimEnd('\')
        $sourceRoot = [IO.Path]::GetFullPath($packageRoot).TrimEnd('\')
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
                $previousPayload = Get-PayloadRoot $target
                if (-not (Test-Path -LiteralPath (Join-Path $previousPayload 'main.py')) -or
                    -not (Test-Path -LiteralPath (Join-Path $previousPayload 'mdreader\core.py'))) {
                    # A previous uninstall may leave only the user's own files.
                    # Reinstall is safe when no shipped file would overwrite one.
                    foreach ($name in $packageInventory.files) {
                        if ([IO.Path]::IsPathRooted($name) -or $name -match '(^|[\\/])\.\.([\\/]|$)') { throw '程序文件清单路径无效。' }
                        if (Test-Path -LiteralPath (Join-Path $target $name)) { throw '目标目录中有与程序同名的用户文件，请选择其他目录以保留它。' }
                    }
                }
                $backup = $target + '.backup-' + [guid]::NewGuid().ToString('N')
                robocopy $target $backup /E /XD (Join-Path $target 'scan-runtime') (Join-Path $target 'runtime\photo_ocr_runtime') (Join-Path $target 'resources\scan-runtime') (Join-Path $target 'resources\runtime\photo_ocr_runtime') /NFL /NDL /NJH /NJS /NP | Out-Null
                if ($LASTEXITCODE -ge 8) { throw '旧程序备份失败，安装已停止。' }
                Write-Ok "旧程序已备份到: $backup（识别组件保留原位，不重复复制）"
            }
        } else {
            New-Item -ItemType Directory -Force -Path $target | Out-Null
        }
        $knownFiles = @()
        $previousPayload = Get-PayloadRoot $target
        foreach ($inventoryName in @('program-files.json','installed-files.json')) {
            $previousInventory = Join-Path $previousPayload $inventoryName
            if (Test-Path -LiteralPath $previousInventory -PathType Leaf) {
                $previousDescriptor = Get-Content -LiteralPath $previousInventory -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($previousDescriptor.schema -ne 1) { throw '原程序文件清单无效，已保留原文件。' }
                $knownFiles += @($previousDescriptor.files)
            }
        }
        foreach ($name in $packageInventory.files) {
            if ([IO.Path]::IsPathRooted($name) -or $name -match '(^|[\\/])\.\.([\\/]|$)' -or $name.Contains(':')) { throw '程序文件清单路径无效。' }
            if ((Test-Path -LiteralPath (Join-Path $target $name)) -and $name -notin $knownFiles) { throw "目标目录中有与新增程序同名的用户文件，已保留：$name" }
            if (-not (Test-Path -LiteralPath (Join-Path $packageRoot $name) -PathType Leaf)) { throw "安装包缺少文件：$name" }
        }
        foreach ($name in $packageInventory.files) {
            if ([IO.Path]::IsPathRooted($name) -or $name -match '(^|[\\/])\.\.([\\/]|$)' -or $name.Contains(':')) { throw '程序文件清单路径无效。' }
            $from = Join-Path $packageRoot $name
            $to = Join-Path $target $name
            if (-not (Test-Path -LiteralPath $from -PathType Leaf)) { throw "安装包缺少文件：$name" }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
            Copy-Item -LiteralPath $from -Destination $to -Force
        }
        if ($resourcePrefix -and $knownFiles.Count) {
            $previousManifest = [IO.Path]::GetTempFileName()
            try {
                [IO.File]::WriteAllText($previousManifest,(ConvertTo-Json -InputObject @($knownFiles) -Compress),[Text.UTF8Encoding]::new($false))
                $migration = & (Join-Path $here 'runtime\python.exe') (Join-Path $here 'tools\migrate_layout.py') --program $target --previous-manifest $previousManifest
                if ($LASTEXITCODE -ne 0) { throw '旧程序目录整理失败，原文件备份已保留。' }
                $migratedFiles = @()
                $migrationResult = ConvertFrom-Json -InputObject ($migration -join "`n")
                foreach ($name in $migrationResult) {
                    if ($name -isnot [string]) { throw '旧程序整理结果无效。' }
                    $migratedFiles += $name
                }
            } finally { Remove-Item -LiteralPath $previousManifest -Force }
        }
        Write-Ok "程序已复制"
    }
}

$target = [IO.Path]::GetFullPath($target).TrimEnd('\')
$targetPayload=Join-Path $target $resourcePrefix
$ownedManifest=Join-Path $targetPayload 'installed-files.json'
$ownedFiles=@($packageInventory.files)+@(($resourcePrefix+'installed-files.json'),($resourcePrefix+'installation.json'))+@($migratedFiles)
if (Test-Path -LiteralPath $ownedManifest -PathType Leaf) {
    $oldInventory=Get-Content -LiteralPath $ownedManifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($oldInventory.schema -ne 1) { throw '原程序文件清单损坏，已保留原文件。' }
    if (-not $resourcePrefix) { $ownedFiles+=@($oldInventory.files) }
}
[IO.File]::WriteAllText($ownedManifest,(@{schema=1;files=@($ownedFiles|Sort-Object -Unique)}|ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
$targetExe = Join-Path $target "$APPNAME.exe"
if (-not (Test-Path -LiteralPath $targetExe)) {
    throw "找不到 $targetExe"
}
New-Item -ItemType Directory -Force -Path $WorkspaceDir | Out-Null
$probe = Join-Path $WorkspaceDir ('.mdreader-write-test-' + [guid]::NewGuid().ToString('N'))
try { [IO.File]::WriteAllText($probe, '') } finally { if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe } }
$configPath = Join-Path $targetPayload 'installation.json'
$installationId = [guid]::NewGuid().ToString()
if ($oldRecord) {
    $old = $oldRecord
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

# Refresh Open With metadata on every install/upgrade, including stale paths.
$associationReady = $false
try {
        & (Join-Path $PSScriptRoot 'Register-FileAssociation.ps1') -Exe $targetExe -ClassesRoot $AssociationClassesRoot
        $associationReady = $true
        Write-Ok "已登记到 .md 的「打开方式」（不会抢默认程序）"
} catch {
        Write-Warn2 "登记 .md 关联失败: $_"
}

# The extracted package remembers the destination without changing portable runtime settings.
if ([IO.Path]::GetFullPath($packageRoot).TrimEnd('\') -ne $target) {
    try { [IO.File]::WriteAllText((Join-Path $here 'installed-target.json'), $config, [Text.UTF8Encoding]::new($false)) }
    catch { Write-Warn2 '无法在下载包保存安装地址，请从已安装目录运行卸载入口。' }
}
# ------------------------------------------------------------------ 完成
$ws = $WorkspaceDir
Write-Host ""
Write-Host "  安装完成！" -ForegroundColor Green
Write-Host "  程序目录: $target"
Write-Host "  数据目录: $ws  （卸载保留项目与用户文件，API 和程序配置将清除）"
Write-Host ""
if (-not $NoShortcut) { Write-Host "  双击桌面上的 $APPNAME 图标即可启动。" -ForegroundColor Cyan }
Write-Host ""
$installStatus = 'installed'
if ($Gui) {
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show("安装完成。`n`n程序目录：$target`n配置与数据：$ws`n`n双击 MDReader.exe 或桌面快捷方式即可启动。", '安装 MDReader') | Out-Null
}
if ($SetDefault -and -not $NoUI -and $associationReady) {
    try { & (Join-Path $PSScriptRoot 'Register-FileAssociation.ps1') -Exe $targetExe -ClassesRoot $AssociationClassesRoot -OpenDefaultSettings }
    catch { Write-Warn2 "无法打开默认应用设置，请在 MDReader 的「更多」菜单重试：$_" }
}
} catch {
    $installError = $_.Exception.Message
    Write-Err "安装失败: $($_.Exception.Message)"
    if ($Gui) { Add-Type -AssemblyName System.Windows.Forms; [Windows.Forms.MessageBox]::Show($installError,'安装失败') | Out-Null }
    throw
} finally {
    if ($ResultFile) {
        [IO.File]::WriteAllText([IO.Path]::GetFullPath($ResultFile),(@{status=$installStatus;error=$installError;install_dir=$target;workspace=$WorkspaceDir}|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    }
    if (-not $NoPause) { Read-Host '按 Enter 关闭窗口' | Out-Null }
}
