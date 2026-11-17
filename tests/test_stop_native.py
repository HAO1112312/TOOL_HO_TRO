"""Stop a synthetic Host round by scoped accessibility, never physical keys.

The native scripts operate only on an invisible WPF fixture's HWND/PID. Local
state replaces server/follower results; no real Odx room or capture is touched.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

if __package__:
    from .test_ready_native import _FixtureJob
else:
    from test_ready_native import _FixtureJob


TASK = "stop-task-01"
FIXTURE = r'''
param([string]$StatePath,[string]$CommandPath,[string]$Mode='covered',
      [string]$Language='vi',[string]$Role='host',[string]$Phase='recording',
      [string]$Task='stop-task-01',[int]$Delay=600)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public static class StopFixtureWindow {
 [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h,int n);
 [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h,int n,int v);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
 public static void NoActivate(IntPtr h) {SetWindowLong(h,-20,GetWindowLong(h,-20)|0x08000080);}
}
'@
$script:w=New-Object System.Windows.Window
$w.Title='Odx Capture'; $w.Width=420; $w.Height=260; $w.Left=30; $w.Top=30
$w.Opacity=0; $w.ShowInTaskbar=$false; $w.ShowActivated=$false
$w.Add_SourceInitialized({
 $script:hwnd=(New-Object System.Windows.Interop.WindowInteropHelper($w)).Handle.ToInt64()
 [StopFixtureWindow]::NoActivate([IntPtr]$script:hwnd)
})
$script:clicks=0; $script:ended=($Phase -eq 'idle'); $script:pendingAt=$null
$script:cover=$null
$panel=New-Object System.Windows.Controls.StackPanel
function Id($control,[string]$name) {
 [System.Windows.Automation.AutomationProperties]::SetAutomationId($control,$name)
 return $control
}
$script:player=Id (New-Object System.Windows.Controls.TextBox) 'PlayerBox'
$player.Text=if ($Role -eq 'host') {'HOST_TEST'} else {'PLAYER_TEST'}
$script:room=Id (New-Object System.Windows.Controls.TextBox) 'RoomBox'
$room.Text='ABC123'; $room.IsReadOnly=$true
$script:taskBox=Id (New-Object System.Windows.Controls.TextBox) 'TaskBox'
$taskBox.Text=$Task
$create=Id (New-Object System.Windows.Controls.Button) 'CreateButton'
$create.Content=if ($Role -eq 'host') {
 if ($Language -eq 'vi') {'Đóng room'} else {'Close room'}
} else {if ($Language -eq 'vi') {'Tạo room'} else {'Create room'}}
$join=Id (New-Object System.Windows.Controls.Button) 'JoinButton'
$join.Content=if ($Language -eq 'vi') {'Rời room'} else {'Leave room'}
$script:button=Id (New-Object System.Windows.Controls.Button) 'StartStopButton'
$button.Height=35
function Set-PhaseLabel {
 $button.Content=if ($script:ended) {
  if ($Language -eq 'vi') {'Bắt đầu round (F10)'} else {'Start round (F10)'}
 } else {if ($Language -eq 'vi') {'Kết thúc round (F10)'} else {'End round (F10)'}}
}
Set-PhaseLabel
function Write-State {
 if (-not $script:hwnd) {return}
 $state=@{hwnd=$script:hwnd;pid=$PID;clicks=$script:clicks;ended=$script:ended;
  task=$taskBox.Text;role=$Role;button=[string]$button.Content}
 if ($script:cover) {$state.cover_hwnd=(New-Object System.Windows.Interop.WindowInteropHelper($cover)).Handle.ToInt64()}
 $temporary=$StatePath+'.tmp'
 [IO.File]::WriteAllText($temporary,($state|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
 Move-Item -LiteralPath $temporary -Destination $StatePath -Force
}
$button.Add_Click({
 $script:clicks++
 if ($script:ended) {throw 'A stop command must never restart the round'}
 # Preserve the End round label briefly, as an asynchronous native action can.
 if (-not $script:pendingAt) {$script:pendingAt=[DateTime]::UtcNow.AddMilliseconds($Delay)}
 Write-State
})
foreach ($control in @($player,$room,$taskBox,$create,$join,$button)) {$panel.Children.Add($control)|Out-Null}
$w.Content=$panel
$script:timer=New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval=[TimeSpan]::FromMilliseconds(30)
$timer.Add_Tick({
 if ($script:pendingAt -and [DateTime]::UtcNow -ge $script:pendingAt) {
  $script:pendingAt=$null; $script:ended=$true; Set-PhaseLabel; Write-State
 }
 if ([IO.File]::Exists($CommandPath)) {
  $command=[IO.File]::ReadAllText($CommandPath)
  if ($command -eq 'change-task') {$taskBox.Text='new-task-02'}
  Remove-Item -LiteralPath $CommandPath
  Write-State
 }
})
$w.Add_ContentRendered({
 switch ($Mode) {
  'minimized' {[StopFixtureWindow]::ShowWindow([IntPtr]$script:hwnd,7)|Out-Null}
  'offscreen' {$w.Left=-30000; $w.Top=-30000}
  'covered' {
   $script:cover=New-Object System.Windows.Window
   $cover.Title='Stop fixture cover'; $cover.Width=420; $cover.Height=260
   $cover.Left=30; $cover.Top=30; $cover.Opacity=0; $cover.ShowInTaskbar=$false
   $cover.ShowActivated=$false; $cover.Topmost=$true; $cover.Show()
  }
 }
 Write-State; $timer.Start()
})
$w.Show()
[System.Windows.Threading.Dispatcher]::Run()
'''


@unittest.skipUnless(os.name == "nt", "Windows WPF/UIAutomation")
class BackgroundHostStopNativeTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, language="vi", mode="covered", role="host", phase="recording"):
        import odx_backend as backend
        with tempfile.TemporaryDirectory(prefix="odx-stop-native-") as temporary:
            folder = Path(temporary)
            script, state_file, command = (folder / name for name in ("fixture.ps1", "state.json", "command.txt"))
            script.write_text(FIXTURE, encoding="utf-8-sig")
            job, process = _FixtureJob(), None
            try:
                process = subprocess.Popen([
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Sta", "-File", str(script),
                    "-StatePath", str(state_file), "-CommandPath", str(command), "-Mode", mode,
                    "-Language", language, "-Role", role, "-Phase", phase, "-Task", TASK],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
                job.assign(process)
                deadline = time.monotonic() + 15
                while not state_file.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.03)
                if not state_file.exists():
                    self.fail(process.stderr.read().decode(errors="replace") if process.poll() is not None else "Fixture startup timed out")

                def read_state():
                    for _ in range(30):
                        try:
                            return json.loads(state_file.read_text(encoding="utf-8-sig"))
                        except (OSError, ValueError):
                            time.sleep(.01)
                    self.fail("Could not read stop fixture state")

                initial = read_state()
                context = initial["hwnd"], initial["pid"]
                with patch.object(backend, "_owned_uia_context", return_value=context), \
                     patch.object(backend, "find_odx_window", return_value=context[0]), \
                     patch.object(backend, "is_managed_odx_window", side_effect=lambda value: value == context[0]), \
                     patch.object(backend, "find_unmanaged_odx_window", return_value=None):
                    yield backend, context, read_state, command
            finally:
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

    def report(self, backend, context, *, task=TASK, invoke=False):
        result = backend.run_ps_script(backend._host_stop_script(task, invoke=invoke, context=context), timeout=6)
        report = backend._host_stop_report(result)
        self.assertIsNotNone(report, getattr(result, "stderr", "Missing native stop report"))
        return report

    def background_stop(self, mode, language):
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetForegroundWindow.restype = wintypes.HWND
        user.IsIconic.argtypes = [wintypes.HWND]
        user.IsIconic.restype = wintypes.BOOL
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        callback_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                          wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
        user.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, callback_type,
                                        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        user.SetWinEventHook.restype = wintypes.HANDLE
        user.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                     wintypes.UINT, wintypes.UINT, wintypes.UINT]
        with self.fixture(language=language, mode=mode) as (backend, context, read_state, _):
            events = []
            callback = callback_type(lambda hook, event, hwnd, obj, child, thread, stamp: events.append(hwnd))
            event_hook = user.SetWinEventHook(3, 3, None, callback, context[1], 0, 0)
            self.assertTrue(event_hook)
            original_minimized = bool(user.IsIconic(context[0]))
            original_rect = wintypes.RECT()
            user.GetWindowRect(context[0], ctypes.byref(original_rect))
            follower = types.SimpleNamespace(current_recording=TASK, last_armed=None,
                                             current_stopped=None, current_stopped_at=None,
                                             last_round_result=None, last_all_ok=None)

            def follow_native():
                if read_state()["ended"]:
                    follower.current_recording = None
                    follower.current_stopped = (TASK, "requested")
                    follower.current_stopped_at = time.monotonic()
                    follower.last_round_result = (TASK, "all ok")
                    follower.last_all_ok = TASK

            def room(*args, **kwargs):
                return {"host": "HOST_TEST", "members": [{"player_id": "HOST_TEST", "ready": True}],
                        "current_session": None if read_state()["ended"] else {"task": TASK}}

            follower.check_new_lines = follow_native
            try:
                with patch.object(backend, "fetch_room_status", side_effect=room), \
                     patch.object(backend, "send_toggle_hotkey", side_effect=AssertionError("A verified stop must not send global F10")):
                    ok = backend.stop_recording_verified(follower, TASK, hwnd=context[0], timeout=10,
                                                         can_stop=lambda: True)
                self.assertTrue(ok, "The exact native Host action must stop the synthetic round")
                self.assertEqual(read_state()["clicks"], 1, "Delayed native acceptance must not cause a second toggle")
                self.assertTrue(read_state()["ended"])
                # Repeating the verified request after completion is a read-only success.
                with patch.object(backend, "send_toggle_hotkey", side_effect=AssertionError("Do not restart a completed round")), \
                     patch.object(backend, "fetch_room_status", side_effect=AssertionError("A completed round must not make live room calls")):
                    self.assertTrue(backend.stop_recording_verified(follower, TASK, hwnd=context[0], timeout=2))
                self.assertEqual(read_state()["clicks"], 1)
                deadline = time.monotonic() + .15
                message = wintypes.MSG()
                while time.monotonic() < deadline:
                    while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                        user.TranslateMessage(ctypes.byref(message))
                        user.DispatchMessageW(ctypes.byref(message))
                    time.sleep(.01)
                self.assertEqual(events, [], "Stopping must never activate Odx")
                self.assertNotIn(user.GetForegroundWindow(), {context[0], read_state().get("cover_hwnd")},
                                 "Stopping must not activate either synthetic fixture window")
                self.assertEqual(bool(user.IsIconic(context[0])), original_minimized)
                after_rect = wintypes.RECT()
                user.GetWindowRect(context[0], ctypes.byref(after_rect))
                self.assertEqual(tuple(getattr(after_rect, key) for key in ("left", "top", "right", "bottom")),
                                 tuple(getattr(original_rect, key) for key in ("left", "top", "right", "bottom")))
            finally:
                user.UnhookWinEvent(event_hook)

    def test_vietnamese_host_stops_while_another_window_covers_it(self):
        self.background_stop("covered", "vi")

    def test_english_host_stops_while_minimized(self):
        self.background_stop("minimized", "en")

    def test_english_host_stops_while_offscreen(self):
        self.background_stop("offscreen", "en")

    def test_wrong_task_cannot_invoke_end_round(self):
        with self.fixture() as (backend, context, read_state, _):
            report = self.report(backend, context, task="different-task", invoke=True)
            self.assertFalse(report.get("invoked"))
            self.assertEqual(read_state()["clicks"], 0)

    def test_player_role_cannot_invoke_even_an_end_round_label(self):
        with self.fixture(role="player") as (backend, context, read_state, _):
            report = self.report(backend, context, invoke=True)
            self.assertFalse(report.get("invoked"))
            self.assertEqual(read_state()["clicks"], 0)

    def test_idle_start_round_label_is_never_invoked_as_stop(self):
        with self.fixture(phase="idle") as (backend, context, read_state, _):
            report = self.report(backend, context, invoke=True)
            self.assertFalse(report.get("invoked"))
            self.assertEqual(read_state()["clicks"], 0)

    def test_changed_native_task_is_rechecked_before_invoke(self):
        with self.fixture() as (backend, context, read_state, command):
            before = self.report(backend, context)
            self.assertEqual(before.get("status"), "stop_action")
            command.write_text("change-task", encoding="utf-8")
            deadline = time.monotonic() + 3
            while command.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(command.exists())
            report = self.report(backend, context, invoke=True)
            self.assertFalse(report.get("invoked"))
            self.assertEqual(read_state()["clicks"], 0)

    def test_different_window_context_is_never_used_for_stop(self):
        with self.fixture() as (backend, context, read_state, _):
            result = backend.run_ps_script(
                backend._host_stop_script(TASK, invoke=True, context=(context[0] + 1, context[1])), timeout=6)
            report = backend._host_stop_report(result)
            self.assertFalse(report and report.get("invoked"))
            self.assertEqual(read_state()["clicks"], 0)


if __name__ == "__main__":
    unittest.main()
