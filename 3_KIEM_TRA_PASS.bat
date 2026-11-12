@echo off
chcp 65001 >nul
cd /d "%~dp0"
title KIEM TRA PASS (ODX VALIDATE)
echo ===================================================================
echo               CONG CU KIEM TRA PASS CHUAN ADMIN (ODX VALIDATE)
echo ===================================================================
if "%~1"=="" (
    python odx_check_pass.py
) else (
    python odx_check_pass.py "%~1"
)
echo.
pause
