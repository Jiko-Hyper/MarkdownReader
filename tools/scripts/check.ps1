[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # tools/scripts -> 仓库根
$previousPythonPath = $env:PYTHONPATH
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
# Keep every test artifact inside the repository: sandboxed shells, locked
# profile temp folders and cleanup policies all behave better this way.
$testTemp = Join-Path $projectRoot '.testtmp'
New-Item -ItemType Directory -Force -Path $testTemp | Out-Null
Push-Location $projectRoot
try {
    $env:PYTHONPATH = $projectRoot
    $env:TEMP = $testTemp
    $env:TMP = $testTemp
    # `-t .` makes `tests` a package, so tests/__init__.py runs first and points
    # tempfile at a writable project-local directory.
    & python -m unittest discover -s tests -t . -p 'test_*.py' -v
    if ($LASTEXITCODE -ne 0) { throw 'Python regression tests failed.' }
    
    
    & node --check webui/app.js
    if ($LASTEXITCODE -ne 0) { throw 'JavaScript syntax check failed.' }
    & node --test tests/editor.test.cjs tests/webui_find.test.cjs
    if ($LASTEXITCODE -ne 0) { throw 'Editor regression tests failed.' }
    & python main.py --version
    if ($LASTEXITCODE -ne 0) { throw 'Application startup check failed.' }
    Write-Host 'All checks passed.' -ForegroundColor Green
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    Pop-Location
    # Remove only what this run creates. `.testtmp` is shared: another session may
    # keep a live review workspace there (its own `reviewNNN/` folder), and wiping
    # the whole directory would delete data that is not ours to delete.
    foreach ($pattern in @('python', 'mdreader-smoke-*', 'mdreader-selftest')) {
        Get-ChildItem -LiteralPath $testTemp -Filter $pattern -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
}
