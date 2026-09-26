@echo off
chcp 65001 >nul
setlocal
rem Process-only execution policy; no permanent system policy changes.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0安装到桌面.ps1" -NoPause %*
set "result=%errorlevel%"
echo.
if not "%result%"=="0" echo Installation failed. See the error above.
pause
exit /b %result%
