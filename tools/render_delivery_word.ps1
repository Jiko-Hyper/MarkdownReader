[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$EvidenceDir, [string]$PythonExecutable = 'python')
$ErrorActionPreference = 'Stop'
$evidence = (Resolve-Path -LiteralPath $EvidenceDir).Path
$samples = Join-Path $evidence 'samples'
if (-not (Test-Path -LiteralPath (Join-Path $evidence 'report.json'))) { throw 'Not a delivery evidence directory.' }
$rendered = Join-Path $evidence 'word-pages'
$report = Get-Content -LiteralPath (Join-Path $evidence 'report.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$validIds = @($report.results | Where-Object { $_.format -eq 'docx' -and $_.inspection } | ForEach-Object { $_.id })
New-Item -ItemType Directory -Path $rendered -Force | Out-Null
$wordApp = New-Object -ComObject Word.Application
Write-Output 'Word instance created'
try {
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordApp.AutomationSecurity = 3
    foreach ($file in Get-ChildItem -LiteralPath $samples -Filter '*.docx' -File) {
        if ($file.BaseName -notin $validIds) { continue }
        $target = Join-Path $rendered ($file.BaseName + '.pdf')
        if (Test-Path -LiteralPath $target) { throw "Preview already exists: $target" }
        $document = $null
        try {
            Write-Output "Opening $($file.FullName)"
            $document = $wordApp.Documents.OpenNoRepairDialog($file.FullName, $false, $true, $false)
            Write-Output "Rendering $($file.BaseName)"
            $document.ExportAsFixedFormat($target, 17)
            Write-Output $target
        } finally {
            if ($null -ne $document) {
                $document.Close(0)
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
            }
        }
    }
} finally {
    $wordApp.Quit()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordApp)
}
& $PythonExecutable (Join-Path $PSScriptRoot 'render_delivery_pages.py') $evidence
if ($LASTEXITCODE -ne 0) { throw 'Word page rendering failed.' }
