@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

title ODX CAPTURE AUTO-PILOT STUDIO v3.0

:: 1. Kiem tra va Tu dong xin quyen Administrator
net session >nul 2>&1
if %errorlevel% == 0 goto :ADMIN_OK

echo ===================================================================
echo [*] DANG XIN QUYEN ADMINISTRATOR CHO GIAO DIEN AUTO-PILOT...
echo     (Vui long bam [Yes] tren hop thoai Windows UAC de tiep tuc)
echo ===================================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:ADMIN_OK

:: Tu dong don sach moi tien trinh Odx chay ngam cu, reset pktmon cho may net
taskkill /F /IM odx.exe /T >nul 2>&1
taskkill /F /IM odx-app.exe /T >nul 2>&1
pktmon stop >nul 2>&1
pktmon reset >nul 2>&1

:: 2. Kiem tra Python tren he thong
set PY_BIN=python
where python >nul 2>&1
if %errorlevel% == 0 goto :PY_OK

where py >nul 2>&1
if %errorlevel% == 0 (
    set PY_BIN=py
    goto :PY_OK
)

if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" (
    set PY_BIN="%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe"
    goto :PY_OK
)

echo ===================================================================
echo [*] LOI: CHUA CAI DAT PYTHON!
echo ===================================================================
pause
exit /b 1

:PY_OK

%PY_BIN% odx_gui.py
pause

