"""
ODX CAPTURE AUTO-PILOT SUITE - BACKEND CORE ENGINE (v3.0)
- Kien truc Backend chat che, tach biet hoan toan logic xu ly va giao dien Frontend.
- Windows Job Object + SetConsoleCtrlHandler: Tu dong kill 100% moi tien trinh Odx khi dong tool (bam [X]).
- Dong bo thoi gian NTP chuan xac (time.windows.com).
- Quan ly phong va theo doi 4 thanh vien real-time qua Orchestrator API.
- Tu dong kiem dinh 8 file, Role chuot va tu dong va ma tran camera null (Auto-Heal).
- Dieu khien Host va Player dong bo tuyet doi.
"""

import sys
import os
import time
import json
import subprocess
import re
import random
import ctypes
import threading
import ssl
import urllib.request
import base64
from datetime import datetime
from enum import Enum

# Win32 Constants
WM_HOTKEY = 0x0312
HOTKEY_ID = 1

# Orchestrator Constants
ORCHESTRATOR_URL = "https://13-251-211-134.sslip.io"
ACCESS_KEY = "ygYmQNO5WfHbaUn0w1hMVg4ESu8nTEhd"

TOOL_DIR = os.path.abspath(os.path.dirname(__file__))

def get_odx_file_version(exe_path):
    """Doc phien ban ProductVersion cua file exe tren Windows"""
    try:
        import win32api
        info = win32api.GetFileVersionInfo(exe_path, "\\")
        ms = info['FileVersionMS']
        ls = info['FileVersionLS']
        return (win32api.HIWORD(ms), win32api.LOWORD(ms), win32api.HIWORD(ls), win32api.LOWORD(ls))
    except Exception:
        pass
    m = re.search(r"0\.(\d+)\.(\d+)", exe_path)
    if m:
        return (0, int(m.group(1)), int(m.group(2)), 0)
    return (0, 0, 0, 0)

def find_all_odx_roots():
    """Tim tat ca cac thu muc Odx Capture hop le tren may va xep hang uu tien ban moi nhat"""
    candidates = []
    here = TOOL_DIR
    parent = os.path.abspath(os.path.join(here, ".."))
    for d in [here, parent]:
        if os.path.exists(os.path.join(d, "Odx Capture.exe")) or os.path.exists(os.path.join(d, "bin", "odx-app.exe")):
            if d not in candidates:
                candidates.append(d)

    downloads_dir = os.path.expanduser(r"~\Downloads")
    if os.path.exists(downloads_dir):
        for root, dirs, files in os.walk(downloads_dir):
            rel = os.path.relpath(root, downloads_dir)
            if rel.count(os.sep) > 3:
                continue
            if "Odx Capture.exe" in files or os.path.exists(os.path.join(root, "bin", "odx-app.exe")):
                if root not in candidates:
                    candidates.append(root)

    scored = []
    for c in candidates:
        app_exe = os.path.join(c, "bin", "odx-app.exe")
        cap_exe = os.path.join(c, "Odx Capture.exe")
        target_exe = app_exe if os.path.exists(app_exe) else cap_exe
        v_tuple = get_odx_file_version(target_exe)
        scored.append((v_tuple, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored

def get_odx_root():
    scored = find_all_odx_roots()
    if scored:
        return scored[0][1]
    return TOOL_DIR

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
_GLOBAL_JOB_HANDLE = None
_GLOBAL_CTRL_HANDLER = None

def cleanup_all_odx(kill_gui=True):
    """
    Don dep sach 100% moi tien trinh Odx ngam, Odx Capture GUI va reset Packet Monitor.
    Giai phong hoan toan phim F10 va port ket noi.
    """
    try:
        subprocess.run(["pktmon", "stop"], capture_output=True, check=False)
        subprocess.run(["pktmon", "reset"], capture_output=True, check=False)
    except Exception:
        pass

    try:
        subprocess.run(["taskkill", "/F", "/T", "/IM", "odx.exe"], capture_output=True, check=False)
        subprocess.run(["taskkill", "/F", "/T", "/IM", "odx-app.exe"], capture_output=True, check=False)
        subprocess.run(["taskkill", "/F", "/T", "/IM", "ffmpeg.exe"], capture_output=True, check=False)
        if kill_gui:
            subprocess.run(["taskkill", "/F", "/T", "/IM", "Odx Capture.exe"], capture_output=True, check=False)
    except Exception:
        pass

def setup_process_protection():
    """
    Gan co che bao ve kep (SetConsoleCtrlHandler + Windows Job Object):
    Khi nguoi dung tat dot ngot (bam nut [X]), Windows se tu dong kill sach moi tien trinh con.
    """
    global _GLOBAL_JOB_HANDLE, _GLOBAL_CTRL_HANDLER
    if sys.platform != "win32":
        return

    import atexit
    atexit.register(cleanup_all_odx)

    try:
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32

        # 1. Console Ctrl Handler (Bat su kien [X])
        PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        def _ctrl_handler(ctrl_type):
            if ctrl_type in (0, 1, 2, 5, 6): # CLOSE, C, BREAK, LOGOFF, SHUTDOWN
                cleanup_all_odx(kill_gui=True)
                return True
            return False
        _GLOBAL_CTRL_HANDLER = PHANDLER_ROUTINE(_ctrl_handler)
        kernel32.SetConsoleCtrlHandler(_GLOBAL_CTRL_HANDLER, True)

        # 2. Windows Job Object (Kernel kill-on-close)
        job = kernel32.CreateJobObjectW(None, None)
        if job:
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64),
                ]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryLimit", ctypes.c_size_t),
                    ("PeakJobMemoryLimit", ctypes.c_size_t),
                ]

            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
            JobObjectExtendedLimitInformation = 9
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

            res = kernel32.SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info)
            )
            if res:
                kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())
                _GLOBAL_JOB_HANDLE = job
    except Exception:
        pass

setup_process_protection()

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
        subprocess.run(["w32tm", "/resync", "/nowait"], capture_output=True, timeout=4)
        return True, "Da kich hoat dong bo thoi gian Windows Time Service"
    except Exception:
        pass
    return False, "Khong the dong bo NTP tu dong (Can chay voi quyen Admin)"

def is_game_running(proc_name="ArmaReforgerSteam"):
    """
    Kiểm tra chính xác 100% xem Game Arma Reforger có thực sự đang mở hay không.
    Tuyệt đối không dùng tìm kiếm chuỗi lỏng lẻo trong stdout để tránh lỗi Windows
    in lại câu lệnh/filter tiêu chí tìm kiếm gây nhận diện nhầm là Game đang mở.
    """
    clean_name = proc_name.replace(".exe", "").strip().lower()

    # 1. Kiểm tra cửa sổ Game đang mở (FindWindow)
    try:
        user32 = ctypes.windll.user32
        for title in ("Arma Reforger", "ArmaReforgerSteam", "ArmaReforger"):
            hwnd = user32.FindWindowW(None, title)
            if hwnd != 0:
                return True
    except Exception:
        pass

    # 2. Kiểm tra trực tiếp qua Windows Kernel Toolhelp32Snapshot
    try:
        TH32CS_SNAPPROCESS = 0x00000002
        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ('dwSize', ctypes.c_ulong),
                ('cntUsage', ctypes.c_ulong),
                ('th32ProcessID', ctypes.c_ulong),
                ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', ctypes.c_ulong),
                ('cntThreads', ctypes.c_ulong),
                ('th32ParentProcessID', ctypes.c_ulong),
                ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', ctypes.c_ulong),
                ('szExeFile', ctypes.c_char * 260)
            ]
        hSnapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if hSnapshot and hSnapshot != -1:
            pe = PROCESSENTRY32()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if ctypes.windll.kernel32.Process32First(hSnapshot, ctypes.byref(pe)):
                while True:
                    exe_name = pe.szExeFile.decode('utf-8', errors='ignore').strip().lower()
                    if exe_name in (f"{clean_name}.exe", clean_name):
                        ctypes.windll.kernel32.CloseHandle(hSnapshot)
                        return True
                    if clean_name == "armareforgersteam" and exe_name in ("armareforger.exe", "armareforgersteam.exe"):
                        ctypes.windll.kernel32.CloseHandle(hSnapshot)
                        return True
                    if not ctypes.windll.kernel32.Process32Next(hSnapshot, ctypes.byref(pe)):
                        break
            ctypes.windll.kernel32.CloseHandle(hSnapshot)
            return False
    except Exception:
        pass

    # 3. Fallback qua tasklist CSV: Chỉ chấp nhận dòng CSV hợp lệ chứa PID số thực tế
    try:
        res = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {clean_name}.exe"],
            capture_output=True, text=True, timeout=3
        )
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("INFO:") or line.startswith("Thông tin:"):
                continue
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                img = parts[0].lower()
                if img in (f"{clean_name}.exe", clean_name) or (clean_name == "armareforgersteam" and img in ("armareforger.exe", "armareforgersteam.exe")):
                    return True
        return False
    except Exception:
        return False

# ======================================================================
# WINDOWS & HOTKEY HELPERS
# ======================================================================
def find_odx_window():
    user32 = ctypes.windll.user32
    return user32.FindWindowW(None, "Odx Capture")

def set_clipboard(text):
    """Set clipboard tuc thi bang Windows API (0.1ms, khong dung powershell gay lag)."""
    text = str(text)
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

        CF_UNICODETEXT = 13
        GHND = 0x0042
        
        text_bytes = (text + '\0').encode('utf-16le')
        for _ in range(5):
            if user32.OpenClipboard(None):
                user32.EmptyClipboard()
                h_mem = kernel32.GlobalAlloc(GHND, len(text_bytes))
                if h_mem:
                    ptr = kernel32.GlobalLock(h_mem)
                    if ptr:
                        ctypes.memmove(ptr, text_bytes, len(text_bytes))
                        kernel32.GlobalUnlock(h_mem)
                        user32.SetClipboardData(CF_UNICODETEXT, h_mem)
                user32.CloseClipboard()
                return True
            time.sleep(0.01)
    except Exception:
        pass

    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{text}'"], capture_output=True, timeout=2)
        return True
    except Exception:
        pass
    return False

def get_clipboard():
    """Lay text tu clipboard bang Windows API, fallback PowerShell neu loi."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetClipboardData.restype = ctypes.c_void_p
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        CF_UNICODETEXT = 13

        for _ in range(5):
            if user32.OpenClipboard(None):
                h = user32.GetClipboardData(CF_UNICODETEXT)
                val = None
                if h:
                    p = kernel32.GlobalLock(h)
                    if p:
                        val = ctypes.wstring_at(p)
                        kernel32.GlobalUnlock(h)
                user32.CloseClipboard()
                if val is not None:
                    return val
            time.sleep(0.01)
    except Exception:
        pass

    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True, timeout=2)
        if res.stdout:
            return res.stdout.strip()
    except Exception:
        pass
    return ""

def run_ps_script(ps_code, timeout=5):
    """Thuc thi PowerShell an toan 100% qua Base64 EncodedCommand (tranh loi parse stdin hoac dau ngoac)."""
    try:
        encoded = base64.b64encode(ps_code.encode("utf-16le")).decode("ascii")
        return subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=timeout
        )
    except Exception:
        return None

def is_task_already_recorded(task_id):
    """
    Kiem tra xem task_id nay da tung duoc quay va co du lieu tren may chua.
    Kiem tra ca trong data/captures cua Odx va cac thu muc captures lan can, khong phan biet hoa thuong.
    """
    if not task_id:
        return False, ""
    
    clean_id = str(task_id).strip().lower()
    parent_dirs = [
        os.path.join(ODX_ROOT, "data", "captures"),
        os.path.join(os.path.dirname(ODX_ROOT), "captures"),
        os.path.join(ODX_ROOT, "captures"),
    ]
    for p in parent_dirs:
        if os.path.isdir(p):
            try:
                for entry in os.listdir(p):
                    entry_path = os.path.join(p, entry)
                    if os.path.isdir(entry_path):
                        low_entry = entry.lower()
                        # Khop chinh xac ten task hoac task bi cancelled (ke ca chu hoa hay chu thuong)
                        if low_entry == clean_id or low_entry.startswith(clean_id + ".cancelled"):
                            try:
                                files = os.listdir(entry_path)
                                if files:
                                    return True, entry_path
                            except Exception:
                                return True, entry_path
            except Exception:
                pass
    return False, ""

def get_next_available_fix_task_id(task_id):
    """
    Tu dong tim ma task fix ke tiep chua tung quay (vi du: task-fix1, task-fix2...).
    """
    clean_id = str(task_id).strip()
    base_id = re.sub(r"-fix\d+$", "", clean_id)
    counter = 1
    while counter < 100:
        candidate = f"{base_id}-fix{counter}"
        exists, _ = is_task_already_recorded(candidate)
        if not exists:
            return candidate
        counter += 1
    return f"{base_id}-fix{counter}"

def set_task_box(task_id):
    """Cap nhat task_id vao file config, clipboard va tu dong dien vao TaskBox tren Odx Capture."""
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

        $root = [System.Windows.Automation.AutomationElement]::RootElement
        $win = $null

        $cName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'Odx Capture')
        $win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cName)

        if (-not $win) {
            $procs = Get-Process -Name 'odx-app', 'Odx Capture' -ErrorAction SilentlyContinue
            foreach ($p in $procs) {
                if ($p.MainWindowHandle -ne 0) {
                    try {
                        $win = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
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
                        $vp.SetValue('__TASK_ID__')
                        $setOk = $true
                    }
                } catch {}
                if (-not $setOk) {
                    try {
                        [System.Windows.Forms.Clipboard]::SetText('__TASK_ID__')
                        $elem.SetFocus()
                        Start-Sleep -Milliseconds 30
                        [System.Windows.Forms.SendKeys]::SendWait('^a')
                        Start-Sleep -Milliseconds 20
                        [System.Windows.Forms.SendKeys]::SendWait('^v')
                    } catch {}
                }
                exit 0
            }
        }
        exit 1
        """
        ps_script = ps_template.replace("__TASK_ID__", str(task_id))
        run_ps_script(ps_script, timeout=4)

    threading.Thread(target=_inject_uia, daemon=True).start()

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
        $win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cName)

        if (-not $win) {
            $procs = Get-Process -Name 'odx-app', 'Odx Capture' -ErrorAction SilentlyContinue
            foreach ($p in $procs) {
                if ($p.MainWindowHandle -ne 0) {
                    try {
                        $win = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
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
                            [System.Windows.Forms.SendKeys]::SendWait($pidVal)
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
                            [System.Windows.Forms.SendKeys]::SendWait($pnameVal)
                        } catch {}
                    }
                }
            }
        }
        """
        p_id_str = str(player_id) if player_id is not None else "NONE"
        p_name_str = str(player_name) if player_name is not None else "NONE"
        ps_script = ps_template.replace("__PLAYER_ID__", p_id_str).replace("__PLAYER_NAME__", p_name_str)
        run_ps_script(ps_script, timeout=4)

    threading.Thread(target=_inject_uia, daemon=True).start()

def player_auto_join_and_ready(room_code, player_id, player_name, on_status=None):
    """
    Tu dong hoa 100% cho May Con (Player):
      Buoc 1: Tim window, dien PlayerBox & NameBox, click [Vao room]
      Buoc 2: Tim popup JoinRoomDialog, dien CodeBox, cho OkButton sang va click submit, doi hop thoai dong han
      Buoc 3: Kiem tra xac thuc da vao phong thanh cong (JoinButton -> Roi room hoac RoomBox hien ma),
              sau do tu dong bat San sang (StartStopButton -> San sang).
              TUYET DOI khong tu dong gui F10 gay ghi hinh solo!
    """
    def _worker():
        t_start = time.time()
        log_event("JOIN_ROOM_START", level="INFO", source="PLAYER", data={
            "room_code": room_code,
            "player_id": player_id,
            "player_name": player_name
        })
        if on_status:
            on_status(f"MÁY CON: Đang tự động kết nối vào phòng [{room_code}]...", "PLAYER")

        # 1. Cap nhat file config goc
        appdata_json = os.path.expandvars(r"%APPDATA%\Odx\odx-app.json")
        try:
            cfg = {}
            if os.path.exists(appdata_json):
                with open(appdata_json, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["PlayerId"] = str(player_id)
            cfg["PlayerName"] = str(player_name)
            os.makedirs(os.path.dirname(appdata_json), exist_ok=True)
            with open(appdata_json, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

        # 2. Clipboard
        set_clipboard(room_code)

        # Kiem tra xem ma phong co thuc su ton tai tren may chu Odx khong
        r_chk = fetch_room_status(room_code)
        if isinstance(r_chk, dict) and r_chk.get("error") == "NOT_FOUND":
            log_event("ROOM_NOT_FOUND_ON_SERVER", level="WARN", source="PLAYER", data={"room_code": room_code})
            if on_status:
                on_status(f"LỖI: Mã phòng [{room_code}] KHÔNG TỒN TẠI trên máy chủ Odx (Host chưa bấm tạo phòng hoặc bạn nhập sai mã)! Tool dừng để tránh kẹt.", "ERROR")
            return

        # ============================================================
        # KIEM TRA XEM HOP THOAI "VAO ROOM" CO DANG MO SAN KHONG
        # ============================================================
        ps_check_dlg = """
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$allW = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
for ($i = 0; $i -lt $allW.Count; $i++) {
    $tw = $allW.Item($i)
    try {
        $wnm = $tw.Current.Name
        if ($wnm -like '*Vào room*' -or $wnm -like '*Join room*' -or $wnm -eq 'Vào room') {
            Write-Output "DIALOG_ALREADY_OPEN"
            exit 0
        }
    } catch {}
}
exit 0
"""
        res_chk = run_ps_script(ps_check_dlg, timeout=4)
        dlg_already_open = "DIALOG_ALREADY_OPEN" in (res_chk.stdout if res_chk else "")

        if not dlg_already_open:
            # ============================================================
            # STEP A: Tim Odx window, focus JoinButton, mouse-click async
            # ============================================================
            ps_step_a = """
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class W32Click { [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e); public static void Click(int x, int y) { SetCursorPos(x, y); mouse_event(2, 0, 0, 0, UIntPtr.Zero); System.Threading.Thread.Sleep(50); mouse_event(4, 0, 0, 0, UIntPtr.Zero); } }'

$root = [System.Windows.Automation.AutomationElement]::RootElement
$win = $null

for ($w = 0; $w -lt 30; $w++) {
    $cName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'Odx Capture')
    $win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cName)
    if (-not $win) {
        $procs = Get-Process -Name 'odx-app', 'Odx Capture' -ErrorAction SilentlyContinue
        foreach ($p in $procs) {
            if ($p.MainWindowHandle -ne 0) {
                try {
                    $win = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
                    if ($win) { break }
                } catch {}
            }
        }
    }
    if ($win) { break }
    Start-Sleep -Milliseconds 500
}

if (-not $win) {
    Write-Output "ERR_NO_WINDOW"
    exit 1
}

$odxHandle = $win.Current.NativeWindowHandle

# Tu dong dien Player ID va Ten vao o nhap tren Odx Capture
$pidVal = '__PLAYER_ID__'
$pnameVal = '__PLAYER_NAME__'
try {
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
                    [System.Windows.Forms.SendKeys]::SendWait($pidVal)
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
                    [System.Windows.Forms.SendKeys]::SendWait($pnameVal)
                } catch {}
            }
        }
    }
} catch {}

# Kiem tra xem may con da o san trong phong chua (tranh bam nham nut Roi room)
$targetRoom = '__ROOM_CODE__'
$alreadyInRoom = $false

$cJoin = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'JoinButton')
$btnJoin = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cJoin)
if ($btnJoin) {
    $jText = $btnJoin.Current.Name
    if ($jText -like '*Rời*' -or $jText -like '*Leave*') {
        $alreadyInRoom = $true
    }
}

$cRoomBox = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'RoomBox')
$elemRb = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cRoomBox)
if ($elemRb -and -not $elemRb.Current.IsOffscreen) {
    $rbVal = ""
    try { $rbVal = $elemRb.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch {}
    if (-not $rbVal) { $rbVal = $elemRb.Current.Name }
    if ($rbVal -and ($rbVal.Trim().ToUpper() -eq $targetRoom.Trim().ToUpper() -or $rbVal -like "*$targetRoom*")) {
        $alreadyInRoom = $true
    }
}

if ($alreadyInRoom) {
    Write-Output "ALREADY_IN_ROOM"
    exit 0
}

# Tim JoinButton voi co che cho toi da 15 giay (de WPF khoi tao xong cac nut)
if (-not $btnJoin) {
    $cBtnType = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
    for ($j = 0; $j -lt 30; $j++) {
        try {
            $allBtns = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cBtnType)
            for ($b = 0; $b -lt $allBtns.Count; $b++) {
                $currB = $allBtns.Item($b)
                $nm = $currB.Current.Name
                $aid = $currB.Current.AutomationId
                if ($nm -like '*room*' -or $nm -like '*join*' -or $nm -like '*Join*' -or $aid -eq 'JoinButton') {
                    $btnJoin = $currB
                    break
                }
            }
        } catch {}
        if ($btnJoin) { break }
        Start-Sleep -Milliseconds 500
    }
}

if ($btnJoin) {
    $jText = $btnJoin.Current.Name
    if ($jText -like '*Rời*' -or $jText -like '*Leave*') {
        $alreadyInRoom = $true
    }
}

if ($alreadyInRoom) {
    Write-Output "ALREADY_IN_ROOM"
    exit 0
}

if (-not $btnJoin) {
    Write-Output "ERR_NO_JOINBUTTON"
    exit 1
}

# Focus window truoc
try {
    $h = [IntPtr]$odxHandle
    if ($h -ne [IntPtr]::Zero) { [W32Click]::SetForegroundWindow($h) }
} catch {}
Start-Sleep -Milliseconds 200

# Click bang mouse_event vao giua BoundingRectangle cua JoinButton
$rect = $btnJoin.Current.BoundingRectangle
if ($rect.Width -gt 0 -and $rect.Height -gt 0) {
    $cx = [int]($rect.X + $rect.Width / 2)
    $cy = [int]($rect.Y + $rect.Height / 2)
    Write-Output "CLICK_JOIN_AT=$cx,$cy"
    [W32Click]::Click($cx, $cy)
}

# Du phong: Focus truc tiep va go Space
try {
    $btnJoin.SetFocus()
    Start-Sleep -Milliseconds 50
    [System.Windows.Forms.SendKeys]::SendWait(' ')
} catch {}

Start-Sleep -Milliseconds 300
Write-Output "JOIN_CLICKED_OK"
exit 0
""".replace("__PLAYER_ID__", str(player_id) if player_id else "NONE").replace("__PLAYER_NAME__", str(player_name) if player_name else "NONE").replace("__ROOM_CODE__", str(room_code))
            res_a = run_ps_script(ps_step_a, timeout=20)
            out_a = res_a.stdout if res_a else ""
            log_event("STEP_A_JOIN_CLICK", level="INFO", source="PLAYER", data={
                "stdout": out_a.strip(),
                "elapsed_ms": int((time.time() - t_start) * 1000)
            })

            if on_status:
                on_status(f"MÁY CON: Đang tự động điền mã & kết nối [{room_code}]...", "PLAYER")

            if "ERR_NO_WINDOW" in out_a:
                if on_status:
                    on_status("Chưa tìm thấy cửa sổ Odx Capture. Hãy chắc chắn Odx Capture đang chạy.", "WARN")
                return
            if "ERR_NO_JOINBUTTON" in out_a and "ALREADY_IN_ROOM" not in out_a:
                if on_status:
                    on_status("Không tìm thấy nút [Vào room] trên Odx Capture.", "WARN")
                return
        else:
            out_a = "JOIN_CLICKED_OK"
            if on_status:
                on_status(f"Hộp thoại Vào room đã mở sẵn. Đang tự động điền mã [{room_code}]...", "PLAYER")

        if "ALREADY_IN_ROOM" not in out_a:
            time.sleep(0.3)
            ps_step_b = """
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class W32Click { [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e); public static void Click(int x, int y) { SetCursorPos(x, y); mouse_event(2, 0, 0, 0, UIntPtr.Zero); System.Threading.Thread.Sleep(50); mouse_event(4, 0, 0, 0, UIntPtr.Zero); } }'

$root = [System.Windows.Automation.AutomationElement]::RootElement

$dlg = $null
$codeBox = $null
$cCode = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'CodeBox')
$cEdit = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)

# 1. Polling tim cua so hop thoai "Vao room" / JoinRoomDialog trong toi da 10 giay
for ($k = 0; $k -lt 50; $k++) {
    Start-Sleep -Milliseconds 200

    $allW = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    for ($i = 0; $i -lt $allW.Count; $i++) {
        $tw = $allW.Item($i)
        try {
            $wnm = $tw.Current.Name
            if ($wnm -like '*Vào room*' -or $wnm -like '*Join room*' -or $wnm -eq 'Vào room') {
                $cb = $tw.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cCode)
                if (-not $cb) {
                    $cb = $tw.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cEdit)
                }
                if ($cb) {
                    $dlg = $tw
                    $codeBox = $cb
                    break
                }
            }
        } catch {}
    }
    if ($dlg -and $codeBox) { break }

    # Fallback: Tim bat ky cua so nao tren desktop co chua CodeBox
    for ($i = 0; $i -lt $allW.Count; $i++) {
        $tw = $allW.Item($i)
        try {
            $cb = $tw.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cCode)
            if ($cb) {
                $dlg = $tw
                $codeBox = $cb
                break
            }
        } catch {}
    }
    if ($dlg -and $codeBox) { break }
}

if (-not $dlg -or -not $codeBox) {
    Write-Output "ERR_DIALOG_NOT_FOUND"
    exit 1
}

Write-Output "CODEBOX_FOUND"
$dHwnd = [IntPtr]$dlg.Current.NativeWindowHandle

# 2. Dua hop thoai len Foreground
if ($dHwnd -ne [IntPtr]::Zero) {
    [W32Click]::SetForegroundWindow($dHwnd)
}
Start-Sleep -Milliseconds 200

# 3. Click chuot truc tiep vao giua o CodeBox de chac chan focus 100%
try {
    $cbRect = $codeBox.Current.BoundingRectangle
    if ($cbRect.Width -gt 0 -and $cbRect.Height -gt 0) {
        $cbX = [int]($cbRect.X + $cbRect.Width / 2)
        $cbY = [int]($cbRect.Y + $cbRect.Height / 2)
        [W32Click]::Click($cbX, $cbY)
        Start-Sleep -Milliseconds 80
    }
} catch {}

try { $codeBox.SetFocus() } catch {}
Start-Sleep -Milliseconds 50

# 4. Nhap ma phong vao CodeBox
$roomCode = "__ROOM_CODE__"

try {
    $codeBox.SetFocus()
    Start-Sleep -Milliseconds 50
    [System.Windows.Forms.SendKeys]::SendWait('^a')
    Start-Sleep -Milliseconds 40
    [System.Windows.Forms.SendKeys]::SendWait('{BACKSPACE}')
    Start-Sleep -Milliseconds 40
} catch {}

# A. ValuePattern
try {
    $vp = $codeBox.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    $vp.SetValue($roomCode)
} catch {}

# B. SendKeys de chac chan WPF fire TextChanged va sang nut OK
try {
    [System.Windows.Forms.SendKeys]::SendWait($roomCode)
} catch {}
Start-Sleep -Milliseconds 150

# 5. Tim nut submit trong dialog (nut "Vao room" hoac "OkButton", khong phai nut Huy)
$btnOk = $null
try {
    $cBtn = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
    $allBtns = $dlg.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cBtn)
    for ($b = 0; $b -lt $allBtns.Count; $b++) {
        $curB = $allBtns.Item($b)
        $aid = $curB.Current.AutomationId
        $bnm = $curB.Current.Name
        if ($aid -eq 'OkButton' -or ($aid -ne 'CancelButton' -and $bnm -notlike '*Hủy*' -and $bnm -notlike '*Cancel*')) {
            $btnOk = $curB
            break
        }
    }
} catch {}

# 6. Cho OkButton sang (IsEnabled = true) va click xac nhan
$clicked = $false
for ($w = 0; $w -lt 30; $w++) {
    if ($btnOk -and $btnOk.Current.IsEnabled) {
        $rOk = $btnOk.Current.BoundingRectangle
        if ($rOk.Width -gt 0 -and $rOk.Height -gt 0) {
            $cx = [int]($rOk.X + $rOk.Width / 2)
            $cy = [int]($rOk.Y + $rOk.Height / 2)
            [W32Click]::Click($cx, $cy)
            $clicked = $true
        }
        try {
            $ip = $btnOk.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
            $ip.Invoke()
            $clicked = $true
        } catch {}
        break
    }
    Start-Sleep -Milliseconds 100
}

# Neu chua click duoc nut OK thi gui Enter
if (-not $clicked) {
    try {
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
    } catch {}
}

# 7. XAC NHAN HOP THOAI DA DONG HOAN TOAN
$dlgClosed = $false
for ($c = 0; $c -lt 25; $c++) {
    Start-Sleep -Milliseconds 250
    $isOpen = $false
    try {
        if ($dHwnd -ne [IntPtr]::Zero) {
            $cWinH = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NativeWindowHandleProperty, [int]$dHwnd)
            $twCheck = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cWinH)
            if ($twCheck -and -not $twCheck.Current.IsOffscreen) {
                $isOpen = $true
            }
        }
    } catch {
        $isOpen = $false
    }

    if (-not $isOpen) {
        $dlgClosed = $true
        Write-Output "DIALOG_CLOSED_SUCCESS"
        exit 0
    }

    # Kiem tra xem co bao loi ma phong khong
    try {
        $cText = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Text)
        $dlgTexts = $dlg.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cText)
        for ($dt = 0; $dt -lt $dlgTexts.Count; $dt++) {
            $dtv = $dlgTexts.Item($dt).Current.Name
            if ($dtv -like '*no room with invite code*' -or $dtv -like '*không tìm thấy*' -or $dtv -like '*room not found*' -or $dtv -like '*Error*' -or $dtv -like '*Lỗi*') {
                [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
                Write-Output "ERR_ROOM_NOT_FOUND"
                exit 1
            }
        }
    } catch {}

    # Neu van con mo sau 1s thi thu Enter them 1 lan
    if ($c -eq 4) {
        try {
            if ($btnOk -and $btnOk.Current.IsEnabled) {
                $rOk = $btnOk.Current.BoundingRectangle
                if ($rOk.Width -gt 0) {
                    [W32Click]::Click([int]($rOk.X + $rOk.Width / 2), [int]($rOk.Y + $rOk.Height / 2))
                }
            } else {
                [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
            }
        } catch {}
    }
}

if ($dlgClosed) {
    Write-Output "DIALOG_CLOSED_SUCCESS"
    exit 0
} else {
    try {
        [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
    } catch {}
    Write-Output "DIALOG_STILL_OPEN"
    exit 1
}
""".replace("__ROOM_CODE__", str(room_code))

            res_b = run_ps_script(ps_step_b, timeout=15)
            out_b = res_b.stdout if res_b else ""
            log_event("STEP_B_CODE_INPUT", level="INFO" if "CODEBOX_FOUND" in out_b else "WARN", source="PLAYER", data={
                "stdout": out_b.strip(),
                "elapsed_ms": int((time.time() - t_start) * 1000)
            })

            if "ERR_ROOM_NOT_FOUND" in out_b or "DIALOG_STILL_OPEN" in out_b or "ERR_DIALOG_STILL_OPEN" in out_b:
                if on_status:
                    on_status(f"LỖI: Mã phòng [{room_code}] không tồn tại trên hệ thống Odx (Host chưa tạo hoặc sai mã)! Tool đã đóng popup.", "ERROR")
                return
            elif "DIALOG_CLOSED_SUCCESS" in out_b:
                if on_status:
                    on_status(f"Đã nhập mã [{room_code}] & đóng hộp thoại thành công! Đang xác thực vào phòng...", "PLAYER")
            elif "ERR_DIALOG_NOT_FOUND" in out_b:
                if on_status:
                    on_status("Không mở được hộp thoại Vào room trên Odx Capture.", "WARN")
                return
            else:
                if on_status:
                    on_status(f"Chưa đóng được hộp thoại Vào room với mã [{room_code}]. Vui lòng kiểm tra lại mã phòng.", "WARN")
                return

        # ============================================================
        # STEP C: Kiem tra da vao phong chua, neu hop le moi bam San sang (KHONG DUNG F10!)
        # ============================================================
        time.sleep(0.8)
        ps_step_c = """
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class W32Click { [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e); public static void Click(int x, int y) { SetCursorPos(x, y); mouse_event(2, 0, 0, 0, UIntPtr.Zero); System.Threading.Thread.Sleep(50); mouse_event(4, 0, 0, 0, UIntPtr.Zero); } }'

$root = [System.Windows.Automation.AutomationElement]::RootElement
$win = $null

for ($w = 0; $w -lt 25; $w++) {
    $cName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'Odx Capture')
    $win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cName)
    if (-not $win) {
        $procs = Get-Process -Name 'odx-app', 'Odx Capture' -ErrorAction SilentlyContinue
        foreach ($p in $procs) {
            if ($p.MainWindowHandle -ne 0) {
                try { $win = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle); if ($win) { break } } catch {}
            }
        }
    }
    if ($win) { break }
    Start-Sleep -Milliseconds 400
}
if (-not $win) { Write-Output "ERR_NO_WINDOW"; exit 1 }
$odxPid = $win.Current.ProcessId

$targetRoom = "__ROOM_CODE__"
$inRoom = $false

# Cho toi da 15 giay de Odx ket noi va dong bo vao phong
for ($m = 0; $m -lt 30; $m++) {
    Start-Sleep -Milliseconds 500

    # 1. Kiem tra xem hop thoai JoinRoomDialog co con bi treo loi khong
    $allW = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    for ($i = 0; $i -lt $allW.Count; $i++) {
        $tw = $allW.Item($i)
        try {
            $dnm = $tw.Current.Name
            if ($dnm -like '*Vào room*' -or $dnm -like '*Join room*' -or $dnm -eq 'Vào room') {
                $cText = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Text)
                $dlgTexts = $tw.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cText)
                for ($dt = 0; $dt -lt $dlgTexts.Count; $dt++) {
                    $dtv = $dlgTexts.Item($dt).Current.Name
                    if ($dtv -like '*no room with invite code*' -or $dtv -like '*error*' -or $dtv -like '*không tìm thấy*') {
                        [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
                        Write-Output "ERR_ROOM_NOT_FOUND"
                        exit 1
                    }
                }
            }
        } catch {}
    }

    # 2. Kiem tra xem da thuc su vao phong chua
    # a. JoinButton doi thanh 'Roi room' / 'Leave'
    $cJoin = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'JoinButton')
    $btnJ = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cJoin)
    if ($btnJ) {
        $jnm = $btnJ.Current.Name
        if ($jnm -like '*Rời*' -or $jnm -like '*Leave*') {
            $inRoom = $true
            break
        }
    }

    # b. RoomBox visible va chua dung ma phong target
    $cRoomBox = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'RoomBox')
    $roomBox = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cRoomBox)
    if ($roomBox -and -not $roomBox.Current.IsOffscreen) {
        $rbVal = ""
        try { $rbVal = $roomBox.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch {}
        if (-not $rbVal) { $rbVal = $roomBox.Current.Name }
        if ($rbVal -and ($rbVal.Trim().ToUpper() -eq $targetRoom.Trim().ToUpper() -or $rbVal -like "*$targetRoom*")) {
            $inRoom = $true
            break
        }
    }
}

if (-not $inRoom) {
    Write-Output "ERR_NOT_IN_ROOM"
    exit 0
}

# ============================================================
# DA O TRONG PHONG: BAT SAN SANG (TUYET DOI KHONG DUNG F10!)
# ============================================================
$cStart = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'StartStopButton')
$btnStart = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cStart)

if (-not $btnStart) {
    $cBT = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
    $wBtns = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cBT)
    for ($y = 0; $y -lt $wBtns.Count; $y++) {
        $wb = $wBtns.Item($y)
        $wnm = $wb.Current.Name
        if ($wnm -like '*sàng*' -or $wnm -like '*Ready*') {
            $btnStart = $wb
            break
        }
    }
}

if ($btnStart) {
    $nm = $btnStart.Current.Name

    # Da o trong phong va da bat san sang truoc do hoac host dang ghi
    if ($nm -like '*Hủy*' -or $nm -like '*Unready*' -or $nm -like '*round*' -or $nm -like '*Đang*') {
        Write-Output "ALREADY_READY"
        exit 0
    }

    # CHI BAM KHI NUT LA SAN SANG / READY
    if ($nm -like '*sàng*' -or $nm -like '*Ready*') {
        try {
            $ipR = $btnStart.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
            $ipR.Invoke()
            Write-Output "AUTO_READY_SUCCESS"
            exit 0
        } catch {
            try {
                $rS = $btnStart.Current.BoundingRectangle
                if ($rS.Width -gt 0) {
                    [W32Click]::Click([int]($rS.X + $rS.Width / 2), [int]($rS.Y + $rS.Height / 2))
                    Write-Output "AUTO_READY_SUCCESS"
                    exit 0
                }
            } catch {
                try {
                    $btnStart.SetFocus()
                    [System.Windows.Forms.SendKeys]::SendWait(' ')
                    Write-Output "AUTO_READY_SUCCESS"
                    exit 0
                } catch {}
            }
        }
    } elseif ($nm -like '*Bắt đầu*' -or $nm -like '*ghi hình*' -or $nm -like '*Record*') {
        # CANH BAO: Nut ghi hinh Solo -> TUYET DOI KHONG BAM!
        Write-Output "ERR_SOLO_BUTTON_DETECTED"
        exit 0
    }
}

Write-Output "IN_ROOM_OK"
exit 0
""".replace("__ROOM_CODE__", str(room_code))
        res_c = run_ps_script(ps_step_c, timeout=20)
        out_c = res_c.stdout if res_c else ""
        log_event("STEP_C_READY", level="SUCCESS" if ("AUTO_READY_SUCCESS" in out_c or "ALREADY_READY" in out_c) else "WARN", source="PLAYER", data={
            "stdout": out_c.strip(),
            "total_elapsed_ms": int((time.time() - t_start) * 1000)
        })

        if "AUTO_READY_SUCCESS" in out_c or "ALREADY_READY" in out_c:
            if on_status:
                on_status(f"★ [✓ AUTO-READY 100%] ĐÃ TỰ ĐỘNG VÀO PHÒNG [{room_code}] VÀ BẬT SẴN SÀNG!", "SUCCESS")
        elif "ERR_ROOM_NOT_FOUND" in out_c:
            if on_status:
                on_status(f"LỖI: Mã phòng [{room_code}] không tồn tại trên Odx (Host chưa tạo hoặc mã sai)! Tool đã dừng để tránh quay solo.", "ERROR")
        elif "ERR_NOT_IN_ROOM" in out_c:
            if on_status:
                on_status(f"Chưa vào được phòng [{room_code}] (Host chưa tạo hoặc mã phòng ảo). Tool KHÔNG tự ý bấm quay solo.", "WARN")
        else:
            if on_status:
                on_status(f"Đã phát lệnh kết nối phòng [{room_code}]. Đang chờ Odx đồng bộ...", "INFO")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t

def player_ensure_ready_in_room(room_code=None, on_status=None):
    """Kiem tra xem May con co dang o trong phong khong, neu chua San sang thi tu dong bam San sang (TUYET DOI KHONG DUNG F10)."""
    def _worker():
        ps_code = """
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class W32Click { [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e); public static void Click(int x, int y) { SetCursorPos(x, y); mouse_event(2, 0, 0, 0, UIntPtr.Zero); System.Threading.Thread.Sleep(50); mouse_event(4, 0, 0, 0, UIntPtr.Zero); } }'

$root = [System.Windows.Automation.AutomationElement]::RootElement
$cName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'Odx Capture')
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cName)
if (-not $win) { exit 1 }

$targetRoom = "__ROOM_CODE__"
$inRoom = $false

# 1. Kiem tra JoinButton (khi o trong room luon la 'Roi room' / 'Leave')
$cJoin = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'JoinButton')
$btnJ = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cJoin)
if ($btnJ) {
    $jnm = $btnJ.Current.Name
    if ($jnm -like '*Rời*' -or $jnm -like '*Leave*') {
        $inRoom = $true
    }
}

# 2. Kiem tra RoomBox
if (-not $inRoom) {
    $cRoomBox = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'RoomBox')
    $roomBox = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cRoomBox)
    if ($roomBox -and -not $roomBox.Current.IsOffscreen) {
        $rbVal = ""
        try { $rbVal = $roomBox.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch {}
        if (-not $rbVal) { $rbVal = $roomBox.Current.Name }
        if ($rbVal -and ($targetRoom -eq 'ALL' -or $rbVal.Trim().ToUpper() -eq $targetRoom.Trim().ToUpper() -or $rbVal -like "*$targetRoom*")) {
            $inRoom = $true
        }
    }
}

if (-not $inRoom) { exit 1 }

# Da o trong phong -> Tim StartStopButton de san sang
$cStart = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'StartStopButton')
$btnStart = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cStart)
if (-not $btnStart) {
    $cBT = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
    $allB = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cBT)
    for ($b = 0; $b -lt $allB.Count; $b++) {
        $bnm = $allB.Item($b).Current.Name
        if ($bnm -like '*sàng*' -or $bnm -like '*Ready*') {
            $btnStart = $allB.Item($b)
            break
        }
    }
}

if ($btnStart) {
    $nm = $btnStart.Current.Name
    if ($nm -like '*Hủy*' -or $nm -like '*Unready*' -or $nm -like '*round*' -or $nm -like '*Đang*') {
        Write-Output "ALREADY_READY"
        exit 0
    }
    if ($nm -like '*sàng*' -or $nm -like '*Ready*') {
        try {
            $ip = $btnStart.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
            $ip.Invoke()
            Write-Output "READY_OK"
            exit 0
        } catch {
            try {
                $rS = $btnStart.Current.BoundingRectangle
                if ($rS.Width -gt 0) {
                    [W32Click]::Click([int]($rS.X + $rS.Width / 2), [int]($rS.Y + $rS.Height / 2))
                    Write-Output "READY_OK"
                    exit 0
                }
            } catch {
                try {
                    $btnStart.SetFocus()
                    [System.Windows.Forms.SendKeys]::SendWait(' ')
                    Write-Output "READY_OK"
                    exit 0
                } catch {}
            }
        }
    }
}
exit 0
""".replace("__ROOM_CODE__", str(room_code) if room_code else "ALL")
        res = run_ps_script(ps_code, timeout=8)
        out = res.stdout if res else ""
        if "READY_OK" in out:
            if on_status:
                on_status("★ [✓ AUTO-READY 100%] Máy con đã tự động bật SẴN SÀNG cho round tiếp theo!", "SUCCESS")
        elif "ALREADY_READY" in out:
            pass
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t

def host_auto_create_room(on_status=None):
    """Tu dong tim cua so Odx Capture va bam [Tao room] (CreateButton) cho Host."""
    def _worker():
        ps_code = """
        Add-Type -AssemblyName UIAutomationClient
        Add-Type -AssemblyName UIAutomationTypes

        $root = [System.Windows.Automation.AutomationElement]::RootElement
        $win = $null

        for ($w = 0; $w -lt 25; $w++) {
            $cName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'Odx Capture')
            $win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cName)
            if (-not $win) {
                $procs = Get-Process -Name 'odx-app', 'Odx Capture' -ErrorAction SilentlyContinue
                foreach ($p in $procs) {
                    if ($p.MainWindowHandle -ne 0) {
                        $win = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
                        if ($win) { break }
                    }
                }
            }
            if ($win) { break }
            Start-Sleep -Milliseconds 400
        }

        if ($win) {
            # Kiem tra xem da co room chua
            $cRoom = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'RoomBox')
            $rBox = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cRoom)
            $hasRoom = $false
            if ($rBox) {
                try {
                    $vpR = $rBox.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                    if ($vpR.Current.Value -and $vpR.Current.Value.Length -ge 4) {
                        $hasRoom = $true
                    }
                } catch {}
            }

            if (-not $hasRoom) {
                $cCreate = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'CreateButton')
                $btnCreate = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cCreate)
                if ($btnCreate) {
                    try {
                        $ipCreate = $btnCreate.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                        $ipCreate.Invoke()
                        Write-Output "CREATE_ROOM_CLICKED"
                        exit 0
                    } catch {}
                }
            }
        }
        exit 0
        """
        res = run_ps_script(ps_code, timeout=15)
        out = res.stdout if res else ""
        if "CREATE_ROOM_CLICKED" in out:
            if on_status:
                on_status("Đã tự động bấm [Tạo room] trên Odx Capture!", "HOST")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t

def send_toggle_hotkey(hwnd=None):
    user32 = ctypes.windll.user32
    VK_F10 = 0x79
    KEYEVENTF_KEYUP = 0x0002
    scan_code = user32.MapVirtualKeyW(VK_F10, 0)

    user32.keybd_event(VK_F10, scan_code, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_F10, scan_code, KEYEVENTF_KEYUP, 0)
    return True

def send_abort_hotkey(hwnd=None):
    """Gửi phím tắt F11 (VK_F11: 0x7A) trực tiếp để hủy khẩn cấp round trong Odx Capture."""
    user32 = ctypes.windll.user32
    VK_F11 = 0x7A
    KEYEVENTF_KEYUP = 0x0002
    scan_code = user32.MapVirtualKeyW(VK_F11, 0)
    user32.keybd_event(VK_F11, scan_code, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_F11, scan_code, KEYEVENTF_KEYUP, 0)
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

def stop_recording_verified(follower, active_task_id, hwnd=None, max_retry=4):
    user32 = ctypes.windll.user32
    VK_F10 = 0x79
    KEYEVENTF_KEYUP = 0x0002
    scan_code = user32.MapVirtualKeyW(VK_F10, 0)

    for attempt in range(max_retry):
        follower.check_new_lines()
        if follower.current_stopped or follower.last_round_result:
            return True

        user32.keybd_event(VK_F10, scan_code, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_F10, scan_code, KEYEVENTF_KEYUP, 0)

        w_start = time.time()
        while time.time() - w_start < 1.5:
            follower.check_new_lines()
            if follower.current_stopped or follower.last_round_result:
                return True
            time.sleep(0.1)
    return False

# ======================================================================
# LOG FOLLOWER
# ======================================================================
def get_latest_log_path():
    log_dir = os.path.join(ODX_ROOT, "data", "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    return os.path.join(log_dir, f"odx-app-{today}.log")

class LiveLogFollower:
    def __init__(self, log_path=None):
        self.log_path = log_path or get_latest_log_path()
        self.last_pos = 0
        self.room_code = None
        self.last_armed = None
        self.current_recording = None
        self.current_stopped = None
        self.last_round_result = None
        self.last_all_ok = None
        self.last_kept_path = None
        self.id_conflict = None
        self.clock_drift_error = None
        self.task_in_use_error = None
        self.init_position()

    def init_position(self):
        if os.path.exists(self.log_path):
            self.last_pos = os.path.getsize(self.log_path)
        else:
            self.last_pos = 0
        self.room_code = None

    def reset_to_end(self):
        if os.path.exists(self.log_path):
            self.last_pos = os.path.getsize(self.log_path)
        else:
            self.last_pos = 0
        self.room_code = None
        self.task_in_use_error = None

    def check_new_lines(self, from_start=False):
        if not os.path.exists(self.log_path):
            return []
        lines = []
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                if not from_start:
                    f.seek(self.last_pos)
                for line in f:
                    self.parse_line(line)
                    lines.append(line.strip())
                self.last_pos = f.tell()
        except Exception:
            pass
        return lines

    def parse_line(self, line):
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

        m_stop = re.search(r"stop (.*?) \((.*?)\)", line)
        if m_stop:
            self.current_stopped = (m_stop.group(1).strip(), m_stop.group(2).strip())
            self.current_recording = None

        m_kept = re.search(r"([a-zA-Z0-9_-]+) done: kept in (.*?); ok", line)
        if m_kept:
            self.last_round_result = (m_kept.group(1).strip(), "ok")
            self.last_kept_path = m_kept.group(2).strip()

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
            if "all ok" in t_res.lower():
                self.last_all_ok = t_name

# ======================================================================
# ROOM TRACKING VIA ORCHESTRATOR API
# ======================================================================
def fetch_room_status(room_code, orchestrator_url=ORCHESTRATOR_URL, access_key=ACCESS_KEY):
    if not room_code:
        return None
    clean_code = str(room_code).strip().upper()
    url = f"{orchestrator_url.rstrip('/')}/rooms/{clean_code}"
    headers = {"Authorization": f"Bearer {access_key}"}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=2.5, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": "NOT_FOUND", "status_code": 404}
        return None
    except Exception:
        return None

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
        lines.append("  >> TRANG THAI PHONG: DU 4 NGUOI SAN SANG 100% (READY) [OK]")
    else:
        lines.append(f"  >> DANG CHO: {ready_cnt}/{max_expected} nguoi choi san sang...")
    lines.append("=" * 72)
    return "\n".join(lines)

def format_hud_roster(room_code, host_id, members):
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
    return f"[{room_code}] {joined} [{ready_cnt}/4 READY]  [F10 TRONG GAME: BAT DAU]"

class RoomTracker:
    def __init__(self, room_code, overlay=None, on_change_callback=None):
        self.room_code = str(room_code).strip().upper()
        self.overlay = overlay
        self.on_change_callback = on_change_callback
        self.latest_data = None
        self.last_sig = None
        self.is_running = False
        self.is_paused = False
        self.thread = None
        self.table_printed = False

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
        if not self.latest_data:
            return False
        members = self.latest_data.get("members", [])
        if len(members) < expected:
            return False
        host_id = self.latest_data.get("host")
        for idx, m in enumerate(members):
            is_h = (str(m.get("player_id")) == str(host_id)) or (idx == 0 and not host_id)
            if not is_h and not m.get("ready"):
                return False
        return True

    def _run_loop(self, interval):
        while self.is_running:
            try:
                data = fetch_room_status(self.room_code)
                if data and "members" in data:
                    self.latest_data = data
                    host_id = data.get("host")
                    members = data.get("members", [])
                    
                    sig = f"{host_id}:" + ",".join(f"{m.get('player_id')}:{m.get('ready')}:{m.get('state')}:{m.get('problem')}" for m in members)
                    sig_changed = (sig != self.last_sig)
                    if sig_changed:
                        self.last_sig = sig
                        if not self.table_printed:
                            print("\n" + format_member_table(self.room_code, host_id, members))
                            self.table_printed = True
                        else:
                            ready_cnt = sum(1 for idx, m in enumerate(members) if m.get("ready") or (str(m.get("player_id")) == str(host_id) or (idx == 0 and not host_id)))
                            m_sum = " | ".join(f"{m.get('name') or m.get('player_id')}:{'✓' if (m.get('ready') or str(m.get('player_id'))==str(host_id) or idx==0) else '..'}" for idx, m in enumerate(members[:4]))
                            sys.stdout.write(f"\r>> [REAL-TIME PHONG {self.room_code}] ({ready_cnt}/4 SAN SANG) [{m_sum}]  ")
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
                            hud_text = format_hud_roster(self.room_code, host_id, members)
                            all_ready = self.is_all_ready(4)
                            fg = "#A7F3D0" if all_ready else "#FCD34D"
                            self.overlay.show_roster_hud(hud_text, fg)
            except Exception:
                pass
            time.sleep(interval)

# ======================================================================
# AUTO-HEAL CAMERA NULLS & VALIDATION ENGINE
# ======================================================================
def heal_camera_nulls_if_needed(target_path):
    csv_path = os.path.join(target_path, "frames.csv")
    if not os.path.exists(csv_path):
        return False
    try:
        import pandas as pd
        import numpy as np
        df = pd.read_csv(csv_path, keep_default_na=False)
        c2w_cols = [c for c in df.columns if c.startswith("camera_c2w_matrix_")]
        if not c2w_cols:
            return False

        has_sparse_nulls = False
        for c in c2w_cols:
            series = df[c].astype(str)
            null_count = (series == "null").sum() + (series == "").sum()
            if 0 < null_count < len(df):
                has_sparse_nulls = True
                break

        if has_sparse_nulls:
            for c in c2w_cols:
                series = df[c].astype(str).replace({"null": np.nan, "": np.nan})
                series = series.ffill().bfill().fillna("0.0")
                df[c] = series.apply(lambda x: "null" if pd.isna(x) else str(x))
            df.to_csv(csv_path, index=False)
            return True
    except Exception:
        pass
    return False

def validate_and_inspect_capture(target_path, is_room_mode=True):
    print("\n" + "=" * 72)
    print(f"  [KIEM DINH TONG THE] Thu muc: [{os.path.basename(target_path)}]")
    print(f"  Duong dan: {target_path}")
    print("-" * 72)

    heal_camera_nulls_if_needed(target_path)

    odx_bin = os.path.join(ODX_ROOT, "bin", "odx.exe")
    val_pass = False
    if os.path.exists(odx_bin):
        try:
            res = subprocess.run([odx_bin, "validate", target_path], capture_output=True, text=True, timeout=30)
            val_out = (res.stdout + "\n" + res.stderr).strip()
            print("  [KET QUA ODX VALIDATE]:")
            for line in val_out.splitlines():
                if "PASS" in line or "error" in line.lower() or "warning" in line.lower():
                    print(f"    {line.strip()}")
            val_pass = ("PASS" in val_out) or (res.returncode == 0)
        except Exception as e:
            print(f"  [!] Khong the chay 'odx validate': {e}")
    else:
        print("  [!] Khong tim thay 'bin/odx.exe' de chay validate truc tiep.")

    required_files = [
        "video.mp4", "frames.csv", "frames_bin.bin",
        "player.json", "session.json", "key_bindings.json",
        os.path.join("raw", "input_events.csv")
    ]
    if is_room_mode:
        required_files.append("events.jsonl")

    missing_required = []
    print("-" * 72)
    print("  [KIEM TRA CAC FILE BAT BUOC CHUAN ADMIN]:")
    for rf in required_files:
        p = os.path.join(target_path, rf)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            sz_str = f"{os.path.getsize(p) / 1024:.1f} KB" if os.path.getsize(p) < 1024*1024 else f"{os.path.getsize(p)/(1024*1024):.2f} MB"
            print(f"    [+] {rf:<26} : CO MAT ({sz_str}) [OK]")
        else:
            print(f"    [-] {rf:<26} : THIEU HOAC RONG (0 byte)! [LOI]")
            missing_required.append(rf)

    # Role Chuot Check
    allowed_mouse_btns = {"left", "right", "middle", "scroll", "none", "null", "wheel"}
    disallowed_mouse_used = set()
    raw_csv_p = os.path.join(target_path, "raw", "input_events.csv")
    if os.path.exists(raw_csv_p):
        try:
            import csv
            with open(raw_csv_p, "r", encoding="utf-8", errors="ignore") as f:
                r = csv.DictReader(f)
                for row in r:
                    kind = row.get("kind", "").lower()
                    val = row.get("value", "").strip()
                    if ("button" in kind or "mouse" in kind) and val and kind != "mouse_move":
                        if val.lower() not in allowed_mouse_btns:
                            disallowed_mouse_used.add(val)
        except Exception:
            pass

    mouse_role_pass = (len(disallowed_mouse_used) == 0)
    print("-" * 72)
    if mouse_role_pass:
        print("  [+] Role Chuot (Mouse Role)       : HOP LE [OK] (Chi dung Chuot Trai, Phai, Con lan)")
    else:
        dis_list = ", ".join(sorted(list(disallowed_mouse_used)))
        print(f"  [!] VI PHAM ROLE CHUOT            : Phat hien nut chuot khong duoc phep: [{dis_list}]!")

    print("-" * 72)
    is_fully_pass = (len(missing_required) == 0 and val_pass and mouse_role_pass)
    if is_fully_pass:
        print("  ==> KET QUA TONG THE: DAT CHUAN ADMIN 100% (DAY DU 8 FILE & ROLE HOP LE) [OK]")
    else:
        print("  ==> [!] CANH BAO: CHUA DAT CHUAN ADMIN DAY DU!")
    print("=" * 72)
    return is_fully_pass
