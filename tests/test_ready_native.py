"""Exercise background Ready against an isolated, invisible WPF fixture.

Only the fixture's HWND/PID and a local file replace Odx/server endpoints. No
real room, game, key input or cursor movement is involved.
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


class _FixtureJob:
    """Kill only assigned test children if this interpreter aborts."""

    def __init__(self):
        from ctypes import wintypes

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

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, process):
        # Popen's handle identifies the exact launched process, avoiding PID reuse.
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


FIXTURE = r'''
param([string]$StatePath,[string]$Mode,[string]$CommandPath)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class FixtureWindow { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); }'
$script:w=New-Object System.Windows.Window
$w.Title='Odx Capture'; $w.Width=400; $w.Height=250
$w.Left=30; $w.Top=30; $w.Opacity=0
$w.ShowInTaskbar=$false; $w.ShowActivated=$false
$script:clicks=0; $script:ready=$false; $script:cover=$null
$panel=New-Object System.Windows.Controls.StackPanel
$player=New-Object System.Windows.Controls.TextBox
$player.Text='PLAYER_TEST'
[System.Windows.Automation.AutomationProperties]::SetAutomationId($player,'PlayerBox')
$room=New-Object System.Windows.Controls.TextBox
$room.Text='ABC123'
[System.Windows.Automation.AutomationProperties]::SetAutomationId($room,'RoomBox')
$leave=New-Object System.Windows.Controls.Button
$leave.Content='Rời room'
[System.Windows.Automation.AutomationProperties]::SetAutomationId($leave,'JoinButton')
$script:readyButton=New-Object System.Windows.Controls.Button
$readyButton.Content='Sẵn sàng (F10)'; $readyButton.Height=35
[System.Windows.Automation.AutomationProperties]::SetAutomationId($readyButton,'StartStopButton')
function Write-FixtureState {
    $state=@{hwnd=$script:hwnd;pid=$PID;ready=$script:ready;clicks=$script:clicks;mode=$Mode}
    if ($script:cover) { $state.cover_hwnd=(New-Object System.Windows.Interop.WindowInteropHelper($script:cover)).Handle.ToInt64() }
    $temp=$StatePath+'.tmp'
    [IO.File]::WriteAllText($temp,($state|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temp -Destination $StatePath -Force
}
$readyButton.Add_Click({
    $script:clicks++
    $script:ready=-not $script:ready
    $readyButton.Content=if ($script:ready) {'Hủy sẵn sàng (F10)'} else {'Sẵn sàng (F10)'}
    Write-FixtureState
})
foreach ($control in @($player,$room,$leave,$readyButton)) {$panel.Children.Add($control)|Out-Null}
$w.Content=$panel
$script:roundTimer=New-Object System.Windows.Threading.DispatcherTimer
$roundTimer.Interval=[TimeSpan]::FromMilliseconds(30)
$roundTimer.Add_Tick({
    if ($CommandPath -and [IO.File]::Exists($CommandPath)) {
        $command=[IO.File]::ReadAllText($CommandPath)
        if ($command -in @('round-finished','round-cancelled')) {
            # Odx/server resets Ready after every round. The next action is
            # deliberately English, matching the Player UI in the report.
            $script:ready=$false
            $readyButton.Content="I'm ready (F10)"
            Write-FixtureState
        }
        Remove-Item -LiteralPath $CommandPath
    }
})
$w.Add_ContentRendered({
    $script:hwnd=(New-Object System.Windows.Interop.WindowInteropHelper($w)).Handle.ToInt64()
    switch ($Mode) {
        'minimized' { [FixtureWindow]::ShowWindow([IntPtr]$script:hwnd,7)|Out-Null }
        'hidden' { $w.Hide() }
        'offscreen' { $w.Left=-30000; $w.Top=-30000 }
        'covered' {
            # Keep fixture windows invisible while testing a different top-level
            # window above Odx in Z order. No foreground activation is requested.
            $script:cover=New-Object System.Windows.Window
            $cover.Title='Ready test cover'; $cover.Width=400; $cover.Height=250
            $cover.Left=30; $cover.Top=30; $cover.Opacity=0
            $cover.ShowInTaskbar=$false; $cover.ShowActivated=$false; $cover.Topmost=$true
            $cover.Show()
        }
    }
    Write-FixtureState
    $roundTimer.Start()
})
$w.Show()
[System.Windows.Threading.Dispatcher]::Run()
'''


@unittest.skipUnless(os.name == "nt", "Windows WPF/UIAutomation")
class BackgroundReadyNativeTests(unittest.TestCase):
    def _run_case(self, mode, rearm=False):
        import odx_backend as backend
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.IsIconic.argtypes = [wintypes.HWND]
        user.IsIconic.restype = wintypes.BOOL
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.IsWindowVisible.restype = wintypes.BOOL
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        event_callback_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.HWND, wintypes.LONG, wintypes.LONG,
                                                wintypes.DWORD, wintypes.DWORD)
        user.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                                        event_callback_type, wintypes.DWORD,
                                        wintypes.DWORD, wintypes.DWORD]
        user.SetWinEventHook.restype = wintypes.HANDLE
        user.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                     wintypes.UINT, wintypes.UINT, wintypes.UINT]
        with tempfile.TemporaryDirectory(prefix="odx-ready-native-") as temporary:
            folder = Path(temporary)
            script, state_file = folder / "fixture.ps1", folder / "state.json"
            command_file = folder / "round-command.txt"
            script.write_text(FIXTURE, encoding="utf-8-sig")
            job = _FixtureJob()
            process = None
            worker = None
            event_hook = None
            try:
                process = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Sta",
                     "-File", str(script), "-StatePath", str(state_file), "-Mode", mode,
                     "-CommandPath", str(command_file)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                job.assign(process)
                deadline = time.monotonic() + 15
                while not state_file.exists() and time.monotonic() < deadline and process.poll() is None:
                    time.sleep(0.05)
                if not state_file.exists():
                    if process.poll() is not None:
                        error = process.stderr.read().decode(errors="replace")
                    else:
                        error = "WPF fixture startup timed out"
                    self.fail(error)

                def read_state():
                    # The fixture replaces its state file after updating it.
                    for _ in range(10):
                        try:
                            return json.loads(state_file.read_text(encoding="utf-8-sig"))
                        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
                            time.sleep(0.01)
                    self.fail("Could not read the fixture state")

                state = read_state()
                hwnd, pid = state["hwnd"], state["pid"]
                self.assertNotEqual(backend.find_unmanaged_odx_window(), hwnd,
                                    "A same-caption PowerShell fixture is not foreign Odx")
                self.assertNotEqual(backend.find_odx_window(), hwnd,
                                    "Odx discovery must verify the actual executable")
                original_minimized = bool(user.IsIconic(hwnd))
                original_visible = bool(user.IsWindowVisible(hwnd))
                original_rect = wintypes.RECT()
                user.GetWindowRect(hwnd, ctypes.byref(original_rect))
                foreground_events = []
                def on_foreground(hook, event, window, object_id, child_id, thread, timestamp):
                    foreground_events.append(window)
                event_callback = event_callback_type(on_foreground)
                message = wintypes.MSG()
                user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
                # Observe only the fixture PID, including transient activation.
                event_hook = user.SetWinEventHook(3, 3, None, event_callback, pid, 0, 0)
                self.assertTrue(event_hook, "Cannot observe fixture foreground transitions")
                self.assertNotIn(user.GetForegroundWindow(), {hwnd, state.get("cover_hwnd")},
                                 "The fixture itself must start without activation")

                def local_roster(*args, **kwargs):
                    return {"host": "HOST_TEST", "current_session": None, "members": [
                        {"player_id": "HOST_TEST", "ready": True},
                        {"player_id": "PLAYER_TEST", "ready": read_state()["ready"]}]}

                # Native scripts retain their real HWND/PID restrictions. Only
                # ownership discovery and HTTP are redirected to our fixture.
                with patch.object(backend, "_owned_uia_context", return_value=(hwnd, pid)), \
                     patch.object(backend, "is_managed_odx_window", side_effect=lambda value: value == hwnd), \
                     patch.object(backend, "find_odx_window", return_value=hwnd), \
                     patch.object(backend, "find_unmanaged_odx_window", return_value=None), \
                     patch.object(backend, "fetch_room_status", side_effect=local_roster):
                    worker = backend.player_ensure_ready_in_room("ABC123", player_id="PLAYER_TEST", timeout=12)
                    worker.join(timeout=18)
                    self.assertFalse(worker.is_alive(), "Ready worker must finish within its deadline")
                    self.assertTrue(worker.success, getattr(worker, "error", "Ready failed"))
                    if rearm:
                        for expected_clicks, transition in enumerate(("round-finished", "round-cancelled"), start=2):
                            command_file.write_text(transition, encoding="utf-8")
                            deadline = time.monotonic() + 3
                            while command_file.exists() and time.monotonic() < deadline:
                                time.sleep(.02)
                            self.assertFalse(command_file.exists(), "Fixture must complete the round transition")
                            self.assertFalse(read_state()["ready"])
                            worker = backend.player_ensure_ready_in_room("ABC123", player_id="PLAYER_TEST", timeout=12)
                            worker.join(timeout=18)
                            self.assertFalse(worker.is_alive())
                            self.assertTrue(worker.success, worker.error)
                            self.assertTrue(worker.invoked)
                            self.assertEqual(read_state()["clicks"], expected_clicks)
                            worker = backend.player_ensure_ready_in_room("ABC123", player_id="PLAYER_TEST", timeout=12)
                            worker.join(timeout=18)
                            self.assertFalse(worker.is_alive())
                            self.assertTrue(worker.success, worker.error)
                            self.assertFalse(worker.invoked, "An already Ready player must not be toggled off")
                            self.assertEqual(read_state()["clicks"], expected_clicks)

                updated = read_state()
                self.assertTrue(updated["ready"])
                self.assertEqual(updated["clicks"], 3 if rearm else 1, "Only one Ready invocation is allowed per round")
                self.assertEqual(bool(user.IsIconic(hwnd)), original_minimized)
                self.assertEqual(bool(user.IsWindowVisible(hwnd)), original_visible)
                after_rect = wintypes.RECT()
                user.GetWindowRect(hwnd, ctypes.byref(after_rect))
                self.assertEqual((after_rect.left, after_rect.top, after_rect.right, after_rect.bottom),
                                 (original_rect.left, original_rect.top, original_rect.right, original_rect.bottom))
                event_deadline = time.monotonic() + 0.15
                while time.monotonic() < event_deadline:
                    while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                        user.TranslateMessage(ctypes.byref(message))
                        user.DispatchMessageW(ctypes.byref(message))
                    time.sleep(0.01)
                self.assertEqual(foreground_events, [], "Ready must not even briefly activate its window")
                fixture_windows = {hwnd, updated.get("cover_hwnd")}
                self.assertNotIn(user.GetForegroundWindow(), fixture_windows,
                                 "Background Ready must not activate a fixture window")
            finally:
                if event_hook:
                    user.UnhookWinEvent(event_hook)
                if worker is not None and worker.is_alive():
                    event = getattr(worker, "cancel_event", None)
                    if event:
                        event.set()
                    worker.join(timeout=7)
                job.close()
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                    process.stderr.close()

    def test_fixture_child_ends_when_test_interpreter_aborts(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with tempfile.TemporaryDirectory(prefix="odx-fixture-job-") as temporary:
            folder = Path(temporary)
            state, release = folder / "child.pid", folder / "release"
            code = """
import os, pathlib, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from test_ready_native import _FixtureJob
job = _FixtureJob()
child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'],
                         creationflags=subprocess.CREATE_NO_WINDOW)
try:
    job.assign(child)
except BaseException:
    child.terminate()
    child.wait(timeout=5)
    raise
pathlib.Path(sys.argv[2]).write_text(str(child.pid))
deadline = time.monotonic() + 10
while not pathlib.Path(sys.argv[3]).exists() and time.monotonic() < deadline:
    time.sleep(0.01)
os._exit(27)
"""
            parent = subprocess.Popen(
                [sys.executable, "-B", "-c", code, str(Path(__file__).parent),
                 str(state), str(release)], stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            child_handle = None
            try:
                deadline = time.monotonic() + 8
                while not state.exists() and time.monotonic() < deadline and parent.poll() is None:
                    time.sleep(0.01)
                self.assertTrue(state.exists(), "Fixture parent did not create its child")
                child_handle = kernel.OpenProcess(0x00100000 | 0x0001, False, int(state.read_text()))
                self.assertTrue(child_handle, "Cannot observe the launched fixture child")
                release.touch()
                self.assertEqual(parent.wait(timeout=5), 27)
                self.assertEqual(kernel.WaitForSingleObject(child_handle, 5000), 0,
                                 "Aborted test interpreter must not leave fixture children running")
            finally:
                if parent.poll() is None:
                    parent.terminate()
                    parent.wait(timeout=5)
                if child_handle:
                    if kernel.WaitForSingleObject(child_handle, 0) != 0:
                        kernel.TerminateProcess(child_handle, 1)
                        kernel.WaitForSingleObject(child_handle, 5000)
                    kernel.CloseHandle(child_handle)
                parent.stderr.close()

    def test_ready_when_another_window_is_above_odx(self):
        self._run_case("covered")

    def test_same_window_readies_again_after_finished_and_cancelled_rounds(self):
        self._run_case("covered", rearm=True)

    def test_ready_while_minimized_keeps_window_minimized(self):
        self._run_case("minimized")

    def test_ready_outside_screen_does_not_require_mouse_coordinates(self):
        self._run_case("offscreen")

    def test_ready_while_hidden_restores_original_visibility(self):
        self._run_case("hidden")


if __name__ == "__main__":
    unittest.main()
