"""Run native UIA against only invisible synthetic game/Odx windows."""
import ctypes
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


GAME_FIXTURE = r'''
using System;
using System.IO;
using System.Windows.Forms;
public sealed class GameFixture : Form {
    protected override bool ShowWithoutActivation { get { return true; } }
    [STAThread] public static void Main(string[] args) {
        var window = new GameFixture();
        window.Text=args[0]; window.ShowInTaskbar=true; window.Opacity=0;
        window.StartPosition=FormStartPosition.Manual; window.Left=-32000; window.Top=-32000;
        window.Shown += delegate { File.WriteAllText(args[1], window.Handle.ToInt64().ToString()); };
        Application.Run(window);
    }
}
'''

ODX_FIXTURE = r'''
param([string]$StatePath,[string]$CommandPath,[string]$LiveLabel,[string]$Profile,[string]$ProcessName,[string]$Initial)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class GameFixtureWindow { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); }'
$script:w=New-Object System.Windows.Window
$w.Title='Odx Capture';$w.Width=420;$w.Height=230;$w.Opacity=0;$w.ShowInTaskbar=$false;$w.ShowActivated=$false
$panel=New-Object System.Windows.Controls.StackPanel
$script:source=New-Object System.Windows.Controls.ComboBox
$script:game=New-Object System.Windows.Controls.ComboBox
$script:audio=New-Object System.Windows.Controls.CheckBox
$script:refresh=New-Object System.Windows.Controls.Button
$script:start=New-Object System.Windows.Controls.Button
[System.Windows.Automation.AutomationProperties]::SetAutomationId($source,'SourceBox')
[System.Windows.Automation.AutomationProperties]::SetAutomationId($game,'GameBox')
[System.Windows.Automation.AutomationProperties]::SetAutomationId($audio,'AudioBox')
[System.Windows.Automation.AutomationProperties]::SetAutomationId($refresh,'RefreshButton')
[System.Windows.Automation.AutomationProperties]::SetAutomationId($start,'StartStopButton')
$audio.Content='Record audio';$audio.IsChecked=$false;$refresh.Content='Refresh';$start.Content='Start round (F10)'
$source.Items.Add('Primary monitor')|Out-Null
$source.Items.Add($ProcessName+'.exe (không chạy)')|Out-Null
$source.SelectedIndex=1
if($Initial -notin @('refresh','stale')) {$source.Items.Add($LiveLabel)|Out-Null}
$game.Items.Add('None (keep raw capture only)')|Out-Null
$game.Items.Add('Arma 3')|Out-Null;$game.Items.Add('Arma Reforger')|Out-Null
$game.SelectedIndex=0
$script:refreshes=0
if($Initial -eq 'locked') {$source.IsEnabled=$false;$game.IsEnabled=$false}
if($Initial -eq 'busy') {$start.Content='Kết thúc round (F10)'}
if($Initial -eq 'saving') {$start.Content='Đang lưu...'}
if($Initial -eq 'audio-locked') {$audio.IsEnabled=$false}
if($Initial -eq 'locked-correct') {
    $source.SelectedIndex=2;$game.SelectedItem=$Profile;$audio.IsChecked=$true
    $source.IsEnabled=$false;$game.IsEnabled=$false;$audio.IsEnabled=$false
}
foreach($control in @($source,$game,$audio,$refresh,$start)) {$panel.Children.Add($control)|Out-Null}
$w.Content=$panel
function Write-State {
    if(-not $script:hwnd) {return}
    $value=@{hwnd=$script:hwnd;pid=$PID;source=[string]$source.SelectedItem;game=[string]$game.SelectedItem;
             audio=($audio.IsChecked -eq $true);refreshes=$script:refreshes;source_open=$source.IsDropDownOpen;game_open=$game.IsDropDownOpen}
    [IO.File]::WriteAllText($StatePath,($value|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
}
$source.Add_SelectionChanged({Write-State})
$game.Add_SelectionChanged({
    # Native profile selection may reset the source. The tool must apply the source last.
    if($game.SelectedItem -eq $Profile) {$source.SelectedIndex=0}
    Write-State
})
$audio.Add_Checked({Write-State});$audio.Add_Unchecked({Write-State})
$refresh.Add_Click({
    $script:refreshes++
    if($Initial -eq 'refresh' -and -not $source.Items.Contains($LiveLabel)) {$source.Items.Add($LiveLabel)|Out-Null}
    Write-State
})
$script:timer=New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval=[TimeSpan]::FromMilliseconds(30)
$timer.Add_Tick({
    if(Test-Path -LiteralPath $CommandPath) {
        $command=[IO.File]::ReadAllText($CommandPath);Remove-Item -LiteralPath $CommandPath
        if($command -eq 'minimize') {[GameFixtureWindow]::ShowWindow([IntPtr]$script:hwnd,7)|Out-Null}
        if($command -eq 'hide') {[GameFixtureWindow]::ShowWindow([IntPtr]$script:hwnd,0)|Out-Null}
        Write-State
    }
})
$w.Add_ContentRendered({
    $script:hwnd=[System.Windows.Interop.WindowInteropHelper]::new($w).Handle.ToInt64()
    Write-State;$timer.Start()
})
$w.Show();[System.Windows.Threading.Dispatcher]::Run()
'''


@unittest.skipUnless(os.name == "nt", "Windows WPF/UIAutomation")
class GameCaptureNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="odx-game-native-")
        cls.addClassCleanup(cls._cleanup_fixture)
        cls.folder = Path(cls.temporary.name)
        source = cls.folder / "game.cs"
        source.write_text(GAME_FIXTURE, encoding="utf-8")
        cls.games = {}
        compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        for game, process in (("arma3", "arma3_x64"), ("arma-reforger", "ArmaReforgerSteam")):
            executable = cls.folder / (process + ".exe")
            result = subprocess.run([str(compiler), "/nologo", "/target:winexe", "/reference:System.Windows.Forms.dll",
                                     "/out:" + str(executable), str(source)], capture_output=True, text=True, timeout=15,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.games[game] = executable

    @classmethod
    def _cleanup_fixture(cls):
        for attempt in range(20):
            try:
                cls.temporary.cleanup()
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(.1)

    def _case(self, game="arma3", initial="normal", command=None, expect_error=None, audio_only=False, cancelled=False):
        import odx_backend as backend
        import odx_games as games
        with tempfile.TemporaryDirectory(prefix="fixture-", dir=self.folder) as temporary:
            folder = Path(temporary)
            state_path, command_path = folder / "state.json", folder / "command.txt"
            game_ready = folder / "game.txt"
            script = folder / "fixture.ps1"
            script.write_text(ODX_FIXTURE, encoding="utf-8-sig")
            title = "Synthetic game " + folder.name
            executable = self.games[game]
            live_label = title + " (" + executable.name + ")"
            job = _FixtureJob()
            processes, worker, timer = [], None, None
            try:
                fake_game = subprocess.Popen([str(executable), title, str(game_ready)],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(fake_game);job.assign(fake_game)
                self._wait_file(game_ready, fake_game)
                fixture = subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Sta", "-File", str(script),
                                            "-StatePath", str(state_path), "-CommandPath", str(command_path),
                                            "-LiveLabel", live_label, "-Profile", games.GAMES[game]["label"],
                                            "-ProcessName", executable.stem, "-Initial", initial],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                processes.append(fixture);job.assign(fixture)
                self._wait_file(state_path, fixture)
                def state():
                    for _ in range(30):
                        try:
                            return json.loads(state_path.read_text(encoding="utf-8-sig"))
                        except (OSError, ValueError):
                            time.sleep(.01)
                    self.fail("Fixture state is unavailable")
                before = state()
                if command:
                    command_path.write_text(command, encoding="utf-8")
                    deadline = time.monotonic() + 3
                    while command_path.exists() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertFalse(command_path.exists())
                foreground = ctypes.windll.user32.GetForegroundWindow()
                original_visible = bool(ctypes.windll.user32.IsWindowVisible(before["hwnd"]))
                original_minimized = bool(ctypes.windll.user32.IsIconic(before["hwnd"]))
                cancel = threading.Event()
                if cancelled:
                    timer = threading.Timer(.4, cancel.set);timer.start()
                with patch.object(backend, "_owned_uia_context", return_value=(before["hwnd"], before["pid"])):
                    start = time.monotonic()
                    worker = (games.ensure_capture_audio if audio_only else lambda **kw: games.set_game_capture(game, **kw))(
                        timeout=4 if initial == "stale" else 12, cancel_event=cancel)
                    worker.join(timeout=18)
                    self.assertFalse(worker.is_alive(), "Game configuration must have a bounded deadline")
                after = state()
                self.assertNotIn(ctypes.windll.user32.GetForegroundWindow(),
                                 {before["hwnd"], int(game_ready.read_text())} - {foreground},
                                 "Background setup must not activate its windows")
                self.assertEqual(bool(ctypes.windll.user32.IsWindowVisible(before["hwnd"])), original_visible)
                self.assertEqual(bool(ctypes.windll.user32.IsIconic(before["hwnd"])), original_minimized)
                if cancelled or expect_error:
                    self.assertFalse(worker.success, "Unsafe setup must not be accepted")
                    self.assertEqual((after["source"], after["game"], after["audio"]),
                                     (before["source"], before["game"], before["audio"]))
                    if expect_error:
                        self.assertIn(expect_error, worker.error)
                    if cancelled:
                        self.assertLess(time.monotonic()-start, 4)
                else:
                    self.assertTrue(worker.success, worker.error)
                    self.assertTrue(after["audio"])
                    if audio_only:
                        self.assertEqual((after["source"], after["game"]), (before["source"], before["game"]))
                    else:
                        self.assertEqual(after["source"], live_label)
                        self.assertEqual(after["game"], games.GAMES[game]["label"])
                        self.assertEqual(worker.process.lower(), executable.name.lower())
                        self.assertNotIn("không chạy", worker.source_label)
                    self.assertEqual(after["refreshes"], 1 if initial == "refresh" else 0)
                    self.assertFalse(after["source_open"])
                    self.assertFalse(after["game_open"])
            finally:
                if timer:
                    timer.cancel();timer.join(timeout=2)
                if worker and worker.is_alive():
                    worker.join(timeout=4)
                job.close()
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        process.kill();process.wait(timeout=2)
                    if process.stderr:
                        process.stderr.close()

    def _wait_file(self, path, process):
        deadline = time.monotonic() + 12
        while not path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.025)
        if not path.exists():
            self.fail(process.stderr.read().decode(errors="replace") if process.poll() is not None and process.stderr else "Fixture startup timed out")

    def test_arma3_source_profile_audio_with_hidden_native_window(self):
        self._case(command="hide")

    def test_reforger_refreshes_live_source_with_minimized_native_window(self):
        self._case(game="arma-reforger", initial="refresh", command="minimize")

    def test_stale_not_running_placeholder_never_counts_as_valid_source(self):
        self._case(initial="stale", expect_error="GAME_SOURCE_UNAVAILABLE")

    def test_capture_and_saving_block_all_native_configuration_changes(self):
        for initial in ("busy", "saving"):
            with self.subTest(initial=initial):
                self._case(initial=initial, expect_error="GAME_CAPTURE_BUSY")

    def test_locked_wrong_room_configuration_is_preserved(self):
        self._case(initial="locked", expect_error="GAME_CAPTURE_LOCKED")

    def test_disabled_correct_room_configuration_can_be_verified(self):
        self._case(initial="locked-correct")

    def test_test_mode_enables_audio_without_changing_desktop_or_raw_profile(self):
        self._case(audio_only=True)

    def test_disabled_audio_cannot_be_silently_accepted(self):
        self._case(initial="audio-locked", audio_only=True, expect_error="GAME_AUDIO_DISABLED")

    def test_loading_configuration_is_cancellable(self):
        self._case(initial="stale", cancelled=True)


if __name__ == "__main__":
    unittest.main()
