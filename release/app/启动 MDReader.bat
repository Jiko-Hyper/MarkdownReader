@echo off
rem ============================================================
rem  MDReader 免安装版启动器
rem
rem  双击即可运行；把 .md 文件拖到本文件（或 exe）上也能直接打开。
rem  这里刻意只用 ASCII 字符：cmd.exe 按系统 OEM 代码页读 .bat，
rem  写进中文会让解析出错。
rem ============================================================
setlocal
cd /d "%~dp0"

set "MDEXE="
if exist "%~dp0MDReader.exe" set "MDEXE=%~dp0MDReader.exe"

if not defined MDEXE (
  echo.
  echo   [X] MDReader.exe not found in this folder.
  echo       Keep this .bat next to MDReader.exe, or run MDReader.exe directly.
  echo.
  pause
  exit /b 1
)

set "MDARGS="
:parse
if "%~1"=="" goto run
set "MDARGS=%MDARGS% --open "%~1""
shift
goto parse

:run
start "" "%MDEXE%"%MDARGS%
exit /b 0
