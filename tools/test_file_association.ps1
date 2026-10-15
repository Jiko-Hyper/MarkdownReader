param([string]$Exe = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Exe) { $Exe = Join-Path $root 'build/launcher/MDReader.exe' }
$Exe = (Get-Item -LiteralPath $Exe).FullName
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
$expected = 'MDReader - Markdown 阅读与项目整理'
Assert ((Get-Item -LiteralPath $Exe).VersionInfo.FileDescription -ceq $expected) 'Executable description is corrupt'
Add-Type 'using System; using System.Runtime.InteropServices; public class MDReaderIconTest { [DllImport("shell32.dll",CharSet=CharSet.Unicode)] public static extern uint ExtractIconEx(string f,int i,IntPtr[] a,IntPtr[] b,uint n); [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr h); }'
$large = New-Object IntPtr[] 1
$small = New-Object IntPtr[] 1
try {
    $count = [MDReaderIconTest]::ExtractIconEx($Exe,0,$large,$small,1)
    Assert ($count -gt 0 -and $large[0] -ne [IntPtr]::Zero -and $small[0] -ne [IntPtr]::Zero) 'Shell cannot extract both icon sizes'
} finally {
    foreach ($handle in @($large[0],$small[0])) { if ($handle -ne [IntPtr]::Zero) { [MDReaderIconTest]::DestroyIcon($handle) | Out-Null } }
}
$sandbox = 'HKCU:\Software\MDReaderAssociationTest-' + [guid]::NewGuid().ToString('N')
try {
    $script = Join-Path $root 'release/app/Register-FileAssociation.ps1'
    & $script -Exe $Exe -ClassesRoot $sandbox
    $userChoice = "$sandbox\.md\UserChoice"
    New-Item -Path $userChoice -Force | Out-Null
    New-ItemProperty -LiteralPath $userChoice -Name 'ProgId' -Value 'OtherReader.md' -Force | Out-Null
    New-ItemProperty -LiteralPath "$sandbox\.md\OpenWithProgids" -Name 'OtherReader.md' -PropertyType None -Value ([byte[]]@()) -Force | Out-Null
    New-ItemProperty -LiteralPath "$sandbox\Applications\MDReader.exe\shell\open\command" -Name '(default)' -Value '"D:\removed\MDReader.exe" --open "%1"' -Force | Out-Null
    & $script -Exe $Exe -ClassesRoot $sandbox
    $app = "$sandbox\Applications\MDReader.exe"
    Assert ((Get-Item -LiteralPath $app).GetValue('FriendlyAppName') -ceq 'MDReader') 'Wrong Open With name'
    foreach ($key in @($app,"$sandbox\MDReader.md")) {
        Assert ((Get-Item "$key\DefaultIcon").GetValue('') -ceq ('"{0}",0' -f $Exe)) 'Wrong icon path'
        Assert ((Get-Item "$key\shell\open\command").GetValue('') -ceq ('"{0}" --open "%1"' -f $Exe)) 'Wrong open command'
    }
    Assert ((Get-Item "$sandbox\.md\OpenWithProgids").GetValueNames() -contains 'MDReader.md') 'Missing Open With entry'
    Assert ((Get-Item "$sandbox\.md\OpenWithProgids").GetValueNames() -contains 'OtherReader.md') 'Other reader registration was removed'
    Assert ((Get-Item -LiteralPath $userChoice).GetValue('ProgId') -eq 'OtherReader.md') 'Registration must preserve default choice'
    $capabilities = "$sandbox\Software\MDReader\Capabilities"
    Assert ((Get-Item -LiteralPath $capabilities).GetValue('ApplicationName') -ceq 'MDReader') 'Missing default apps display name'
    Assert ((Get-Item -LiteralPath "$capabilities\FileAssociations").GetValue('.md') -ceq 'MDReader.md') 'Missing default apps capability'
    Assert ((Get-Item -LiteralPath "$sandbox\Software\RegisteredApplications").GetValue('MDReader') -ceq 'Software\MDReader\Capabilities') 'Missing registered application'
} finally { if (Test-Path -LiteralPath $sandbox) { Remove-Item -LiteralPath $sandbox -Recurse -Force } }
Write-Host 'PASS: Unicode executable name, large/small shell icons, quoted registration, idempotent repair'
