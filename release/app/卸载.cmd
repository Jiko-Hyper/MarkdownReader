@echo off
chcp 65001 >nul
setlocal DisableDelayedExpansion
rem Transfer to a temporary runner before removing this installation.
if defined MDREADER_UNINSTALL_SCRIPT goto run
set "MDREADER_UNINSTALL_SCRIPT=%~dp0卸载.ps1"
set "runner=%TEMP%\MDReader-uninstall-%RANDOM%-%RANDOM%.cmd"
copy /y "%~f0" "%runner%" >nul
if errorlevel 1 (
    echo Cannot prepare the uninstall runner.
    pause
    exit /b 1
)
"%runner%" %*
exit /b 1
:run
cd /d "%TEMP%"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%MDREADER_UNINSTALL_SCRIPT%" -NoPause %*
set "result=%errorlevel%"
if not "%result%"=="0" echo Uninstall failed. See the error above.
pause
exit /b %result%
