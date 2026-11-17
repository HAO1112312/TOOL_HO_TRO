@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0odx_launch.ps1" --self-test
set "ODX_EXIT=%errorlevel%"
pause
exit /b %ODX_EXIT%
