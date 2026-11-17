"""Desktop-source UIA against an isolated WPF fixture, never the real Odx UI."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

if __package__:
    from .test_ready_native import _FixtureJob
else:
    from test_ready_native import _FixtureJob


FIXTURE = r'''
param([string]$StatePath,[string]$CommandPath,[string]$Language,[string]$Initial)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
Add-Type -TypeDefinition @'
namespace CaptureFixture {
    public sealed class SourceItem {
        public string Label { get; private set; }
        public string Process { get; private set; }
        public SourceItem(string label, string process) { Label=label; Process=process; }
        public override string ToString() { return Label; }
    }
    public sealed class GameItem {
        public string Label { get; private set; }
        public string Path { get; private set; }
        public string WindowProcess { get; private set; }
        public string Game { get; private set; }
        public GameItem(string label, string path, string process, string game) {
            Label=label; Path=path; WindowProcess=process; Game=game;
        }
        public override string ToString() { return Label; }
    }
}
'@
$script:w=New-Object System.Windows.Window
$w.Title='Odx Capture'; $w.Width=500; $w.Height=180
$w.Left=30; $w.Top=30; $w.Opacity=0
$w.ShowInTaskbar=$false; $w.ShowActivated=$false
$panel=New-Object System.Windows.Controls.StackPanel
$script:source=New-Object System.Windows.Controls.ComboBox
$script:game=New-Object System.Windows.Controls.ComboBox
[System.Windows.Automation.AutomationProperties]::SetAutomationId($source,'SourceBox')
[System.Windows.Automation.AutomationProperties]::SetAutomationId($game,'GameBox')
$desktop=if ($Language -eq 'vi') {'Màn hình chính'} else {'Primary monitor'}
$raw=if ($Language -eq 'vi') {'Không có (chỉ giữ dữ liệu thô)'} else {'None (keep raw capture only)'}
$script:phase='created'
function Load-FixtureItems {
    # The native app uses object ItemsSource, with Label and ToString(), rather
    # than string items. This exercises WPF's real virtualized item providers.
    $source.ItemsSource=@(
        [CaptureFixture.SourceItem]::new($desktop,$null),
        [CaptureFixture.SourceItem]::new('ArmaReforgerSteam.exe','ArmaReforgerSteam'),
        [CaptureFixture.SourceItem]::new('User chosen source','user-source'))
    $game.ItemsSource=@(
        [CaptureFixture.GameItem]::new($raw,$null,$null,$null),
        [CaptureFixture.GameItem]::new('Arma Reforger','games/arma-reforger.json','ArmaReforgerSteam','Arma Reforger'),
        [CaptureFixture.GameItem]::new("User profile O'Brien",'games/user.json','user-source','User Game'))
    $source.SelectedIndex=-1; $game.SelectedIndex=-1
}
function Select-FixtureItems {
    $source.SelectedIndex=1; $game.SelectedIndex=1
    if ($Initial -ne 'production') {$source.SelectedIndex=0; $game.SelectedIndex=0}
    if ($Initial -eq 'test-disabled') {$source.IsEnabled=$false; $game.IsEnabled=$false}
    $script:phase='ready'
}
if ($Initial -notlike 'delayed-controls*') {
    $panel.Children.Add($source)|Out-Null; $panel.Children.Add($game)|Out-Null
}
if ($Initial -notlike 'delayed-*') {Load-FixtureItems; Select-FixtureItems}
$create=New-Object System.Windows.Controls.Button
$join=New-Object System.Windows.Controls.Button
$hotkey=New-Object System.Windows.Controls.TextBox
[System.Windows.Automation.AutomationProperties]::SetAutomationId($create,'CreateButton')
[System.Windows.Automation.AutomationProperties]::SetAutomationId($join,'JoinButton')
[System.Windows.Automation.AutomationProperties]::SetAutomationId($hotkey,'HotkeyBox')
$create.Content='Create room'; $join.Content='Join room'; $hotkey.Text='F10'
$panel.Children.Add($create)|Out-Null; $panel.Children.Add($join)|Out-Null; $panel.Children.Add($hotkey)|Out-Null
$w.Content=$panel
function Write-FixtureState {
    if (-not $script:hwnd) {return}
    $state=@{hwnd=$script:hwnd;pid=$PID;source=[string]$source.SelectedItem;game=[string]$game.SelectedItem;phase=$script:phase;
             source_open=$source.IsDropDownOpen;game_open=$game.IsDropDownOpen}
    [IO.File]::WriteAllText($StatePath,($state|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
}
$source.Add_SelectionChanged({Write-FixtureState})
$game.Add_SelectionChanged({
    # The native game callback may choose its process when leaving desktop.
    if ($game.SelectedIndex -eq 1 -and $source.SelectedIndex -eq 0) {$source.SelectedIndex=1}
    Write-FixtureState
})
$script:timer=New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval=[TimeSpan]::FromMilliseconds(30)
$timer.Add_Tick({
    $elapsed=([DateTime]::UtcNow-$script:renderedAt).TotalMilliseconds
    if ($Initial -like 'delayed-*' -and $Initial -ne 'delayed-never') {
        if ($script:phase -eq 'created' -and $elapsed -gt 900) {
            if ($Initial -like 'delayed-controls*') {
                $panel.Children.Add($source)|Out-Null; $panel.Children.Add($game)|Out-Null
            }
            Load-FixtureItems
            $script:phase='items-loaded'
            Write-FixtureState
        }
        if ($script:phase -eq 'items-loaded' -and $elapsed -gt 1700) {
            Select-FixtureItems
            Write-FixtureState
        }
    }
    if (Test-Path -LiteralPath $CommandPath) {
        $command=[IO.File]::ReadAllText($CommandPath)
        Remove-Item -LiteralPath $CommandPath
        if ($command -eq 'manual') {$game.SelectedIndex=2; $source.SelectedIndex=2}
        if ($command -eq 'disable-source') {$source.IsEnabled=$false}
        Write-FixtureState
    }
})
$w.Add_ContentRendered({
    $script:hwnd=(New-Object System.Windows.Interop.WindowInteropHelper($w)).Handle.ToInt64()
    $script:renderedAt=[DateTime]::UtcNow
    Write-FixtureState
    $timer.Start()
})
$w.Show()
[System.Windows.Threading.Dispatcher]::Run()
'''


@unittest.skipUnless(os.name == "nt", "Windows WPF/UIAutomation")
class DesktopCaptureNativeTests(unittest.TestCase):
    def _run_case(self, language, manual=False, disabled=False, preconfigured=False,
                  startup=None, cancelled=False, expires=False):
        import odx_backend as backend
        with tempfile.TemporaryDirectory(prefix="odx-desktop-native-") as temporary:
            folder = Path(temporary)
            script, state_path, command_path = (folder / name for name in ("fixture.ps1", "state.json", "command.txt"))
            script.write_text(FIXTURE, encoding="utf-8-sig")
            job = _FixtureJob()
            process = None
            workers = []
            cancellation_timer = None
            try:
                process = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Sta", "-File", str(script),
                     "-StatePath", str(state_path), "-CommandPath", str(command_path), "-Language", language,
                     "-Initial", startup or ("test-disabled" if preconfigured else "production")],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
                job.assign(process)
                deadline = time.monotonic() + 12
                while not state_path.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.03)
                if not state_path.exists():
                    self.fail(process.stderr.read().decode(errors="replace") if process.poll() is not None else "Fixture startup timed out")

                def read_state():
                    for _ in range(30):
                        try:
                            return json.loads(state_path.read_text(encoding="utf-8-sig"))
                        except (OSError, ValueError):
                            time.sleep(0.01)
                    self.fail("Could not read fixture state")

                initial = read_state()
                hwnd, pid = initial["hwnd"], initial["pid"]

                def run_mode(enabled):
                    if startup:
                        worker = backend.set_capture_test_mode(enabled, timeout=2 if expires else 8,
                                                               cancel_event=cancel_event, on_status=lambda *args: statuses.append(args))
                    else:
                        worker = backend.set_capture_test_mode(enabled)
                    workers.append(worker)
                    worker.join(timeout=18)
                    self.assertFalse(worker.is_alive(), "Configuration worker must finish")
                    return worker

                def command(value):
                    command_path.write_text(value, encoding="utf-8")
                    deadline = time.monotonic() + 3
                    while command_path.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertFalse(command_path.exists(), "Fixture command did not finish")

                with patch.object(backend, "_owned_uia_context", return_value=(hwnd, pid)), \
                     patch.object(backend, "_capture_test_snapshots", {}):
                    cancel_event, statuses = threading.Event(), []
                    if cancelled:
                        cancellation_timer = threading.Timer(.3, cancel_event.set)
                        cancellation_timer.start()
                    if disabled:
                        command("disable-source")
                    started = time.monotonic()
                    enabled = run_mode(True)
                    if cancelled or expires:
                        elapsed = time.monotonic() - started
                        self.assertFalse(enabled.success)
                        self.assertEqual(backend._capture_test_snapshots, {})
                        self.assertEqual(read_state()["phase"], "created")
                        self.assertTrue(enabled.error)
                        self.assertTrue(statuses, "A load wait must be reported before failing")
                        if cancelled:
                            self.assertTrue(cancel_event.is_set(), "A transient loading error must wait until cancelled")
                            self.assertLess(elapsed, 4, "Cancellation must not wait for the full deadline")
                        else:
                            self.assertGreaterEqual(elapsed, 1.7, "A missing selection must be retried until deadline")
                            self.assertLess(elapsed, 6, "Loading must have a bounded deadline")
                        return
                    if disabled:
                        self.assertFalse(enabled.success)
                        self.assertIn("CAPTURE_COMBO_DISABLED", enabled.error)
                        actual = read_state()
                        self.assertEqual((actual["source"], actual["game"]), (initial["source"], initial["game"]))
                        return
                    self.assertTrue(enabled.success, enabled.error)
                    actual = read_state()
                    if startup:
                        self.assertEqual(actual["phase"], "ready")
                        self.assertTrue(statuses, "Loading wait should be visible to the caller")
                    self.assertIn(actual["source"], backend._desktop_source_names)
                    self.assertIn(actual["game"], backend._raw_profile_names)
                    again = run_mode(True)
                    self.assertTrue(again.success, again.error)
                    self.assertFalse(again.changed, "An idempotent enable must retain the original snapshot")
                    if preconfigured:
                        self.assertFalse(enabled.changed)
                        self.assertEqual(backend._capture_test_snapshots, {})
                        self.assertTrue(run_mode(False).success)
                        self.assertEqual(read_state(), actual)
                        return
                    if manual:
                        command("manual")
                    restored = run_mode(False)
                    self.assertTrue(restored.success, restored.error)
                    final = read_state()
                    expected = ("User chosen source", "User profile O'Brien") if manual else (initial["source"], initial["game"])
                    self.assertEqual((final["source"], final["game"]), expected)
                    self.assertEqual(restored.preserved_user_choice, manual)
                    self.assertEqual(restored.restored, not manual)
                    self.assertEqual(backend._capture_test_snapshots, {})
                    self.assertTrue(run_mode(False).success)
                    self.assertFalse(final["source_open"])
                    self.assertFalse(final["game_open"])
            finally:
                if cancellation_timer is not None:
                    cancellation_timer.cancel()
                    cancellation_timer.join(timeout=2)
                for worker in workers:
                    if worker.is_alive():
                        worker.join(timeout=5)
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

    def test_vietnamese_desktop_and_raw_profile_restore_exact_original(self):
        self._run_case("vi")

    def test_english_preserves_later_manual_selection(self):
        self._run_case("en", manual=True)

    def test_disabled_source_rolls_back_profile(self):
        self._run_case("en", disabled=True)

    def test_prepared_test_room_verifies_disabled_selectors_without_changing_them(self):
        self._run_case("vi", preconfigured=True)

    def test_delayed_native_controls_wait_for_items_and_final_selection(self):
        self._run_case("vi", startup="delayed-controls", preconfigured=True)

    def test_delayed_native_items_and_blank_selection_are_not_fatal(self):
        self._run_case("en", startup="delayed-selection", preconfigured=True)

    def test_loading_native_capture_configuration_can_be_cancelled(self):
        self._run_case("vi", startup="delayed-never", cancelled=True)

    def test_loading_native_capture_configuration_has_bounded_deadline(self):
        self._run_case("en", startup="delayed-never", expires=True)


if __name__ == "__main__":
    unittest.main()
