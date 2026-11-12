"""
odx_logger.py - Hệ thống Ghi nhật ký có cấu trúc (Structured Logging) & Xuất gói gỡ lỗi cho Odx Auto-Pilot
Theo chuẩn Hướng 3: Ghi file JSONL theo từng dòng sự kiện, đo lường thời gian trễ và xuất gói ZIP kiểm định.
"""

import os
import sys
import time
import json
import socket
import zipfile
import platform
import threading
from datetime import datetime

# Thu muc goc cua tool
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")

_log_lock = threading.Lock()

def _ensure_logs_dir():
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
    except Exception:
        pass

def get_today_log_path():
    _ensure_logs_dir()
    today_str = datetime.now().strftime("%Y%m%d")
    return os.path.join(LOGS_DIR, f"odx_events_{today_str}.jsonl")

def log_event(event: str, level: str = "INFO", source: str = "SYSTEM", data: dict = None, **kwargs):
    """
    Ghi một sự kiện có cấu trúc vào file JSONL trong logs/
    """
    payload = data.copy() if data else {}
    if kwargs:
        payload.update(kwargs)

    now = time.time()
    dt_str = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    entry = {
        "ts": round(now, 3),
        "time": dt_str,
        "level": str(level).upper(),
        "source": str(source).upper(),
        "event": str(event),
        "data": payload
    }

    try:
        line = json.dumps(entry, ensure_ascii=False)
        log_file = get_today_log_path()
        with _log_lock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        try:
            print(f"[LOGGER ERR] Không thể ghi log: {e}", file=sys.stderr)
        except Exception:
            pass

def collect_system_telemetry():
    """Thu thap thong so moi truong may, man hinh, OS de phuc vu debug"""
    info = {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    # Thong so man hinh tren Windows
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        info["screen_width"] = user32.GetSystemMetrics(0)
        info["screen_height"] = user32.GetSystemMetrics(1)
    except Exception:
        pass

    # Thong tin ban Odx Capture
    try:
        odx_json = os.path.join(BASE_DIR, "bin", "odx-capture.json")
        if not os.path.exists(odx_json):
            odx_json = os.path.join(BASE_DIR, "..", "bin", "odx-capture.json")
        if os.path.exists(odx_json):
            with open(odx_json, "r", encoding="utf-8") as f:
                data = json.load(f)
                info["odx_time_server"] = data.get("time_server")
                info["odx_orchestrator"] = data.get("orchestrator")
                info["odx_vendor_name"] = data.get("vendor_name")
    except Exception:
        pass

    return info

def export_debug_logs(dest_dir=None, player_id=None, player_name=None):
    """
    Gom toan bo log JSONL, file cau hinh va thong tin telemetry thanh file ZIP
    Tra ve duong dan toi file ZIP tao duoc.
    """
    _ensure_logs_dir()
    if not dest_dir:
        dest_dir = os.path.join(BASE_DIR, "export_logs")
    os.makedirs(dest_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    p_tag = f"ID{player_id}" if player_id else "DEVICE"
    zip_name = f"odx_debug_{timestamp}_{p_tag}.zip"
    zip_path = os.path.join(dest_dir, zip_name)

    telemetry = collect_system_telemetry()
    telemetry["player_id"] = player_id
    telemetry["player_name"] = player_name
    telemetry["export_time"] = timestamp

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Ghi telemetry.json
        zf.writestr("telemetry.json", json.dumps(telemetry, ensure_ascii=False, indent=2))

        # 2. Gom tat ca file .jsonl trong logs/
        if os.path.exists(LOGS_DIR):
            for fn in os.listdir(LOGS_DIR):
                if fn.endswith(".jsonl") or fn.endswith(".log"):
                    fp = os.path.join(LOGS_DIR, fn)
                    try:
                        zf.write(fp, arcname=f"logs/{fn}")
                    except Exception:
                        pass

        # 3. Gom file autopilot_config.json (an bo access_key neu co)
        cfg_path = os.path.join(BASE_DIR, "autopilot_config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg_data = json.load(f)
                zf.writestr("autopilot_config.json", json.dumps(cfg_data, ensure_ascii=False, indent=2))
            except Exception:
                pass

    log_event("EXPORT_LOGS", level="SUCCESS", source="SYSTEM", data={
        "zip_path": zip_path,
        "size_bytes": os.path.getsize(zip_path)
    })

    return zip_path

