"""Shared local Odx discovery and lifecycle management.

Importing this module never launches programs or installs exit handlers.
"""

import atexit
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading


def get_odx_file_version(exe_path):
    """Read the Windows file version, falling back to a versioned folder name."""
    try:
        from ctypes import wintypes
        version = ctypes.WinDLL("version", use_last_error=True)
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        version.VerQueryValueW.restype = wintypes.BOOL
        size = version.GetFileVersionInfoSizeW(str(exe_path), None)
        if size:
            buffer = ctypes.create_string_buffer(size)
            if version.GetFileVersionInfoW(str(exe_path), 0, size, buffer):
                pointer, length = ctypes.c_void_p(), wintypes.UINT()
                if version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)) and length.value >= 52:
                    words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
                    if words[0] == 0xFEEF04BD:
                        ms, ls = words[2], words[3]
                        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except (AttributeError, OSError, ValueError):
        pass
    matches = re.findall(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", str(exe_path))
    if matches:
        return tuple(int(part or 0) for part in matches[-1])
    return (0, 0, 0, 0)


def _normalise_root(value, tool_dir):
    candidate = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if not candidate.is_absolute():
        candidate = tool_dir / candidate
    candidate = candidate.resolve()
    if candidate.name.lower().endswith(".exe"):
        candidate = candidate.parent
    if candidate.name.lower() == "bin":
        candidate = candidate.parent
    return candidate


def _is_odx_root(path):
    return any((path / relative).is_file() for relative in (
        "Odx Capture.exe", "bin/odx-app.exe", "bin/odx.exe"))


def _version_for_root(path):
    for relative in ("bin/odx-app.exe", "bin/odx.exe", "Odx Capture.exe"):
        executable = path / relative
        if executable.is_file():
            return get_odx_file_version(executable)
    return (0, 0, 0, 0)


def find_all_odx_roots(tool_dir=None):
    """Return valid roots in precedence order, without scanning Downloads."""
    here = Path(tool_dir or Path(__file__).parent).resolve()
    result, seen = [], set()

    def add(path):
        key = os.path.normcase(str(path))
        if key not in seen and _is_odx_root(path):
            seen.add(key)
            result.append((_version_for_root(path), str(path)))

    explicit = os.environ.get("ODX_ROOT")
    if explicit:
        try:
            add(_normalise_root(explicit, here))
        except (OSError, ValueError, TypeError):
            pass
    try:
        config = json.loads((here / "autopilot_config.json").read_text(encoding="utf-8-sig"))
        configured = config.get("odx_root") or config.get("ODX_ROOT")
        if configured:
            add(_normalise_root(configured, here))
    except (OSError, ValueError, TypeError, AttributeError):
        pass

    for base in (here, here.parent):
        add(base)
        try:
            children = [p for p in base.iterdir() if p.is_dir() and _is_odx_root(p)]
            children.sort(key=lambda p: str(p).casefold())
            children.sort(key=_version_for_root, reverse=True)
            for child in children:
                add(child)
        except OSError:
            continue
    return result


def resolve_odx_root(tool_dir=None):
    roots = find_all_odx_roots(tool_dir)
    return roots[0][1] if roots else str(Path(tool_dir or Path(__file__).parent).resolve())


_managed_processes = {}
_process_lock = threading.RLock()
_job_handle = None
_ctrl_handler = None
_protection_ready = False


def _kernel32():
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _assign_to_job(proc):
    if sys.platform != "win32" or not _job_handle:
        return
    kernel = _kernel32()
    handle = kernel.OpenProcess(0x0100 | 0x0001, False, proc.pid)  # SET_QUOTA | TERMINATE
    if handle:
        try:
            kernel.AssignProcessToJobObject(_job_handle, handle)
        finally:
            kernel.CloseHandle(handle)


def register_managed_process(proc):
    """Own only this launched process tree; unrelated Odx/FFmpeg remain untouched."""
    if not isinstance(getattr(proc, "pid", None), int) or proc.pid <= 0:
        raise ValueError("A launched subprocess with a positive PID is required")
    with _process_lock:
        _managed_processes[proc.pid] = proc
        _assign_to_job(proc)
    return proc


def owns_pid(pid):
    """Recognise registered launchers and their Windows job descendants."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    with _process_lock:
        proc = _managed_processes.get(pid)
        if proc is not None and proc.poll() is None:
            return True
        job = _job_handle
    if sys.platform != "win32" or not job:
        return False
    try:
        from ctypes import wintypes
        kernel = _kernel32()
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if handle:
            try:
                belongs = wintypes.BOOL()
                return bool(kernel.IsProcessInJob(handle, job, ctypes.byref(belongs)) and belongs.value)
            finally:
                kernel.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        pass
    return False


def _is_gui_process(proc):
    args = getattr(proc, "args", [])
    executable = args[0] if isinstance(args, (list, tuple)) and args else args
    return Path(str(executable)).name.lower() in ("odx-app.exe", "odx capture.exe")


def cleanup_managed_processes(kill_gui=True, *, tool_closing=False):
    """Terminate owned children only as part of closing this tool.

    Recovery, retries and connection failures must leave both the native GUI
    and its related processes alive. The explicit exit flag also prevents a
    non-GUI launcher ancestor from killing Odx through a tree cleanup.
    """
    if not tool_closing:
        return False
    with _process_lock:
        owned = list(_managed_processes.items())
        if kill_gui and _job_handle and sys.platform == "win32":
            # The job also contains descendants whose launcher already exited.
            try:
                _kernel32().TerminateJobObject(_job_handle, 1)
            except (OSError, AttributeError):
                pass
    for pid, proc in owned:
        if not kill_gui and _is_gui_process(proc):
            continue
        try:
            if proc.poll() is None:
                if sys.platform == "win32":
                    result = subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True, check=False, timeout=5,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if result.returncode != 0 and proc.poll() is None:
                        proc.terminate()
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            continue
        with _process_lock:
            if _managed_processes.get(pid) is proc:
                _managed_processes.pop(pid, None)
    return True


def _shutdown():
    global _job_handle
    cleanup_managed_processes(tool_closing=True)
    with _process_lock:
        if _job_handle:
            _kernel32().CloseHandle(_job_handle)
            _job_handle = None


def setup_process_protection():
    """Install cleanup explicitly; only registered children join the Windows job.

    The GUI stays outside this job so an independent updater can outlive it.
    """
    global _protection_ready, _job_handle, _ctrl_handler
    with _process_lock:
        if _protection_ready:
            return
        _protection_ready = True
        atexit.register(_shutdown)
        if sys.platform != "win32":
            return
        try:
            from ctypes import wintypes
            kernel = _kernel32()

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC_LIMIT(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED_LIMIT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            job = kernel.CreateJobObjectW(None, None)
            if job:
                limits = EXTENDED_LIMIT()
                limits.BasicLimitInformation.LimitFlags = 0x00002000
                if kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                    _job_handle = job
                    for proc in _managed_processes.values():
                        _assign_to_job(proc)
                else:
                    kernel.CloseHandle(job)

            handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

            def on_console_close(event):
                # Ctrl+C/Ctrl+Break may be handled without exiting Python/Tk.
                # The normal atexit path owns cleanup if they do end the tool.
                if event in (2, 5, 6):
                    cleanup_managed_processes(tool_closing=True)
                return False

            _ctrl_handler = handler_type(on_console_close)
            kernel.SetConsoleCtrlHandler.argtypes = [handler_type, wintypes.BOOL]
            kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
            kernel.SetConsoleCtrlHandler(_ctrl_handler, True)
        except (OSError, AttributeError, ValueError):
            # PID-scoped cleanup remains available when job objects are unavailable.
            pass
