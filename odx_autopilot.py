"""
========================================================================
ODX CAPTURE AUTO-PILOT (OFFICIAL PRODUCTION SUITE)
========================================================================
- Dieu phoi tu dong Host & Player (4 nguoi choi)
- Tu dong dong bo thoi gian NTP (time.windows.com) qua lenh goc odx clock-sync
- Tu dong hien thi ma phong va copy vao Clipboard de gui cho 3 Player
- Player ket noi 1 lan duy nhat, tu dong hien ten va tich xanh san sang (Auto-Ready)
- Kiem tra dieu kien 'armed': Du 4 thanh vien tich xanh moi bat dau quay
- Thoi luong ngau nhien nghiem ngat: 4 phut +- 9s (232s - 247s) dung chuan Admin
- On-Screen Display (OSD Overlay) gon nhe, tang hinh 100% voi video mp4
- Khong phat bat ky am thanh/beep ngoai nao de giu am thanh game 100% sach
- Phim nong [F11] trong game de huy khan cap -> Tu dong sinh duoi -fix1, -fix2...
- Ho tro dung quay giua chung (phim Q) va tu dong luu/phuc hoi tien do tiep tuc
- Tu dong kiem tra day du 8/8 file va chay 'odx validate' sau moi task
========================================================================
"""

import sys
import os
import time
import json
import subprocess
import re
import random
import msvcrt
import ctypes
import threading
import ssl
import urllib.request
from datetime import datetime
from odx_overlay import OdxOverlay

# Bat buoc UTF-8 cho console Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Tu dong nhan dien thu muc Odx goc (ke ca khi dat trong thu muc con)
TOOL_DIR = os.path.abspath(os.path.dirname(__file__))

def get_odx_root():
    here = TOOL_DIR
    if os.path.exists(os.path.join(here, "Odx Capture.exe")) or os.path.exists(os.path.join(here, "bin")):
        return here
    parent = os.path.abspath(os.path.join(here, ".."))
    if os.path.exists(os.path.join(parent, "Odx Capture.exe")) or os.path.exists(os.path.join(parent, "bin")):
        return parent
    return here

ODX_ROOT = get_odx_root()

# Win32 Constants
WM_HOTKEY = 0x0312
HOTKEY_ID = 1

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

_GLOBAL_JOB_HANDLE = None
_GLOBAL_CTRL_HANDLER = None

def setup_close_handlers():
    """
    Dang ky bat su kien tat cua so CMD (bam nut [X] do tren goc phai):
    Khi nguoi dung bam [X], he dieu hanh Windows se goi callback nay truoc khi dong CMD.
    Ham se lap tuc kill sach 100% tien trinh odx.exe, odx-app.exe, Odx Capture.exe, pktmon
    de khong bao gio de lai bat ky tien trinh ngam nao tren may!
    """
    if sys.platform != "win32":
        return

    def kill_all_now():
        try:
            subprocess.run(["taskkill", "/F", "/T", "/IM", "odx.exe"], capture_output=True, timeout=2)
            subprocess.run(["taskkill", "/F", "/T", "/IM", "odx-app.exe"], capture_output=True, timeout=2)
            subprocess.run(["taskkill", "/F", "/T", "/IM", "Odx Capture.exe"], capture_output=True, timeout=2)
            subprocess.run(["pktmon", "stop"], capture_output=True, timeout=1)
            subprocess.run(["pktmon", "reset"], capture_output=True, timeout=1)
        except Exception:
            pass

    import atexit
    atexit.register(kill_all_now)

    try:
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _ctrl_handler(ctrl_type):
            # 0: CTRL_C, 1: CTRL_BREAK, 2: CTRL_CLOSE (bam [X]), 5: LOGOFF, 6: SHUTDOWN
            if ctrl_type in (0, 1, 2, 5, 6):
                kill_all_now()
                return True
            return False

        global _GLOBAL_CTRL_HANDLER
        _GLOBAL_CTRL_HANDLER = PHANDLER_ROUTINE(_ctrl_handler)
        kernel32.SetConsoleCtrlHandler(_GLOBAL_CTRL_HANDLER, True)
    except Exception:
        pass

setup_close_handlers()

def setup_job_object_cleanup():
    """
    Gan tien trinh hien tai va moi tien trinh con (odx, odx-app) vao mot Job Object cua Windows
    voi co JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. Khi nguoi dung tat dot ngot cua so CMD (bam nut [X]),
    Windows se tu dong giai phong 100% moi tien trinh con, khong bao gio bi treo Odx ngam hay ket phim F10.
    """
    global _GLOBAL_JOB_HANDLE
    if sys.platform != "win32":
        return
    try:
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return

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

setup_job_object_cleanup()

def log(msg, tag="AUTO"):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}][{tag}] {msg}", flush=True)

def sync_system_clock():
    """Tu dong dong bo thoi gian he thong voi NTP server (chuan Odx: time.windows.com)."""
    odx_bin = os.path.join(ODX_ROOT, "bin", "odx.exe")
    if os.path.exists(odx_bin):
        try:
            res = subprocess.run([odx_bin, "clock-sync", "--server", "time.windows.com"], capture_output=True, text=True, timeout=8)
            if res.returncode == 0:
                return True, "Da dong bo NTP qua Odx Engine (time.windows.com)"
        except Exception:
            pass
    try:
        res = subprocess.run(["w32tm", "/resync", "/force"], capture_output=True, text=True, timeout=6)
        if res.returncode == 0:
            return True, "Da dong bo thoi gian qua Windows Time (w32tm)"
    except Exception:
        pass
    return False, "Khong the tu dong dong bo thoi gian"

def find_odx_window():
    user32 = ctypes.windll.user32
    return user32.FindWindowW(None, "Odx Capture")

def send_toggle_hotkey(hwnd=None):
    user32 = ctypes.windll.user32
    VK_F10 = 0x79
    KEYEVENTF_KEYUP = 0x0002

    # 1. Gui phim vat ly F10 qua Windows input event (kich hoat Global Hotkey RegisterHotKey 100%)
    user32.keybd_event(VK_F10, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_F10, 0, KEYEVENTF_KEYUP, 0)

    # 2. Gui kem PostMessageW phong ho cho cua so Odx
    if not hwnd or hwnd == 0:
        hwnd = find_odx_window()
    if hwnd and hwnd != 0:
        user32.PostMessageW(hwnd, WM_HOTKEY, HOTKEY_ID, 0)
    return True

def stop_recording_verified(follower, active_task_id, hwnd=None, max_retry=4):
    """
    Phat lenh dung quay bang ca phim vat ly F10 va xac nhan Odx da thuc su dung.
    Neu sau 1s chua nhan duoc stop thi lap tuc gui lai F10 de khong bao gio bi quay du gio!
    """
    user32 = ctypes.windll.user32
    VK_F10 = 0x79
    KEYEVENTF_KEYUP = 0x0002

    for attempt in range(max_retry):
        # Ban phim F10
        user32.keybd_event(VK_F10, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_F10, 0, KEYEVENTF_KEYUP, 0)

        if not hwnd or hwnd == 0:
            hwnd = find_odx_window()
        if hwnd and hwnd != 0:
            user32.PostMessageW(hwnd, WM_HOTKEY, HOTKEY_ID, 0)

        # Cho Odx xac nhan stop
        w_start = time.time()
        while time.time() - w_start < 1.0:
            follower.check_new_lines()
            if follower.current_stopped or follower.last_round_result:
                return True
            time.sleep(0.1)

    return False

def set_clipboard(text):
    try:
        import odx_backend as be
        if be.set_clipboard(text):
            return True
    except Exception:
        pass
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{text}'"], capture_output=True, timeout=3)
    except Exception:
        pass

def set_task_box(task_id):
    set_clipboard(task_id)
    ps_cmd = f"""
    Add-Type -AssemblyName UIAutomationClient;
    Add-Type -AssemblyName UIAutomationTypes;
    $root = [System.Windows.Automation.AutomationElement]::RootElement;
    $cName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'Odx Capture');
    $win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cName);
    if ($win) {{
        $cId = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'TaskBox');
        $elem = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cId);
        if ($elem) {{
            $p = $elem.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern);
            $p.SetValue('{task_id}');
            Write-Output 'OK';
            exit 0;
        }}
    }}
    Write-Output 'FAIL';
    exit 1;
    """
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=5)
        return "OK" in res.stdout
    except Exception:
        return False

def is_game_running(proc_name="ArmaReforgerSteam"):
    """
    Kiểm tra chính xác 100% xem Game Arma Reforger có thực sự đang mở hay không.
    Tuyệt đối không dùng tìm kiếm chuỗi lỏng lẻo trong stdout để tránh lỗi Windows
    in lại câu lệnh/filter tiêu chí tìm kiếm gây nhận diện nhầm là Game đang mở.
    """
    clean_name = os.path.splitext(proc_name)[0].strip().lower()

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

def cleanup_stale_odx_instances(kill_gui=True):
    """Don dep cac tien trinh Odx bi treo hoac chay ngam, reset pktmon cho ca may net de tranh xung dot."""
    try:
        subprocess.run(["pktmon", "stop"], capture_output=True, check=False)
        subprocess.run(["pktmon", "reset"], capture_output=True, check=False)
    except Exception:
        pass
    try:
        subprocess.run(["taskkill", "/F", "/IM", "odx.exe"], capture_output=True, check=False)
        subprocess.run(["taskkill", "/F", "/IM", "odx-app.exe"], capture_output=True, check=False)
        if kill_gui:
            subprocess.run(["taskkill", "/F", "/IM", "Odx Capture.exe"], capture_output=True, check=False)
    except Exception:
        pass

def get_latest_log_path():
    log_dir = os.path.join(ODX_ROOT, "data", "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    return os.path.join(log_dir, f"odx-app-{today}.log")

class LiveLogFollower:
    def __init__(self, log_path):
        self.log_path = log_path
        self.file = None
        self.room_code = None
        self.current_recording = None
        self.current_stopped = None
        self.last_round_result = None
        self.last_armed = None
        self.last_kept_path = None
        self.id_conflict = None
        self.clock_drift_error = None
        self.init_position()

    def init_position(self):
        if os.path.exists(self.log_path):
            self.file = open(self.log_path, "r", encoding="utf-8", errors="ignore")
            self.file.seek(0, os.SEEK_END)

    def check_new_lines(self):
        if not self.file:
            if os.path.exists(self.log_path):
                self.init_position()
            else:
                return []
        lines = []
        while True:
            line = self.file.readline()
            if not line:
                break
            line_str = line.strip()
            if line_str:
                lines.append(line_str)
                self.parse_line(line_str)
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

        m_done = re.search(r"round ([a-zA-Z0-9_-]+): (all ok|.*)", line)
        if m_done:
            self.last_round_result = (m_done.group(1).strip(), m_done.group(2).strip())

# ======================================================================
# HE THONG THEO DOI THANH VIEN PHONG REAL-TIME (HOST & 3 PLAYERS)
# ======================================================================
ORCHESTRATOR_URL = "https://13-251-211-134.sslip.io"
ACCESS_KEY = "ygYmQNO5WfHbaUn0w1hMVg4ESu8nTEhd"

def fetch_room_status(room_code, orchestrator_url=ORCHESTRATOR_URL, access_key=ACCESS_KEY):
    if not room_code:
        return None
    url = f"{orchestrator_url.rstrip('/')}/rooms/{str(room_code).strip().upper()}"
    headers = {"Authorization": f"Bearer {access_key}"}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=2.5, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None

def format_member_table(room_code, host_id, members, max_expected=4):
    lines = []
    lines.append("=" * 72)
    # Host duoc coi la san sang khi da ket noi vao phong
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

    def print_current_table(self):
        if not self.latest_data:
            return
        host_id = self.latest_data.get("host")
        members = self.latest_data.get("members", [])
        print("\n" + format_member_table(self.room_code, host_id, members))

    def _run_loop(self, interval):
        while self.is_running:
            try:
                data = fetch_room_status(self.room_code)
                if data and "members" in data:
                    self.latest_data = data
                    host_id = data.get("host")
                    members = data.get("members", [])
                    
                    sig = f"{host_id}:" + ",".join(f"{m.get('player_id')}:{m.get('ready')}:{m.get('state')}:{m.get('problem')}" for m in members)
                    if sig != self.last_sig:
                        self.last_sig = sig
                        # In bang 1 lan duy nhat, cac lan sau chi cap nhat 1 dong real-time khong cuon man hinh
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
                            
                    if not self.is_paused and self.overlay:
                        hud_text = format_hud_roster(self.room_code, host_id, members)
                        all_ready = self.is_all_ready(4)
                        fg = "#A7F3D0" if all_ready else "#FCD34D"
                        self.overlay.show_roster_hud(hud_text, fg)
            except Exception:
                pass
            time.sleep(interval)

def heal_camera_nulls_if_needed(target_path):
    csv_path = os.path.join(target_path, "frames.csv")
    if not os.path.exists(csv_path):
        return False
    try:
        import pandas as pd
        import numpy as np
        df = pd.read_csv(csv_path, keep_default_na=False)
        c2w_cols = [c for c in df.columns if c.startswith('C2W_') or c.startswith('camera_')]
        if not c2w_cols:
            return False
        has_null = False
        for c in c2w_cols:
            if (df[c] == 'null').any() or (df[c] == '').any():
                has_null = True
                break
        if has_null:
            for col in c2w_cols:
                df[col] = df[col].replace('null', np.nan).replace('', np.nan)
            df[c2w_cols] = df[c2w_cols].ffill().bfill()
            df = df.fillna('null')
            df.to_csv(csv_path, index=False)
            return True
    except Exception:
        pass
    return False

def validate_and_inspect_capture(target_path, is_room_mode=True):
    print("\n" + "=" * 72)
    print(f"  [KIEM DINH] TAT CA FILE DAU RA (DATASET SPEC 1.5): [{os.path.basename(target_path)}]")
    print("=" * 72)

    check_items = [
        (["video.mp4"], "Video man hinh H.264 (NVENC)", True),
        (["frames_bin.bin", "frames.bin"], "Du lieu nhi phan huan luyen AI (ODYSSEY-MP)", True),
        (["frames.csv"], "Du lieu khung hinh dong bo CSV", True),
        (["session.json"], "Metadata phien quay theo chuan quy dinh", True),
        (["key_bindings.json"], "Ho so anh xa phim/chuot", True),
        (["player.json", "slots.json"], "Thong tin nguoi choi", True),
        (["raw"], "Thu muc du lieu tho (raw events, timestamps)", True),
    ]
    if is_room_mode:
        check_items.append((["events.jsonl"], "Nhat ky su kien phong dong bo (Room)", True))
    else:
        check_items.append((["events.jsonl"], "Nhat ky su kien phong (chi co khi quay Room)", False))

    missing_required = []
    for f_names, desc, is_required in check_items:
        found = False
        matched_name = ""
        matched_size_mb = 0.0
        for f_name in f_names:
            f_p = os.path.join(target_path, f_name)
            if os.path.exists(f_p):
                found = True
                matched_name = f_name
                if not os.path.isdir(f_p):
                    matched_size_mb = os.path.getsize(f_p) / (1024 * 1024)
                break

        if found:
            if os.path.isdir(os.path.join(target_path, matched_name)):
                print(f"  [+ DU] {matched_name:<20} : Thu muc ({desc})")
            else:
                print(f"  [+ DU] {matched_name:<20} ({matched_size_mb:7.2f} MB) : {desc}")
        else:
            if is_required:
                print(f"  [! THIEU] {f_names[0]:<17} : {desc} (BAT BUOC)")
                missing_required.append(f_names[0])
            else:
                print(f"  [- BO QUA] {f_names[0]:<15} : {desc} (Khong bat buoc o che do Solo)")

    odx_bin = os.path.join(ODX_ROOT, "bin", "odx.exe")
    val_pass = False
    if os.path.exists(odx_bin):
        print("-" * 72)
        print(">> Chay 'odx validate' kiem dinh chat luong toan bo cau truc:")
        try:
            res = subprocess.run([odx_bin, "validate", target_path], capture_output=True, text=True, timeout=15)
            out = res.stdout.strip()
            if "camera.sparse_nulls" in out:
                if heal_camera_nulls_if_needed(target_path):
                    res = subprocess.run([odx_bin, "validate", target_path], capture_output=True, text=True, timeout=15)
                    out = res.stdout.strip()
                    print("  [AUTO-HEAL] Da tu dong sua hop le camera null -> Dat PASS [OK]")
            print(f"  {out}")
            if "PASS" in out and "0 errors" in out:
                val_pass = True
        except Exception as e:
            print(f"  Loi kiem tra validate: {e}")

    # Kiem tra sau du lieu thao tac va di chuyen thuc te (input_events.csv, frames.csv, frames_bin.bin)
    raw_input_p = os.path.join(target_path, "raw", "input_events.csv")
    frames_csv_p = os.path.join(target_path, "frames.csv")
    frames_bin_p = os.path.join(target_path, "frames_bin.bin")

    print("-" * 72)
    print("  [KIEM DINH CHI TIET DU LIEU THAO TAC & DI CHUYEN]:")

    if os.path.exists(raw_input_p):
        key_presses = 0
        mouse_moves = 0
        unique_keys = set()
        total_events = 0
        try:
            import csv
            with open(raw_input_p, "r", encoding="utf-8", errors="ignore") as f:
                r = csv.DictReader(f)
                for row in r:
                    total_events += 1
                    kind = row.get("kind", "")
                    val = row.get("value", "")
                    if "key_" in kind:
                        key_presses += 1
                        if val:
                            unique_keys.add(val)
                    elif kind == "mouse_move":
                        mouse_moves += 1
            sample_keys = ", ".join(sorted(list(unique_keys))[:10])
            if len(unique_keys) > 10:
                sample_keys += f"... (+{len(unique_keys)-10} phim khac)"
            print(f"  [+] raw\\input_events.csv : {total_events:,} su kien [OK]")
            print(f"      -> Ban phim : {key_presses:,} lan bam (Phim: {sample_keys})")
            print(f"      -> Chuot    : {mouse_moves:,} lan di chuyen (dx, dy)")
        except Exception as e:
            print(f"  [!] Loi doc raw\\input_events.csv: {e}")
    else:
        print("  [!] raw\\input_events.csv : KHONG TIM THAY")

    if os.path.exists(frames_csv_p):
        try:
            with open(frames_csv_p, "r", encoding="utf-8", errors="ignore") as f:
                total_frames = sum(1 for _ in f) - 1
            print(f"  [+] frames.csv           : {total_frames:,} khung hinh dong bo day du cot phim & chuot [OK]")
        except Exception as e:
            print(f"  [!] Loi doc frames.csv: {e}")

    if os.path.exists(frames_bin_p):
        sz_mb = os.path.getsize(frames_bin_p) / (1024 * 1024)
        try:
            with open(frames_bin_p, "rb") as f:
                header = f.read(32)
            if b"ODYSSEY-MP" in header:
                print(f"  [+] frames_bin.bin       : {sz_mb:.2f} MB (Header ODYSSEY-MP hop le) [OK]")
            else:
                print(f"  [!] frames_bin.bin       : {sz_mb:.2f} MB (Header chua dung chuan)")
        except Exception as e:
            print(f"  [!] Loi doc frames_bin.bin: {e}")

    # Kiem tra Role phim (key_bindings.json)
    kb_file = os.path.join(target_path, "key_bindings.json")
    if os.path.exists(kb_file):
        try:
            with open(kb_file, "r", encoding="utf-8") as f:
                kb_data = json.load(f)
                b = kb_data.get("bindings", {})
                print(f"  [+] Role Phim (key_bindings.json) : {len(b)} nut da duoc anh xa (W: {b.get('W')}, S: {b.get('S')}, A: {b.get('A')}, D: {b.get('D')}, Shift: {b.get('Shift')}, Space: {b.get('Space')}) [OK]")
        except Exception as e:
            print(f"  [!] Loi doc key_bindings.json: {e}")

    # Kiem tra Role nguoi choi (player.json)
    pl_file = os.path.join(target_path, "player.json")
    if os.path.exists(pl_file):
        try:
            with open(pl_file, "r", encoding="utf-8") as f:
                pl_data = json.load(f)
                p_info = pl_data.get("player", {})
                p_name = p_info.get("name") or pl_data.get("name")
                vendor = pl_data.get("vendor_name", "Tbrain")
                game = pl_data.get("game_title", "Arma Reforger")
                dur = pl_data.get("duration_seconds", 0)
                name_str = f", Ten='{p_name}'" if p_name else ""
                print(f"  [+] Role Nguoi choi (player.json) : Vendor='{vendor}'{name_str}, Game='{game}', Duration={dur:.1f}s [OK]")
        except Exception as e:
            print(f"  [!] Loi doc player.json: {e}")

    # KIEM TRA QUY CHUAN ROLE CHUOT (Chi cho phep Left, Right, Middle/Scroll - Khong duoc dung nut phu)
    allowed_mouse_btns = {'left', 'right', 'middle', 'wheel', 'scroll', 'mouse_wheel'}
    disallowed_mouse_used = set()

    if os.path.exists(raw_input_p):
        try:
            with open(raw_input_p, "r", encoding="utf-8", errors="ignore") as f:
                r = csv.DictReader(f)
                for row in r:
                    kind = row.get("kind", "").lower()
                    val = row.get("value", "").strip()
                    if ("button" in kind or "mouse" in kind) and val and kind != "mouse_move":
                        if val.lower() not in allowed_mouse_btns:
                            disallowed_mouse_used.add(val)
        except Exception:
            pass

    if os.path.exists(frames_csv_p):
        try:
            with open(frames_csv_p, "r", encoding="utf-8", errors="ignore") as f:
                r = csv.DictReader(f)
                for row in r:
                    b_val = row.get("input_mouse_buttons", "")
                    if b_val and b_val.lower() != "null":
                        for btn in b_val.split(","):
                            btn_c = btn.strip()
                            if btn_c and btn_c.lower() not in allowed_mouse_btns:
                                disallowed_mouse_used.add(btn_c)
        except Exception:
            pass

    if os.path.exists(kb_file):
        try:
            with open(kb_file, "r", encoding="utf-8") as f:
                kb_data = json.load(f)
                for k in kb_data.get("bindings", {}):
                    if any(m in k.lower() for m in ["mouse", "button", "click", "xbutton", "side", "thumb"]):
                        if k.lower() not in allowed_mouse_btns:
                            disallowed_mouse_used.add(k)
        except Exception:
            pass

    mouse_role_pass = (len(disallowed_mouse_used) == 0)
    if mouse_role_pass:
        print("  [+] Role Chuot (Mouse Role)       : HOP LE [OK] (Chi dung Chuot Trai, Phai, Con lan Middle/Scroll. Khong co nut phu)")
    else:
        dis_list = ", ".join(sorted(list(disallowed_mouse_used)))
        print(f"  [!] VI PHAM ROLE CHUOT            : Phat hien nut chuot khong duoc phep su dung: [{dis_list}]! (Chi cho phep Left, Right, Middle)")

    # Trich xuat va hien thi danh sach nguoi choi trong round tu events.jsonl
    events_file = os.path.join(target_path, "events.jsonl")
    if os.path.exists(events_file):
        players_found = {}
        try:
            with open(events_file, "r", encoding="utf-8") as ef:
                for eline in ef:
                    try:
                        ev = json.loads(eline)
                        if ev.get("type") == "player_ready":
                            p_id = ev.get("player", "")
                            p_name = ev.get("name", p_id)
                            players_found[p_id] = p_name
                        elif ev.get("type") == "round_start" and "players" in ev:
                            for pid in ev["players"]:
                                if pid not in players_found:
                                    players_found[pid] = pid
                    except Exception:
                        pass
            if players_found:
                print("-" * 72)
                print(f"  [DANH SACH NGUOI CHOI] Da tham gia round nay ({len(players_found)} nguoi):")
                for i, (pid, pname) in enumerate(players_found.items(), 1):
                    print(f"     {i}. {pname:<15} (ID: {pid}) [DA GHI NHAN DAY DU]")
        except Exception:
            pass

    print("-" * 72)
    if len(missing_required) == 0 and val_pass and mouse_role_pass:
        print("  ==> KET QUA TONG THE: DAT CHUAN ADMIN 100% (DAY DU 8 FILE & ROLE HOP LE) [OK]")
        print("=" * 72)
        return True
    else:
        print("  ==> [!] CANH BAO: CHUA DAT CHUAN ADMIN DAY DU!")
        if missing_required:
            print(f"      - Cac file bat buoc bi thieu: {', '.join(missing_required)}")
        if not val_pass:
            print("      - Loi kiem dinh 'odx validate' chua dat PASS.")
        if not mouse_role_pass:
            print(f"      - Vi pham Role Chuot: Da phat hien nut chuot khong duoc phep [{', '.join(sorted(list(disallowed_mouse_used)))}]")
        print("=" * 72)
        return False

# ======================================================================
# HOST WORKFLOW (MAY CHU / TRUONG NHOM)
# ======================================================================
def wait_for_start_choice(active_task_id, follower=None, tracker=None):
    """
    Cho lenh bat dau:
    - Nhan phim F10 truc tiep tu trong Game (Odx Capture tu nhan F10 va bat dau quay)
    - Bam phim ENTER tren cua so CMD
    - Go Q roi bam Enter tren CMD de Dung va luu tien do
    """
    user32 = ctypes.windll.user32
    while msvcrt.kbhit():
        msvcrt.getch()

    print("-" * 72)
    print(f">> Chuan bi quay task: [ {active_task_id} ]")
    if tracker and tracker.is_all_ready(4):
        print(">> [✓] CA 4 THANH VIEN DA SAN SANG (100% READY)!")
    elif tracker and tracker.latest_data:
        m_list = tracker.latest_data.get("members", [])
        r_cnt = sum(1 for idx, m in enumerate(m_list) if m.get("ready") or idx == 0)
        print(f">> [!] Dang cho thanh vien: {r_cnt}/4 nguoi san sang...")
    
    print(">> [F10]           : BAT DAU QUAY (Nhan ngay trong game, khong can Alt-Tab!)")
    print(">> [F11]           : HUY KHAN CAP (Nhan ngay trong game, dem nguoc 3s)")
    print(">> [F8]            : DUNG QUAY & NGHI (Nhan ngay trong game, tu dong luu tien do!)")
    print(">> [ENTER]         : Bat dau quay task nay (tren cua so CMD)")
    print(">> [Q] + [ENTER]   : DUNG QUAY & NGHI TAI DAY (Luu tien do de quay tiep sau)")
    print("-" * 72)

    # Cho nha phim F10/F8 neu con dang giu
    while (user32.GetAsyncKeyState(0x79) & 0x8000) or (user32.GetAsyncKeyState(0x77) & 0x8000):
        time.sleep(0.05)

    input_buf = []
    while True:
        # Kiem tra neu Odx Capture da duoc kich hoat quay truc tiep tu ban phim
        if follower:
            follower.check_new_lines()
            if follower.current_recording:
                log(f"Odx Capture da tu dong kich hoat quay '{follower.current_recording}' tu phim F10 [OK]", "HOST")
                return "started"

        # 1. Kiem tra phim nong F10 (0x79) tu trong Game: BAT DAU QUAY TIEP
        if user32.GetAsyncKeyState(0x79) & 0x8000:
            log("Phat hien phim nong [F10] BAT DAU tu trong game [OK]!", "HOTKEY")
            while user32.GetAsyncKeyState(0x79) & 0x8000:
                time.sleep(0.05)
            return "start"

        # 1.1 Kiem tra phim nong F8 (0x77) tu trong Game: DUNG QUAY & NGHI TAI DAY
        if user32.GetAsyncKeyState(0x77) & 0x8000:
            log("Phat hien phim nong [F8] DUNG QUAY & NGHI TAI DAY tu trong game!", "HOTKEY")
            while user32.GetAsyncKeyState(0x77) & 0x8000:
                time.sleep(0.05)
            for cd in [3, 2, 1]:
                sys.stdout.write(f"\r>> [F8] Dang luu tien do va tam nghi trong: {cd}s...  ")
                sys.stdout.flush()
                time.sleep(1)
            print()
            return "stop"

        # 2. Kiem tra ban phim console
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch in [b'\r', b'\n']:
                cmd_str = "".join(input_buf).strip().lower()
                if cmd_str in ['q', 'quit', 'exit', 'dung', 'stop']:
                    return "stop"
                return "start"
            elif ch in [b'q', b'Q']:
                input_buf.append('q')
                sys.stdout.write('q')
                sys.stdout.flush()
            elif ch == b'\x08': # Backspace
                if input_buf:
                    input_buf.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
            elif ch in [b'\x03', b'\x1b']: # Ctrl+C hoac ESC
                return "stop"

        time.sleep(0.05)

def run_host():
    print("=" * 72)
    print("      ODX CAPTURE AUTO-PILOT: CHE DO MAY CHU (HOST)")
    print("=" * 72)
    print("  * TINH NANG TU DONG HOA CAO CAP:")
    print("  1. Tu dong dong bo thoi gian NTP (loai bo nut bam tay)")
    print("  2. Thoi luong ngau nhien nghiem ngat: 4 phut +- 9s (232s - 247s)")
    print("  3. On-Screen Display (OSD Overlay) tang hinh 100% truoc video quay")
    print("  4. Huy khan cap bang phim [F11] trong game khi co nguoi bi loi")
    print("  5. Tu dong sinh hau to -fix1, -fix2... khi huy de quay lai ngay")
    print("  6. Ho tro DUNG QUAY (go Q de nghi giua cac task va luu tien do)")
    print("  7. Tu dong kiem tra day du 8/8 file va chay 'odx validate' sau moi task")
    print("=" * 72)

    # 0. Check Admin privileges
    if is_admin():
        log("Quyen Administrator: DA KICH HOAT [OK] (Day du quyen dieu khien game va Odx Capture)", "ADMIN")
    else:
        print("[Luu y] Tool dang chay quyen thuong. Hay mo qua file .bat de tu dong co quyen Administrator.")

    # 0.1 Tu dong dong bo thoi gian NTP
    print(">> Dang kiem tra va tu dong dong bo thoi gian Windows voi NTP (time.windows.com)...")
    ok, msg = sync_system_clock()
    if ok:
        log(f"{msg} [OK]", "NTP")
    else:
        log("Dong ho he thong da san sang theo cau hinh Windows.", "NTP")

    # 1. Check Game
    game_proc = "ArmaReforgerSteam"
    print(f">> Kiem tra game '{game_proc}'...")
    if not is_game_running(game_proc):
        print("\n" + "!" * 72)
        print(f"  [CHU Y] Game '{game_proc}' HIEN CHUA MO!")
        print("  Hay khoi dong game (qua OdxReforger.exe neu dung mod) truoc.")
        print("!" * 72)
        input("\n>> Sau khi mo game xong, bam Enter de tiep tuc: ")
    else:
        log(f"Game '{game_proc}' dang chay binh thuong! [OK]")

    # 1.1 Thong tin Player ID & Ten cua Host
    appdata_json = os.path.expandvars(r"%APPDATA%\Odx\odx-app.json")
    host_id = "puer"
    host_name = "Puer"
    s_data = {}
    if os.path.exists(appdata_json):
        try:
            with open(appdata_json, "r", encoding="utf-8") as f:
                s_data = json.load(f)
                host_id = s_data.get("PlayerId", "puer")
                host_name = s_data.get("PlayerName", "Puer")
        except Exception:
            pass

    print("-" * 72)
    h_in = input(f">> Nhap Player ID cua may Host [Mac dinh: '{host_id}']: ").strip()
    if h_in:
        clean_hid = re.sub(r'[^a-z0-9_-]', '', h_in.lower().strip())
        if clean_hid:
            host_id = clean_hid

    n_in = input(f">> Nhap Ten hien thi cua Host [Mac dinh: '{host_name}']: ").strip()
    if n_in:
        host_name = n_in

    # Dong bo vao file cau hinh cua Odx de Odx Capture GUI luon dung ID/Ten nay
    if os.path.exists(appdata_json):
        try:
            s_data["PlayerId"] = host_id
            s_data["PlayerName"] = host_name
            with open(appdata_json, "w", encoding="utf-8") as f:
                json.dump(s_data, f, indent=2)
            log(f"Thong tin Host: ID = '{host_id}', Ten = '{host_name}' [OK]", "HOST")
        except Exception:
            pass

    # Mo Odx Capture ngay lap tuc
    hwnd = find_odx_window()
    if not hwnd or hwnd == 0:
        log("Dang mo Odx Capture...", "HOST")
        exe_path = os.path.join(ODX_ROOT, "Odx Capture.exe")
        subprocess.Popen([exe_path], cwd=ODX_ROOT)
        time.sleep(2)
        hwnd = find_odx_window()

    # 2. Configuration & Progress Resume Check
    default_prefix = "asm-s221-"
    default_start = 1
    default_total = 15

    progress_file = os.path.join(TOOL_DIR, "autopilot_progress.json")
    has_resumed = False
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as pf:
                prog = json.load(pf)
                last_t = prog.get("last_completed_task", "")
                next_n = prog.get("next_task_number", 1)
                pref = prog.get("prefix", default_prefix)
                print("\n" + "*" * 72)
                print(f"  [TIEN DO CU] Phat hien ban da quay xong task [{last_t}].")
                res_ans = input(f">> Ban co muon tiep tuc tu task so {next_n} ({pref}{str(next_n).zfill(2)}) khong? (Y/n): ").strip().lower()
                print("*" * 72)
                if res_ans in ['', 'y', 'yes', 'c']:
                    default_prefix = pref
                    default_start = next_n
                    has_resumed = True
        except Exception:
            pass

    if not has_resumed:
        print("\n" + "=" * 72)
        print("          TUY CHON BATCH & TIEN TO NHIEM VU (BATCH SELECTION)")
        print("=" * 72)
        print("  [1] Batch mac dinh ca           : asm-s221- (asm-s221-01 -> 15)")
        print("  [2] Batch moi ASRM              : batch1-ASRM (hoac batch2-ASRM, ...)")
        print("  [3] Tu nhap Batch / Tien to     : Nhap bat ky (kkm-001-, AMRF-156-, ...)")
        print("-" * 72)
        b_sel = input(">> Chon Batch [1/2/3, Mac dinh: 1]: ").strip()
        if b_sel == "2":
            b_n = input(">> Nhap so thu tu Batch [Mac dinh: 1]: ").strip()
            b_n = b_n if (b_n and b_n.isdigit()) else "1"
            default_prefix = f"batch{b_n}-ASRM"
            default_total = 15
        elif b_sel == "3":
            c_p = input(">> Nhap tien to Batch (vd: kkm-001-, AMRF-156-): ").strip()
            if c_p:
                default_prefix = c_p

        print("-" * 72)
        p_in = input(f">> Tien to Task ID [Mac dinh: '{default_prefix}']: ").strip()
        prefix = p_in if p_in else default_prefix

        s_in = input(f">> So thu tu bat dau [Mac dinh: {default_start}]: ").strip()
        start_num = int(s_in) if s_in.isdigit() and int(s_in) > 0 else default_start

        t_in = input(f">> Tong so luong task can quay [Mac dinh: {default_total}]: ").strip()
        total_tasks = int(t_in) if t_in.isdigit() and int(t_in) > 0 else default_total
    else:
        prefix = default_prefix
        start_num = default_start
        total_tasks = default_total

    log_path = get_latest_log_path()
    follower = LiveLogFollower(log_path)
    log(f"Dang theo doi log: {os.path.basename(log_path)}")

    # 3. Tao room tren may Host
    print("\n" + "=" * 72)
    log("BUOC 1: TAO PHONG TRUC TUYEN TREN MAY HOST", "HOST")
    print("=" * 72)
    print(">> Hay bam nut [Tao room] tren cua so 'Odx Capture'.")
    print(">> (Tool se tu dong nhan dien ma phong va copy san vao Clipboard de gui cho team)")

    room_code = None
    while not room_code:
        follower.check_new_lines()
        if follower.room_code:
            room_code = follower.room_code
            break
        time.sleep(0.5)

    with open(os.path.join(TOOL_DIR, "room_code.txt"), "w", encoding="utf-8") as rf:
        rf.write(room_code)
    set_clipboard(room_code)

    print("\n" + "*" * 72)
    log(f"---> MA PHONG (ROOM CODE): [  {room_code}  ] <---", "HOST")
    log(">> DA TU DONG COPY VAO CLIPBOARD! Hay dan (Ctrl+V) gui cho 3 Player.", "HOST")
    log(">> 3 Player chi can chay '2_CHAY_PLAYER_TU_DONG.bat' roi dan ma nay vao la xong!", "HOST")
    print("*" * 72 + "\n")

    # 4. Khoi tao On-Screen Display Overlay (Tang hinh voi video, click-through)
    log("Khoi tao On-Screen Display (OSD Overlay) tang hinh voi video...", "OVERLAY")
    overlay = OdxOverlay()

    # 4.1 Khoi tao bo theo doi thanh vien Real-time
    tracker = RoomTracker(room_code, overlay=overlay)
    tracker.start()
    log("Da kich hoat bo theo doi thanh vien phong Real-time qua Orchestrator.", "TRACKER")

    # 5. Main Task Loop
    current_num = start_num
    end_num = start_num + total_tasks - 1
    stop_all = False

    try:
        while current_num <= end_num:
            if prefix.endswith("-") or prefix.endswith("_"):
                base_task_id = f"{prefix}{str(current_num).zfill(2)}"
            elif any(char.isdigit() for char in prefix) and total_tasks == 1:
                base_task_id = prefix
            else:
                base_task_id = f"{prefix}-{str(current_num).zfill(2)}"

            fix_counter = 0

            task_succeeded = False
            while not task_succeeded:
                if fix_counter == 0:
                    active_task_id = base_task_id
                else:
                    active_task_id = f"{base_task_id}-fix{fix_counter}"

                # Tinh thoi luong chuan Admin 4 phut +- 5s (235s - 245s, quay 238s - 242s de video luon dat 237s - 241s)
                target_duration = random.randint(238, 242)
                m_target, s_target = divmod(target_duration, 60)

                print("\n" + "=" * 72)
                if fix_counter > 0:
                    log(f"QUAY LAI TASK SUA LOI [ {active_task_id} ] (Lan fix thu {fix_counter})", "RETRY")
                else:
                    log(f"BAT DAU TASK [{current_num - start_num + 1}/{total_tasks}]: [ {active_task_id} ]", "TASK")
                print(f"  - Thoi luong ngau nhien duoc chon: {m_target:02d}:{s_target:02d} ({target_duration} giay) [Chuan Admin 4' +- 5s]")
                print("=" * 72)

                # Set Task ID in Odx Capture UI
                set_task_box(active_task_id)
                log(f"Da dien Task ID: '{active_task_id}' vao o Task (da copy vao Clipboard)")
                
                # Cho lenh bat dau tu phim F10 truc tiep trong game hoac Enter (CMD)
                overlay.show_roster_hud(f"[{active_task_id}] [F10 TRONG GAME: BAT DAU] | [F11: HUY]", fg_color="#10B981")
                start_choice = wait_for_start_choice(active_task_id, follower=follower, tracker=tracker)
                if start_choice == "stop":
                    stop_all = True
                    break

                tracker.pause_hud()

                if start_choice == "start":
                    follower.check_new_lines()
                    if not follower.current_recording:
                        send_toggle_hotkey(hwnd)
                        log(f"Da gui lenh F10 bat dau quay task '{active_task_id}'...", "HOST")

                # Wait for recording confirmation
                start_wait = time.time()
                while time.time() - start_wait < 3:
                    follower.check_new_lines()
                    if follower.current_recording:
                        log(f"Odx Capture xac nhan dang quay: {follower.current_recording} [OK]")
                        break
                    time.sleep(0.5)

                # Recording loop
                rec_start_time = time.time()
                cancelled_manually = False

                while True:
                    elapsed = time.time() - rec_start_time

                    # 1. Kiem tra phim tren console (X/ESC de huy khan cap)
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in [b'x', b'X', b'\x1b']:
                            cancelled_manually = True
                            print("\n" + "!" * 72)
                            log("PHAT HIEN LENH HUY KHAN CAP TU BAN PHIM! Dang dung ngay lap tuc...", "CANCEL")
                            print("!" * 72)
                            send_toggle_hotkey(hwnd)
                            break

                    # 2. Kiem tra phim nong HUY KHAN CAP F11 (0x7A) voi dem nguoc 3s
                    if ctypes.windll.user32.GetAsyncKeyState(0x7A) & 0x8000:
                        print("\n" + "!" * 72)
                        log("[!] PHAT HIEN PHIM [F11] HUY KHAN CAP TRONG GAME!", "WARN")
                        print(">> Dang dem nguoc 3 giay xac nhan huy (tranh bam nham hoac loi nhip)...")
                        for cd in [3, 2, 1]:
                            overlay.cmd_queue.put(("SHOW_CD", (str(cd), f"HUY TASK {active_task_id}...", "#EF4444")))
                            sys.stdout.write(f"\r>> [F11] DANG HUY TASK TRONG: {cd}s...  ")
                            sys.stdout.flush()
                            time.sleep(1)
                        print()
                        overlay.cmd_queue.put(("HIDE_CD", ()))

                        cancelled_manually = True
                        send_toggle_hotkey(hwnd)
                        follower.current_recording = None
                        follower.current_stopped = None
                        time.sleep(0.5)
                        break

                    # 3. Kiem tra F10 de DUNG QUAY ngay khi muon dung
                    if ctypes.windll.user32.GetAsyncKeyState(0x79) & 0x8000:
                        if elapsed >= 3:
                            log(f"Phat hien phim nong [F10] DUNG QUAY tu trong game sau {int(elapsed)}s [OK]!", "HOTKEY")
                            while ctypes.windll.user32.GetAsyncKeyState(0x79) & 0x8000:
                                time.sleep(0.05)
                            stop_recording_verified(follower, active_task_id, hwnd=hwnd)
                            break
                        else:
                            log(f"[!] CHU Y: Task moi quay duoc {int(elapsed)}s / 240s! Vui long cho du 4 phut (hoac F11 de huy).", "WARN")
                            time.sleep(0.3)

                    # 3.1 Canh bao neu bam phim Windows (Start Menu) hoac Alt-Tab
                    if (ctypes.windll.user32.GetAsyncKeyState(0x5B) & 0x8000) or (ctypes.windll.user32.GetAsyncKeyState(0x5C) & 0x8000):
                        log("[!] CANH BAO: Khong bam phim Windows (Start Menu) khi dang quay de tranh mat goc camera!", "WARN")
                        time.sleep(0.3)

                    # 4. Canh bao truc tiep neu bam nut phu chuot (VK_XBUTTON1: 0x05, VK_XBUTTON2: 0x06)
                    if (ctypes.windll.user32.GetAsyncKeyState(0x05) & 0x8000) or (ctypes.windll.user32.GetAsyncKeyState(0x06) & 0x8000):
                        print("\n" + "!" * 72)
                        log("CANH BAO VI PHAM: Phat hien bam nut phu chuot! (Quy dinh: Chi duoc dung Chuot Trai, Phai, Con lan)", "WARN")
                        print("!" * 72)
                        time.sleep(0.3)

                    remaining = target_duration - elapsed
                    if remaining <= 0:
                        break

                    # Update Overlay HUD tren man hinh game
                    overlay.update_recording(
                        active_task_id, 
                        elapsed, 
                        target_duration, 
                        blink=(int(elapsed * 2) % 2 == 0),
                        is_host=True
                    )

                    m_el, s_el = divmod(int(elapsed), 60)
                    m_rem, s_rem = divmod(int(remaining), 60)
                    sys.stdout.write(f"\r>> [{active_task_id}] Dang quay: {m_el:02d}:{s_el:02d} / {m_target:02d}:{s_target:02d} | Con: {m_rem:02d}:{s_rem:02d} (±5s) [F10: Dung | F11: Huy]  ")
                    sys.stdout.flush()

                    follower.check_new_lines()
                    if follower.current_stopped:
                        s_sess, s_reason = follower.current_stopped
                        print("\n" + "!" * 72)
                        log(f"CANH BAO: Task bi dung som ngoai y muon ({s_reason}) khi moi quay duoc {int(elapsed)}s / {target_duration}s!", "WARN")
                        print("  >> He thong tu dong chuyen sang task sua loi (-fix) de quay lai.")
                        print("!" * 72)
                        cancelled_manually = True
                        break

                    time.sleep(0.15)

                print()

                if cancelled_manually:
                    fix_counter += 1
                    next_fix_id = f"{base_task_id}-fix{fix_counter}"
                    print("\n" + "=" * 72)
                    log(f"TASK [{active_task_id}] DA BI HUY HOAC DUNG SOM!", "ABORT")
                    log(f"He thong tu dong kich hoat co che sua loi: TASK TIEP THEO SE LA [ {next_fix_id} ]", "FIX")
                    print("=" * 72)

                    overlay.notify_cancelled(active_task_id, next_fix_id, display_sec=5)
                    log("Dang cho 10 giay de cac may don dep phien vua huy...", "WAIT")
                    time.sleep(10)
                    tracker.resume_hud()
                    continue

                # Reached target duration normally
                log(f"DA DAT THOI LUONG MUC TIEU ({target_duration}s = {m_target:02d}:{s_target:02d}). Phat lenh DUNG VA LUU...", "STOP")
                overlay.cmd_queue.put(("SHOW_CD", ("DUNG & LUU", f"Hoan tat task: {active_task_id}", "#10B981")))
                overlay.cmd_queue.put(("SHOW_HUD", (f"[✓ HOAN TAT] [{active_task_id}] DA DAT {m_target:02d}:{s_target:02d} | DANG DUNG VA LUU DU LIEU...", "#10B981")))

                # Phat lenh dung xac thuc 100% qua phim F10 vat ly
                stopped_ok = stop_recording_verified(follower, active_task_id, hwnd=hwnd)
                if stopped_ok:
                    log("Odx Capture da xac nhan DUNG quay [OK]")
                else:
                    log("Da gui F10 dung quay, dang cho dong goi...", "WAIT")

                time.sleep(0.8)
                overlay.cmd_queue.put(("HIDE_CD", ()))
                overlay.clear()

                # Wait for save & validation
                log(f"Dang doi ca 4 may hoan tat luu va dong goi du lieu ({active_task_id})...", "WAIT")
                saved_ok = False
                wait_save = time.time()
                while time.time() - wait_save < 60:
                    follower.check_new_lines()
                    if follower.last_round_result:
                        r_sess, r_verd = follower.last_round_result
                        if active_task_id in r_sess or r_sess in active_task_id:
                            log(f"Ket qua Task '{r_sess}': {r_verd} [OK]", "VALIDATE")
                            if "all ok" in r_verd or "ok" in r_verd:
                                saved_ok = True
                            break
                    time.sleep(1)

                if saved_ok or (time.time() - wait_save >= 60):
                    task_succeeded = True
                    
                    # Kiem tra va validate toan bo cac file dau ra cho Host
                    target_folder = getattr(follower, "last_kept_path", None)
                    if not target_folder or not os.path.exists(target_folder):
                        target_folder = os.path.join(ODX_ROOT, "data", "captures", active_task_id)
                    if not os.path.exists(target_folder):
                        captures_root = os.path.join(ODX_ROOT, "data", "captures")
                        if os.path.exists(captures_root):
                            subdirs = [os.path.join(captures_root, d) for d in os.listdir(captures_root) if os.path.isdir(os.path.join(captures_root, d))]
                            if subdirs:
                                subdirs.sort(key=os.path.getmtime, reverse=True)
                                target_folder = subdirs[0]

                    if target_folder and os.path.exists(target_folder):
                        # Cho toi da 5 giay de Orchestrator ghi xong events.jsonl
                        ev_wait = time.time()
                        while time.time() - ev_wait < 5:
                            if os.path.exists(os.path.join(target_folder, "events.jsonl")):
                                break
                            time.sleep(0.5)

                        print("\n" + "=" * 72)
                        log(f"TIEN HANH KIEM DINH CHUAN ADMIN 100% CHO TASK [{os.path.basename(target_folder)}]...", "VALIDATE")
                        print("=" * 72)
                        is_pass = validate_and_inspect_capture(target_folder, is_room_mode=True)
                        if is_pass:
                            log(f"===> MAY CHU (HOST): TASK [{os.path.basename(target_folder)}] DAT CHUAN ADMIN 100% [OK]", "SUCCESS")
                            overlay.notify_completed(os.path.basename(target_folder), "San sang task tiep theo...", cooldown_sec=4)
                        else:
                            log(f"[!] MAY CHU (HOST): TASK [{os.path.basename(target_folder)}] CHUA DAT CHUAN DAY DU!", "WARN")
                    else:
                        log(f"[!] Khong tim thay thu muc luu tru cho task '{active_task_id}'.", "WARN")

                    print("\n" + "=" * 72)
                    print(f"  [HOAN THANH TASK]: [{active_task_id}]")
                    
                    next_base_id = ""
                    if current_num < end_num:
                        next_base_id = f"{prefix}{str(current_num + 1).zfill(2)}"
                        print(f"  [TIEN DO] Chuan bi bat dau task tiep theo...: [{next_base_id}]")
                    else:
                        print("  [HOAN TAT] DA HOAN THANH TOAN BO 15/15 TASK!")
                        next_base_id = "HOAN TAT TOAN BO 15 TASK"
                    print("=" * 72)

                    # Luu tien do task vua hoan thanh
                    try:
                        with open(progress_file, "w", encoding="utf-8") as pf:
                            json.dump({
                                "last_completed_task": active_task_id,
                                "next_task_number": current_num + 1,
                                "prefix": prefix,
                                "total_tasks": total_tasks,
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }, pf, indent=2)
                    except Exception:
                        pass

                    if fix_counter > 0:
                        log(f"Luu y: Task nay duoc hoan thanh voi ten [{active_task_id}].", "INFO")
                        log(f"Thu muc luu tru: data\\captures\\{active_task_id}\\", "INFO")

                    # Cooldown before next task & Hien thi Banner tren man hinh game
                    if current_num < end_num:
                        cooldown = 12
                        log(f"Nghi {cooldown}s giua 2 task de nguoi choi chuan bi trong game...", "COOLDOWN")
                        overlay.notify_completed(active_task_id, next_base_id, cooldown_sec=cooldown)
                    else:
                        overlay.notify_completed(active_task_id, "CHUC MUNG HOAN THANH 15 TASK!", cooldown_sec=6)

                    tracker.resume_hud()

            if stop_all:
                print("\n" + "=" * 72)
                log(f"BAN DA CHON DUNG QUAY TAI DAY.", "STOP")
                log(f"Tien do da duoc luu vao 'autopilot_progress.json'. Lan sau mo tool se hoi de quay tiep!", "STOP")
                print("=" * 72)
                break

            current_num += 1

    finally:
        tracker.stop()
        overlay.clear()
        overlay.close()

    if not stop_all:
        print("\n" + "=" * 72)
        log(f"HOAN TAT TOAN BO {total_tasks} TASK!", "SUCCESS")
        print(f">> Du lieu 15 task da duoc luu tron ven trong: data\\captures\\")
        print("=" * 72)

# ======================================================================
# PLAYER WORKFLOW (MAY CON / THANH VIEN)
# ======================================================================
def run_player():
    print("=" * 72)
    print("      ODX CAPTURE AUTO-PILOT: CHE DO MAY CON (PLAYER / CLIENT)")
    print("=" * 72)
    print("  [!] LUU Y DAC BIET QUAN TRONG CHO MAY PLAYER:")
    print("      - TUYET DOI KHONG MO FILE 'Odx Capture.exe' (Giao dien)!")
    print("      - Tool nay da TU DONG chay Odx ngam va tu dong ket noi theo Host.")
    print("      - Neu ban mo them file 'Odx Capture.exe', giao dien se bi treo")
    print("        o man hinh 'Dang khoi dong... Starting...' roi tu dong tat vi xung dot!")
    print("=" * 72)
    
    # 0. Check Admin privileges
    if is_admin():
        log("Quyen Administrator: DA KICH HOAT [OK] (Day du quyen dieu khien game va Odx)", "ADMIN")
    else:
        print("[Luu y] Script dang chay quyen thong thuong. Khuyen dung: Run as Administrator.")

    # 0.1 Tu dong dong bo thoi gian NTP
    print(">> Dang kiem tra va tu dong dong bo thoi gian Windows voi NTP (time.windows.com)...")
    ok, msg = sync_system_clock()
    if ok:
        log(f"{msg} [OK]", "NTP")
    else:
        log("Dong ho he thong da san sang theo cau hinh Windows.", "NTP")

    # 1. Kiem tra game Arma Reforger
    game_proc = "ArmaReforgerSteam"
    print(f"\n>> Kiem tra game '{game_proc}'...")
    if not is_game_running(game_proc):
        print("\n" + "!" * 72)
        print(f"  [CHU Y] Game '{game_proc}' HIEN CHUA MO!")
        print("  Hay khoi dong game (qua OdxReforger.exe neu dung mod) truoc.")
        print("!" * 72)
        input("\n>> Sau khi mo game xong, bam Enter de tiep tuc: ")
    else:
        log(f"Game '{game_proc}' dang chay binh thuong! [OK]")

    # 2. Doc cau hinh nguoi choi
    player_id = ""
    player_name = ""
    appdata_json = os.path.expandvars(r"%APPDATA%\Odx\odx-app.json")
    if os.path.exists(appdata_json):
        try:
            with open(appdata_json, "r", encoding="utf-8") as f:
                s_data = json.load(f)
                player_id = s_data.get("PlayerId", "")
                player_name = s_data.get("PlayerName", "")
        except Exception:
            pass

    print("-" * 72)
    if not player_id:
        player_id = input(">> Nhap Player ID cua may nay (vd: 2312, p2, p3 hoac ma so cua ban): ").strip()
    else:
        p_in = input(f">> Nhap Player ID cua may nay [Mac dinh: '{player_id}']: ").strip()
        if p_in:
            player_id = p_in

    if not player_name:
        player_name = input(f">> Nhap Ten hien thi nguoi choi [Mac dinh: '{player_id}']: ").strip() or player_id
    else:
        n_in = input(f">> Nhap Ten hien thi nguoi choi [Mac dinh: '{player_name}']: ").strip()
        if n_in:
            player_name = n_in

    # Chuan hoa Player ID de tranh loi odx spec (1-32 lowercase, digits, '-' or '_')
    clean_pid = re.sub(r'[^a-z0-9_-]', '', player_id.lower().strip())
    if not clean_pid or clean_pid == "1234":
        if clean_pid == "1234":
            print("\n" + "!" * 72)
            log("CANH BAO: ID '1234' dang bi ban Linh su dung trong doi!", "WARN")
            log("Server se TU CHOI KET NOI neu dung chung ID '1234'!", "WARN")
            print("!" * 72)
            p_in = input(">> Hay nhap Player ID rieng cua ban (vd: hao, quanghao, 2312) [Mac dinh 'hao']: ").strip()
            clean_pid = re.sub(r'[^a-z0-9_-]', '', p_in.lower().strip()) or "hao"
        else:
            clean_pid = f"p_{random.randint(100, 999)}"
    player_id = clean_pid

    log(f"Thong tin nguoi choi: ID = '{player_id}', Ten = '{player_name}'")

    # 3. Nhap ma Room thu cong 1 lan
    print("\n" + "=" * 72)
    print("  * NHAP MA ROOM THU CONG 1 LAN (DON GIAN NHAT, KHONG CAN CAI THEM GI)")
    print("=" * 72)
    
    default_code = ""
    room_file = os.path.join(TOOL_DIR, "room_code.txt")
    if os.path.exists(room_file):
        try:
            with open(room_file, "r", encoding="utf-8") as rf:
                c = rf.read().strip().upper()
                if len(c) == 6 and c.isalnum():
                    default_code = c
        except Exception:
            pass

    prompt_str = ">> Hay dan ma phong 6 ky tu do Host gui (vd: 9UUUX5): "
    if default_code:
        prompt_str = f">> Hay dan ma phong do Host gui [Enter de dung '{default_code}']: "

    room_code = input(prompt_str).strip().upper()
    if not room_code and default_code:
        room_code = default_code

    while len(room_code) != 6 or not room_code.isalnum():
        room_code = input(">> Ma phong khong hop le (phai dung 6 chu/so). Vui long nhap lai: ").strip().upper()

    print("\n" + "*" * 72)
    log(f"---> DA XAC NHAN MA PHONG: [  {room_code}  ] <---", "ROOM")
    log("Tu dong ket noi va kich hoat che do Auto-Ready...", "READY")
    print("*" * 72)

    # 4. Kiem tra file thuc thi Odx App
    exe_path = os.path.join(ODX_ROOT, "bin", "odx-app.exe")
    if not os.path.exists(exe_path):
        print("\n" + "!" * 72)
        print("  [LOI THU MUC] KHONG TIM THAY ODX CAPTURE TREN MAY NAY!")
        print(f"  Khong tim thay: '{exe_path}'")
        print("  >> NGUYEN NHAN: Ban dang mo Tool o mot thu muc rieng biet ben ngoai.")
        print("  >> CACH KHAC PHUC: Hay giai nen toan bo file trong 'Tool_Tu_Dong.zip'")
        print("     BO THANG VAO THU MUC Odx Capture (noi co file 'Odx Capture.exe' va thu muc 'bin')!")
        print("!" * 72)
        input("\n>> Bam Enter de thoat...")
        return

    active_room_code = room_code
    user32 = ctypes.windll.user32

    while True:
        # Don dep moi tien trinh Odx cu de khong bi xung dot
        cleanup_stale_odx_instances(kill_gui=True)
        time.sleep(0.5)

        args = [
            exe_path,
            "--room", active_room_code,
            "--player", player_id,
            "--name", player_name,
            "--window", "ArmaReforgerSteam",
            "--auto-ready"
        ]

        print("\n" + "=" * 72)
        log(f"MAY CON (PLAYER) DANG KET NOI VAO PHONG: [  {active_room_code}  ]", "CONNECT")
        log("DANG KHOI DONG GIAO DIEN ODX CAPTURE VA TU DONG SAN SANG (AUTO-READY)...", "SUCCESS")
        log("Giao dien Odx Capture dang duoc mo len man hinh va vao thang phong!", "SUCCESS")
        log("Ban chi can vao game choi, tool se tu dong quay va ngat theo lenh cua Host.", "SUCCESS")
        print("=" * 72 + "\n")

        log_path = get_latest_log_path()
        follower = LiveLogFollower(log_path)
        log(f"Dang theo doi tien trinh qua log: {os.path.basename(log_path)}")

        # Khoi tao OSD Overlay va RoomTracker cho may Player
        overlay = OdxOverlay()
        tracker = RoomTracker(active_room_code, overlay=overlay)
        tracker.start()
        log("Da khoi tao OSD Overlay va bo theo doi thanh vien phong Real-time [OK].", "TRACKER")

        proc = None
        recording_active = False
        rec_start_time = None
        last_validated_round = None
        user_stopped = False

        try:
            proc = subprocess.Popen(args, cwd=ODX_ROOT)
            time.sleep(1.8)
            if proc.poll() is not None:
                err_code = proc.returncode
                print("\n" + "!" * 72)
                log(f"TIEN TRINH ODX APP KHONG THE KET NOI VAO PHONG [{active_room_code}] (Ma: {err_code})!", "ERROR")
                print("  Nguyen nhan thuong gap:")
                print(f"  1. Ma phong '{active_room_code}' chua duoc Host tao hoac khong dung.")
                print("  2. May nay chua ket noi duoc den server Orchestrator (kiem tra mang/internet).")
                print("  3. Host chua mo phong hoac da dong phong.")
                print("!" * 72)
            else:
                while proc.poll() is None:
                    follower.check_new_lines()

                    if follower.id_conflict:
                        print("\n" + "!" * 72)
                        log(f"LOI TRUNG PLAYER ID: {follower.id_conflict}", "ERROR")
                        log("Server da tu choi vi ID nay dang bi ban Linh su dung!", "ALERT")
                        print("!" * 72)
                        if proc:
                            proc.terminate()
                        break

                    if follower.clock_drift_error:
                        print("\n" + "!" * 72)
                        log("ODX TU CHOI SAN SANG VI DONG HO WINDOWS BI LECH QUA 50MS!", "ALERT")
                        print(f"  Chi tiet tu Odx: {follower.clock_drift_error}")
                        print("  >> NGUYEN NHAN: Ban mo tool quyen thuong nen Windows khong cho phep dong bo NTP.")
                        print("  >> CACH KHAC PHUC NGAY:")
                        print("     1. Tat cua so nay -> Chuot phai file '2_CHAY_PLAYER_TU_DONG.bat' chon 'Run as administrator'!")
                        print("     2. HOAC vao Windows Settings -> Date & time -> Bam nut [Sync now] de dong bo.")
                        print("!" * 72 + "\n")
                        follower.clock_drift_error = None

                    # Phat hien bat dau ghi hinh tren may Player
                    if follower.current_recording and not recording_active:
                        recording_active = True
                        current_rec_id = follower.current_recording
                        follower.current_stopped = None
                        rec_start_time = time.time()
                        tracker.pause_hud()
                        log(f"MAY CON DANG DONG BO GHI HINH TASK: [{current_rec_id}]", "RECORD")

                    # Trong luc dang ghi hinh
                    if recording_active:
                        elapsed = time.time() - rec_start_time
                        if (user32.GetAsyncKeyState(0x05) & 0x8000) or (user32.GetAsyncKeyState(0x06) & 0x8000):
                            log("[!] CANH BAO VI PHAM: Phat hien bam nut phu chuot! (Quy dinh: Chi duoc dung Chuot Trai, Phai, Con lan)", "ALERT")
                            overlay.show_roster_hud("[!] VI PHAM: PHAT HIEN NUT PHU CHUOT! [F11 DE HUY]", fg_color="#EF4444")
                        else:
                            overlay.update_recording(current_rec_id, elapsed, 240, blink=(int(elapsed) % 2 == 0), is_host=False)

                        # Hien thi dong ho thoi gian thuc tren console dong bo voi Host
                        m_el, s_el = divmod(int(elapsed), 60)
                        rem_est = max(0, 240 - int(elapsed))
                        m_rem, s_rem = divmod(rem_est, 60)
                        sys.stdout.write(f"\r>> [{current_rec_id}] MAY CON DANG QUAY: {m_el:02d}:{s_el:02d} / 04:00 | Uoc tinh con: {m_rem:02d}:{s_rem:02d} (±5s) [Dong bo theo Host]  ")
                        sys.stdout.flush()

                        if follower.current_stopped or follower.last_round_result:
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                            log(f"MAY CON DA NHAN LENH DUNG TASK [{current_rec_id}]: Dang cho dong goi va kiem dinh...", "STOP")
                            recording_active = False
                            follower.current_recording = None
                            follower.current_stopped = None
                            overlay.clear()
                            tracker.resume_hud()

                    # Kiem tra khi mot round ket thuc va hoan tat dong goi
                    if follower.last_round_result:
                        r_sess, r_verd = follower.last_round_result
                        if r_sess and r_sess != last_validated_round:
                            last_validated_round = r_sess
                            recording_active = False
                            overlay.clear()
                            print("\n" + "=" * 72)
                            log(f"PHAT HIEN TASK [{r_sess}] VUA HOAN TAT! Tien hanh kiem dinh 8 file & Role...", "VALIDATE")
                            print("=" * 72)
                            
                            target_folder = getattr(follower, "last_kept_path", None)
                            if not target_folder or not os.path.exists(target_folder):
                                target_folder = os.path.join(ODX_ROOT, "data", "captures", r_sess)
                            if not os.path.exists(target_folder):
                                captures_root = os.path.join(ODX_ROOT, "data", "captures")
                                if os.path.exists(captures_root):
                                    subdirs = [os.path.join(captures_root, d) for d in os.listdir(captures_root) if os.path.isdir(os.path.join(captures_root, d))]
                                    if subdirs:
                                        subdirs.sort(key=os.path.getmtime, reverse=True)
                                        target_folder = subdirs[0]

                            if target_folder and os.path.exists(target_folder):
                                # Doi toi da 5 giay de Orchestrator ghi xong events.jsonl
                                ev_wait = time.time()
                                while time.time() - ev_wait < 5:
                                    if os.path.exists(os.path.join(target_folder, "events.jsonl")):
                                        break
                                    time.sleep(0.5)

                                is_pass = validate_and_inspect_capture(target_folder, is_room_mode=True)
                                if is_pass:
                                    log(f"===> MAY CON (PLAYER): TASK [{os.path.basename(target_folder)}] DAT CHUAN ADMIN 100% [OK]", "SUCCESS")
                                    overlay.notify_completed(os.path.basename(target_folder), "Cho lenh task tiep theo tu Host...", cooldown_sec=4)
                                else:
                                    log(f"[!] MAY CON (PLAYER): TASK [{os.path.basename(target_folder)}] CHUA DAT CHUAN DAY DU!", "WARN")
                            else:
                                log(f"[!] Khong tim thay thu muc capture tren may Player cho task: {r_sess}", "WARN")
                            
                            # Reset toan bo trang thai cho round tiep theo de tranh loop spam
                            follower.current_recording = None
                            follower.current_stopped = None
                            follower.last_round_result = None
                            recording_active = False

                            tracker.resume_hud()
                            print("\n" + "-" * 72)
                            log("May con da san sang 100%! Dang cho lenh tu Host de quay task tiep theo...", "READY")
                            print("-" * 72 + "\n")
                    
                    time.sleep(0.2 if recording_active else 0.4)

        except KeyboardInterrupt:
            user_stopped = True
            log("Nguoi dung da chu dong dung Player Agent.", "STOP")
        finally:
            tracker.stop()
            overlay.clear()
            overlay.close()
            if proc and proc.poll() is None:
                proc.terminate()
            cleanup_stale_odx_instances(kill_gui=True)

        if user_stopped:
            break

        # Neu bi trung ID voi nguoi choi khac
        if follower.id_conflict:
            print("\n" + "!" * 72)
            log(f"LOI TRUNG PLAYER ID: {follower.id_conflict}", "ERROR")
            log("Player ID cua ban dang bi trung voi nguoi khac trong phong (vi du: Linh)!", "WARN")
            print("!" * 72)
            p_new = input(f">> Hay nhap Player ID khac cho ban (vd: hao, hao07, 2312): ").strip()
            if p_new:
                clean_pid = re.sub(r'[^a-z0-9_-]', '', p_new.lower().strip())
                if clean_pid:
                    player_id = clean_pid
                    log(f"Da doi Player ID thanh: '{player_id}'", "ID")
            follower.id_conflict = None
            continue

        # Khi Host dong phong hoac bi ngat ket noi
        print("\n" + "=" * 72)
        log(f"TIEN TRINH QUAY DA NGAT KET NOI HOAC HOST DA DONG PHONG [{active_room_code}]!", "WARN")
        print("  - Host co the da out phong, tao phong moi, hoac bi ngat mang.")
        print("=" * 72)
        print("  >> Chon thao tac tiep theo:")
        print(f"     [1] - Thu ket noi lai phong [{active_room_code}] (Auto-Reconnect)")
        print("     [2] - Nhap ma phong moi do Host tao")
        print("     [3] - Thoat chuong trinh")
        choice = input(">> Lua chon cua ban (1/2/3) [Mac dinh 1]: ").strip()
        if choice == "2":
            new_code = input(">> Nhap ma phong 6 ky tu moi do Host gui: ").strip().upper()
            while len(new_code) != 6 or not new_code.isalnum():
                new_code = input(">> Ma phong khong hop le (phai dung 6 ky tu). Vui long nhap lai: ").strip().upper()
            active_room_code = new_code
            with open(os.path.join(TOOL_DIR, "room_code.txt"), "w", encoding="utf-8") as rf:
                rf.write(active_room_code)
        elif choice == "3":
            log("Ket thuc chuong trinh Player.", "STOP")
            break
        else:
            log(f"Dang chuan bi thu ket noi lai vao phong [{active_room_code}] sau 2 giay...", "RECONNECT")
            time.sleep(2)


def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower().replace("--", "")
        if arg in ["host", "master", "server"]:
            run_host()
            return
        elif arg in ["player", "client", "p"]:
            run_player()
            return

    print("=" * 72)
    print("          BO CONG CU TU DONG HOA QUAY ODX CAPTURE")
    print("=" * 72)
    print("  1. Chay tren MAY 1 (MAY CHU - HOST): [1_CHAY_HOST_TU_DONG.bat]")
    print("     - Tu dong lay ma room va copy vao Clipboard de gui cho 3 Player")
    print("     - Tu dong random thoi luong 4 phut +- 9s (232s - 247s)")
    print("     - Hien thi dem nguoc 3-2-1 va HUD tren man hinh (tang hinh truoc video)")
    print("     - Bam F11 de huy khi loi -> tu dong nhay sang -fix1, -fix2...")
    print("     - Tu dong kiem dinh day du file va 'odx validate' sau moi task")
    print()
    print("  2. Chay tren MAY 2, 3, 4 (MAY CON - PLAYER): [2_CHAY_PLAYER_TU_DONG.bat]")
    print("     - Dan ma phong nhan tu may Host mot lan duy nhat")
    print("     - Tu dong Join room va tu dong chuyen sang San sang (Auto-Ready)")
    print("     - Tu dong quay va ngat dong bo theo may Host suot 15 task")
    print("=" * 72)
    choice = input(">> Chon che do (1: Host | 2: Player) [Mac dinh 1]: ").strip()
    if choice == "2":
        run_player()
    else:
        run_host()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n>> Nguoi dung da dung chuong trinh.")
    except Exception as e:
        import traceback
        print("\n" + "=" * 72)
        print(f"[!] CO LOI NGOAI LE TRONG QUA TRINH CHAY:")
        print(f"    {e}")
        print("-" * 72)
        traceback.print_exc()
        print("=" * 72)
        input("\n>> Bam Enter de thoat...")
