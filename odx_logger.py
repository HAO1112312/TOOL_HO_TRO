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
import re
import threading
from collections.abc import Mapping
from datetime import datetime

from odx_runtime import resolve_odx_root

# Thu muc goc cua tool
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")

_log_lock = threading.Lock()
_log_state = threading.local()
_REDACTED = "[REDACTED]"


def _is_secret_key(key):
    name = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return (name in {"authorization", "cookie", "setcookie", "token", "jwt", "credentials"}
            or any(part in name for part in ("password", "passwd", "accesskey", "apikey", "privatekey", "clientsecret", "authtoken", "accesstoken", "refreshtoken"))
            or name.endswith("secret"))


def _redact_text(text, known_secrets=()):
    text = str(text)
    for secret in known_secrets:
        if secret:
            text = text.replace(secret, _REDACTED)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer " + _REDACTED, text)
    return re.sub(
        r"(?i)((?:access[_-]?key|api[_-]?key|password|passwd|client[_-]?secret|(?:auth|refresh|access)[_-]?token|secret|token|authorization)\s*[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;}]+)",
        lambda match: match.group(1) + _REDACTED, text)


def _redact(value, known_secrets=()):
    if isinstance(value, Mapping):
        return {str(key): _REDACTED if _is_secret_key(key) else _redact(item, known_secrets)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        result = []
        secret_next = False
        for item in value:
            result.append(_REDACTED if secret_next else _redact(item, known_secrets))
            secret_next = isinstance(item, str) and item.startswith("--") and _is_secret_key(item)
        return result
    if isinstance(value, str):
        return _redact_text(value, known_secrets)
    return value


def _secret_values(value):
    values = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _is_secret_key(key) and isinstance(item, str) and item:
                values.append(item)
            else:
                values.extend(_secret_values(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            values.extend(_secret_values(item))
    return values

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
    if getattr(_log_state, "active", False):
        return False
    _log_state.active = True
    try:
        payload = dict(data) if isinstance(data, Mapping) else ({"value": data} if data is not None else {})
        payload.update(kwargs)
        now = time.time()
        entry = {
            "ts": round(now, 3),
            "time": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "level": str(level).upper(),
            "source": str(source).upper(),
            "event": str(event),
            "data": payload
        }
        line = json.dumps(_redact(entry), ensure_ascii=False, default=str)
        log_file = get_today_log_path()
        with _log_lock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception as e:
        try:
            # GUI redirects sys.stderr into log_event. The original stream avoids
            # recursively trying to log the logger's own disk-write failure.
            if sys.__stderr__ is not None:
                sys.__stderr__.write(f"[LOGGER ERR] Cannot write log: {e}\n")
        except Exception:
            pass
        return False
    finally:
        _log_state.active = False

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
        odx_root = resolve_odx_root(BASE_DIR)
        info["odx_root"] = odx_root
        odx_json = os.path.join(odx_root, "bin", "odx-capture.json")
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

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(player_id or "")).strip("_")[:64]
    p_tag = f"ID{safe_id}" if safe_id else "DEVICE"
    zip_name = f"odx_debug_{timestamp}_{p_tag}.zip"
    zip_path = os.path.join(dest_dir, zip_name)

    telemetry = collect_system_telemetry()
    telemetry["player_id"] = player_id
    telemetry["player_name"] = player_name
    telemetry["export_time"] = timestamp

    cfg_data = None
    cfg_path = os.path.join(BASE_DIR, "autopilot_config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            cfg_data = json.load(f)
    except (OSError, ValueError):
        pass
    known_secrets = _secret_values(cfg_data)

    with zipfile.ZipFile(zip_path, "x", zipfile.ZIP_DEFLATED) as zf:
        # 1. Ghi telemetry.json
        zf.writestr("telemetry.json", json.dumps(_redact(telemetry, known_secrets), ensure_ascii=False, indent=2, default=str))

        # 2. Gom tat ca file .jsonl trong logs/
        if os.path.exists(LOGS_DIR):
            for fn in sorted(os.listdir(LOGS_DIR)):
                if fn.endswith(".jsonl") or fn.endswith(".log"):
                    fp = os.path.join(LOGS_DIR, fn)
                    try:
                        if not os.path.isfile(fp) or os.path.islink(fp):
                            continue
                        with open(fp, "r", encoding="utf-8", errors="replace") as source, zf.open(f"logs/{fn}", "w") as destination:
                            for line in source:
                                try:
                                    cleaned = json.dumps(_redact(json.loads(line), known_secrets), ensure_ascii=False, default=str) + "\n"
                                except ValueError:
                                    cleaned = _redact_text(line, known_secrets)
                                destination.write(cleaned.encode("utf-8"))
                    except Exception:
                        pass

        # 3. Gom file autopilot_config.json (an bo access_key neu co)
        if cfg_data is not None:
            zf.writestr("autopilot_config.json", json.dumps(_redact(cfg_data), ensure_ascii=False, indent=2, default=str))

    log_event("EXPORT_LOGS", level="SUCCESS", source="SYSTEM", data={
        "zip_path": zip_path,
        "size_bytes": os.path.getsize(zip_path)
    })

    return zip_path

