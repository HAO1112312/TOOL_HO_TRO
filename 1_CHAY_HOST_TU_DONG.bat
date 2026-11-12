@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

title ODX CAPTURE AUTO-PILOT [MAY CHU - HOST]

:: 1. Kiem tra va Tu dong xin quyen Administrator
net session >nul 2>&1
if %errorlevel% == 0 goto :ADMIN_OK

echo ===================================================================
echo [*] DANG XIN QUYEN ADMINISTRATOR CHO TOOL VA ODX CAPTURE...
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

if exist "C:\Users\Admin\AppData\Local\Microsoft\WindowsApps\python.exe" (
    set PY_BIN="C:\Users\Admin\AppData\Local\Microsoft\WindowsApps\python.exe"
    goto :PY_OK
)

echo ===================================================================
echo [*] LOI: MAY TINH CUA BAN CHUA CAI DAT PYTHON!
echo     De chay duoc bo Tool tu dong, ban can cai Python 3:
echo     1. Vao trang: https://www.python.org/downloads/
echo     2. Tai va chay bo cai dat Python 3.10 tro len.
echo     3. Tich chon vao o: 'Add python.exe to PATH' khi cai dat.
echo ===================================================================
pause
exit /b 1

:PY_OK

:: 3. Kiem tra thu muc Odx goc
if exist "Odx Capture.exe" goto :DIR_OK
if exist "..\Odx Capture.exe" goto :DIR_OK

echo ===================================================================
echo [*] LOI: BAN DANG MO TOOL O THU MUC KHONG CO ODX CAPTURE!
echo     Khong tim thay file 'Odx Capture.exe'.
echo     HUONG DAN KHAC PHUC:
echo     - Hay giai nen toan bo file trong 'Tool_Tu_Dong.zip'
echo     - BO THANG VAO THU MUC Odx Capture (noi co file 'Odx Capture.exe').
echo ===================================================================
pause
exit /b 1

:DIR_OK

echo ===================================================================
echo     ODX CAPTURE AUTO-PILOT: DIEU PHOI TU DONG [MAY CHU - HOST]
echo     [XAC NHAN] Quyen Administrator: DA KICH HOAT CHO CA TOOL VA ODX
echo     - Tu dong dong bo thoi gian NTP (khong can bam tay)
echo     - Random thoi luong chuan 4p +- 9s (232s - 247s)
echo     - OSD Overlay dem 3-2-1, HUD dem nguoc (tang hinh voi video)
echo     - Kiem tra armed: du 4 nguoi tich xanh moi bat dau
echo     - Phim F10/F9 bat dau (chi bam 1 lan khi bat dau), F11 huy khan cap, F8 nghi
echo     - Tu dong kiem tra day du 8/8 file va chay 'odx validate' sau moi task
echo ===================================================================
%PY_BIN% odx_gui.py --host
if %errorlevel% neq 0 (
    echo.
    echo [*] Tien trinh da ket thuc hoac bi dung boi nguoi dung.
)
pause
