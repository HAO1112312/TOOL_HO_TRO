"""
ODX CAPTURE AUTO-PILOT SUITE - BACKEND CORE ENGINE (v3.0)
- Kien truc Backend chat che, tach biet hoan toan logic xu ly va giao dien Frontend.
- Windows Job Object + SetConsoleCtrlHandler: Don dep dung cay tien trinh do tool khoi chay.
- Dong bo thoi gian NTP chuan xac (time.windows.com).
- Quan ly phong va theo doi 4 thanh vien real-time qua Orchestrator API.
- Kiem dinh dau ra chi doc; sua camera la thao tac rieng co luu ban goc.
- Dieu khien Host va Player dong bo tuyet doi.
"""

import sys
import os
import time
import json
import subprocess
import re
import random
import math
import ctypes
import threading
import ssl
import urllib.request
import base64
import hashlib
import tempfile
import queue
from contextlib import contextmanager, ExitStack
from odx_uia import ready_action_preamble
from datetime import datetime
from enum import Enum

# Win32 Constants
WM_HOTKEY = 0x0312
HOTKEY_ID = 1

# Orchestrator Constants
ORCHESTRATOR_URL = "https://13-251-211-134.sslip.io"
ACCESS_KEY = "ygYmQNO5WfHbaUn0w1hMVg4ESu8nTEhd"

TOOL_DIR = os.path.abspath(os.path.dirname(__file__))

import odx_runtime as _runtime
from odx_runtime import get_odx_file_version, register_managed_process, setup_process_protection

def find_all_odx_roots():
    return _runtime.find_all_odx_roots(TOOL_DIR)

def get_odx_root():
    return _runtime.resolve_odx_root(TOOL_DIR)

def get_odx_version_display():
    root = get_odx_root()
    app_exe = os.path.join(root, "bin", "odx-app.exe")
    cap_exe = os.path.join(root, "Odx Capture.exe")
    target = app_exe if os.path.exists(app_exe) else cap_exe
    if os.path.exists(target):
        v = get_odx_file_version(target)
        if v != (0, 0, 0, 0):
            return f"v{v[0]}.{v[1]}.{v[2]}"
    m = re.search(r"0\.\d+\.\d+", root)
    if m:
        return f"v{m.group(0)}"
    return "v0.3.5"

ODX_ROOT = get_odx_root()

try:
    from odx_logger import log_event, export_debug_logs
except Exception:
    def log_event(*args, **kwargs): pass
    def export_debug_logs(*args, **kwargs): return ""

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

# ======================================================================
# PROCESS MANAGEMENT & SYSTEM CLEANUP
# ======================================================================
def cleanup_all_odx(kill_gui=True, *, tool_closing=False):
    """Close managed Odx trees only as part of closing this tool."""
    return _runtime.cleanup_managed_processes(kill_gui=kill_gui, tool_closing=tool_closing)

# ======================================================================
# SYSTEM NTP SYNC & GAME CHECK
# ======================================================================
def sync_system_clock():
    """Tu dong dong bo thoi gian he thong voi NTP server (time.windows.com)."""
    odx_bin = os.path.join(ODX_ROOT, "bin", "odx.exe")
    if os.path.exists(odx_bin):
        try:
            res = subprocess.run([odx_bin, "clock-sync", "--server", "time.windows.com"], capture_output=True, text=True, timeout=8)
            if res.returncode == 0:
                return True, "Da dong bo NTP qua Odx Engine (time.windows.com)"
        except Exception:
            pass
    try:
        result = subprocess.run(["w32tm", "/resync"], capture_output=True, text=True, timeout=8)
        if result.returncode == 0:
            return True, "Da dong bo thoi gian Windows Time Service"
    except Exception:
        pass
    return False, "Khong the dong bo NTP tu dong (Can chay voi quyen Admin)"

def is_game_running(proc_name="ArmaReforgerSteam"):
    """Match actual executable names; a similarly titled window is insufficient."""
    clean_name = os.path.basename(str(proc_name or "").strip()).lower()
    if clean_name.endswith(".exe"):
        clean_name = clean_name[:-4]
    if not clean_name:
        return False
    accepted = {clean_name + ".exe"}
    if clean_name == "armareforgersteam":
        accepted.add("armareforger.exe")
    try:
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for function in (kernel.Process32FirstW, kernel.Process32NextW):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            function.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot and snapshot != ctypes.c_void_p(-1).value:
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(entry)
                if kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
                    while True:
                        if entry.szExeFile.strip().lower() in accepted:
                            return True
                        if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                            return False
                elif ctypes.get_last_error() == 18:  # ERROR_NO_MORE_FILES
                    return False
            finally:
                kernel.CloseHandle(snapshot)
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        pass

    # Native enumeration can be unavailable in a restricted Windows session.
    try:
        import csv
        result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            for row in csv.reader(result.stdout.splitlines()):
                if len(row) >= 2 and row[1].isdigit() and row[0].strip().lower() in accepted:
                    return True
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return False

# ======================================================================
# WINDOWS & HOTKEY HELPERS
# ======================================================================
def _window_process_path(hwnd):
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    pid = wintypes.DWORD()
    if not user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
        return None, None
    process = kernel.OpenProcess(0x1000, False, pid.value)
    if not process:
        return None, None
    try:
        size = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(size.value)
        if kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
            return pid.value, path.value
    finally:
        kernel.CloseHandle(process)
    return None, None


def _is_odx_executable(path):
    return bool(path) and os.path.basename(path).lower() in ("odx-app.exe", "odx capture.exe")


def _is_selected_odx_path(path):
    if not _is_odx_executable(path):
        return False
    try:
        root = os.path.normcase(os.path.realpath(ODX_ROOT))
        candidate = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([root, candidate]) == root
    except (OSError, ValueError):
        return False


def is_managed_odx_window(hwnd):
    try:
        pid, path = _window_process_path(hwnd)
        return _is_selected_odx_path(path) and _runtime.owns_pid(pid)
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        return False


def find_odx_window():
    """Find only an Odx window from the selected installation, with 64-bit handles."""
    try:
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user.FindWindowW.restype = wintypes.HWND
        candidate = user.FindWindowW(None, "Odx Capture")
        if candidate and _is_selected_odx_path(_window_process_path(candidate)[1]):
            return candidate
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.EnumWindows.restype = wintypes.BOOL
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetWindowTextW.restype = ctypes.c_int
        found = []

        def visit(hwnd, _):
            title = ctypes.create_unicode_buffer(256)
            if user.GetWindowTextW(hwnd, title, len(title)) and title.value == "Odx Capture":
                if _is_selected_odx_path(_window_process_path(hwnd)[1]):
                    found.append(hwnd)
                    return False
            return True

        user.EnumWindows(callback_type(visit), 0)
        return found[0] if found else 0
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        return 0


def find_unmanaged_odx_window():
    """Return any unowned Odx window, including one from another installation.

    A foreign Odx may own the global F10/F11 hotkeys even when its executable is
    outside our selected root or its window is hidden. A matching caption alone
    is insufficient: unrelated applications can also have an "Odx Capture" window.
    """
    try:
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.EnumWindows.restype = wintypes.BOOL
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetWindowTextW.restype = ctypes.c_int
        found = []

        def visit(hwnd, _):
            title = ctypes.create_unicode_buffer(256)
            if user.GetWindowTextW(hwnd, title, len(title)) and title.value == "Odx Capture":
                try:
                    pid, path = _window_process_path(hwnd)
                except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
                    return True
                if not pid or not _is_odx_executable(path):
                    return True
                try:
                    owned = _runtime.owns_pid(pid)
                except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
                    owned = False
                if not owned:
                    found.append(hwnd)
                    return False
            return True

        user.EnumWindows(callback_type(visit), 0)
        return found[0] if found else None
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        return None


def _ps_literal(value):
    """A PowerShell literal, including quotes, for arbitrary user text."""
    return "'" + str(value).replace("'", "''") + "'"

def _clipboard_apis():
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.OpenClipboard.restype = wintypes.BOOL
    user.CloseClipboard.argtypes = []
    user.CloseClipboard.restype = wintypes.BOOL
    user.EmptyClipboard.argtypes = []
    user.EmptyClipboard.restype = wintypes.BOOL
    user.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user.SetClipboardData.restype = ctypes.c_void_p
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = ctypes.c_void_p
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = ctypes.c_void_p
    kernel.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel.GlobalUnlock.restype = wintypes.BOOL
    kernel.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel.GlobalFree.restype = ctypes.c_void_p
    kernel.GetConsoleWindow.argtypes = []
    kernel.GetConsoleWindow.restype = wintypes.HWND
    return user, kernel


def set_clipboard(text):
    """Transfer Unicode text only after allocation, locking and Win32 ownership succeed."""
    text = str(text)
    memory = None
    kernel = None
    transferred = False
    try:
        user, kernel = _clipboard_apis()
        owner = kernel.GetConsoleWindow()
        if not owner:
            raise OSError("No native clipboard owner window; use PowerShell fallback")
        raw = (text + "\0").encode("utf-16le")
        memory = kernel.GlobalAlloc(0x0042, len(raw))
        if memory:
            pointer = kernel.GlobalLock(memory)
            if pointer:
                try:
                    ctypes.memmove(pointer, raw, len(raw))
                finally:
                    kernel.GlobalUnlock(memory)
                for _ in range(5):
                    if user.OpenClipboard(owner):
                        try:
                            if user.EmptyClipboard() and user.SetClipboardData(13, memory):
                                transferred = True
                                return True
                        finally:
                            user.CloseClipboard()
                        break
                    time.sleep(0.01)
    except Exception:
        pass
    finally:
        if memory and not transferred and kernel is not None:
            try:
                kernel.GlobalFree(memory)
            except Exception:
                pass

    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value " + _ps_literal(text)], capture_output=True, timeout=2)
        return result.returncode == 0
    except Exception:
        return False


def get_clipboard():
    """Read Unicode text, always releasing both the global lock and clipboard."""
    try:
        user, kernel = _clipboard_apis()
        for _ in range(5):
            if user.OpenClipboard(None):
                try:
                    handle = user.GetClipboardData(13)
                    if handle:
                        pointer = kernel.GlobalLock(handle)
                        if pointer:
                            try:
                                return ctypes.wstring_at(pointer)
                            finally:
                                kernel.GlobalUnlock(handle)
                finally:
                    user.CloseClipboard()
            time.sleep(0.01)
    except Exception:
        pass
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0 and result.stdout:
            return result.stdout.rstrip("\r\n")
    except Exception:
        pass
    return ""

def _owned_uia_context(timeout):
    """Wait briefly for our newly launched window, never adopt a foreign one."""
    deadline = time.monotonic() + max(0, timeout)
    while True:
        if find_unmanaged_odx_window():
            return None
        hwnd = find_odx_window()
        if hwnd and is_managed_odx_window(hwnd):
            pid, path = _window_process_path(hwnd)
            # Resolve again after checking ownership to catch a replaced window.
            if _is_selected_odx_path(path) and _runtime.owns_pid(pid):
                return int(hwnd), pid
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def _uia_script_prefix(hwnd, pid):
    """Definitions referenced by the static templates, before user text is added.

    Only the captured main HWND is used. Desktop popup searches are constrained
    to its PID; no title-only selection or process-name fallback is permitted.
    """
    return """$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$script:OdxTargetPid = __ODX_PID__
$script:OdxTargetHwnd = [IntPtr][Int64]__ODX_HWND__
$script:OdxProcessCondition = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$script:OdxTargetPid)
function Get-OwnedOdxMainWindow {
    try {
        $owned = [System.Windows.Automation.AutomationElement]::FromHandle($script:OdxTargetHwnd)
        if ($owned -and $owned.Current.ProcessId -eq $script:OdxTargetPid -and
            $owned.Current.Name -ceq 'Odx Capture') {
            return $owned
        }
    } catch { }
    return $null
}
if (-not (Get-OwnedOdxMainWindow)) {
    Write-Output 'ODX_WINDOW_NOT_OWNED'
    exit 1
}
""".replace("__ODX_PID__", str(int(pid))).replace("__ODX_HWND__", str(int(hwnd)))


def run_ps_script(ps_code, timeout=5):
    """Run scoped UI automation as a managed child, with bounded lifetime."""
    process = None
    try:
        deadline = time.monotonic() + timeout
        context = _owned_uia_context(timeout)
        if context is None:
            return None
        code = _uia_script_prefix(*context) + ps_code
        # Keep the command line short: native accessibility definitions can
        # exceed Windows' 32K command-line limit once base64 encoded.
        bootstrap = ("[Console]::InputEncoding=[Text.UTF8Encoding]::new($false); "
                     "$source=[Console]::In.ReadToEnd().TrimStart([char]0xFEFF); "
                     "& ([ScriptBlock]::Create($source))")
        encoded = base64.b64encode(bootstrap.encode("utf-16le")).decode("ascii")
        command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8-sig", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        register_managed_process(process)
        stdout, stderr = process.communicate(input=code, timeout=max(0.01, deadline - time.monotonic()))
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except Exception:
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
        return None


# These choices belong to the exact Odx window that this tool configured. Never
# carry a restore request into a different native process/window.
_capture_test_mode_lock = threading.Lock()
_capture_test_snapshots = {}
_desktop_source_names = ("Màn hình chính", "Primary monitor")
_raw_profile_names = ("Không có (chỉ giữ dữ liệu thô)", "None (keep raw capture only)")


def _capture_test_config_paths():
    native = os.path.expandvars(r"%APPDATA%\Odx\odx-app.json")
    snapshot = os.path.join(TOOL_DIR, "odx_capture_test_restore.json")
    identity = hashlib.sha256((os.path.normcase(os.path.abspath(native)) + "\0" +
                               os.environ.get("COMPUTERNAME", "")).encode("utf-8")).hexdigest()
    return native, snapshot, identity


def _write_capture_test_json(path, value):
    """Replace one JSON file atomically; never store the native config backup."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".odx-capture-test-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def prepare_capture_mode_launch(enabled):
    """Prepare exactly two native capture settings before launching owned Odx.

    Returns ``(success, error)``. The small restore file contains only the prior
    WindowProcess/GameProfile fields and a machine identity, never credentials.
    A normal launch restores only fields still set to TEST's null value, keeping
    any later native/user changes. Restoration survives tool/native restarts.
    """
    with _capture_test_mode_lock:
        try:
            native_path, snapshot_path, identity = _capture_test_config_paths()
            snapshot = None
            if os.path.exists(snapshot_path):
                with open(snapshot_path, encoding="utf-8") as stream:
                    candidate = json.load(stream)
                if (not isinstance(candidate, dict) or candidate.get("version") != 1 or
                        not isinstance(candidate.get("identity"), str) or not isinstance(candidate.get("original"), dict) or
                        any(key not in ("WindowProcess", "GameProfile") or (value is not None and not isinstance(value, str))
                            for key, value in candidate["original"].items())):
                    raise ValueError("Invalid capture restore metadata")
                if candidate["identity"] == identity:
                    snapshot = candidate
            if not enabled and snapshot is None:
                return True, ""
            # A running Odx can overwrite these persisted settings on close.
            # This API deliberately cannot change any live room or recorder.
            if find_odx_window() or find_unmanaged_odx_window():
                return False, "Hãy đóng Odx do tool mở trước khi chuẩn bị cấu hình quay."
            config = {}
            if os.path.exists(native_path):
                with open(native_path, encoding="utf-8-sig") as stream:
                    config = json.load(stream)
            if not isinstance(config, dict):
                raise ValueError("Invalid native config")
            keys = ("WindowProcess", "GameProfile")
            if any(config.get(key) is not None and not isinstance(config[key], str) for key in keys):
                raise ValueError("Invalid capture settings")
            if enabled:
                original = dict(snapshot["original"]) if snapshot is not None else {}
                for key in keys:
                    if snapshot is None or config.get(key) is not None:
                        if key in config:
                            original[key] = config[key]
                        else:
                            original.pop(key, None)
                # Write the two-field backup before modifying the native file.
                _write_capture_test_json(snapshot_path, {"version": 1, "identity": identity, "original": original})
                for key in keys:
                    config[key] = None
                _write_capture_test_json(native_path, config)
            else:
                for key in keys:
                    if key in config and config[key] is None:
                        if key in snapshot["original"]:
                            config[key] = snapshot["original"][key]
                        else:
                            config.pop(key, None)
                _write_capture_test_json(native_path, config)
                os.unlink(snapshot_path)
                _capture_test_snapshots.clear()
            return True, ""
        except (OSError, ValueError, TypeError):
            # Do not include native config contents or credentials in errors.
            return False, "Không đọc/ghi được cấu hình quay test hoặc bản khôi phục; chưa mở Odx."


def _capture_test_selection_script(context, action, original=None, applied=None, expected=None):
    """Configure startup selectors through UIA, without clicks or key input.

    Native LoadSources/LoadGames put the null-process/null-profile items first;
    their exact VI/EN labels identify those choices without depending on the
    user's installed game profiles or their order.
    """
    payload = {
        "hwnd": int(context[0]), "pid": int(context[1]), "action": action,
        "original": original, "applied": applied, "expected": expected,
        "source_labels": _desktop_source_names, "game_labels": _raw_profile_names,
    }
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return r"""
$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__REQUEST__')) | ConvertFrom-Json
if ($script:OdxTargetHwnd.ToInt64() -ne [Int64]$request.hwnd -or
    $script:OdxTargetPid -ne [int]$request.pid) { throw 'ODX_WINDOW_CHANGED' }
$win = Get-OwnedOdxMainWindow
if (-not $win) { throw 'ODX_WINDOW_NOT_OWNED' }
function Get-CaptureCombo([string]$id) {
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, $id)
    try { $box = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition) }
    catch { throw 'CAPTURE_UI_LOADING' }
    if (-not $box -or $box.Current.ControlType -ne [System.Windows.Automation.ControlType]::ComboBox) {
        throw "CAPTURE_COMBO_MISSING: $id"
    }
    return $box
}
function Get-CaptureChoice($box) {
    try { $selected = @($box.GetCurrentPattern([System.Windows.Automation.SelectionPattern]::Pattern).Current.GetSelection()) }
    catch { throw 'CAPTURE_UI_LOADING' }
    if ($selected.Count -ne 1 -or -not $selected[0].Current.Name) { throw 'CAPTURE_SELECTION_UNKNOWN' }
    return [string]$selected[0].Current.Name
}
function Find-CaptureChoice($box, [string]$name) {
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, $name)
    $matches = @($box.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition) |
        Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::ListItem })
    # ItemContainerPattern also identifies virtualized, collapsed items.
    if ($matches.Count -eq 0) {
        try {
            $container = $box.GetCurrentPattern([System.Windows.Automation.ItemContainerPattern]::Pattern)
            $item = $container.FindItemByProperty($null,
                [System.Windows.Automation.AutomationElement]::NameProperty, $name)
            if ($item) {
                $matches = @($item)
                $next = $container.FindItemByProperty($item,
                    [System.Windows.Automation.AutomationElement]::NameProperty, $name)
                if ($next) { $matches += $next }
            }
        } catch { }
    }
    if ($matches.Count -eq 0) { throw 'CAPTURE_TEST_CHOICES_UNAVAILABLE' }
    if ($matches.Count -ne 1) { throw 'CAPTURE_CHOICE_NOT_UNIQUE' }
    return $matches[0]
}
function Set-CaptureChoice($box, $item, [string]$name) {
    if ((Get-CaptureChoice $box) -ceq $name) { return }
    if (-not $box.Current.IsEnabled) { throw 'CAPTURE_COMBO_DISABLED' }
    $expand = $box.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
    $wasCollapsed = $expand.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Collapsed
    try {
        try {
            $item.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
        } catch {
            # Collapsed WPF ComboBox items are virtualized. Realize supplies
            # their native item provider; WPF may briefly open the dropdown.
            $item.GetCurrentPattern([System.Windows.Automation.VirtualizedItemPattern]::Pattern).Realize()
            $item = Find-CaptureChoice $box $name
            $item.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
        }
        for ($attempt=0; $attempt -lt 15; $attempt++) {
            if ((Get-CaptureChoice $box) -ceq $name) { return }
            Start-Sleep -Milliseconds 40
        }
        throw "CAPTURE_READBACK_FAILED: $name"
    } finally {
        if ($wasCollapsed -and $expand.Current.ExpandCollapseState -ne [System.Windows.Automation.ExpandCollapseState]::Collapsed) {
            $expand.Collapse()
        }
    }
}
function Write-CaptureResult($result) {
    $result.hwnd = $script:OdxTargetHwnd.ToInt64()
    $result.pid = $script:OdxTargetPid
    Write-Output ('CAPTURE_TEST_RESULT:' + ($result | ConvertTo-Json -Compress -Depth 5))
}
trap {
    $reason = 'CAPTURE_UI_ERROR'
    if ($_.Exception.Message -match '\b(CAPTURE_[A-Z_]+|ODX_WINDOW_[A-Z_]+)\b') { $reason=$Matches[1] }
    Write-CaptureResult @{success=$false;error=$reason}
    exit 1
}
$sourceBox = Get-CaptureCombo 'SourceBox'
$gameBox = Get-CaptureCombo 'GameBox'
$before = @{source=(Get-CaptureChoice $sourceBox); game=(Get-CaptureChoice $gameBox)}
if ($request.action -ceq 'inspect') {
    $sourceTarget = $null; $gameTarget = $null
    foreach ($name in $request.source_labels) {
        try { $null = Find-CaptureChoice $sourceBox $name; $sourceTarget=$name; break }
        catch { if ($_.Exception.Message -notmatch 'CAPTURE_TEST_CHOICES_UNAVAILABLE') { throw } }
    }
    foreach ($name in $request.game_labels) {
        try { $null = Find-CaptureChoice $gameBox $name; $gameTarget=$name; break }
        catch { if ($_.Exception.Message -notmatch 'CAPTURE_TEST_CHOICES_UNAVAILABLE') { throw } }
    }
    if (-not $sourceTarget -or -not $gameTarget) { throw 'CAPTURE_TEST_CHOICES_UNAVAILABLE' }
    Write-CaptureResult @{success=$true; selection=$before; target=@{source=$sourceTarget;game=$gameTarget}}
    exit 0
}
if ($request.action -ceq 'restore') {
    $sourceName = if ($before.source -ceq $request.applied.source) {$request.original.source} else {$before.source}
    $gameName = if ($before.game -ceq $request.applied.game) {$request.original.game} else {$before.game}
    $preserved = $before.source -cne $request.applied.source -or $before.game -cne $request.applied.game
} elseif ($request.action -ceq 'apply') {
    if ($before.source -cne $request.expected.source -or $before.game -cne $request.expected.game) {
        throw 'CAPTURE_SELECTION_CHANGED'
    }
    $sourceName=$request.applied.source; $gameName=$request.applied.game; $preserved=$false
} else { throw 'CAPTURE_ACTION_INVALID' }
if ($sourceName -ceq $before.source -and $gameName -ceq $before.game) {
    Write-CaptureResult @{success=$true;selection=$before;changed=$false;preserved=$preserved}
    exit 0
}
# Resolve every choice, including rollback items, before changing either box.
$sourceItem = Find-CaptureChoice $sourceBox $sourceName
$gameItem = Find-CaptureChoice $gameBox $gameName
$oldSourceItem = Find-CaptureChoice $sourceBox $before.source
$oldGameItem = Find-CaptureChoice $gameBox $before.game
try {
    # Selecting a game can automatically change SourceBox. Apply source last.
    Set-CaptureChoice $gameBox $gameItem $gameName
    Set-CaptureChoice $sourceBox $sourceItem $sourceName
    $actual = @{source=(Get-CaptureChoice $sourceBox);game=(Get-CaptureChoice $gameBox)}
    if ($actual.source -cne $sourceName -or $actual.game -cne $gameName) { throw 'CAPTURE_READBACK_FAILED' }
    Write-CaptureResult @{success=$true;selection=$actual;changed=$true;preserved=$preserved}
    exit 0
} catch {
    $failure = $_.Exception.Message
    $rolledBack = $false
    try {
        Set-CaptureChoice $gameBox $oldGameItem $before.game
        Set-CaptureChoice $sourceBox $oldSourceItem $before.source
        $rolledBack = ((Get-CaptureChoice $sourceBox) -ceq $before.source -and
                       (Get-CaptureChoice $gameBox) -ceq $before.game)
    } catch { }
    Write-CaptureResult @{success=$false;error=$failure;rolled_back=$rolledBack}
    exit 1
}
""".replace("__REQUEST__", encoded)


class _CaptureConfigurationError(RuntimeError):
    """A bounded, safe UIA reason; never forward raw PowerShell stderr."""
    _loading = frozenset(("CAPTURE_COMBO_MISSING", "CAPTURE_SELECTION_UNKNOWN",
                          "CAPTURE_TEST_CHOICES_UNAVAILABLE", "CAPTURE_UI_LOADING", "CAPTURE_UI_TIMEOUT"))
    _messages = {
        "CAPTURE_COMBO_MISSING": "Odx đang tải bộ chọn nguồn quay hoặc hồ sơ game.",
        "CAPTURE_SELECTION_UNKNOWN": "Odx chưa tải xong lựa chọn nguồn quay và hồ sơ game.",
        "CAPTURE_TEST_CHOICES_UNAVAILABLE": "Odx chưa tải xong danh sách nguồn quay và hồ sơ game.",
        "CAPTURE_UI_LOADING": "Giao diện Odx đang khởi tạo.",
        "CAPTURE_UI_TIMEOUT": "Giao diện Odx chưa phản hồi trong thời gian chờ.",
        "CAPTURE_CHOICE_NOT_UNIQUE": "Nhãn nguồn quay hoặc hồ sơ game bị trùng; chưa thay đổi cấu hình.",
        "CAPTURE_COMBO_DISABLED": "Odx đang khóa bộ chọn nguồn quay; hãy kết thúc phiên trước khi đổi chế độ.",
        "CAPTURE_SELECTION_CHANGED": "Lựa chọn nguồn quay thay đổi trong lúc chuẩn bị; chưa tạo phòng.",
        "CAPTURE_READBACK_FAILED": "Odx chưa xác nhận nguồn quay vừa chọn.",
        "CAPTURE_ACTION_INVALID": "Yêu cầu cấu hình nguồn quay không hợp lệ.",
        "ODX_WINDOW_CHANGED": "Cửa sổ Odx đã thay đổi; chưa áp dụng chế độ test.",
        "ODX_WINDOW_NOT_OWNED": "Không truy cập được đúng cửa sổ Odx do tool mở.",
        "CAPTURE_RESPONSE_MISSING": "Odx chưa trả xác nhận cấu hình nguồn quay.",
        "CAPTURE_RESPONSE_INVALID": "Xác nhận cấu hình nguồn quay của Odx không hợp lệ.",
        "CAPTURE_UI_ERROR": "Không đọc được cấu hình nguồn quay trên giao diện Odx.",
    }

    def __init__(self, code):
        self.code = code if code in self._messages else "CAPTURE_UI_ERROR"
        self.retryable = self.code in self._loading
        super().__init__(f"{self._messages[self.code]} [{self.code}]")


def _capture_test_result(context, action, timeout, **kwargs):
    result = run_ps_script(_capture_test_selection_script(context, action, **kwargs), timeout=timeout)
    if result is None:
        raise _CaptureConfigurationError("CAPTURE_UI_TIMEOUT")
    for line in (result.stdout or "").splitlines():
        if not line.startswith("CAPTURE_TEST_RESULT:"):
            continue
        try:
            data = json.loads(line.partition(":")[2])
        except (TypeError, ValueError):
            raise _CaptureConfigurationError("CAPTURE_RESPONSE_INVALID") from None
        if not isinstance(data, dict):
            raise _CaptureConfigurationError("CAPTURE_RESPONSE_INVALID")
        if data.get("hwnd") != context[0] or data.get("pid") != context[1]:
            raise _CaptureConfigurationError("ODX_WINDOW_CHANGED")
        if result.returncode != 0 or data.get("success") is not True:
            reason = re.search(r"\b(CAPTURE_[A-Z_]+|ODX_WINDOW_[A-Z_]+)\b", str(data.get("error") or ""))
            raise _CaptureConfigurationError(reason[0] if reason else "CAPTURE_UI_ERROR")
        return data
    # Prefix ownership checks and older providers can fail before the JSON
    # trap. Report only recognized codes, never arbitrary stderr or UI values.
    reason = re.search(r"\b(CAPTURE_[A-Z_]+|ODX_WINDOW_[A-Z_]+)\b",
                       (result.stdout or "") + "\n" + (result.stderr or ""))
    raise _CaptureConfigurationError(reason[0] if reason else "CAPTURE_RESPONSE_MISSING")


def set_capture_test_mode(enabled, timeout=12, cancel_event=None, on_status=None):
    """Configure owned Odx for desktop tests, or restore this session's choices.

    The worker's ``success`` is true only after exact native UI readback. A
    normal-mode call with no test snapshot never changes the user's Odx choices.
    """
    def configure():
        with _capture_test_mode_lock:
            try:
                def current():
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("Đã hủy chuẩn bị nguồn quay Odx.")

                current()
                if not enabled and not _capture_test_snapshots:
                    worker.success = True
                    worker.status = "unchanged"
                    return
                deadline = time.monotonic() + max(1, float(timeout))
                context = _owned_uia_context(min(1, max(0, float(timeout))))
                if context is None:
                    raise RuntimeError("Hãy mở Odx bằng tool trước khi cấu hình test màn hình.")
                context = tuple(context)
                # Old windows cannot be restored through a new HWND/PID.
                for old in list(_capture_test_snapshots):
                    if old != context:
                        del _capture_test_snapshots[old]
                snapshot = _capture_test_snapshots.get(context)

                def request(action, **kwargs):
                    current()
                    if _owned_uia_context(0) != context:
                        raise _CaptureConfigurationError("ODX_WINDOW_CHANGED")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("Hết thời gian xác nhận cấu hình Odx.")
                    return _capture_test_result(context, action, min(3, remaining), **kwargs)

                if enabled:
                    state, stable, last_error, announced = None, None, None, False
                    while time.monotonic() < deadline:
                        current()
                        try:
                            state = request("inspect")
                            signature = (state.get("selection"), state.get("target"))
                            if signature == stable:
                                break
                            stable = signature
                        except _CaptureConfigurationError as exc:
                            if not exc.retryable:
                                raise
                            stable, state, last_error = None, None, exc
                        if not announced and on_status is not None:
                            on_status("Đang chờ Odx tải xong nguồn quay và hồ sơ game...", "WAIT")
                            announced = True
                        delay = min(.2, max(0, deadline - time.monotonic()))
                        if cancel_event is not None:
                            cancel_event.wait(delay)
                        else:
                            time.sleep(delay)
                    else:
                        detail = f" {last_error}" if last_error else ""
                        raise RuntimeError("Hết thời gian chờ Odx tải xong nguồn quay và hồ sơ game." + detail)
                    current()
                    before, applied = state.get("selection"), state.get("target")
                    if (not isinstance(before, dict) or not isinstance(applied, dict) or
                            not all(isinstance(before.get(key), str) and before[key] for key in ("source", "game")) or
                            applied.get("source") not in _desktop_source_names or applied.get("game") not in _raw_profile_names):
                        raise RuntimeError("Nguồn quay test của Odx không được nhận diện chính xác.")
                    if snapshot is None and before != applied:
                        snapshot = {"original": dict(before), "applied": dict(applied)}
                        # Retain the original before any mutation, even if the
                        # PowerShell child times out during a partial change.
                        _capture_test_snapshots[context] = snapshot
                    result = request("apply", applied=applied, expected=before)
                    if result.get("selection") != applied:
                        raise RuntimeError("Odx chưa chọn đúng màn hình chính và hồ sơ thô.")
                    if snapshot is not None:
                        snapshot["applied"] = dict(applied)
                    worker.changed = bool(result.get("changed"))
                    worker.status = "configured"
                elif snapshot is not None:
                    result = request("restore", original=snapshot["original"], applied=snapshot["applied"])
                    worker.changed = bool(result.get("changed"))
                    worker.restored = worker.changed
                    worker.preserved_user_choice = bool(result.get("preserved"))
                    del _capture_test_snapshots[context]
                    worker.status = "restored" if worker.restored else "unchanged"
                else:
                    worker.status = "unchanged"
                worker.success = True
            except Exception as exc:
                worker.error = str(exc)
                worker.status = "failed"

    worker = threading.Thread(target=configure, daemon=True)
    worker.success = False
    worker.error = ""
    worker.status = "pending"
    worker.changed = False
    worker.restored = False
    worker.preserved_user_choice = False
    worker.start()
    return worker


def configure_desktop_test_capture(timeout=12):
    """Compatibility helper for callers enabling the desktop test source."""
    return set_capture_test_mode(True, timeout=timeout)

def _task_capture_roots():
    return [
        os.path.join(ODX_ROOT, "data", "captures"),
        os.path.join(os.path.dirname(ODX_ROOT), "captures"),
        os.path.join(ODX_ROOT, "captures"),
    ]


def _task_capture_entries(parent):
    """Return a snapshot, or None if this root cannot be inspected safely."""
    try:
        with os.scandir(parent) as entries:
            return [(entry.name, entry.path) for entry in entries]
    except FileNotFoundError:
        # A broken link is an obstruction, not a safe missing capture root.
        return None if os.path.lexists(parent) else []
    except OSError:
        return None


def is_task_already_recorded(task_id):
    """Reserve existing task names, including empty folders and cancelled takes.

    A failed directory inspection also blocks capture, rather than treating an
    unknown result as permission to overwrite. This function never changes data.
    """
    if not task_id:
        return False, ""
    clean_id = str(task_id).strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,128}", clean_id):
        return True, ""
    for parent in _task_capture_roots():
        entries = _task_capture_entries(parent)
        if entries is None:
            return True, parent
        for name, path in entries:
            low_name = name.lower()
            if low_name == clean_id or low_name.startswith(clean_id + ".cancelled"):
                return True, path
    return False, ""

def get_next_available_fix_task_id(task_id):
    """Find a genuinely unused FIX name; never return an unchecked fallback."""
    clean_id = str(task_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", clean_id):
        raise ValueError("Ma task khong hop le; chua the chon FIX.")
    base_id = re.sub(r"-fix\d+$", "", clean_id, flags=re.IGNORECASE)
    reserved = set()
    for parent in _task_capture_roots():
        entries = _task_capture_entries(parent)
        if entries is None:
            raise RuntimeError(f"Khong doc duoc thu muc captures; chua the chon FIX: {parent}")
        for name, _ in entries:
            low_name = name.lower()
            reserved.add(low_name)
            if ".cancelled" in low_name:
                reserved.add(low_name.split(".cancelled", 1)[0])
    for counter in range(1, 10001):
        candidate = f"{base_id}-fix{counter}"
        if len(candidate) > 128:
            raise ValueError("Ma task qua dai de them hau to FIX (toi da 128 ky tu).")
        if candidate.lower() not in reserved:
            return candidate
    raise RuntimeError("Khong con ma FIX trong 10000 lan thu; khong ghi de task cu.")

_task_request_lock = threading.Lock()
_task_injection_lock = threading.Lock()
_task_request_generation = 0


def set_task_box(task_id):
    """Cap nhat task_id vao file config, clipboard va tu dong dien vao TaskBox tren Odx Capture."""
    global _task_request_generation
    with _task_request_lock:
        _task_request_generation += 1
        generation = _task_request_generation
    # 1. Cap nhat file config odx-app.json
    appdata_json = os.path.expandvars(r"%APPDATA%\Odx\odx-app.json")
    try:
        data = {}
        if os.path.exists(appdata_json):
            with open(appdata_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["Task"] = str(task_id)
        os.makedirs(os.path.dirname(appdata_json), exist_ok=True)
        with open(appdata_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

    # 2. Clipboard nhanh 0.1ms
    set_clipboard(task_id)

    # 3. UIAutomation qua EncodedCommand (khong bi loi parse bien, chay trong thread ngam)
    def _inject_uia():
        ps_template = """
        Add-Type -AssemblyName UIAutomationClient
        Add-Type -AssemblyName UIAutomationTypes
        Add-Type -AssemblyName System.Windows.Forms
        $taskValue = '__TASK_ID__'

        $root = [System.Windows.Automation.AutomationElement]::RootElement
        $win = $null

        $cName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'Odx Capture')
        $win = Get-OwnedOdxMainWindow

        if (-not $win) {
            $procs = Get-Process -Id $script:OdxTargetPid -ErrorAction SilentlyContinue
            foreach ($p in $procs) {
                if ($p.MainWindowHandle -eq $script:OdxTargetHwnd) {
                    try {
                        $win = (Get-OwnedOdxMainWindow)
                        if ($win) { break }
                    } catch {}
                }
            }
        }

        if ($win) {
            $cId = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'TaskBox')
            $elem = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cId)
            if ($elem) {
                $setOk = $false
                try {
                    $vp = $elem.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                    if ($vp) {
                        $vp.SetValue($taskValue)
                        $setOk = $true
                    }
                } catch {}
                if (-not $setOk) {
                    try {
                        [System.Windows.Forms.Clipboard]::SetText($taskValue)
                        $elem.SetFocus()
                        Start-Sleep -Milliseconds 30
                        [System.Windows.Forms.SendKeys]::SendWait('^a')
                        Start-Sleep -Milliseconds 20
                        [System.Windows.Forms.SendKeys]::SendWait('^v')
                    } catch {}
                }
                for ($attempt = 0; $attempt -lt 15; $attempt++) {
                    try {
                        $actual = $elem.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value
                        if ($actual -ceq $taskValue) {
                            Write-Output 'TASK_SET_OK'
                            exit 0
                        }
                    } catch {}
                    Start-Sleep -Milliseconds 50
                }
                exit 1
            }
        }
        exit 1
        """
        ps_script = ps_template.replace("'__TASK_ID__'", _ps_literal(task_id))
        result = run_ps_script(ps_script, timeout=5)
        task_thread.success = bool(result is not None and result.returncode == 0 and "TASK_SET_OK" in (result.stdout or ""))

    def _run_latest():
        with _task_injection_lock:
            with _task_request_lock:
                if generation != _task_request_generation:
                    return
            _inject_uia()
            with _task_request_lock:
                if generation != _task_request_generation:
                    task_thread.success = False

    task_thread = threading.Thread(target=_run_latest, daemon=True)
    task_thread.success = False
    task_thread.start()
    return task_thread

def set_player_info(player_id=None, player_name=None):
    """Cap nhat player_id va player_name vao file config odx-app.json va tu dong dien vao PlayerBox/NameBox tren Odx Capture."""
    appdata_json = os.path.expandvars(r"%APPDATA%\Odx\odx-app.json")
    try:
        data = {}
        if os.path.exists(appdata_json):
            with open(appdata_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        if player_id is not None:
            data["PlayerId"] = str(player_id)
        if player_name is not None:
            data["PlayerName"] = str(player_name)
        os.makedirs(os.path.dirname(appdata_json), exist_ok=True)
        with open(appdata_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

    def _inject_uia():
        ps_template = """
        Add-Type -AssemblyName UIAutomationClient
        Add-Type -AssemblyName UIAutomationTypes
        Add-Type -AssemblyName System.Windows.Forms

        $root = [System.Windows.Automation.AutomationElement]::RootElement
        $win = $null

        $cName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'Odx Capture')
        $win = Get-OwnedOdxMainWindow

        if (-not $win) {
            $procs = Get-Process -Id $script:OdxTargetPid -ErrorAction SilentlyContinue
            foreach ($p in $procs) {
                if ($p.MainWindowHandle -eq $script:OdxTargetHwnd) {
                    try {
                        $win = (Get-OwnedOdxMainWindow)
                        if ($win) { break }
                    } catch {}
                }
            }
        }

        if ($win) {
            $pidVal = '__PLAYER_ID__'
            $pnameVal = '__PLAYER_NAME__'

            if ($pidVal -ne 'NONE') {
                $cP = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'PlayerBox')
                $elemP = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cP)
                if ($elemP) {
                    try {
                        $vpP = $elemP.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                        $vpP.SetValue($pidVal)
                    } catch {
                        try {
                            $elemP.SetFocus()
                            Start-Sleep -Milliseconds 30
                            [System.Windows.Forms.SendKeys]::SendWait('^a')
                            Start-Sleep -Milliseconds 20
                            [System.Windows.Forms.Clipboard]::SetText($pidVal)
                        [System.Windows.Forms.SendKeys]::SendWait('^v')
                        } catch {}
                    }
                }
            }

            if ($pnameVal -ne 'NONE') {
                $cN = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'NameBox')
                $elemN = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cN)
                if ($elemN) {
                    try {
                        $vpN = $elemN.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                        $vpN.SetValue($pnameVal)
                    } catch {
                        try {
                            $elemN.SetFocus()
                            Start-Sleep -Milliseconds 30
                            [System.Windows.Forms.SendKeys]::SendWait('^a')
                            Start-Sleep -Milliseconds 20
                            [System.Windows.Forms.Clipboard]::SetText($pnameVal)
                        [System.Windows.Forms.SendKeys]::SendWait('^v')
                        } catch {}
                    }
                }
            }
        }
        """
        p_id_str = str(player_id) if player_id is not None else "NONE"
        p_name_str = str(player_name) if player_name is not None else "NONE"
        ps_script = ps_template.replace("'__PLAYER_ID__'", _ps_literal(p_id_str)).replace("'__PLAYER_NAME__'", _ps_literal(p_name_str))
        run_ps_script(ps_script, timeout=4)

    threading.Thread(target=_inject_uia, daemon=True).start()

def _player_join_script(room_code, player_id, player_name, action="inspect", context=None):
    """Read or join through exact owned native controls, never screen input."""
    payload = dict(room_code=str(room_code), player_id=str(player_id), player_name=str(player_name),
                   action=action, hwnd=int(context[0]) if context else 0,
                   pid=int(context[1]) if context else 0)
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return r"""
$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__JOIN_REQUEST__')) | ConvertFrom-Json
$result = @{status='waiting';room_code='';player_id='';invoked=$false;code_set=$false;error='';dialog_hwnd=0}
function Write-JoinResult {
    $result.hwnd=$script:OdxTargetHwnd.ToInt64(); $result.pid=$script:OdxTargetPid
    Write-Output ('ODX_PLAYER_JOIN:' + ($result | ConvertTo-Json -Compress))
}
function Get-JoinControl($parent,[string]$id) {
    $condition=[System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,$id)
    $control=$parent.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$condition)
    if ($control -and $control.Current.ProcessId -eq $script:OdxTargetPid) { return $control }
    return $null
}
function Get-JoinValue($control) {
    if (-not $control) { return '' }
    try { return [string]$control.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value }
    catch { return [string]$control.Current.Name }
}
function Set-JoinValue($control,[string]$value) {
    if (-not $control -or -not $control.Current.IsEnabled) { throw 'JOIN_FIELD_UNAVAILABLE' }
    $pattern=$control.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($pattern.Current.IsReadOnly) { throw 'JOIN_FIELD_READ_ONLY' }
    $pattern.SetValue($value)
    if ((Get-JoinValue $control) -cne $value) { throw 'JOIN_FIELD_READBACK_FAILED' }
}
function Get-JoinLabel($control) {
    if (-not $control) { return '' }
    return ([string]$control.Current.Name).Replace('&','').Replace('_','').Trim().ToLowerInvariant()
}
try {
    if (($request.hwnd -and $request.hwnd -ne $script:OdxTargetHwnd.ToInt64()) -or
        ($request.pid -and $request.pid -ne $script:OdxTargetPid)) { throw 'JOIN_WINDOW_CHANGED' }
    $win=Get-OwnedOdxMainWindow
    if (-not $win) { throw 'JOIN_WINDOW_NOT_OWNED' }
    $identity=Get-JoinControl $win 'PlayerBox'
    $name=Get-JoinControl $win 'NameBox'
    $join=Get-JoinControl $win 'JoinButton'
    $room=Get-JoinControl $win 'RoomBox'
    if (-not $identity -or -not $name -or -not $join) {
        $result.error='JOIN_UI_LOADING'; Write-JoinResult; exit 0
    }
    $result.player_id=(Get-JoinValue $identity).Trim()
    $result.room_code=(Get-JoinValue $room).Trim()
    $label=Get-JoinLabel $join
    if ($label -in @('rời room','rời phòng','leave room','leave')) {
        if ($result.player_id -cne $request.player_id) { $result.status='wrong_player'; $result.error='JOIN_WRONG_PLAYER' }
        elseif ($result.room_code -cne $request.room_code) { $result.status='wrong_room'; $result.error='JOIN_WRONG_ROOM' }
        else { $result.status='in_room' }
        Write-JoinResult; exit 0
    }
    if ($label -notin @('vào room','vào phòng','join room','join')) {
        $result.error='JOIN_UI_LOADING'; Write-JoinResult; exit 0
    }
    if (-not ('OdxJoinOwner' -as [type])) {
        Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class OdxJoinOwner { [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint command); }'
    }
    $root=[System.Windows.Automation.AutomationElement]::RootElement
    $windows=@()
    foreach ($candidate in $root.FindAll([System.Windows.Automation.TreeScope]::Children,$script:OdxProcessCondition)) {
        $windows += $candidate
    }
    # WPF's owned ShowDialog window can be a descendant of its owner in UIA,
    # although its HWND is a native top-level dialog. Search both placements.
    $windowType=[System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Window)
    $ownedWindowCondition=[System.Windows.Automation.AndCondition]::new($script:OdxProcessCondition,$windowType)
    foreach ($candidate in $win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$ownedWindowCondition)) {
        $windows += $candidate
    }
    $seenDialogHandles=@{}
    $dialogs=@()
    foreach ($window in $windows) {
        if ($window.Current.ProcessId -ne $script:OdxTargetPid) { continue }
        $dialogHandle=[IntPtr]$window.Current.NativeWindowHandle
        if ($dialogHandle -eq [IntPtr]::Zero -or
            [OdxJoinOwner]::GetWindow($dialogHandle,4) -ne $script:OdxTargetHwnd) { continue }
        if ($seenDialogHandles.ContainsKey($dialogHandle.ToInt64())) { continue }
        $seenDialogHandles[$dialogHandle.ToInt64()]=$true
        $code=Get-JoinControl $window 'CodeBox'
        $ok=Get-JoinControl $window 'OkButton'
        if ($code -and $ok -and $code.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and
            $ok.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button) {
            $dialogs += @{window=$window;code=$code;ok=$ok;hwnd=$dialogHandle.ToInt64()}
        }
    }
    if ($dialogs.Count -gt 1) { throw 'JOIN_DIALOG_AMBIGUOUS' }
    if ($dialogs.Count -eq 1) {
        $dialog=$dialogs[0]; $result.dialog_hwnd=$dialog.hwnd
        $result.status='dialog'
        if ($request.action -ceq 'submit') {
            if ($result.player_id -cne $request.player_id) { throw 'JOIN_PLAYER_CHANGED' }
            Set-JoinValue $dialog.code $request.room_code
            $result.code_set=$true
            for ($attempt=0; $attempt -lt 20 -and -not $dialog.ok.Current.IsEnabled; $attempt++) {
                Start-Sleep -Milliseconds 50
            }
            if (-not $dialog.ok.Current.IsEnabled) { $result.status='dialog_waiting'; $result.error='JOIN_SUBMIT_DISABLED' }
            elseif ((Get-JoinValue $dialog.code) -cne $request.room_code) { throw 'JOIN_CODE_CHANGED' }
            else {
                $invoke=$dialog.ok.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                $result.invoked=$true
                $invoke.Invoke()
                $result.status='submitted'
            }
        }
        Write-JoinResult; exit 0
    }
    $result.status='can_open'
    if (-not $join.Current.IsEnabled) { $result.status='waiting'; $result.error='JOIN_BUTTON_DISABLED' }
    elseif ($request.action -ceq 'open') {
        Set-JoinValue $identity $request.player_id
        Set-JoinValue $name $request.player_name
        if ((Get-JoinLabel $join) -notin @('vào room','vào phòng','join room','join') -or
            -not $join.Current.IsEnabled) { throw 'JOIN_STATE_CHANGED' }
        $invoke=$join.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $result.player_id=Get-JoinValue $identity
        $result.invoked=$true
        $invoke.Invoke()
        $result.status='opened'
    }
} catch {
    $result.status='error'
    $result.error='JOIN_UI_UNAVAILABLE'
    if ($_.Exception.Message -match '\bJOIN_[A-Z_]+\b') { $result.error=$Matches[0] }
}
Write-JoinResult
""".replace("__JOIN_REQUEST__", encoded)


def _player_join_report(result):
    if result is None or result.returncode != 0:
        return None
    for line in reversed((result.stdout or "").splitlines()):
        if not line.startswith("ODX_PLAYER_JOIN:"):
            continue
        try:
            report = json.loads(line.partition(":")[2])
        except (TypeError, ValueError):
            return None
        if isinstance(report, dict) and report.get("status") in {
                "waiting", "can_open", "opened", "dialog", "dialog_waiting", "submitted",
                "in_room", "wrong_room", "wrong_player", "error"}:
            return report
    return None


_player_join_lock = threading.Lock()


def player_auto_join_and_ready(room_code, player_id, player_name, on_status=None,
                               cancel_event=None, can_ready=None, timeout=60):
    """Join the exact native room, confirm server membership, then request Ready.

    Joining never depends on the separate settings/Ready gate. Lost command
    results never cause a repeated invoke, and failures keep Odx/dialogs open.
    """
    cancellation = cancel_event if cancel_event is not None else threading.Event()

    def notify(message, tag="PLAYER"):
        if on_status:
            try:
                on_status(message, tag)
            except Exception:
                pass

    def finish(status, error="", success=False, joined=False):
        worker.status, worker.error = status, str(error)
        worker.success, worker.joined = bool(success), bool(joined)
        if error:
            notify(str(error), "ERROR")

    def current():
        return not cancellation.is_set()

    def verify_api(data, identity):
        if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("members"), list):
            return "unverified", "Chưa đọc được trạng thái phòng trên máy chủ Odx."
        if str(data.get("host") or "").strip() == identity:
            return "identity_conflict", f"Player ID [{identity}] trùng Host. Hãy dùng ID khác trên Player."
        matches = [member for member in data["members"] if isinstance(member, dict)
                   and str(member.get("player_id") or "").strip() == identity]
        if len(matches) > 1:
            return "identity_conflict", f"Player ID [{identity}] bị trùng trong phòng."
        if matches and matches[0].get("problem"):
            return "blocked", str(matches[0]["problem"])
        return ("member" if matches else "absent"), ""

    def run():
        if not _player_join_lock.acquire(blocking=False):
            finish("busy", "Một thao tác vào phòng đang chạy; không tạo kết nối trùng.")
            return
        try:
            target = str(room_code or "").strip().upper()
            identity, name = str(player_id or "").strip(), str(player_name or "").strip()
            if not re.fullmatch(r"[A-Z0-9]{6}", target, flags=re.ASCII):
                finish("invalid_room", "Mã phòng phải gồm đúng 6 chữ/số ASCII.")
                return
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity, flags=re.ASCII) or not name:
                finish("invalid_player", "Player ID hoặc tên không hợp lệ.")
                return
            try:
                seconds = float(timeout)
                if not 0 < seconds <= 180:
                    raise ValueError()
            except (TypeError, ValueError):
                finish("error", "Thời gian chờ vào phòng không hợp lệ.")
                return
            if not current():
                finish("cancelled")
                return
            deadline = time.monotonic() + seconds
            data = fetch_room_status(target)
            api_status, api_error = verify_api(data, identity)
            if api_status in {"identity_conflict", "blocked", "unverified"}:
                finish(api_status, api_error)
                return
            if data.get("current_session"):
                finish("recording", "Phòng đang quay hoặc xử lý round; hãy chờ trước khi vào phòng.")
                return
            context = _owned_uia_context(min(2, max(.01, deadline - time.monotonic())))
            if context is None:
                finish("unavailable", "Chưa tìm thấy cửa sổ Odx do tool mở; giữ nguyên để thử lại.")
                return
            context = tuple(context)
            worker.room_code, worker.player_id = target, identity
            opened, submitted = False, False
            last_error = "Đang chờ Odx xác nhận vào phòng."
            announced_code = False

            def request(action):
                if not current():
                    return None
                if _owned_uia_context(0) != context:
                    raise RuntimeError("Cửa sổ Odx đã thay đổi; không thao tác lên phiên khác.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                report = _player_join_report(run_ps_script(
                    _player_join_script(target, identity, name, action, context=context),
                    timeout=min(5, remaining)))
                if report and (report.get("hwnd") != context[0] or report.get("pid") != context[1]):
                    raise RuntimeError("Xác nhận cửa sổ Odx không đúng phiên đang kết nối.")
                return report

            notify(f"Đang kiểm tra Odx và vào phòng [{target}] bằng Player ID [{identity}]...")
            while current() and time.monotonic() < deadline:
                report = request("inspect")
                if not current():
                    break
                if not report:
                    last_error = "Chưa đọc được giao diện Odx; chưa bấm lại để tránh kết nối trùng."
                elif report["status"] in {"wrong_room", "wrong_player"}:
                    finish(report["status"], "Odx đang ở phòng hoặc Player ID khác; giữ nguyên cửa sổ để bạn kiểm tra.")
                    return
                elif report["status"] == "error":
                    finish("error", "Không thao tác được giao diện vào phòng Odx: " + str(report.get("error") or "JOIN_UI_UNAVAILABLE"))
                    return
                elif report["status"] == "in_room":
                    # The main room UI and a fresh server response must agree.
                    data = fetch_room_status(target)
                    api_status, api_error = verify_api(data, identity)
                    if api_status in {"identity_conflict", "blocked"}:
                        finish(api_status, api_error)
                        return
                    if api_status == "member":
                        worker.joined = worker.success = True
                        worker.status = "joined_waiting_ready"
                        notify(f"Đã xác nhận Player [{identity}] vào đúng phòng [{target}].", "SUCCESS")
                        break
                    last_error = api_error or "Odx đã hiện phòng; đang chờ server xác nhận thành viên Player."
                else:
                    # An API identity already used by another native session is
                    # never adopted merely because its name matches this config.
                    data = fetch_room_status(target)
                    api_status, api_error = verify_api(data, identity)
                    if api_status in {"identity_conflict", "blocked"}:
                        finish(api_status, api_error)
                        return
                    if api_status == "member":
                        finish("identity_conflict", f"Player ID [{identity}] đã có trong phòng nhưng Odx này chưa vào phòng. Hãy dùng ID riêng.")
                        return
                    if report["status"] == "can_open" and not opened:
                        opened = True  # Before dispatch, even if the reply is lost.
                        response = request("open")
                        if response and response["status"] == "error":
                            finish("error", "Chưa mở được hộp thoại vào phòng: " + str(response.get("error") or "JOIN_UI_UNAVAILABLE"))
                            return
                        last_error = "Đã gửi một lệnh mở hộp thoại vào phòng; đang chờ Odx."
                    elif report["status"] in {"dialog", "dialog_waiting"} and not submitted:
                        submitted = True
                        response = request("submit")
                        if response and response.get("code_set") and not announced_code:
                            notify(f"Đã đọc lại đúng mã [{target}] trong ô CodeBox của Odx; đang chờ xác nhận vào phòng.")
                            announced_code = True
                        if response and response["status"] == "dialog_waiting" and not response.get("invoked"):
                            submitted = False  # Exact report proves no submit was sent.
                        elif response and response["status"] == "error":
                            finish("error", "Chưa điền/xác nhận được mã phòng: " + str(response.get("error") or "JOIN_UI_UNAVAILABLE"))
                            return
                        last_error = "Đã gửi mã phòng một lần; đang chờ Odx và máy chủ xác nhận."
                cancellation.wait(min(.25, max(0, deadline - time.monotonic())))
            if not current():
                finish("cancelled")
                return
            if not worker.joined:
                finish("timeout", "Chưa xác nhận vào phòng. " + last_error + " Odx và hộp thoại được giữ nguyên.")
                return
            # Do not make successful room entry depend on the GUI's settings ACK.
            allowed = True
            if can_ready is not None:
                try:
                    allowed = bool(can_ready())
                except Exception:
                    allowed = False
            if not allowed:
                notify("Đã vào phòng; chờ đồng bộ cấu hình trước khi bật Ready.", "WAIT")
                return
            ready = player_ensure_ready_in_room(target, on_status=on_status, player_id=identity,
                                                cancel_event=cancellation, can_ready=can_ready,
                                                timeout=min(20, max(1, seconds)))
            ready.join()
            if not current():
                finish("cancelled")
                return
            worker.ready = bool(ready.success)
            worker.invoked = bool(ready.invoked)
            if worker.ready:
                worker.status = "confirmed"
            else:
                worker.ready_error = str(ready.error or "")
                notify("Đã vào phòng; chưa xác nhận Ready, sẽ thử lại khi cấu hình và phòng sẵn sàng.", "WAIT")
        except Exception as exc:
            finish("error", f"Chưa hoàn tất vào phòng: {exc}. Odx được giữ nguyên.")
        finally:
            _player_join_lock.release()

    worker = threading.Thread(target=run, daemon=True)
    worker.success, worker.joined, worker.ready, worker.invoked = False, False, False, False
    worker.status, worker.error, worker.ready_error = "joining", "", ""
    worker.room_code, worker.player_id = "", ""
    worker.cancel_event = cancellation
    worker.start()
    return worker



def _player_ready_script(room_code=None, player_id=None, allow_invoke=False):
    """Read/invoke a semantic Ready action; visibility is never room identity."""
    script = r"""
$targetRoom = __READY_ROOM__
$targetPlayer = __READY_PLAYER__
$allowInvoke = __READY_INVOKE__
$result = @{status='unavailable'; room_code=''; player_id=''; button_state='unknown'; invoked=$false; error=''}
function Write-PlayerReadyResult {
    Write-Output ('ODX_PLAYER_READY:' + ($result | ConvertTo-Json -Compress))
}
function Get-ReadyControl($window, $id) {
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, $id)
    $element = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
    if ($element -and $element.Current.ProcessId -eq $script:OdxTargetPid) { return $element }
    return $null
}
function Get-ReadyText($element) {
    if (-not $element) { return '' }
    try {
        $pattern = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        if ($pattern) { return ([string]$pattern.Current.Value).Trim() }
    } catch { }
    try { return ([string]$element.Current.Name).Trim() } catch { return '' }
}
function Normalize-ReadyLabel($text) {
    $text = ([string]$text).Replace([char]0x2019, [char]0x27).Replace('&', '').Replace('_', '').Trim().ToLowerInvariant()
    $text = [regex]::Replace($text, '\s*\([^)]*\)\s*$', '')
    $buffer = New-Object System.Text.StringBuilder
    foreach ($character in $text.Normalize([Text.NormalizationForm]::FormD).ToCharArray()) {
        if ([Globalization.CharUnicodeInfo]::GetUnicodeCategory($character) -ne [Globalization.UnicodeCategory]::NonSpacingMark) {
            [void]$buffer.Append($character)
        }
    }
    return ([regex]::Replace($buffer.ToString().Replace('đ','d'), '\s+', ' ')).Trim()
}
function Get-ReadyButtonState($text) {
    $label = Normalize-ReadyLabel $text
    if ($label -in @('huy san sang', 'bo san sang', 'unready', 'not ready', 'cancel ready', 'cancel readiness')) { return 'already_ready' }
    if ($label -in @('san sang', 'toi san sang', 'ready', "i'm ready", 'im ready', 'i am ready', 'mark ready', 'set ready')) { return 'ready_action' }
    if ($label -match '^(dang |dung ghi|dung quay|ket thuc round|end round|stop recording|stopping|recording|starting capture|countdown|saving|finishing|bat dau ghi sau)') { return 'recording' }
    return 'unknown'
}
try {
    $win = Get-OwnedOdxMainWindow
    if (-not $win) { throw 'Managed Odx window is not available.' }
    $button = Get-ReadyControl $win 'StartStopButton'
    if (-not $button) {
        $condition = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
        $buttons = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
        foreach ($candidate in $buttons) {
            if ($candidate.Current.ProcessId -eq $script:OdxTargetPid -and
                (Get-ReadyButtonState $candidate.Current.Name) -in @('ready_action', 'already_ready')) {
                $button = $candidate
                break
            }
        }
    }
    if (-not $button) { throw 'Ready control is unavailable.' }
    $result.button_state = Get-ReadyButtonState $button.Current.Name
    if ($result.button_state -eq 'recording') {
        $result.status = 'recording'
        Write-PlayerReadyResult
        exit 0
    }
    $room = Get-ReadyText (Get-ReadyControl $win 'RoomBox')
    $identity = Get-ReadyText (Get-ReadyControl $win 'PlayerBox')
    $result.room_code = $room
    $result.player_id = $identity
    $join = Get-ReadyControl $win 'JoinButton'
    $joinLabel = if ($join) { Normalize-ReadyLabel $join.Current.Name } else { '' }
    if ($joinLabel -in @('vao room','vao phong','join room','join')) {
        $result.status='not_in_room'; $result.error='Odx is not in a room.'
    } elseif ($room -cnotmatch '^[A-Z0-9]{6}$' -or -not $identity -or -not $join) {
        $result.status='unavailable'; $result.error='Room/player controls are unavailable.'
    } elseif ($targetRoom -and $room -cne $targetRoom) {
        $result.status='wrong_room'; $result.error='The Odx window is in a different room.'
    } elseif ($targetPlayer -and $identity -cne $targetPlayer) {
        $result.status='wrong_player'; $result.error='The Odx window belongs to a different player.'
    } elseif ($joinLabel -notin @('roi room','roi phong','leave room','leave')) {
        $result.status='unavailable'; $result.error='Room membership cannot be verified in Odx.'
    } elseif ($result.button_state -eq 'unknown') {
        $result.status='blocked'; $result.error='The button is not a Ready action.'
    } elseif ($result.button_state -eq 'already_ready') {
        $result.status='already_ready'
    } elseif (-not $button.Current.IsEnabled) {
        $result.status='disabled'; $result.error='The Ready action is disabled by Odx.'
    } else {
        $result.status='ready_action'
        if ($allowInvoke) {
            # Recheck the semantic action immediately before invoking, never
            # synthesize mouse/keyboard events into a covered application.
            if ((Get-ReadyButtonState $button.Current.Name) -ne 'ready_action' -or -not $button.Current.IsEnabled) {
                $result.status='blocked'; $result.error='Odx changed state before Ready.'
            } else {
                $result.invoked=$true
                # InvokePattern activates some WPF providers. The native
                # default action validates this exact control again and does
                # not move focus, the pointer, or the window.
                [OdxLegacyReady]::Invoke($script:OdxTargetHwnd.ToInt64(), $script:OdxTargetPid,
                    $button.Current.AutomationId, $button.Current.Name, $button.GetRuntimeId())
                $result.status='invoked'
            }
        }
    }
} catch {
    $result.status='unavailable'
    $result.error=[string]$_.Exception.Message
}
Write-PlayerReadyResult
"""
    result = (script.replace('__READY_ROOM__', _ps_literal(str(room_code or '').strip().upper()))
              .replace('__READY_PLAYER__', _ps_literal(str(player_id or '').strip()))
              .replace('__READY_INVOKE__', '$true' if allow_invoke else '$false'))
    return (ready_action_preamble() if allow_invoke else '') + result


def _player_ready_report(result):
    if result is None or result.returncode != 0:
        return None
    for line in reversed((result.stdout or '').splitlines()):
        if line.startswith('ODX_PLAYER_READY:'):
            try:
                report = json.loads(line.partition(':')[2])
                return report if isinstance(report, dict) else None
            except (TypeError, ValueError):
                return None
    return None


def _ready_api_member(data, player_id):
    """Classify a fresh server result without treating absent state as Ready."""
    if not isinstance(data, dict) or data.get('error'):
        return 'unverified', None, str((data or {}).get('message') or 'Không đọc được trạng thái phòng.') if isinstance(data, dict) else 'Không đọc được trạng thái phòng.'
    if data.get('current_session'):
        return 'recording', None, 'Phòng đang quay hoặc xử lý round; không đổi Ready.'
    members = data.get('members')
    if not isinstance(members, list):
        return 'unverified', None, 'Danh sách thành viên không hợp lệ.'
    matches = [member for member in members if isinstance(member, dict) and str(member.get('player_id', '')).strip() == player_id]
    if len(matches) != 1:
        return 'not_in_room', None, f'Chưa xác nhận thành viên [{player_id}] trong phòng.'
    member = matches[0]
    if str(data.get('host', '')).strip() == player_id:
        return 'blocked', member, 'Đây là Host; không bật Ready bằng nút của Player.'
    if member.get('problem'):
        return 'blocked', member, str(member['problem'])
    return ('confirmed' if member.get('ready') is True else 'pending'), member, ''


@contextmanager
def _temporary_ready_window_access():
    """Temporarily make an unavailable provider reachable without activation.

    Most WPF controls work minimized and need none of this. This fallback never
    sends input, changes foreground ownership, or touches a foreign window.
    """
    hwnd, placement, user, was_visible, was_iconic, old_rect = None, None, None, None, None, None
    changed = False
    try:
        from ctypes import wintypes
        hwnd = find_odx_window()
        if not hwnd or find_unmanaged_odx_window() or not is_managed_odx_window(hwnd):
            raise ValueError('No owned window is available for recovery')

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT), ('showCmd', wintypes.UINT),
                        ('ptMinPosition', wintypes.POINT), ('ptMaxPosition', wintypes.POINT), ('rcNormalPosition', wintypes.RECT)]

        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
        user.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
        user.IsIconic.argtypes = [wintypes.HWND]
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user.MonitorFromWindow.restype = wintypes.HANDLE
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(placement)
        if not user.GetWindowPlacement(hwnd, ctypes.byref(placement)):
            raise ValueError('Cannot save the Odx window placement')
        was_visible, was_iconic = bool(user.IsWindowVisible(hwnd)), bool(user.IsIconic(hwnd))
        old_rect = wintypes.RECT()
        if not user.GetWindowRect(hwnd, ctypes.byref(old_rect)):
            raise ValueError('Cannot save the Odx window rectangle')
        if was_iconic or not was_visible:
            user.ShowWindowAsync(hwnd, 4)  # SW_SHOWNOACTIVATE; never steal focus.
            changed = True
        if not user.MonitorFromWindow(hwnd, 0):
            # Return a wholly offscreen window to the primary work area only
            # while querying its provider. Preserve size and z-order.
            user.SetWindowPos(hwnd, None, 40, 40, 0, 0, 0x0010 | 0x0004 | 0x0001)
            changed = True
        if changed:
            time.sleep(0.15)
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        pass
    try:
        yield changed
    finally:
        if changed and user and hwnd and is_managed_odx_window(hwnd):
            try:
                if was_iconic:
                    placement.showCmd = 7  # SW_SHOWMINNOACTIVE
                    user.SetWindowPlacement(hwnd, ctypes.byref(placement))
                else:
                    user.SetWindowPos(hwnd, None, old_rect.left, old_rect.top,
                                      old_rect.right - old_rect.left, old_rect.bottom - old_rect.top,
                                      0x0010 | 0x0004)
                    if not was_visible:
                        user.ShowWindowAsync(hwnd, 0)
            except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
                pass


_player_ready_lock = threading.Lock()


def player_ensure_ready_in_room(room_code=None, on_status=None, player_id=None,
                                cancel_event=None, can_ready=None, timeout=20):
    """Set Ready through UIA once and confirm this player on the room server."""
    cancellation = cancel_event if cancel_event is not None else threading.Event()

    def notify(message, tag='PLAYER'):
        if on_status:
            try:
                on_status(message, tag)
            except Exception:
                pass

    def allowed():
        if cancellation.is_set():
            return False
        if can_ready:
            try:
                return bool(can_ready())
            except Exception:
                return False
        return True

    def finish(status, error='', success=False):
        worker.status, worker.error, worker.success = status, str(error), bool(success)
        if success:
            notify(f'Odx/server đã xác nhận Player [{worker.player_id}] SẴN SÀNG trong phòng [{worker.room_code}].', 'SUCCESS')
        elif status != 'cancelled':
            notify(worker.error or 'Chưa xác nhận trạng thái Ready từ Odx/server.', 'INFO' if status in {'recording', 'inflight'} else 'WARN')

    def _worker():
        if not _player_ready_lock.acquire(blocking=False):
            finish('inflight', 'Một thao tác Ready khác đang chạy; không gửi lệnh trùng.')
            return
        try:
            if not allowed():
                finish('cancelled')
                return
            target_room = str(room_code or '').strip().upper()
            identity = str(player_id or '').strip()
            if target_room and not re.fullmatch(r'[A-Z0-9]{6}', target_room, flags=re.ASCII):
                finish('invalid_room', 'Mã phòng phải gồm đúng 6 chữ/số ASCII.')
                return
            try:
                duration = float(timeout)
                if not 0 < duration <= 120:
                    raise ValueError()
            except (TypeError, ValueError):
                finish('error', 'Thời gian chờ Ready không hợp lệ.')
                return
            deadline, attempted, restored = time.monotonic() + duration, False, False
            last_status, last_error = 'unverified', 'Chưa nhận được xác nhận Ready.'
            with ExitStack() as access:
                while time.monotonic() < deadline:
                    if not allowed():
                        finish('cancelled')
                        return
                    data = fetch_room_status(target_room) if target_room else None
                    if isinstance(data, dict) and data.get('current_session'):
                        finish('recording', 'Phòng đã bắt đầu round; không đổi Ready.')
                        return
                    report = _player_ready_report(run_ps_script(
                        _player_ready_script(target_room, identity), timeout=min(5, max(.01, deadline - time.monotonic()))))
                    if not allowed():
                        finish('cancelled')
                        return
                    if report and report.get('status') == 'recording':
                        finish('recording', 'Odx đang quay/đếm ngược/lưu; không đổi Ready.')
                        return
                    if report and report.get('status') in {'wrong_room', 'wrong_player', 'blocked'}:
                        finish(report['status'], report.get('error', 'Odx chưa ở đúng phòng/trạng thái Player.'))
                        return
                    if report and report.get('status') == 'not_in_room':
                        # Closing JoinRoomDialog can precede room binding in
                        # WPF. Wait for that transition without ever toggling
                        # a Solo recording button.
                        last_status = 'not_in_room'
                        last_error = report.get('error') or 'Chưa xác nhận Odx vào phòng.'
                    elif not report or report.get('status') == 'unavailable':
                        last_error = report.get('error', '') if report else 'Không đọc được UIAutomation của Odx.'
                        # Require an idle room response before changing window
                        # presentation; never restore an unknown active capture.
                        if not restored and isinstance(data, dict) and not data.get('error') and target_room:
                            restored = True
                            access.enter_context(_temporary_ready_window_access())
                            continue
                        last_status = 'unverified'
                    else:
                        actual_room = str(report.get('room_code') or '').strip()
                        actual_player = str(report.get('player_id') or '').strip()
                        if not re.fullmatch(r'[A-Z0-9]{6}', actual_room, flags=re.ASCII) or not actual_player:
                            finish('unverified', 'Không xác nhận được mã phòng và Player ID từ Odx.')
                            return
                        target_room = target_room or actual_room
                        identity = identity or actual_player
                        if actual_room != target_room or actual_player != identity:
                            finish('wrong_identity', 'Phòng hoặc Player ID của Odx đã thay đổi.')
                            return
                        worker.room_code, worker.player_id = target_room, identity
                        if data is None:
                            data = fetch_room_status(target_room)
                        api_status, member, error = _ready_api_member(data, identity)
                        worker.joined = member is not None
                        if api_status == 'confirmed':
                            if allowed():
                                finish('confirmed', success=True)
                            else:
                                finish('cancelled')
                            return
                        if api_status in {'recording', 'blocked'}:
                            finish(api_status, error)
                            return
                        last_status, last_error = api_status, error or 'Đang chờ server xác nhận Ready.'
                        if (api_status == 'pending' and report.get('status') == 'ready_action' and not attempted):
                            if not allowed():
                                finish('cancelled')
                                return
                            attempted = True
                            response = _player_ready_report(run_ps_script(
                                _player_ready_script(target_room, identity, True),
                                timeout=min(5, max(.01, deadline - time.monotonic()))))
                            worker.invoked = bool(response and response.get('invoked'))
                            if response and response.get('status') in {'recording', 'wrong_room', 'wrong_player', 'not_in_room', 'blocked'}:
                                finish(response['status'], response.get('error') or 'Odx đã đổi trạng thái trước khi Ready.')
                                return
                            if not response or response.get('status') == 'unavailable':
                                last_error = 'Lệnh Ready chưa được xác nhận; không bấm lại để tránh tắt Ready.'
                    cancellation.wait(min(.4, max(0, deadline - time.monotonic())))
            finish(last_status, last_error or 'Chưa xác nhận Ready trong thời gian chờ.')
        except Exception as exc:
            if not allowed():
                finish('cancelled')
            else:
                finish('error', f'Auto-Ready chưa hoàn tất: {exc}')
        finally:
            _player_ready_lock.release()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.success, worker.status, worker.error = False, 'pending', ''
    worker.invoked, worker.joined = False, False
    worker.room_code, worker.player_id = '', ''
    worker.cancel_event = cancellation
    worker.start()
    return worker


def _host_room_script(allow_create=False, context=None):
    """Read/create only inside the managed Odx window; never use screen clicks."""
    return """
$allowCreate = __ALLOW_CREATE__
$result = @{status='waiting'; room_code=''; player_id=''; is_host=$false; can_create=$false; clicked=$false; error=''}
function Write-HostRoomResult {
    $result.hwnd=$script:OdxTargetHwnd.ToInt64(); $result.pid=$script:OdxTargetPid
    Write-Output ('ODX_HOST_ROOM:' + ($result | ConvertTo-Json -Compress))
}
function Get-HostRoomCode($window) {
    foreach ($automationId in @('RoomBox', 'RoomCodeBox', 'RoomCodeText', 'RoomCodeLabel')) {
        $condition = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty, $automationId)
        $element = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
        if (-not $element -or $element.Current.ProcessId -ne $script:OdxTargetPid) {
            continue
        }
        $text = ''
        $hasValuePattern = $false
        try {
            $valuePattern = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
            if ($valuePattern) {
                $hasValuePattern = $true
                $text = [string]$valuePattern.Current.Value
            }
        } catch { }
        if (-not $hasValuePattern) {
            try { $text = [string]$element.Current.Name } catch { }
        }
        $text = $text.Trim()
        if ($text -cmatch '^[A-Z0-9]{6}$') { return $text }
    }
    return ''
}
function Get-HostCreateButton($window) {
    $buttonCondition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button)
    $buttons = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)
    foreach ($automationId in @('CreateButton', 'CreateRoomButton', 'RoomCreateButton')) {
        foreach ($button in $buttons) {
            if ($button.Current.ProcessId -eq $script:OdxTargetPid -and
                $button.Current.AutomationId -ceq $automationId -and
                $button.Current.IsEnabled) {
                $label = ([string]$button.Current.Name).Replace('&', '').Replace('_', '').Trim()
                if ($label -in @('Tạo room', 'Tạo phòng', 'Tao room', 'Tao phong', 'Create room', 'Create a room')) { return $button }
            }
        }
    }
    foreach ($button in $buttons) {
        if ($button.Current.ProcessId -ne $script:OdxTargetPid -or
            -not $button.Current.IsEnabled) { continue }
        $label = ([string]$button.Current.Name).Replace('&', '').Replace('_', '').Trim()
        if ($label -in @('Tạo room', 'Tạo phòng', 'Tao room', 'Tao phong', 'Create room', 'Create a room')) {
            return $button
        }
    }
    return $null
}
try {
    if ((__EXPECTED_HWND__ -and $script:OdxTargetHwnd.ToInt64() -ne __EXPECTED_HWND__) -or
        (__EXPECTED_PID__ -and $script:OdxTargetPid -ne __EXPECTED_PID__)) { throw 'Managed Odx window changed.' }
    $win = Get-OwnedOdxMainWindow
    if (-not $win) { throw 'Managed Odx window is not ready.' }
    $idCondition=[System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,'PlayerBox')
    $identity=$win.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$idCondition)
    if ($identity) {
        $result.player_id=([string]$identity.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value).Trim()
    }
    $createCondition=[System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,'CreateButton')
    $nativeCreate=$win.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$createCondition)
    if ($nativeCreate -and $nativeCreate.Current.IsEnabled) {
        $nativeLabel=([string]$nativeCreate.Current.Name).Replace('&','').Replace('_','').Trim().ToLowerInvariant()
        $result.is_host=$nativeLabel -in @('đóng room','đóng phòng','close room','close')
    }
    $roomCode = Get-HostRoomCode $win
    if ($roomCode -and $result.is_host) {
        $result.status = 'room'
        $result.room_code = $roomCode
        Write-HostRoomResult
        exit 0
    }
    $create = Get-HostCreateButton $win
    if (-not $create) {
        $result.error = 'Create room button is not ready.'
        Write-HostRoomResult
        exit 0
    }
    $invoke = $create.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    if (-not $invoke) { throw 'Create room button does not support InvokePattern.' }
    $result.can_create = $true
    $result.status = 'ready'
    if ($allowCreate) {
        $label=([string]$create.Current.Name).Replace('&','').Replace('_','').Trim()
        if ($label -notin @('Tạo room', 'Tạo phòng', 'Tao room', 'Tao phong', 'Create room', 'Create a room') -or
            -not $create.Current.IsEnabled) { throw 'Create room state changed; no command sent.' }
        # Mark before Invoke: if the provider fails after dispatch, never click twice.
        $result.clicked = $true
        $invoke.Invoke()
        $result.status = 'clicked'
        $result.room_code = Get-HostRoomCode $win
    }
} catch {
    $result.status = 'error'
    $result.error = [string]$_.Exception.Message
}
Write-HostRoomResult
""".replace('__ALLOW_CREATE__', '$true' if allow_create else '$false').replace(
        '__EXPECTED_HWND__', str(int(context[0])) if context else '0').replace(
        '__EXPECTED_PID__', str(int(context[1])) if context else '0')


def _host_room_code(value):
    text = value.strip() if isinstance(value, str) else ''
    return text if re.fullmatch(r'[A-Z0-9]{6}', text, flags=re.ASCII) else ''


def _host_room_report(result):
    if result is None or result.returncode != 0:
        return None
    for line in reversed((result.stdout or '').splitlines()):
        if not line.startswith('ODX_HOST_ROOM:'):
            continue
        try:
            report = json.loads(line.partition(':')[2])
        except (TypeError, ValueError):
            return None
        if isinstance(report, dict) and report.get('status') in {'waiting', 'ready', 'room', 'clicked', 'error'}:
            return report
    return None


_host_room_creation_lock = threading.Lock()


def host_auto_create_room(on_status=None, cancel_event=None, timeout=60, room_code_supplier=None):
    """Create a Host room once and confirm its code before reporting success.

    ``room_code_supplier`` may return a fresh code from the caller's log follower.
    The returned thread exposes ``success``, ``room_code``, ``error``, ``clicked``
    and ``click_attempted``; these are initialized before the worker starts.
    Cancellation is checked between bounded read-only UI snapshots. No retry can
    issue a second create command after an uncertain first click.
    """
    cancellation = cancel_event if cancel_event is not None else threading.Event()

    def notify(message, tag='HOST'):
        if on_status:
            try:
                on_status(message, tag)
            except Exception:
                pass

    def cancelled():
        if cancellation.is_set():
            worker_thread.error = 'Đã hủy thao tác tạo phòng.'
            notify(worker_thread.error)
            return True
        return False

    def confirm(code, report):
        code = _host_room_code(code)
        identity = str((report or {}).get('player_id') or '').strip()
        if code and identity and report.get('is_host') is True and not cancelled():
            data = fetch_room_status(code)
            if (not isinstance(data, dict) or data.get('error') or
                    str(data.get('host') or '').strip() != identity or
                    not isinstance(data.get('members'), list) or
                    sum(isinstance(member, dict) and str(member.get('player_id') or '').strip() == identity
                        for member in data['members']) != 1):
                return False
            worker_thread.room_code = code
            worker_thread.success = True
            worker_thread.error = ''
            notify(f'Đã xác nhận phòng Host [{code}]. Đang đồng bộ danh sách thành viên.', 'SUCCESS')
            return True
        return False

    def _worker():
        if not _host_room_creation_lock.acquire(blocking=False):
            worker_thread.error = 'Một thao tác tạo phòng đang chạy; không gửi lệnh tạo trùng.'
            notify(worker_thread.error, 'ERROR')
            return
        try:
            try:
                wait_seconds = float(timeout)
                if not 0 < wait_seconds <= 180:
                    raise ValueError()
            except (TypeError, ValueError):
                worker_thread.error = 'Thời gian chờ tạo phòng phải lớn hơn 0 và tối đa 180 giây.'
                notify(worker_thread.error, 'ERROR')
                return
            deadline = time.monotonic() + wait_seconds
            context = _owned_uia_context(min(2, wait_seconds))
            if context is None:
                raise RuntimeError('Chưa tìm thấy đúng cửa sổ Odx do tool mở.')
            context = tuple(context)

            def read(allow_create, duration):
                if cancelled():
                    return None
                if _owned_uia_context(0) != context:
                    raise RuntimeError('Cửa sổ Odx đã thay đổi; không tạo phòng trên phiên khác.')
                report = _host_room_report(run_ps_script(_host_room_script(allow_create, context=context), timeout=duration))
                if report and (report.get('hwnd') != context[0] or report.get('pid') != context[1]):
                    raise RuntimeError('Xác nhận tạo phòng không đúng cửa sổ Odx đang quản lý.')
                return report

            last_error = 'Chưa tìm thấy nút Tạo room sẵn sàng trong cửa sổ Odx.'
            notify('Đang chờ Odx sẵn sàng để tự động tạo phòng...')
            while time.monotonic() < deadline:
                if cancelled():
                    return
                if room_code_supplier:
                    try:
                        supplied = room_code_supplier()
                    except Exception:
                        supplied = None
                    # The follower is a hint only. Native host state and a fresh
                    # server response must agree before any room is accepted.
                    supplied = _host_room_code(supplied)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                report = read(False, min(6, remaining))
                if cancelled():
                    return
                if report:
                    if confirm(report.get('room_code'), report):
                        return
                    if report.get('is_host') and report.get('room_code'):
                        last_error = 'Odx đang ở phòng Host nhưng máy chủ chưa xác nhận đúng Host/thành viên.'
                    if report.get('error'):
                        last_error = str(report['error'])
                    if report.get('can_create') is True and not worker_thread.click_attempted:
                        # Set before launching the mutating script. Lost output must
                        # never cause a repeated click and a second server room.
                        worker_thread.click_attempted = True
                        notify('Đang tự động bấm [Tạo room] trên Odx Capture...')
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or cancelled():
                            break
                        invoked = read(True, min(8, remaining))
                        if cancelled():
                            return
                        if invoked:
                            worker_thread.clicked = invoked.get('clicked') is True
                            if confirm(invoked.get('room_code'), invoked):
                                return
                            if invoked.get('error'):
                                last_error = str(invoked['error'])
                        else:
                            last_error = 'Chưa xác nhận kết quả lệnh tạo phòng; sẽ không bấm tạo lần hai.'
                        notify('Đã gửi một lệnh tạo phòng; đang chờ mã phòng từ Odx...')
                else:
                    last_error = 'Không đọc được trạng thái cửa sổ Odx do tool quản lý.'
                if cancellation.wait(min(0.25, max(0, deadline - time.monotonic()))):
                    cancelled()
                    return
            worker_thread.error = ('Đã gửi lệnh nhưng chưa nhận được mã phòng hợp lệ. ' if worker_thread.click_attempted else
                                   'Chưa tạo được phòng tự động. ') + last_error
            notify(worker_thread.error, 'ERROR')
        except Exception as exc:
            worker_thread.error = f'Không thể tự động tạo phòng: {exc}'
            notify(worker_thread.error, 'ERROR')
        finally:
            _host_room_creation_lock.release()

    worker_thread = threading.Thread(target=_worker, daemon=True)
    worker_thread.success = False
    worker_thread.room_code = ''
    worker_thread.error = ''
    worker_thread.clicked = False
    worker_thread.click_attempted = False
    worker_thread.cancel_event = cancellation
    worker_thread.start()
    return worker_thread

def send_toggle_hotkey(hwnd=None):
    """Send Odx's F10 start/end command; F11 belongs to tool choices."""
    user32 = ctypes.windll.user32
    VK_F10 = 0x79
    KEYEVENTF_KEYUP = 0x0002
    scan_code = user32.MapVirtualKeyW(VK_F10, 0)

    user32.keybd_event(VK_F10, scan_code, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_F10, scan_code, KEYEVENTF_KEYUP, 0)
    return True

def get_target_duration_for_task(task_id):
    """Tính toán thời lượng chuẩn 4p ± 5s (235s - 245s) đồng bộ 100% giữa Host và các Máy con bằng deterministic hash."""
    if not task_id:
        return 240
    import hashlib
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(task_id)).lower()
    h = int(hashlib.md5(clean_id.encode("utf-8")).hexdigest()[:8], 16)
    offset = (h % 11) - 5  # -5 to +5 (235s to 245s)
    return 240 + offset

def _host_stop_script(task, allow_armed=False, invoke=False, context=None,
                      expected_room=None, expected_player=None):
    """Inspect/invoke only the owned Host's semantic End round action."""
    script = r"""
$targetTask=__TARGET_TASK__; $allowInvoke=__INVOKE__
$expectedRoom=__EXPECTED_ROOM__; $expectedPlayer=__EXPECTED_PLAYER__
$result=@{status='unavailable';room_code='';player_id='';task_id='';is_host=$false;button_state='unknown';invoked=$false;error=''}
function Write-HostStopResult {
    $result.hwnd=$script:OdxTargetHwnd.ToInt64();$result.pid=$script:OdxTargetPid
    Write-Output ('ODX_HOST_STOP:' + ($result|ConvertTo-Json -Compress))
}
function Get-StopControl($win,[string]$id) {
    $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty,$id)
    $control=$win.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$condition)
    if ($control -and $control.Current.ProcessId -eq $script:OdxTargetPid) {return $control}
    return $null
}
function Get-StopText($control) {
    if (-not $control) {return ''}
    try {return ([string]$control.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value).Trim()} catch {}
    return ([string]$control.Current.Name).Trim()
}
function Normalize-StopLabel([string]$text) {
    $text=$text.Replace('&','').Replace('_','').Trim().ToLowerInvariant()
    $text=[regex]::Replace($text,'\s*\([^)]*\)\s*$','')
    $buffer=[Text.StringBuilder]::new()
    foreach($character in $text.Normalize([Text.NormalizationForm]::FormD).ToCharArray()) {
        if([Globalization.CharUnicodeInfo]::GetUnicodeCategory($character) -ne [Globalization.UnicodeCategory]::NonSpacingMark) {[void]$buffer.Append($character)}
    }
    return ([regex]::Replace($buffer.ToString().Replace('đ','d'),'\s+',' ')).Trim()
}
function Get-StopState([string]$text) {
    $label=Normalize-StopLabel $text
    if($label -in @('ket thuc round','end round')) {return 'stop_action'}
    if($label -match '^(dang dung|stopping)') {return 'ending'}
    if($label -in @('bat dau round','start round') -or $label -match '^(cho .* (san sang|luu xong round truoc)|waiting for .* (ready|save the last round))') {return 'stopped'}
    return 'blocked'
}
try {
    if((__EXPECTED_HWND__ -and $script:OdxTargetHwnd.ToInt64() -ne __EXPECTED_HWND__) -or
       (__EXPECTED_PID__ -and $script:OdxTargetPid -ne __EXPECTED_PID__)) {throw 'STOP_WINDOW_CHANGED'}
    $win=Get-OwnedOdxMainWindow
    if(-not $win) {throw 'STOP_WINDOW_UNAVAILABLE'}
    $create=Get-StopControl $win 'CreateButton';$join=Get-StopControl $win 'JoinButton'
    $room=Get-StopControl $win 'RoomBox';$player=Get-StopControl $win 'PlayerBox';$taskBox=Get-StopControl $win 'TaskBox'
    $button=Get-StopControl $win 'StartStopButton'
    $result.room_code=Get-StopText $room;$result.player_id=Get-StopText $player;$result.task_id=Get-StopText $taskBox
    $role=Normalize-StopLabel (Get-StopText $create);$joined=Normalize-StopLabel (Get-StopText $join)
    $result.is_host=$create -and $create.Current.IsEnabled -and $role -in @('dong room','dong phong','close room','close')
    if(-not $result.is_host -or $joined -notin @('roi room','roi phong','leave room','leave') -or
       $result.room_code -cnotmatch '^[A-Z0-9]{6}$' -or -not $result.player_id -or
       $result.task_id -cne $targetTask -or ($expectedRoom -and $result.room_code -cne $expectedRoom) -or
       ($expectedPlayer -and $result.player_id -cne $expectedPlayer)) {
        $result.status='wrong_identity';$result.error='STOP_HOST_ROOM_OR_TASK_CHANGED'
    } elseif(-not $button -or $button.Current.ControlType -ne [System.Windows.Automation.ControlType]::Button) {
        $result.error='STOP_BUTTON_UNAVAILABLE'
    } else {
        $result.button_state=Get-StopState $button.Current.Name;$result.status=$result.button_state
        if($result.status -eq 'stop_action' -and -not $button.Current.IsEnabled) {$result.status='unavailable';$result.error='STOP_BUTTON_DISABLED'}
        if($allowInvoke -and $result.status -eq 'stop_action') {
            # The native room Host displays End round during armed/recording.
            # Start/Ready/standalone Cancel controls are never a stop command.
            $label=[string]$button.Current.Name;$runtime=$button.GetRuntimeId()
            if((Get-StopState $button.Current.Name) -ne 'stop_action' -or -not $button.Current.IsEnabled -or
               (Get-StopText $taskBox) -cne $targetTask) {throw 'STOP_STATE_CHANGED'}
            $result.invoked=$true
            [OdxLegacyReady]::Invoke($script:OdxTargetHwnd.ToInt64(),$script:OdxTargetPid,
                $button.Current.AutomationId,$label,$runtime)
            $result.status='invoked'
        }
    }
} catch {$result.status='error';$result.error='STOP_UI_ACTION_FAILED'}
Write-HostStopResult
"""
    script = (script.replace('__TARGET_TASK__', _ps_literal(str(task or '').strip()))
              .replace('__EXPECTED_ROOM__', _ps_literal(str(expected_room or '')))
              .replace('__EXPECTED_PLAYER__', _ps_literal(str(expected_player or '')))
              .replace('__INVOKE__', '$true' if invoke else '$false')
              .replace('__EXPECTED_HWND__', str(int(context[0])) if context else '0')
              .replace('__EXPECTED_PID__', str(int(context[1])) if context else '0'))
    return (ready_action_preamble() if invoke else '') + script


def _host_stop_report(result):
    if result is None or result.returncode != 0:
        return None
    for line in reversed((result.stdout or '').splitlines()):
        if line.startswith('ODX_HOST_STOP:'):
            try:
                report = json.loads(line.partition(':')[2])
            except (TypeError, ValueError):
                return None
            if isinstance(report, dict) and report.get('status') in {
                    'unavailable', 'wrong_identity', 'stop_action', 'invoked', 'ending', 'stopped', 'blocked', 'error'}:
                return report
    return None


def _prepared_host_stop_script(task, context):
    """Load checked native code once; only an explicit invoke command can act."""
    body = _host_stop_script(task, False, False, context,
                             expected_room='__WARM_ROOM__', expected_player='__WARM_PLAYER__')
    body = (body.replace('$allowInvoke=$false', '$allowInvoke=$invoke')
            .replace(_ps_literal('__WARM_ROOM__'), '$preparedRoom')
            .replace(_ps_literal('__WARM_PLAYER__'), '$preparedPlayer')
            .replace('$result.hwnd=', '$result.request_id=$script:PreparedRequestId;$result.hwnd=')
            .replace("Write-Output ('ODX_HOST_STOP:' + ($result|ConvertTo-Json -Compress))",
                     "[Console]::Out.WriteLine('ODX_HOST_STOP:' + ($result|ConvertTo-Json -Compress))"))
    return (_uia_script_prefix(*context) + ready_action_preamble() +
            '\nfunction Invoke-PreparedHostStop([bool]$invoke,[string]$preparedRoom,[string]$preparedPlayer) {\n' +
            body + '\n}\n' + r"""
$script:PreparedDispatched=$false
while($null -ne ($line=[Console]::In.ReadLine())) {
    try {
        $request=$line|ConvertFrom-Json
        $script:PreparedRequestId=[int]$request.request_id
        if($request.command -eq 'cancel') {break}
        if($request.command -eq 'probe') {
            Invoke-PreparedHostStop $false '' ''
        } elseif($request.command -eq 'invoke' -and -not $script:PreparedDispatched) {
            # Consume the sole permission before touching the native control.
            $script:PreparedDispatched=$true
            Invoke-PreparedHostStop $true ([string]$request.room) ([string]$request.player)
        }
        [Console]::Out.Flush()
    } catch {break}
}
""")


class _PreparedStopProcess:
    """A private PowerShell worker; closing it never closes the Odx GUI."""
    def __init__(self, task, context):
        self._io_lock = threading.Lock()
        self._closed_lock = threading.Lock()
        self._closed = False
        self._responses = queue.Queue()
        self._sequence = 0
        self.process = None
        bootstrap = ("[Console]::InputEncoding=[Text.UTF8Encoding]::new($false); "
                     "$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadLine())); "
                     "& ([ScriptBlock]::Create($source))")
        encoded = base64.b64encode(bootstrap.encode('utf-16le')).decode('ascii')
        try:
            self.process = subprocess.Popen(
                ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            register_managed_process(self.process)
            source = base64.b64encode(_prepared_host_stop_script(task, context).encode('utf-8')).decode('ascii')
            self.process.stdin.write(source + '\n')
            self.process.stdin.flush()
            threading.Thread(target=self._read_reports, daemon=True).start()
        except Exception:
            self.close()
            raise

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None and not self._closed

    def _read_reports(self):
        try:
            for line in self.process.stdout:
                if line.startswith('ODX_HOST_STOP:'):
                    try:
                        self._responses.put(json.loads(line.partition(':')[2]))
                    except (ValueError, TypeError):
                        pass
        except (OSError, ValueError):
            pass
        finally:
            self._responses.put(None)

    def request(self, command, *, room='', player='', timeout=1):
        deadline = time.monotonic() + max(.01, timeout)
        if not self._io_lock.acquire(timeout=max(.01, timeout)):
            return None
        try:
            if not self.alive:
                return None
            self._sequence += 1
            request_id = self._sequence
            self.process.stdin.write(json.dumps(dict(command=command, request_id=request_id,
                                                     room=room, player=player)) + '\n')
            self.process.stdin.flush()
            while time.monotonic() < deadline:
                try:
                    report = self._responses.get(timeout=max(.01, deadline - time.monotonic()))
                except queue.Empty:
                    return None
                if report is None:
                    return None
                if isinstance(report, dict) and report.get('request_id') == request_id:
                    return report
            return None
        finally:
            self._io_lock.release()

    def close(self):
        with self._closed_lock:
            if self._closed:
                return
            self._closed = True
        process = self.process
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()  # EOF makes this worker leave its read loop.
            process.wait(timeout=.3)
        except (OSError, ValueError, subprocess.SubprocessError):
            try:
                if process.poll() is None:
                    process.terminate()  # Only this private worker, never Odx/the job.
                process.wait(timeout=.3)
            except (OSError, subprocess.SubprocessError):
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=.3)
                except (OSError, subprocess.SubprocessError):
                    pass
        finally:
            try:
                if process.stdout:
                    process.stdout.close()
            except (OSError, ValueError):
                pass


class PreparedRecordingStop:
    """Refresh read-only stop checks before the deadline, then hand them off once."""
    MAX_AGE = 2.0

    def __init__(self, follower, task, hwnd, can_prepare=None, on_status=None):
        self.follower, self.task, self.hwnd = follower, task, int(hwnd)
        self.can_prepare, self.on_status = can_prepare, on_status
        self._state_lock = threading.RLock()
        self._stop_refresh = threading.Event()
        self._transport = None
        self.context = None
        self._snapshot = None
        self._claimed = self._cancelled = self._dispatched = False
        threading.Thread(target=self._prepare, daemon=True).start()

    def _allowed(self):
        try:
            return self.can_prepare is None or bool(self.can_prepare())
        except Exception:
            return False

    def _fresh_locked(self):
        return (not self._cancelled and not self._dispatched and self._snapshot is not None
                and self._transport is not None and self._transport.alive
                and 0 <= time.monotonic() - self._snapshot['observed_at'] <= self.MAX_AGE)

    @property
    def ready(self):
        with self._state_lock:
            return self._fresh_locked()

    def claim(self):
        # UI callers never wait for a subprocess, request or preparation thread.
        with self._state_lock:
            if self._claimed or not self._fresh_locked():
                return False
            self._claimed = True
            self._stop_refresh.set()
            return True

    def _close_transport(self, transport):
        if transport is not None:
            threading.Thread(target=transport.close, daemon=True).start()

    def cancel(self):
        with self._state_lock:
            if self._cancelled:
                return
            self._cancelled = True
            self._stop_refresh.set()
            transport = self._transport
        self._close_transport(transport)

    def _cancel_unclaimed(self):
        with self._state_lock:
            if self._claimed or self._cancelled:
                return
            self._cancelled = True
            self._stop_refresh.set()
            transport = self._transport
        self._close_transport(transport)

    def _accept_snapshot(self, report, data, observed_at):
        if (not isinstance(report, dict) or not isinstance(data, dict) or data.get('error')
                or report.get('status') != 'stop_action' or not report.get('is_host')
                or (report.get('hwnd'), report.get('pid')) != self.context
                or report.get('task_id') != self.task
                or not re.fullmatch(r'[A-Z0-9]{6}', str(report.get('room_code') or ''))
                or not str(report.get('player_id') or '')
                or str(data.get('host') or '') != str(report.get('player_id'))):
            return None
        session = data.get('current_session')
        server_task = session if isinstance(session, str) else next(
            (session[key] for key in ('task', 'task_id', 'session_id')
             if isinstance(session, dict) and session.get(key)), None)
        if str(server_task or '') != self.task:
            return None
        return dict(observed_at=observed_at, room=report['room_code'], player=str(report['player_id']))

    def _prepare(self):
        try:
            if not self._allowed() or self._stop_refresh.is_set():
                return
            context = _owned_uia_context(1)
            if context is None or context[0] != self.hwnd:
                return
            with self._state_lock:
                self.context = tuple(context)
                if self._cancelled:
                    return
            transport = _PreparedStopProcess(self.task, self.context)
            with self._state_lock:
                self._transport = transport
                cancelled = self._cancelled
            if cancelled:
                self._close_transport(transport)
                return
            first = True
            while not self._stop_refresh.is_set() and self._allowed():
                if _owned_uia_context(0) != self.context:
                    return
                report = transport.request('probe', timeout=2.5 if first else .4)
                first = False
                observed_at = time.monotonic()
                data = fetch_room_status(str(report.get('room_code') or '')) if isinstance(report, dict) else None
                snapshot = self._accept_snapshot(report, data, observed_at)
                with self._state_lock:
                    if self._claimed or self._cancelled:
                        return
                    self._snapshot = snapshot
                if not transport.alive:
                    return
                self._stop_refresh.wait(.4)
        except Exception:
            pass  # Cold verified stop remains available if preparation fails.
        finally:
            self._cancel_unclaimed()

    def dispatch_once(self, task, hwnd, can_stop, timeout):
        """Return (attempted, report, context); ambiguous writes consume permission."""
        with self._state_lock:
            if (self._dispatched and task == self.task
                    and (hwnd is None or int(hwnd) == self.hwnd)):
                # Retrying the same consumed handoff must not re-enter the cold
                # fallback and toggle again, even after its helper was closed.
                return True, None, self.context
            if (not self._claimed or not self._fresh_locked() or task != self.task
                    or (hwnd is not None and int(hwnd) != self.hwnd)):
                return False, None, None
            snapshot, transport, context = dict(self._snapshot), self._transport, self.context
        try:
            if not can_stop() or _owned_uia_context(0) != context:
                return False, None, None
            with self._state_lock:
                if not self._fresh_locked():
                    return False, None, None
                self._dispatched = True  # Set before writing; a lost reply is never retried.
            report = transport.request('invoke', room=snapshot['room'], player=snapshot['player'],
                                       timeout=min(1.5, max(.01, timeout)))
            return True, report, context
        except Exception:
            with self._state_lock:
                attempted = self._dispatched
            return attempted, None, context


def prepare_recording_stop(follower, active_task_id, hwnd, can_prepare=None, on_status=None):
    """Start bounded, action-free preparation without blocking the GUI thread."""
    task = str(active_task_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', task) or not hwnd:
        return None
    return PreparedRecordingStop(follower, task, hwnd, can_prepare, on_status)


def stop_recording_verified(follower, active_task_id, hwnd=None, max_retry=4, timeout=None,
                            allow_armed=False, can_stop=None, on_status=None, prepared=None):
    """End the verified Host round once, closing only its private prepared helper."""
    try:
        return _stop_recording_verified(follower, active_task_id, hwnd, max_retry, timeout,
                                        allow_armed, can_stop, on_status, prepared)
    finally:
        if prepared is not None:
            try:
                prepared.cancel()
            except Exception:
                pass


def _stop_recording_verified(follower, active_task_id, hwnd=None, max_retry=4, timeout=None,
                             allow_armed=False, can_stop=None, on_status=None, prepared=None):
    """End this owned Host round once, without routing F10 through foreground.

    Native Host/task identity and the live server session must agree before the
    semantic End round button is invoked. Local recorder stop/kept logs alone
    cannot prove the room ended. An uncertain dispatch is never repeated.
    """
    task = str(active_task_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', task):
        return False

    def notify(message, tag='WAIT'):
        if on_status:
            try:
                on_status(message, tag)
            except Exception:
                pass

    def allowed():
        try:
            return can_stop is None or bool(can_stop())
        except Exception:
            return False

    def matching(value):
        return isinstance(value, (tuple, list)) and len(value) >= 2 and str(value[0]).strip() == task

    try:
        seconds = max(1, float(timeout)) if timeout is not None else max(1, max_retry) * 1.5
        if not math.isfinite(seconds):
            return False
    except (TypeError, ValueError):
        return False
    if not allowed():
        return False
    deadline = time.monotonic() + seconds
    context, attempted, announced = None, False, False
    prepared_checked = False
    while time.monotonic() < deadline:
        if not allowed():
            return False
        follower.check_new_lines()
        recording = getattr(follower, 'current_recording', None)
        armed = getattr(follower, 'last_armed', None)
        stopped = getattr(follower, 'current_stopped', None)
        result = getattr(follower, 'last_round_result', None)
        if recording and recording != task:
            return False
        if (recording is None and not armed and matching(result)
                and str(result[1]).strip().lower() not in ('', 'ok')):
            # A room-final result is sufficient and must not issue another
            # toggle or depend on a fresh HTTP request after completion.
            notify(f'Odx đã xác nhận kết thúc round [{task}].', 'SUCCESS')
            return True
        known = recording == task or matching(stopped) or matching(result) or (allow_armed and armed == task)
        if not known:
            if not allow_armed:
                return False
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if prepared is not None and not prepared_checked:
            prepared_checked = True
            sent, response, warm_context = prepared.dispatch_once(task, hwnd, allowed, remaining)
            if sent:
                attempted, context = True, warm_context
                if response and (response.get('hwnd') != context[0] or response.get('pid') != context[1]):
                    return False
                notify(f'Đã yêu cầu Kết thúc round một lần trên Odx [{task}]; chờ xác nhận dừng.', 'HOST')
                if response and response.get('status') in {'wrong_identity', 'blocked', 'error'} and not response.get('invoked'):
                    return False
        if context is None:
            context = _owned_uia_context(min(1, remaining))
            if context is None or (hwnd is not None and int(hwnd) != context[0]):
                notify('Không xác nhận được đúng cửa sổ Host Odx để kết thúc round.', 'ERROR')
                return False
            context = tuple(context)
        if _owned_uia_context(0) != context:
            return False
        report = _host_stop_report(run_ps_script(
            _host_stop_script(task, allow_armed, False, context), timeout=min(3, max(.01, remaining))))
        if not allowed():
            return False
        if report and (report.get('hwnd') != context[0] or report.get('pid') != context[1]):
            return False
        if not report or report.get('status') == 'unavailable':
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
            continue
        if (report.get('status') in {'wrong_identity', 'blocked', 'error'} or not report.get('is_host') or
                report.get('task_id') != task):
            notify('Odx không ở đúng Host/task hoặc nút hiện tại không phải Kết thúc round; giữ nguyên Odx.', 'ERROR')
            return False
        room, identity = str(report.get('room_code') or ''), str(report.get('player_id') or '')
        data = fetch_room_status(room)
        if not allowed() or time.monotonic() >= deadline:
            return False
        if not isinstance(data, dict) or data.get('error'):
            if not announced:
                notify('Chưa xác nhận được máy chủ room; đang chờ để kết thúc đúng phiên Odx.', 'WAIT')
                announced = True
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
            continue
        if str(data.get('host') or '') != identity:
            return False
        session = data.get('current_session')
        if session:
            server_task = session if isinstance(session, str) else next(
                (session[key] for key in ('task', 'task_id', 'session_id') if isinstance(session, dict) and session.get(key)), None)
            if server_task is not None and str(server_task) != task:
                return False
        local_ended = recording is None and (matching(stopped) or matching(result))
        if session is None and (report['status'] in {'stopped', 'ending'} or local_ended):
            notify(f'Odx đã xác nhận kết thúc round [{task}]; đang chờ lưu/kiểm định.', 'SUCCESS')
            return True
        if session and report['status'] == 'stop_action' and not attempted:
            if not allowed() or _owned_uia_context(0) != context or time.monotonic() >= deadline:
                return False
            attempted = True  # Set before dispatch; a lost reply cannot toggle a new round.
            response = _host_stop_report(run_ps_script(
                _host_stop_script(task, allow_armed, True, context, expected_room=room, expected_player=identity),
                timeout=min(5, max(.01, deadline - time.monotonic()))))
            if response and (response.get('hwnd') != context[0] or response.get('pid') != context[1]):
                return False
            notify(f'Đã yêu cầu Kết thúc round một lần trên Odx [{task}]; chờ xác nhận dừng.', 'HOST')
            if response and response.get('status') in {'wrong_identity', 'blocked', 'error'} and not response.get('invoked'):
                return False
        if not announced and (attempted or report['status'] == 'ending' or session is None):
            notify('Đang chờ Odx kết thúc round; không gửi lại F10 và không đóng cửa sổ.', 'WAIT')
            announced = True
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    notify(('Đã gửi lệnh nhưng chưa xác nhận Odx kết thúc round.' if attempted else
            'Chưa gửi được lệnh Kết thúc round an toàn vào đúng Host Odx.') + ' Giữ nguyên Odx.', 'ERROR')
    return False

# ======================================================================
# LOG FOLLOWER
# ======================================================================

def _windows_command_line_arguments(command_line):
    """Parse a native command line without evaluating any command text."""
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    arguments = shell.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(arguments)


def _query_performance_counter_ms():
    """Read the same Windows QPC clock used by Odx's video grid origin."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.QueryPerformanceFrequency.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
    kernel.QueryPerformanceFrequency.restype = wintypes.BOOL
    kernel.QueryPerformanceCounter.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
    kernel.QueryPerformanceCounter.restype = wintypes.BOOL
    frequency, counter = ctypes.c_longlong(), ctypes.c_longlong()
    if (not kernel.QueryPerformanceFrequency(ctypes.byref(frequency)) or frequency.value <= 0
            or not kernel.QueryPerformanceCounter(ctypes.byref(counter))):
        raise ctypes.WinError(ctypes.get_last_error())
    return counter.value * 1000.0 / frequency.value


def _is_live_recorder_process(pid, expected_executable):
    """Check the exact live process after taking a CIM command-line snapshot."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(process, ctypes.byref(exit_code)) or exit_code.value != 259:
            return False
        size = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(size.value)
        return bool(kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size))
                    and os.path.normcase(os.path.realpath(path.value)) == expected_executable)
    finally:
        kernel.CloseHandle(process)


def get_recording_start_time(task_id):
    """Return this owned recorder's true video start on Python's monotonic clock.

    Odx logs ``recording ... into`` while its recorder is still preparing for
    the room countdown. The live recorder command's NTP-aligned QPC grid origin
    is its video start, including when that instant is still in the future.
    Completed capture files are deliberately never used as live evidence.
    Call this bounded read from a worker, not Tk's event thread.
    """
    task = str(task_id or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", task):
        return None
    try:
        result = run_ps_script("""
$recorders = @(Get-CimInstance Win32_Process -Filter "Name = 'odx.exe'" | ForEach-Object {
    [PSCustomObject]@{pid=[int]$_.ProcessId; path=$_.ExecutablePath; command=$_.CommandLine}
})
ConvertTo-Json -InputObject $recorders -Compress
""", timeout=4)
        if not result or result.returncode != 0:
            return None
        records = json.loads(result.stdout.strip().lstrip("\ufeff"))
        if not isinstance(records, list):
            return None
        expected_executable = os.path.normcase(os.path.realpath(os.path.join(ODX_ROOT, "bin", "odx.exe")))
        expected_capture = os.path.normcase(os.path.realpath(os.path.join(ODX_ROOT, "data", "captures", task)))
        origins = []
        for record in records:
            if not isinstance(record, dict):
                continue
            pid, executable, command = record.get("pid"), record.get("path"), record.get("command")
            if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                    or not isinstance(executable, str) or not isinstance(command, str)
                    or os.path.normcase(os.path.realpath(executable)) != expected_executable
                    or not _runtime.owns_pid(pid)
                    or not _is_live_recorder_process(pid, expected_executable)):
                continue
            arguments = _windows_command_line_arguments(command)
            if (len(arguments) < 2 or arguments[1].lower() != "record"
                    or os.path.normcase(os.path.realpath(arguments[0])) != expected_executable
                    or arguments.count("--out") != 1
                    or arguments.count("--grid-origin-qpc-ms") != 1):
                continue
            output_index, origin_index = arguments.index("--out") + 1, arguments.index("--grid-origin-qpc-ms") + 1
            if output_index >= len(arguments) or origin_index >= len(arguments):
                continue
            if os.path.normcase(os.path.realpath(arguments[output_index])) != expected_capture:
                continue
            try:
                origin = float(arguments[origin_index])
            except ValueError:
                continue
            if not math.isfinite(origin) or origin <= 0:
                continue
            # Reject an exited or replaced PID after reading its command line.
            if _runtime.owns_pid(pid) and _is_live_recorder_process(pid, expected_executable):
                origins.append(origin)
        if len(origins) != 1:
            return None
        current_qpc_ms = _query_performance_counter_ms()
        if not math.isfinite(current_qpc_ms):
            return None
        return time.monotonic() + (origins[0] - current_qpc_ms) / 1000.0
    except (OSError, AttributeError, ValueError, TypeError, IndexError, ctypes.ArgumentError):
        return None


def _native_log_monotonic_time(line):
    """Convert Odx's local log timestamp to monotonic time at observation."""
    match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{1,6})\s", line)
    if not match:
        return None
    try:
        epoch = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f").timestamp()
        return time.monotonic() + epoch - time.time()
    except (OSError, OverflowError, ValueError):
        return None

def get_latest_log_path():
    log_dir = os.path.join(ODX_ROOT, "data", "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    return os.path.join(log_dir, f"odx-app-{today}.log")

class LiveLogFollower:
    def __init__(self, log_path=None):
        self._read_lock = threading.RLock()
        self._auto_log_path = not log_path
        self.log_path = log_path or get_latest_log_path()
        self.last_pos = 0
        self._file_identity = None
        self._anchor = b""
        self._clear_state()
        self.init_position()

    def _clear_state(self):
        self.room_code = None
        self.last_armed = None
        self.current_recording = None
        self.current_stopped = None
        self.current_stopped_at = None
        self.last_round_result = None
        self.last_all_ok = None
        self.last_kept_path = None
        self.id_conflict = None
        self.clock_drift_error = None
        self.task_in_use_error = None

    def init_position(self):
        self.reset_to_end()

    def reset_to_end(self):
        with self._read_lock:
            if self._auto_log_path:
                self.log_path = get_latest_log_path()
            self._clear_state()
            self.last_pos = 0
            self._file_identity = None
            self._anchor = b""
            try:
                with open(self.log_path, "rb") as log_file:
                    stat = os.fstat(log_file.fileno())
                    self._file_identity = (stat.st_dev, stat.st_ino)
                    self.last_pos = stat.st_size
                    log_file.seek(max(0, self.last_pos - 64))
                    self._anchor = log_file.read(self.last_pos - log_file.tell())
            except OSError:
                pass

    def check_new_lines(self, from_start=False):
        with self._read_lock:
            if from_start:
                self.last_pos = 0
                self._anchor = b""
            lines = self._read_complete_lines()
            if self._auto_log_path:
                latest = get_latest_log_path()
                if latest != self.log_path and os.path.exists(latest):
                    # Drain the previous day's tail before following the new file.
                    # Recording state intentionally survives daily log rotation.
                    self.log_path = latest
                    self.last_pos = 0
                    self._file_identity = None
                    self._anchor = b""
                    lines.extend(self._read_complete_lines())
            return lines

    def _read_complete_lines(self):
        lines = []
        try:
            with open(self.log_path, "rb") as log_file:
                stat = os.fstat(log_file.fileno())
                identity = (stat.st_dev, stat.st_ino)
                replaced = self._file_identity is not None and identity != self._file_identity
                truncated = stat.st_size < self.last_pos
                if not replaced and not truncated and self._anchor:
                    log_file.seek(self.last_pos - len(self._anchor))
                    truncated = log_file.read(len(self._anchor)) != self._anchor
                if replaced or truncated:
                    self.last_pos = 0
                    self._anchor = b""
                self._file_identity = identity
                log_file.seek(self.last_pos)
                data = log_file.read()
                complete_length = data.rfind(b"\n") + 1
                for raw_line in data[:complete_length].splitlines(keepends=True):
                    line = raw_line.decode("utf-8", errors="replace")
                    self.parse_line(line)
                    lines.append(line.strip())
                    self.last_pos += len(raw_line)
                log_file.seek(max(0, self.last_pos - 64))
                self._anchor = log_file.read(self.last_pos - log_file.tell())
        except OSError:
            pass
        return lines

    def parse_line(self, line):
        with self._read_lock:
            self._parse_line_unlocked(line)

    def _parse_line_unlocked(self, line):
        m_room = re.search(r"in room ([A-Z0-9]{6})", line)
        if m_room:
            self.room_code = m_room.group(1).strip()

        m_armed = re.search(r"armed for (.*)", line)
        if m_armed:
            self.last_armed = m_armed.group(1).strip()

        m_rec = re.search(r"recording (.*?) into", line)
        if m_rec:
            self.current_recording = m_rec.group(1).strip()
            self.current_stopped = None
            self.current_stopped_at = None

        m_stop = re.search(r"stop (.*?) \((.*?)\)", line)
        if m_stop:
            stopped_task = m_stop.group(1).strip()
            # A late stop for an older task must not replace the newer active
            # capture, but a matching terminal event also ends its armed flag.
            if self.current_recording == stopped_task or (
                    self.current_recording is None and self.last_armed in (None, stopped_task)):
                self.current_stopped = (stopped_task, m_stop.group(2).strip())
                self.current_stopped_at = _native_log_monotonic_time(line)
            self._clear_terminal_capture_activity(stopped_task)

        m_kept = re.search(r"([a-zA-Z0-9_-]+) done: kept in (.*?); ok", line)
        if m_kept:
            self.last_round_result = (m_kept.group(1).strip(), "ok")
            self.last_kept_path = m_kept.group(2).strip()

        m_cancelled = re.search(r"([a-zA-Z0-9_-]+): round cancelled, recording kept locally only \((.*)\)", line)
        if m_cancelled:
            cancelled_task = m_cancelled.group(1)
            self.last_round_result = (cancelled_task, "cancelled")
            self.last_kept_path = m_cancelled.group(2).strip()
            self._clear_terminal_capture_activity(cancelled_task)
            if self.last_all_ok == cancelled_task:
                self.last_all_ok = None

        if "is in use on another machine" in line:
            self.id_conflict = line

        if "the Windows clock is" in line and "off NTP" in line:
            self.clock_drift_error = line

        if "is still being recorded or saved" in line:
            self.task_in_use_error = line.strip()

        m_done = re.search(r"round ([a-zA-Z0-9_-]+): (all ok|.*)", line)
        if m_done:
            t_name = m_done.group(1).strip()
            t_res = m_done.group(2).strip()
            self.last_round_result = (t_name, t_res)
            self._clear_terminal_capture_activity(t_name)
            if "all ok" in t_res.lower():
                self.last_all_ok = t_name

    def _clear_terminal_capture_activity(self, task):
        """Release only the activity ended by this task's terminal log event."""
        if self.current_recording == task:
            self.current_recording = None
        if self.last_armed == task:
            self.last_armed = None

# ======================================================================
# ROOM TRACKING VIA ORCHESTRATOR API
# ======================================================================
def fetch_room_status(room_code, orchestrator_url=ORCHESTRATOR_URL, access_key=ACCESS_KEY):
    if not room_code:
        return None
    clean_code = str(room_code).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6}", clean_code):
        return {"error": "INVALID_CODE", "message": "Room code must contain six ASCII letters or digits"}
    url = f"{orchestrator_url.rstrip('/')}/rooms/{clean_code}"
    headers = {"Authorization": f"Bearer {access_key}"}
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=2.5, context=ctx) as r:
            data = json.loads(r.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Room status response must be a JSON object")
            return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": "NOT_FOUND", "status_code": 404, "message": "Room was not found"}
        return {"error": "HTTP_ERROR", "status_code": e.code, "message": f"Room server returned HTTP {e.code}"}
    except (ssl.SSLError, urllib.error.URLError) as e:
        reason = getattr(e, "reason", e)
        category = "TLS_ERROR" if isinstance(reason, ssl.SSLError) else "NETWORK_ERROR"
        return {"error": category, "message": str(reason)}
    except (ValueError, UnicodeError) as e:
        return {"error": "INVALID_RESPONSE", "message": str(e)}
    except (OSError, TimeoutError) as e:
        return {"error": "NETWORK_ERROR", "message": str(e)}

def format_member_table(room_code, host_id, members, max_expected=4):
    lines = []
    lines.append("=" * 72)
    ready_cnt = sum(1 for idx, m in enumerate(members) if m.get("ready") or (str(m.get("player_id")) == str(host_id) or (idx == 0 and not host_id)))
    total_cnt = len(members)
    lines.append(f"  [PHONG: {room_code}] TRANG THAI THANH VIEN REAL-TIME ({ready_cnt}/{max_expected} SAN SANG)")
    lines.append("-" * 72)
    
    for idx in range(max_expected):
        if idx < len(members):
            m = members[idx]
            pid = str(m.get("player_id", ""))
            name = str(m.get("name") or pid)
            is_h = (pid == str(host_id)) or (idx == 0 and not host_id)
            role_tag = "[HOST]  " if is_h else "[PLAYER]"
            
            if is_h:
                st = "[✓] SAN SANG (HOST TRUONG NHOM)"
            elif m.get("ready"):
                st = "[✓] SAN SANG (READY)"
            else:
                st = "[..] DANG CHO (CHUA SAN SANG)"
            
            if m.get("problem"):
                st += f"  [!] LOI: {m.get('problem')}"
                
            p_info = f"{name} (ID: {pid})"
            lines.append(f"  {idx+1}. {role_tag} {p_info:<28} : {st}")
        else:
            lines.append(f"  {idx+1}. [CHO...]   Dang cho nguoi choi thu {idx+1} vao phong...")
            
    lines.append("-" * 72)
    if total_cnt >= max_expected and ready_cnt >= max_expected:
        lines.append(f"  >> TRANG THAI PHONG: DU {max_expected} NGUOI SAN SANG 100% (READY) [OK]")
    else:
        lines.append(f"  >> DANG CHO: {ready_cnt}/{max_expected} nguoi choi san sang...")
    lines.append("=" * 72)
    return "\n".join(lines)

def format_hud_roster(room_code, host_id, members, expected=4):
    parts = []
    ready_cnt = sum(1 for idx, m in enumerate(members) if m.get("ready") or (str(m.get("player_id")) == str(host_id) or (idx == 0 and not host_id)))
    for idx, m in enumerate(members[:4]):
        pid = str(m.get("player_id", ""))
        name = str(m.get("name") or pid)
        is_h = (pid == str(host_id)) or (idx == 0 and not host_id)
        tag = "[H]" if is_h else ""
        mark = "✓" if (is_h or m.get("ready")) else ".."
        parts.append(f"{name}{tag}({mark})")
    
    joined = " | ".join(parts) if parts else "Dang ket noi..."
    return f"[{room_code}] {joined} [{ready_cnt}/{expected} READY]  [F10: BAT DAU]"

class RoomTracker:
    def __init__(self, room_code, overlay=None, on_change_callback=None, expected_players=4):
        self.room_code = str(room_code).strip().upper()
        self.overlay = overlay
        self.on_change_callback = on_change_callback
        self.expected_players = expected_players
        self.latest_data = None
        self.last_sig = None
        self.is_running = False
        self.is_paused = False
        self.thread = None
        self.table_printed = False
        self.last_error = None
        self.last_success_at = None

    def start(self, interval=1.2):
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._run_loop, args=(interval,), daemon=True)
        self.thread.start()

    def stop(self):
        self.is_running = False

    def pause_hud(self):
        self.is_paused = True

    def resume_hud(self):
        self.is_paused = False
        self._force_hud_update = True

    def is_all_ready(self, expected=4):
        data = self.latest_data
        last_success_at = self.last_success_at
        if not data:
            return False
        if last_success_at is None or time.monotonic() - last_success_at > 10:
            return False
        members = data.get("members", [])
        if len(members) < expected:
            return False
        host_id = data.get("host")
        for idx, m in enumerate(members):
            is_h = (str(m.get("player_id")) == str(host_id)) or (idx == 0 and not host_id)
            if m.get("problem"):
                return False
            if not is_h and not m.get("ready"):
                return False
        return True

    def _run_loop(self, interval):
        while self.is_running:
            try:
                data = fetch_room_status(self.room_code)
                if not self.is_running:
                    break
                if data and isinstance(data.get("members"), list):
                    self.latest_data = data
                    self.last_error = None
                    self.last_success_at = time.monotonic()
                    host_id = data.get("host")
                    members = data.get("members", [])
                    
                    sig = f"{host_id}:" + ",".join(f"{m.get('player_id')}:{m.get('ready')}:{m.get('state')}:{m.get('problem')}" for m in members)
                    sig_changed = (sig != self.last_sig)
                    if sig_changed:
                        self.last_sig = sig
                        if not self.table_printed:
                            print("\n" + format_member_table(self.room_code, host_id, members, self.expected_players))
                            self.table_printed = True
                        else:
                            ready_cnt = sum(1 for idx, m in enumerate(members) if m.get("ready") or (str(m.get("player_id")) == str(host_id) or (idx == 0 and not host_id)))
                            m_sum = " | ".join(f"{m.get('name') or m.get('player_id')}:{'✓' if (m.get('ready') or str(m.get('player_id'))==str(host_id) or idx==0) else '..'}" for idx, m in enumerate(members[:4]))
                            sys.stdout.write(f"\r>> [REAL-TIME PHONG {self.room_code}] ({ready_cnt}/{self.expected_players} SAN SANG) [{m_sum}]  ")
                            sys.stdout.flush()

                        if self.on_change_callback:
                            try:
                                self.on_change_callback(self.room_code, host_id, members)
                            except Exception:
                                pass
                            
                    # Chi cap nhat HUD khi chua vao ghi hinh va chi khi co thay doi (tranh giat/nhap nhay HUD)
                    if not self.is_paused and self.overlay:
                        if sig_changed or getattr(self, "_force_hud_update", False):
                            self._force_hud_update = False
                            hud_text = format_hud_roster(self.room_code, host_id, members, self.expected_players)
                            all_ready = self.is_all_ready(self.expected_players)
                            fg = "#A7F3D0" if all_ready else "#FCD34D"
                            self.overlay.show_roster_hud(hud_text, fg, roster=True)
                else:
                    message = (data.get("message") or data.get("error")) if isinstance(data, dict) else None
                    self._invalidate_status(message or "Room status unavailable")
            except Exception as e:
                self._invalidate_status(str(e))
            time.sleep(interval)

    def _invalidate_status(self, message):
        """A failed request cannot leave an old green Ready roster active."""
        message = str(message)
        changed = self.last_error != message or self.latest_data is not None
        self.latest_data = None
        self.last_success_at = None
        self.last_error = message
        self.last_sig = None
        if changed:
            log_event("ROOM_STATUS_ERROR", level="WARN", source="ROOM", data={"room_code": self.room_code, "message": message})
            if self.on_change_callback:
                try:
                    self.on_change_callback(self.room_code, None, [])
                except Exception:
                    pass
            if self.overlay and not self.is_paused:
                self.overlay.show_roster_hud(f"[{self.room_code}] KHONG DOC DUOC TRANG THAI PHONG: {message}", "#EF4444")

# ======================================================================
# AUTO-HEAL CAMERA NULLS & VALIDATION ENGINE
# ======================================================================
def heal_camera_nulls_if_needed(target_path):
    """Explicit repair with preservation of original data; never called by validation."""
    from odx_validation import heal_camera_nulls_if_needed as repair
    return repair(target_path)


def validate_and_inspect_capture(target_path, is_room_mode=True, on_message=None):
    from odx_validation import validate_and_inspect_capture as validate
    return validate(target_path, is_room_mode=is_room_mode, odx_root=ODX_ROOT, on_message=on_message)


def validate_finalized_capture(task_id, follower, should_continue=None, on_message=None):
    from odx_validation import validate_finalized_capture as validate
    return validate(task_id, follower, odx_root=ODX_ROOT,
                    should_continue=should_continue, on_message=on_message)
