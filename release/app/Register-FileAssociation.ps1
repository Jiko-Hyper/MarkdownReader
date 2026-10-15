# Registers Open With metadata without changing the user's default application.
param(
    [Parameter(Mandatory=$true)][string]$Exe,
    [string]$ClassesRoot = 'HKCU:\Software\Classes',
    [string]$SoftwareRoot = '',
    [switch]$OpenDefaultSettings
)
$ErrorActionPreference = 'Stop'
$Exe = (Get-Item -LiteralPath $Exe -ErrorAction Stop).FullName
$description = 'MDReader - Markdown 阅读与项目整理'
$appName = 'MDReader'
if (-not $SoftwareRoot) {
    $SoftwareRoot = if ($ClassesRoot -eq 'HKCU:\Software\Classes') { 'HKCU:\Software' } else { "$ClassesRoot\Software" }
}
$command = '"{0}" --open "%1"' -f $Exe
$icon = '"{0}",0' -f $Exe
function Write-AssociationValue($Path, $Name, $Value) {
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -Path $Path -Force | Out-Null }
    New-ItemProperty -LiteralPath $Path -Name $Name -Value $Value -PropertyType String -Force | Out-Null
}
$app = "$ClassesRoot\Applications\MDReader.exe"
Write-AssociationValue $app 'FriendlyAppName' $appName
Write-AssociationValue "$app\DefaultIcon" '(default)' $icon
Write-AssociationValue "$app\shell\open\command" '(default)' $command
Write-AssociationValue "$app\SupportedTypes" '.md' ''
$prog = "$ClassesRoot\MDReader.md"
Write-AssociationValue $prog '(default)' 'Markdown 文档'
Write-AssociationValue "$prog\Application" 'ApplicationName' $appName
Write-AssociationValue "$prog\Application" 'ApplicationDescription' $description
Write-AssociationValue "$prog\Application" 'ApplicationIcon' $icon
Write-AssociationValue "$prog\DefaultIcon" '(default)' $icon
Write-AssociationValue "$prog\shell\open\command" '(default)' $command
if (-not (Test-Path -LiteralPath "$ClassesRoot\.md\OpenWithProgids")) {
    New-Item -Path "$ClassesRoot\.md\OpenWithProgids" -Force | Out-Null
}
New-ItemProperty -Path "$ClassesRoot\.md\OpenWithProgids" -Name 'MDReader.md' -PropertyType None -Value ([byte[]]@()) -Force | Out-Null
$capabilities = "$SoftwareRoot\MDReader\Capabilities"
Write-AssociationValue $capabilities 'ApplicationName' $appName
Write-AssociationValue $capabilities 'ApplicationDescription' $description
Write-AssociationValue $capabilities 'ApplicationIcon' $icon
Write-AssociationValue "$capabilities\FileAssociations" '.md' 'MDReader.md'
Write-AssociationValue "$SoftwareRoot\RegisteredApplications" 'MDReader' 'Software\MDReader\Capabilities'
if ($ClassesRoot -eq 'HKCU:\Software\Classes') {
    if (-not ('MDReaderAssociationNotify' -as [type])) {
        Add-Type 'using System; using System.Runtime.InteropServices; public class MDReaderAssociationNotify { [DllImport("shell32.dll")] public static extern void SHChangeNotify(uint e, uint f, IntPtr a, IntPtr b); }'
    }
    [MDReaderAssociationNotify]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
    if ($OpenDefaultSettings) {
        $uri = if ([Environment]::OSVersion.Version.Build -ge 22000) { 'ms-settings:defaultapps?registeredAppUser=MDReader' } else { 'ms-settings:defaultapps' }
        Start-Process $uri
    }
}
