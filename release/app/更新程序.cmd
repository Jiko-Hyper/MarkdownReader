@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0更新程序.ps1" %*
