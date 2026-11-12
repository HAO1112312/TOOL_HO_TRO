@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

title GIAI PHONG ODX NGAM - TRA LAI PHIM F10

:: 1. Kiem tra va Tu dong xin quyen Administrator
net session >nul 2>&1
if %errorlevel% == 0 goto :ADMIN_OK

echo ===================================================================
echo [*] DANG XIN QUYEN ADMINISTRATOR DE GIAI PHONG ODX VA F10...
echo     (Vui long bam [Yes] tren hop thoai Windows UAC de tiep tuc)
echo ===================================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:ADMIN_OK

echo ===================================================================
echo   DANG DON SACH TOAN BO TIEN TRINH ODX CHAY NGAM (GIAI PHONG F10)...
echo ===================================================================
taskkill /F /IM odx.exe /T >nul 2>&1
taskkill /F /IM odx-app.exe /T >nul 2>&1
taskkill /F /IM pktmon.exe /T >nul 2>&1
pktmon stop >nul 2>&1
pktmon reset >nul 2>&1

echo.
echo [THANH CONG] Da tat sach moi tien trinh Odx ngam!
echo [OK] Phim F10 va Odx Capture da duoc giai phong hoan toan.
echo Ban co the mo lai Odx Capture hoac mo lai Tool tuy y.
echo ===================================================================
echo Cua so nay se tu dong dong sau 3 giay...
timeout /t 3 >nul
exit /b

