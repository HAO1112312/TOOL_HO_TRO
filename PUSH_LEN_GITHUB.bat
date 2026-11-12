@echo off
chcp 65001 >nul
cd /d "%~dp0"
title DONG BO CODE LEN GITHUB: HAO1112312/TOOL_HO_TRO
echo ===================================================================
echo [*] DANG DONG GOI VA DAY CODE LEN GITHUB...
echo ===================================================================

git add -A
git commit -m "Cap nhat Odx Auto-Pilot: %date% %time%"
git push origin main

echo ===================================================================
if %errorlevel% equ 0 (
    echo [v] DA DAY CODE VA FILE CAP NHAT LEN GITHUB THANH CONG!
    echo     Tat ca cac may Player o xa chi can bam [Cap Nhat Online] la xong.
) else (
    echo [!] CO LOI XAY RA KHI PUSH CODE LEN GITHUB.
)
echo ===================================================================
pause
