"""Room controls against isolated, invisible WPF windows, never actual Odx.

The real UIA scripts retain their HWND/PID and dialog-owner checks. Only native
window discovery and room API data are redirected to disposable local fixtures.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

if __package__:
    from .test_ready_native import _FixtureJob
else:
    from test_ready_native import _FixtureJob


FIXTURE = r'''
param([string]$StatePath,[string]$Role='player',[string]$Language='vi',
      [string]$InitialRoom='',[int]$DialogDelay=0)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RoomFixtureWindow {
    [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int n, int v);
    public static void NoActivate(IntPtr h) { SetWindowLong(h, -20, GetWindowLong(h, -20) | 0x08000080); }
}
'@
$script:w=New-Object System.Windows.Window
$w.Title=if ($Role -eq 'decoy') {'Join room'} else {'Odx Capture'}
$w.Width=420; $w.Height=260; $w.Left=20; $w.Top=20
$w.Opacity=0; $w.ShowActivated=$false; $w.ShowInTaskbar=$false
$w.Add_SourceInitialized({
    $script:hwnd=(New-Object System.Windows.Interop.WindowInteropHelper($w)).Handle.ToInt64()
    [RoomFixtureWindow]::NoActivate([IntPtr]$script:hwnd)
})
$script:opens=0; $script:submits=0; $script:creates=0; $script:readyClicks=0
$script:lastCode=''; $script:joined=($InitialRoom.Length -gt 0); $script:dialog=$null
$script:pendingRoom=$null; $script:pendingAt=$null; $script:dialogControlsAt=$null
$script:code=$null; $script:ok=$null; $script:dialogPanel=$null
$script:ready=$false
$panel=New-Object System.Windows.Controls.StackPanel
function Id($control, [string]$name) {
    [System.Windows.Automation.AutomationProperties]::SetAutomationId($control,$name)
    return $control
}
$script:player=Id (New-Object System.Windows.Controls.TextBox) 'PlayerBox'
$player.Text=if ($Role -eq 'host') {'HOST_TEST'} else {'PLAYER_TEST'}
$script:name=Id (New-Object System.Windows.Controls.TextBox) 'NameBox'
$name.Text='Native fixture'
$script:room=Id (New-Object System.Windows.Controls.TextBox) 'RoomBox'
$room.Text=$InitialRoom; $room.IsReadOnly=$true
$script:join=Id (New-Object System.Windows.Controls.Button) 'JoinButton'
$script:create=Id (New-Object System.Windows.Controls.Button) 'CreateButton'
$script:readyButton=Id (New-Object System.Windows.Controls.Button) 'StartStopButton'
function Labels {
    $join.Content=if ($script:joined) {
        if ($Language -eq 'vi') {'Rời room'} else {'Leave room'}
    } else {if ($Language -eq 'vi') {'Vào room'} else {'Join room'}}
    $create.Content=if ($script:joined -and $Role -eq 'host') {
        if ($Language -eq 'vi') {'Đóng room'} else {'Close room'}
    } else {if ($Language -eq 'vi') {'Tạo room'} else {'Create room'}}
    $readyButton.Content=if ($script:joined) {
        if ($Language -eq 'vi') {'Sẵn sàng (F10)'} else {"I'm ready (F10)"}
    } else {if ($Language -eq 'vi') {'Bắt đầu ghi'} else {'Start recording'}}
}
function Write-State {
    if (-not $script:hwnd) {return}
    $dialogHandle=0
    if ($script:dialog) {
        $dialogHandle=(New-Object System.Windows.Interop.WindowInteropHelper($script:dialog)).Handle.ToInt64()
    }
    $codeText=if ($script:code) {$script:code.Text} else {''}
    $state=@{hwnd=$script:hwnd;pid=$PID;joined=$script:joined;room=$room.Text;
        player=$player.Text;name=$name.Text;opens=$script:opens;submits=$script:submits;
        creates=$script:creates;ready_clicks=$script:readyClicks;last_code=$script:lastCode;
        dialog_hwnd=$dialogHandle;code=$codeText;ready=$script:ready}
    $temporary=$StatePath+'.tmp'
    [IO.File]::WriteAllText($temporary,($state|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $StatePath -Force
}
function Add-DialogControls {
    $script:code=Id (New-Object System.Windows.Controls.TextBox) 'CodeBox'
    $script:ok=Id (New-Object System.Windows.Controls.Button) 'OkButton'
    $cancel=Id (New-Object System.Windows.Controls.Button) 'CancelButton'
    $ok.Content=if ($Language -eq 'vi') {'Vào room'} else {'Join room'}
    $cancel.Content=if ($Language -eq 'vi') {'Hủy'} else {'Cancel'}
    $ok.IsEnabled=$false
    $code.Add_TextChanged({$script:ok.IsEnabled=($script:code.Text.Trim().Length -eq 6); Write-State})
    $ok.Add_Click({
        $script:submits++
        $script:lastCode=$script:code.Text.Trim()
        Write-State
        $script:dialog.DialogResult=$true
    })
    $cancel.Add_Click({$script:dialog.DialogResult=$false})
    foreach ($item in @($code,$ok,$cancel)) {$script:dialogPanel.Children.Add($item)|Out-Null}
    $script:dialogControlsAt=$null
    Write-State
}
$join.Add_Click({
    if ($script:joined) {throw 'A join operation must never invoke Leave room'}
    $script:opens++
    $script:dialog=New-Object System.Windows.Window
    $dialog.Owner=$w; $dialog.Title=if ($Language -eq 'vi') {'Vào room'} else {'Join room'}
    $dialog.Width=310; $dialog.Height=130; $dialog.Opacity=0
    $dialog.ShowActivated=$false; $dialog.ShowInTaskbar=$false
    $dialog.Add_SourceInitialized({
        [RoomFixtureWindow]::NoActivate((New-Object System.Windows.Interop.WindowInteropHelper($script:dialog)).Handle)
        Write-State
    })
    $script:dialogPanel=New-Object System.Windows.Controls.StackPanel
    $dialog.Content=$script:dialogPanel
    if ($DialogDelay -gt 0) {$script:dialogControlsAt=[DateTime]::UtcNow.AddMilliseconds($DialogDelay)}
    else {Add-DialogControls}
    $localDialog=$script:dialog
    $accepted=$localDialog.ShowDialog()
    $script:dialog=$null
    if ($accepted) {
        $script:pendingRoom=$script:lastCode
        $script:pendingAt=[DateTime]::UtcNow.AddMilliseconds(200)
    }
    Write-State
})
$create.Add_Click({
    if ($script:joined) {throw 'A create operation must never invoke Close room'}
    $script:creates++
    $script:pendingRoom='ABC123'
    $script:pendingAt=[DateTime]::UtcNow.AddMilliseconds(200)
    Write-State
})
$readyButton.Add_Click({$script:readyClicks++; $script:ready=$true; Write-State})
foreach ($item in @($player,$name,$room,$join,$create,$readyButton)) {$panel.Children.Add($item)|Out-Null}
Labels
if ($Role -eq 'decoy') {
    $panel.Children.Clear()
    $script:code=Id (New-Object System.Windows.Controls.TextBox) 'CodeBox'
    $code.Text='DECOY0'
    $script:ok=Id (New-Object System.Windows.Controls.Button) 'OkButton'
    $ok.Content='Join room'; $ok.Add_Click({$script:submits++; Write-State})
    $panel.Children.Add($code)|Out-Null; $panel.Children.Add($ok)|Out-Null
}
$w.Content=$panel
$script:timer=New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval=[TimeSpan]::FromMilliseconds(25)
$timer.Add_Tick({
    if ($script:dialogControlsAt -and [DateTime]::UtcNow -ge $script:dialogControlsAt) {Add-DialogControls}
    if ($script:pendingAt -and [DateTime]::UtcNow -ge $script:pendingAt) {
        $room.Text=$script:pendingRoom
        $script:joined=$true; $script:pendingAt=$null; Labels; Write-State
    }
})
$w.Add_ContentRendered({Write-State; $timer.Start()})
$w.Show()
[System.Windows.Threading.Dispatcher]::Run()
'''


@unittest.skipUnless(os.name == "nt", "Windows WPF/UIAutomation")
class RoomNativeTests(unittest.TestCase):
    @contextmanager
    def fixture(self, role="player", language="vi", initial_room="", dialog_delay=0):
        with tempfile.TemporaryDirectory(prefix="odx-room-native-") as temporary:
            folder=Path(temporary)
            script, state_path=folder / "fixture.ps1", folder / "state.json"
            script.write_text(FIXTURE, encoding="utf-8-sig")
            job, process=_FixtureJob(), None
            try:
                process=subprocess.Popen([
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Sta", "-File", str(script),
                    "-StatePath", str(state_path), "-Role", role, "-Language", language,
                    "-InitialRoom", initial_room, "-DialogDelay", str(dialog_delay)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                job.assign(process)

                def read_state():
                    for _ in range(50):
                        try:
                            return json.loads(state_path.read_text(encoding="utf-8-sig"))
                        except (OSError, ValueError):
                            time.sleep(.01)
                    self.fail("Could not read the room fixture state")

                deadline=time.monotonic()+12
                while not state_path.exists() and process.poll() is None and time.monotonic()<deadline:
                    time.sleep(.02)
                if not state_path.exists():
                    self.fail(process.stderr.read().decode(errors="replace") if process.poll() is not None else "Fixture startup timed out")
                yield read_state
            finally:
                job.close()
                if process is not None:
                    try:
                        process.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                    process.stderr.close()

    @contextmanager
    def scoped_backend(self, read_state):
        import odx_backend as backend
        state=read_state()
        hwnd, pid=state["hwnd"], state["pid"]
        with patch.object(backend,"_owned_uia_context",return_value=(hwnd,pid)), \
             patch.object(backend,"find_odx_window",return_value=hwnd), \
             patch.object(backend,"find_unmanaged_odx_window",return_value=None), \
             patch.object(backend,"is_managed_odx_window",side_effect=lambda value: value==hwnd):
            yield backend

    def join_action(self, backend, action, room="ABC123", player="PLAYER_TEST"):
        result=backend.run_ps_script(backend._player_join_script(room,player,"Player Name",action),timeout=5)
        report=backend._player_join_report(result)
        self.assertIsInstance(report,dict,getattr(result,"stderr",None))
        return report

    def wait_for(self, callback, predicate, timeout=4):
        deadline=time.monotonic()+timeout
        state=callback()
        while not predicate(state) and time.monotonic()<deadline:
            time.sleep(.03)
            state=callback()
        self.assertTrue(predicate(state),state)
        return state

    def _join_case(self, language, delay=0):
        with self.fixture(language=language,dialog_delay=delay) as read_state, self.scoped_backend(read_state) as backend:
            inspected=self.join_action(backend,"inspect")
            self.assertIn(inspected["status"],("can_open","waiting"))
            opened=self.join_action(backend,"open")
            self.assertIn(opened["status"],("opened","dialog"))
            self.wait_for(read_state,lambda state: state["dialog_hwnd"]!=0)
            if delay:
                self.wait_for(lambda:self.join_action(backend,"inspect"),lambda state:state["status"]=="dialog")
            submitted=self.join_action(backend,"submit")
            self.assertEqual(submitted["status"],"submitted")
            final=self.wait_for(read_state,lambda state:state["joined"])
            self.assertEqual((final["last_code"],final["room"]),("ABC123","ABC123"))
            self.assertEqual((final["opens"],final["submits"],final["ready_clicks"]),(1,1,0))
            self.assertEqual((final["player"],final["name"]),("PLAYER_TEST","Player Name"))
            in_room=self.join_action(backend,"inspect")
            self.assertEqual(in_room["status"],"in_room")
            repeated=self.join_action(backend,"open")
            self.assertEqual(repeated["status"],"in_room")
            self.assertEqual(read_state()["opens"],1)

    def test_vietnamese_dialog_sets_exact_code_and_submits_once(self):
        self._join_case("vi")

    def test_english_delayed_dialog_waits_for_code_control(self):
        self._join_case("en",900)

    def test_existing_different_room_never_invokes_leave_or_opens_dialog(self):
        with self.fixture(initial_room="OLD123") as read_state,self.scoped_backend(read_state) as backend:
            report=self.join_action(backend,"open")
            self.assertEqual(report["status"],"wrong_room")
            self.assertEqual((read_state()["opens"],read_state()["submits"]),(0,0))

    def test_existing_room_with_different_identity_is_not_overwritten(self):
        with self.fixture(initial_room="ABC123") as read_state,self.scoped_backend(read_state) as backend:
            report=self.join_action(backend,"open",player="OTHER_TEST")
            self.assertEqual(report["status"],"wrong_player")
            current=read_state()
            self.assertEqual(current["player"],"PLAYER_TEST")
            self.assertEqual((current["opens"],current["submits"]),(0,0))

    def test_foreign_process_dialog_is_never_filled_or_submitted(self):
        with self.fixture(role="decoy") as read_decoy,self.fixture() as read_state,self.scoped_backend(read_state) as backend:
            self.join_action(backend,"open")
            self.join_action(backend,"submit")
            self.wait_for(read_state,lambda state:state["joined"])
            decoy=read_decoy()
            self.assertEqual((decoy["code"],decoy["submits"]),("DECOY0",0))

    def test_joined_player_waits_for_settings_without_toggling_ready(self):
        with tempfile.TemporaryDirectory(prefix="odx-room-appdata-") as appdata, \
             self.fixture() as read_state,self.scoped_backend(read_state) as backend:
            def roster(*args,**kwargs):
                members=[{"player_id":"HOST_TEST","ready":True}]
                if read_state()["joined"]:
                    members.append({"player_id":"PLAYER_TEST","ready":False})
                return {"host":"HOST_TEST","members":members,"current_session":None}

            with patch.dict(os.environ,{"APPDATA":appdata}), \
                 patch.object(backend,"set_player_info"),patch.object(backend,"set_clipboard"), \
                 patch.object(backend,"fetch_room_status",side_effect=roster), \
                 patch.object(backend,"player_ensure_ready_in_room",side_effect=AssertionError("Ready must wait for settings")):
                worker=backend.player_auto_join_and_ready("ABC123","PLAYER_TEST","Player Name",
                                                         can_ready=lambda:False,timeout=12)
                worker.join(timeout=16)
                self.assertFalse(worker.is_alive(),"Join must have a bounded deadline")
                self.assertTrue(worker.success,worker.error)
                self.assertTrue(worker.joined)
                self.assertEqual(worker.status,"joined_waiting_ready")
                self.assertEqual(read_state()["ready_clicks"],0)

    def test_duplicate_host_identity_fails_before_changing_native_controls(self):
        with tempfile.TemporaryDirectory(prefix="odx-room-appdata-") as appdata, \
             self.fixture() as read_state,self.scoped_backend(read_state) as backend:
            original=read_state()
            with patch.dict(os.environ,{"APPDATA":appdata}), \
                 patch.object(backend,"set_player_info"),patch.object(backend,"set_clipboard"), \
                 patch.object(backend,"fetch_room_status",return_value={
                     "host":"HOST_TEST","current_session":None,
                     "members":[{"player_id":"HOST_TEST","ready":True}]}):
                worker=backend.player_auto_join_and_ready("ABC123","HOST_TEST","Duplicate Host",timeout=5)
                worker.join(timeout=8)
                self.assertFalse(worker.is_alive())
                self.assertFalse(worker.success)
                self.assertTrue(worker.error)
                current=read_state()
                self.assertEqual((current["player"],current["name"]),(original["player"],original["name"]))
                self.assertEqual((current["opens"],current["submits"],current["ready_clicks"]),(0,0,0))

    def test_host_create_is_once_and_existing_room_is_read_only(self):
        with self.fixture(role="host") as read_state,self.scoped_backend(read_state) as backend:
            inspected=backend._host_room_report(backend.run_ps_script(backend._host_room_script(False),timeout=5))
            self.assertIsInstance(inspected,dict)
            self.assertEqual(inspected["status"],"ready")
            created=backend._host_room_report(backend.run_ps_script(backend._host_room_script(True),timeout=5))
            self.assertIsInstance(created,dict)
            self.assertTrue(created["clicked"])
            self.wait_for(read_state,lambda state:state["joined"])
            again=backend._host_room_report(backend.run_ps_script(backend._host_room_script(True),timeout=5))
            self.assertEqual((again["status"],again["room_code"]),("room","ABC123"))
            self.assertEqual(read_state()["creates"],1)
