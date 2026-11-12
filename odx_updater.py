"""
odx_updater.py - Hệ thống Tự động Cập nhật Online cho Odx Auto-Pilot
Theo chuẩn Hướng 1: Kiểm tra phiên bản từ GitHub Raw / Releases, tải bản mới và tự động cập nhật an toàn.
"""

import os
import sys
import json
import time
import urllib.request
import subprocess
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(BASE_DIR, "version.json")
CONFIG_FILE = os.path.join(BASE_DIR, "autopilot_config.json")
TEMP_DIR = os.path.join(BASE_DIR, "_update_temp")

def get_current_version_info():
    """Doc thong tin phien ban hien tai tren may"""
    if os.path.exists(VERSION_FILE):
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "version": "3.1.0",
        "version_code": 310,
        "release_date": "2026-11-12",
        "update_url": "https://raw.githubusercontent.com/odx-autopilot/odx-tools/main/version.json",
        "download_url": ""
    }

def get_update_url():
    """Lay duong dan URL kiem tra cap nhat tu config hoac version.json"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                custom_url = cfg.get("update_json_url")
                if custom_url and custom_url.strip():
                    return custom_url.strip()
        except Exception:
            pass
    cur = get_current_version_info()
    return cur.get("update_url", "")

def parse_version_str(v_str):
    """Bien chuoi '3.1.0' thanh tuple (3, 1, 0) de so sanh"""
    try:
        clean = v_str.strip().lstrip("vV")
        parts = [int(p) for p in clean.split(".") if p.isdigit()]
        return tuple(parts)
    except Exception:
        return (0, 0, 0)

def check_for_updates(timeout=6):
    """
    Kiem tra tren mang xem co ban moi khong.
    Tra ve: (has_update: bool, remote_info: dict, error_msg: str)
    """
    url = get_update_url()
    if not url:
        return False, None, "Chưa cấu hình URL kiểm tra cập nhật."

    cur_info = get_current_version_info()
    cur_v_code = cur_info.get("version_code", 0)
    cur_v_tuple = parse_version_str(cur_info.get("version", "1.0.0"))

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "OdxAutoPilot-Updater/3.1 (Windows NT)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False, None, f"HTTP Error {resp.status}"
            content = resp.read().decode("utf-8")
            remote_info = json.loads(content)

            rem_v_code = remote_info.get("version_code", 0)
            rem_v_tuple = parse_version_str(remote_info.get("version", "1.0.0"))

            has_update = False
            if rem_v_code and cur_v_code:
                has_update = (rem_v_code > cur_v_code)
            else:
                has_update = (rem_v_tuple > cur_v_tuple)

            return has_update, remote_info, None

    except urllib.error.URLError as e:
        return False, None, f"Không thể kết nối máy chủ cập nhật: {e.reason}"
    except Exception as e:
        return False, None, f"Lỗi kiểm tra cập nhật: {e}"

def check_for_updates_async(callback):
    """Goi check_for_updates trong luong ngam de khong bi treo GUI"""
    def _worker():
        has_up, r_info, err = check_for_updates()
        if callback:
            callback(has_up, r_info, err)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t

def download_and_apply_update(download_url, on_progress=None):
    """
    Tai file ZIP tu download_url, ghi script update.bat va kich hoat de thay the file.
    """
    if not download_url:
        return False, "Thiếu đường dẫn tải file (download_url)."

    os.makedirs(TEMP_DIR, exist_ok=True)
    zip_target = os.path.join(TEMP_DIR, "update.zip")

    try:
        if on_progress:
            on_progress("Đang tải bản cập nhật...", 10)

        def _reporthook(block_num, block_size, total_size):
            if total_size > 0 and on_progress:
                downloaded = block_num * block_size
                percent = min(int((downloaded / total_size) * 85) + 10, 95)
                on_progress(f"Đang tải: {percent}% ({downloaded // 1024} KB / {total_size // 1024} KB)", percent)

        urllib.request.urlretrieve(download_url, zip_target, reporthook=_reporthook)

        if not os.path.exists(zip_target) or os.path.getsize(zip_target) < 1000:
            return False, "File tải về bị hỏng hoặc dung lượng không hợp lệ."

        if on_progress:
            on_progress("Đang chuẩn bị kịch bản cài đặt tự động...", 98)

        # Tao script apply_update.bat de tranh file lock tren Windows
        script_path = os.path.join(BASE_DIR, "apply_update.bat")
        bat_content = f"""@echo off
chcp 65001 >nul
title ODX AUTO-PILOT UPDATER
echo ===================================================================
echo [*] DANG CAP NHAT TOOL ODX AUTO-PILOT LEN BAN MOI NHAT...
echo     Vui long doi trong giay lat de Windows giai phong file.
echo ===================================================================
timeout /t 2 /nobreak >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "$tempDir = '{TEMP_DIR}'; $baseDir = '{BASE_DIR}'; $zipPath = '{zip_target}'; $extDir = Join-Path $tempDir 'extracted'; Expand-Archive -Path $zipPath -DestinationPath $extDir -Force; Get-ChildItem -Path $extDir -Recurse -File | ForEach-Object {{ $dest = Join-Path $baseDir $_.Name; if ($_.Name -ne 'autopilot_config.json' -or -not (Test-Path $dest)) {{ Copy-Item $_.FullName -Destination $dest -Force }} }}; if (Test-Path (Join-Path $baseDir '__pycache__')) {{ Remove-Item (Join-Path $baseDir '__pycache__') -Recurse -Force }}"

if exist "{TEMP_DIR}" rd /s /q "{TEMP_DIR}"

echo ===================================================================
echo [v] CAP NHAT HOAN TAT THANH CONG!
echo     Dang khoi dong lai Tool Odx Auto-Pilot...
echo ===================================================================
start "" "CHAY_TOOL_AUTO_PILOT.bat"
del "%~f0" >nul 2>&1
exit
"""
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(bat_content)

        if on_progress:
            on_progress("Khởi động trình cài đặt...", 100)

        # Chay apply_update.bat va thoat tool
        subprocess.Popen(["cmd.exe", "/c", script_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
        return True, None

    except Exception as e:
        return False, f"Lỗi trong quá trình cập nhật: {e}"

