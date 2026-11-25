"""Stage, verify and install updates while preserving user settings and captures."""
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(BASE_DIR, "version.json")
CONFIG_FILE = os.path.join(BASE_DIR, "autopilot_config.json")
TEMP_DIR = os.path.join(BASE_DIR, "_update_temp")
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_EXTRACT_BYTES = 2 * 1024 * 1024 * 1024
PRESERVED_NAMES = {"autopilot_config.json", "autopilot_progress.json", "autopilot_ui.json", "update_status.json", "odx-capture.json", "odx_capture_test_restore.json"}
PRESERVED_DIRS = {"data", "captures", "delivery", "logs", "_backups", "_update_temp",
                  ".git", ".codex", ".agents", "__pycache__"}
REQUIRED_FILES = {"odx_gui.py", "odx_backend.py", "odx_overlay.py", "odx_updater.py",
                  "odx_runtime.py", "odx_validation.py", "odx_instance.py", "odx_logger.py", "odx_uia.py", "odx_sync.py", "odx_window.py", "odx_hotkeys.py", "odx_timing.py", "odx_games.py",
                  "odx_check_pass.py", "odx_diagnostics.py", "odx_launch.ps1",
                  "CHAY_TOOL_AUTO_PILOT.bat", "version.json"}


def get_current_version_info():
    try:
        value = json.loads(Path(VERSION_FILE).read_text(encoding="utf-8-sig"))
        if isinstance(value, dict):
            return value
    except (OSError, ValueError):
        pass
    return {"version": "3.2.16", "version_code": 336, "update_url": "", "download_url": ""}


def get_update_url():
    try:
        cfg = json.loads(Path(CONFIG_FILE).read_text(encoding="utf-8-sig"))
        custom = cfg.get("update_json_url")
        if isinstance(custom, str) and custom.strip():
            return custom.strip()
    except (OSError, ValueError, AttributeError):
        pass
    return get_current_version_info().get("update_url", "")


def parse_version_str(value):
    match = re.fullmatch(r"[vV]?(\d+(?:\.\d+)*)(?:[-+][0-9A-Za-z.-]+)?", str(value).strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else (0, 0, 0)


def _validate_version_info(info):
    if not isinstance(info, dict) or parse_version_str(info.get("version", "")) == (0, 0, 0):
        raise ValueError("Thông tin phiên bản không hợp lệ.")
    code = info.get("version_code", 0)
    if isinstance(code, bool) or not isinstance(code, int) or code < 0:
        raise ValueError("version_code phải là số nguyên không âm.")
    return info


def check_for_updates(timeout=6):
    url = get_update_url()
    if not url:
        return False, None, "Chưa cấu hình URL kiểm tra cập nhật."
    try:
        if urllib.parse.urlparse(url).scheme != "https":
            raise ValueError("URL cập nhật phải sử dụng HTTPS.")
        request = urllib.request.Request(url, headers={"User-Agent": "OdxAutoPilot-Updater"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("Thông tin cập nhật vượt quá dung lượng cho phép.")
        remote = _validate_version_info(json.loads(content.decode("utf-8-sig")))
        current = get_current_version_info()
        old_code, new_code = current.get("version_code", 0), remote.get("version_code", 0)
        has_update = (new_code > old_code) if isinstance(old_code, int) and old_code and new_code else (
            parse_version_str(remote["version"]) > parse_version_str(current.get("version", "0")))
        return has_update, remote, None
    except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
        return False, None, f"Không thể kiểm tra cập nhật: {exc}"


def check_for_updates_async(callback):
    def worker():
        result = check_for_updates()
        if callback:
            callback(*result)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _safe_relative(name):
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (not normalized or normalized.startswith("/") or ":" in normalized or "\x00" in normalized
            or any(part in {"", ".", ".."} for part in normalized.rstrip("/").split("/"))):
        raise ValueError(f"Đường dẫn không hợp lệ trong ZIP: {name!r}")
    if any(part.endswith((".", " ")) for part in path.parts):
        raise ValueError(f"Tên file không hợp lệ trên Windows: {name!r}")
    for part in path.parts:
        if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise ValueError(f"Tên thiết bị Windows không được phép: {name!r}")
    return path


def _is_preserved(relative):
    parts = [part.lower() for part in relative.parts]
    return parts[-1] in PRESERVED_NAMES or any(part in PRESERVED_DIRS for part in parts[:-1])


def _stage_archive(zip_path, payload_dir, expected_version=None):
    """Validate every member before extraction; retain its directory hierarchy."""
    payload_dir = Path(payload_dir)
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > 10000 or sum(item.file_size for item in infos) > MAX_EXTRACT_BYTES:
            raise ValueError("Gói cập nhật vượt quá dung lượng hoặc số file cho phép.")
        members, seen = [], set()
        for info in infos:
            relative = _safe_relative(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                raise ValueError("ZIP không được chứa liên kết hoặc file mã hóa.")
            key = str(relative).casefold()
            if key in seen:
                raise ValueError(f"ZIP chứa đường dẫn trùng: {relative}")
            seen.add(key)
            if not info.is_dir():
                members.append((info, relative))
        roots = {relative.parts[0] for _, relative in members}
        strip_root = len(roots) == 1 and all(len(relative.parts) > 1 for _, relative in members)
        mapped = [(info, PurePosixPath(*relative.parts[1:]) if strip_root else relative)
                  for info, relative in members]
        if not REQUIRED_FILES.issubset({str(relative) for _, relative in mapped}):
            raise ValueError("Gói cập nhật thiếu file chương trình hoặc version.json.")
        version_member = next(info for info, relative in mapped if str(relative) == "version.json")
        if version_member.file_size > 1024 * 1024:
            raise ValueError("version.json quá lớn.")
        version = _validate_version_info(json.loads(archive.read(version_member).decode("utf-8-sig")))
        if expected_version and version["version"] != expected_version:
            raise ValueError("Phiên bản trong ZIP không khớp thông tin cập nhật.")
        payload_dir.mkdir(parents=True, exist_ok=False)
        for info, relative in mapped:
            if _is_preserved(relative):
                continue
            target = payload_dir.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            if target.suffix.lower() == ".py":
                ast.parse(target.read_text(encoding="utf-8-sig"), filename=str(relative))
    return version


def _download_archive(url, target, on_progress=None, expected_sha256=None):
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError("Đường dẫn tải cập nhật phải sử dụng HTTPS.")
    if expected_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("SHA-256 của gói cập nhật không hợp lệ.")
    digest, downloaded = hashlib.sha256(), 0
    request = urllib.request.Request(url, headers={"User-Agent": "OdxAutoPilot-Updater"})
    with urllib.request.urlopen(request, timeout=20) as response, Path(target).open("xb") as output:
        total = int(response.headers.get("Content-Length", 0))
        if total > MAX_DOWNLOAD_BYTES:
            raise ValueError("Gói tải về quá lớn.")
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            downloaded += len(chunk)
            if downloaded > MAX_DOWNLOAD_BYTES:
                raise ValueError("Gói tải về quá lớn.")
            output.write(chunk)
            digest.update(chunk)
            if on_progress:
                percent = min(85, int(downloaded / total * 85)) if total else 10
                on_progress(f"Đang tải: {downloaded // 1024} KB", percent)
    if total and downloaded != total:
        raise ValueError("Gói cập nhật tải chưa đầy đủ.")
    if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
        raise ValueError("SHA-256 không khớp; gói cập nhật đã bị từ chối.")


def download_and_apply_update(download_url, on_progress=None, expected_sha256=None, expected_version=None):
    if not download_url:
        return False, "Thiếu đường dẫn tải file (download_url)."
    stage = None
    try:
        Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="pending-", dir=TEMP_DIR))
        archive_path = stage / "update.zip"
        if on_progress:
            on_progress("Đang tải bản cập nhật...", 0)
        _download_archive(download_url, archive_path, on_progress, expected_sha256)
        version = _stage_archive(archive_path, stage / "payload", expected_version)
        if parse_version_str(version["version"]) <= parse_version_str(get_current_version_info().get("version", "0")):
            raise ValueError("Gói cập nhật không mới hơn phiên bản hiện tại.")
        worker = stage / "update_worker.py"
        shutil.copy2(Path(__file__).resolve(), worker)
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        subprocess.Popen([sys.executable, "-B", str(worker), "--apply-update", BASE_DIR,
                          str(stage), str(os.getpid())], cwd=BASE_DIR, close_fds=True,
                         creationflags=flags)
        if on_progress:
            on_progress("Đã xác minh gói; chờ đóng ứng dụng để cài đặt.", 100)
        return True, None
    except Exception as exc:
        if stage and stage.exists():
            shutil.rmtree(stage)
        return False, f"Không thể chuẩn bị cập nhật: {exc}"


def _contained(base, target):
    return Path(target).resolve().is_relative_to(Path(base).resolve())


def _install_payload(base_dir, stage_dir):
    """Apply a verified payload with atomic file replacement and rollback."""
    base, stage = Path(base_dir).resolve(), Path(stage_dir).resolve()
    if not _contained(base / "_update_temp", stage) or stage == base / "_update_temp":
        raise ValueError("Thư mục staging nằm ngoài vùng cập nhật.")
    payload, rollback = stage / "payload", stage / "rollback"
    names = {path.relative_to(payload).as_posix() for path in payload.rglob("*") if path.is_file()}
    if not REQUIRED_FILES.issubset(names):
        raise ValueError("Payload bị thiếu file bắt buộc.")
    changes, installed = [], []
    for source in sorted(payload.rglob("*")):
        if not source.is_file():
            continue
        relative = _safe_relative(source.relative_to(payload).as_posix())
        if _is_preserved(relative):
            continue
        destination = base.joinpath(*relative.parts)
        if source.is_symlink() or not _contained(payload, source) or not _contained(base, destination):
            raise ValueError("Phát hiện liên kết hoặc đường dẫn ngoài thư mục cài đặt.")
        if destination.exists() and not destination.is_file():
            raise ValueError(f"Đích cập nhật không phải file: {relative}")
        backup = rollback.joinpath(*relative.parts) if destination.exists() else None
        if backup:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
        changes.append((source, destination, backup))
    try:
        for source, destination, backup in changes:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".odx-update.tmp")
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            installed.append((destination, backup))
    except Exception as exc:
        rollback_errors = []
        for destination, backup in reversed(installed):
            try:
                if backup:
                    shutil.copy2(backup, destination)
                else:
                    destination.unlink(missing_ok=True)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            raise RuntimeError(f"Cập nhật lỗi: {exc}; khôi phục chưa đầy đủ: {rollback_errors}") from exc
        raise RuntimeError(f"Cập nhật lỗi, đã khôi phục phiên bản cũ: {exc}") from exc
    return len(installed)


def _wait_parent_exit(pid, timeout=60):
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 87
        try:
            return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.2)
    return False


def _write_status(base, state, message):
    target = Path(base) / "update_status.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"state": state, "message": message,
                                     "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def _apply_worker(base, stage, parent_pid):
    try:
        if not _wait_parent_exit(parent_pid):
            raise RuntimeError("Ứng dụng chưa đóng; không thay đổi file chương trình.")
        count = _install_payload(base, stage)
        _write_status(base, "installed", f"Đã cài đặt thành công {count} file.")
    except Exception as exc:
        _write_status(base, "failed", str(exc))
        return 1
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen([sys.executable, str(Path(base) / "odx_gui.py")], cwd=base,
                         close_fds=True, creationflags=flags)
    except Exception as exc:
        _write_status(base, "installed_restart_failed", f"Đã cài đặt; mở lại tool thủ công: {exc}")
        return 1
    if _contained(Path(base) / "_update_temp", stage):
        shutil.rmtree(stage, ignore_errors=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--apply-update":
        raise SystemExit(_apply_worker(sys.argv[2], sys.argv[3], int(sys.argv[4])))
    raise SystemExit("Updater chỉ được khởi chạy từ giao diện Odx Auto-Pilot.")
