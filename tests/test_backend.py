import importlib
import ast
import base64
import ctypes
import json
import re
import os
from pathlib import Path
import subprocess
import ssl
import tempfile
import types
import unittest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from unittest.mock import Mock, call, patch

import odx_backend as backend


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class BackendTests(unittest.TestCase):
    def test_version_badge_reads_the_selected_nested_install(self):
        root = os.path.join("selected", "odx-capture-0.3.8-win-x64", "odx-capture-0.3.8-win-x64")
        expected = os.path.join(root, "bin", "odx-app.exe")
        with patch.object(backend, "get_odx_root", return_value=root), \
             patch.object(backend.os.path, "exists", side_effect=lambda path: path == expected), \
             patch.object(backend, "get_odx_file_version", return_value=(0, 3, 8, 0)) as version:
            self.assertEqual(backend.get_odx_version_display(), "v0.3.8")
        version.assert_called_once_with(expected)

    def test_missing_install_never_claims_old_odx_version(self):
        with patch.object(backend, "get_odx_root", return_value="missing-install"), \
             patch.object(backend.os.path, "exists", return_value=False):
            self.assertEqual(backend.get_odx_version_display(), "chưa xác định")

    def test_import_has_no_process_or_handler_side_effects(self):
        with patch("atexit.register") as register, patch("subprocess.run") as run, patch.object(backend._runtime, "setup_process_protection") as protection:
            importlib.reload(backend)
        register.assert_not_called()
        run.assert_not_called()
        protection.assert_not_called()

    def test_clock_sync_nonzero_is_failure(self):
        with patch.object(backend.os.path, "exists", return_value=False), patch.object(backend.subprocess, "run", return_value=Mock(returncode=1)):
            self.assertFalse(backend.sync_system_clock()[0])

    def test_clock_sync_success_requires_successful_command(self):
        with patch.object(backend.os.path, "exists", return_value=False), patch.object(backend.subprocess, "run", return_value=Mock(returncode=0)) as run:
            self.assertTrue(backend.sync_system_clock()[0])
        self.assertEqual(run.call_args.args[0], ["w32tm", "/resync"])

    def test_clock_sync_engine_failure_falls_back(self):
        with patch.object(backend.os.path, "exists", return_value=True), patch.object(backend.subprocess, "run", side_effect=[Mock(returncode=1), Mock(returncode=0)]) as run:
            self.assertTrue(backend.sync_system_clock()[0])
        self.assertEqual(run.call_count, 2)

    def test_player_name_quoted_as_powershell_literal(self):
        with tempfile.TemporaryDirectory() as folder:
            config = str(Path(folder) / "odx-app.json")
            with patch.object(backend.os.path, "expandvars", return_value=config), patch.object(backend.threading, "Thread", ImmediateThread), patch.object(backend, "run_ps_script", return_value=Mock(stdout="")) as run:
                backend.set_player_info("player1", "O'Brien $HOME ` test")
            script = run.call_args.args[0]
            self.assertIn("$pnameVal = 'O''Brien $HOME ` test'", script)
            self.assertNotIn("SendWait($pnameVal)", script)

    def test_task_update_requires_exact_ui_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            config = str(Path(folder) / "odx-app.json")
            with patch.object(backend.os.path, "expandvars", return_value=config), patch.object(backend.threading, "Thread", ImmediateThread), patch.object(backend, "set_clipboard"), patch.object(backend, "run_ps_script", side_effect=[Mock(returncode=0, stdout=""), Mock(returncode=0, stdout="TASK_SET_OK")]) as run:
                failed = backend.set_task_box("task-01")
                succeeded = backend.set_task_box("task-02")
            self.assertFalse(failed.success)
            self.assertTrue(succeeded.success)
            self.assertIn("$actual -ceq $taskValue", run.call_args.args[0])

    def test_queued_preview_cannot_overwrite_latest_task(self):
        class QueuedThread(ImmediateThread):
            def start(self):
                pass

        with tempfile.TemporaryDirectory() as folder:
            config = str(Path(folder) / "odx-app.json")
            with patch.object(backend.os.path, "expandvars", return_value=config), patch.object(backend.threading, "Thread", QueuedThread), patch.object(backend, "set_clipboard"), patch.object(backend, "run_ps_script", return_value=Mock(returncode=0, stdout="TASK_SET_OK")) as run:
                old = backend.set_task_box("old-preview")
                latest = backend.set_task_box("final-task")
                old.target()
                latest.target()
            self.assertFalse(old.success)
            self.assertTrue(latest.success)
            self.assertEqual(run.call_count, 1)
            self.assertIn("$taskValue = 'final-task'", run.call_args.args[0])

    def test_auto_join_script_encodes_name_and_uses_exact_native_code_readback(self):
        name = "O'Brien $HOME " + chr(96) + " test"
        script = backend._player_join_script("ABC123", "player1", name, "submit")
        encoded = re.search(r"FromBase64String\('([^']+)'\)", script).group(1)
        payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
        self.assertEqual(payload["player_name"], name)
        self.assertEqual(payload["room_code"], "ABC123")
        self.assertIn("Set-JoinValue $dialog.code $request.room_code", script)
        self.assertIn("(Get-JoinValue $dialog.code) -cne $request.room_code", script)
        self.assertIn("::GetWindow($dialogHandle,4)", script)
        self.assertIn("$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$ownedWindowCondition)", script)
        for prohibited in ("SendKeys", "mouse_event", "SetCursorPos", "SetForegroundWindow", ".SetFocus(", ".Close("):
            self.assertNotIn(prohibited, script)

    def test_validation_delegates_to_shared_read_only_validator(self):
        validation = types.SimpleNamespace(validate_and_inspect_capture=Mock(return_value=False))
        with patch.dict("sys.modules", {"odx_validation": validation}):
            self.assertFalse(backend.validate_and_inspect_capture("capture", False))
        validation.validate_and_inspect_capture.assert_called_once_with("capture", is_room_mode=False, odx_root=backend.ODX_ROOT, on_message=None)

    def test_room_fetch_requires_verified_tls(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"members": []}'
        with patch.object(backend.urllib.request, "urlopen", return_value=response) as request:
            self.assertEqual(backend.fetch_room_status("ABC123"), {"members": []})
        context = request.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_room_fetch_reports_tls_failure(self):
        error = backend.urllib.error.URLError(ssl.SSLCertVerificationError("certificate failed"))
        with patch.object(backend.urllib.request, "urlopen", side_effect=error):
            result = backend.fetch_room_status("ABC123")
        self.assertEqual(result["error"], "TLS_ERROR")
        self.assertIn("certificate failed", result["message"])

    def test_invalid_room_code_makes_no_request(self):
        with patch.object(backend.urllib.request, "urlopen") as request:
            result = backend.fetch_room_status('abc/12')
        self.assertEqual(result["error"], "INVALID_CODE")
        request.assert_not_called()

    def test_failed_status_invalidates_previous_ready_roster(self):
        callback = Mock()
        tracker = backend.RoomTracker("ABC123", on_change_callback=callback)
        tracker.latest_data = {"host": "host", "members": [{"player_id": "host"}, {"player_id": "p2", "ready": True}]}
        tracker.last_success_at = 10
        with patch.object(backend.time, "monotonic", return_value=11):
            self.assertTrue(tracker.is_all_ready(2))
        with patch.object(backend, "log_event"):
            tracker._invalidate_status("certificate failed")
        self.assertFalse(tracker.is_all_ready(2))
        self.assertIsNone(tracker.latest_data)
        self.assertEqual(tracker.last_error, "certificate failed")
        callback.assert_called_once_with("ABC123", None, [])

    def test_old_room_status_cannot_confirm_ready(self):
        tracker = backend.RoomTracker("ABC123")
        tracker.latest_data = {"host": "host", "members": [{"player_id": "host"}]}
        tracker.last_success_at = 0
        with patch.object(backend.time, "monotonic", return_value=11):
            self.assertFalse(tracker.is_all_ready(1))




class WindowsHelperTests(unittest.TestCase):
    def test_odx_toggle_sends_only_f10_and_never_the_fix_choice_key(self):
        user = Mock()
        user.MapVirtualKeyW.return_value = 68
        with patch.object(backend.ctypes, "windll", types.SimpleNamespace(user32=user)), \
             patch.object(backend.time, "sleep"):
            self.assertTrue(backend.send_toggle_hotkey(123))
        user.MapVirtualKeyW.assert_called_once_with(0x79, 0)
        self.assertEqual(user.keybd_event.call_args_list, [
            call(0x79, 68, 0, 0), call(0x79, 68, 0x0002, 0)])

    def clipboard_apis(self):
        user, kernel = Mock(), Mock()
        user.OpenClipboard.return_value = True
        user.EmptyClipboard.return_value = True
        user.SetClipboardData.return_value = 111
        user.GetClipboardData.return_value = 111
        kernel.GlobalAlloc.return_value = 111
        kernel.GlobalLock.return_value = 222
        kernel.GetConsoleWindow.return_value = 333
        return user, kernel

    def test_failed_allocation_does_not_claim_clipboard_success(self):
        user, kernel = self.clipboard_apis()
        kernel.GlobalAlloc.return_value = 0
        with patch.object(backend, "_clipboard_apis", return_value=(user, kernel)), patch.object(backend.subprocess, "run", return_value=Mock(returncode=1)):
            self.assertFalse(backend.set_clipboard("text"))
        user.OpenClipboard.assert_not_called()
        kernel.GlobalFree.assert_not_called()

    def test_failed_global_lock_frees_untransferred_memory(self):
        user, kernel = self.clipboard_apis()
        kernel.GlobalLock.return_value = 0
        with patch.object(backend, "_clipboard_apis", return_value=(user, kernel)), patch.object(backend.subprocess, "run", return_value=Mock(returncode=1)):
            self.assertFalse(backend.set_clipboard("text"))
        kernel.GlobalFree.assert_called_once_with(111)
        user.OpenClipboard.assert_not_called()

    def test_failed_transfer_closes_clipboard_and_frees_memory(self):
        user, kernel = self.clipboard_apis()
        user.SetClipboardData.return_value = 0
        with patch.object(backend, "_clipboard_apis", return_value=(user, kernel)), patch.object(backend.ctypes, "memmove"), patch.object(backend.subprocess, "run", return_value=Mock(returncode=1)):
            self.assertFalse(backend.set_clipboard("text"))
        user.CloseClipboard.assert_called_once()
        kernel.GlobalFree.assert_called_once_with(111)
        kernel.GlobalUnlock.assert_called_once_with(111)

    def test_successful_transfer_keeps_system_owned_memory(self):
        user, kernel = self.clipboard_apis()
        with patch.object(backend, "_clipboard_apis", return_value=(user, kernel)), patch.object(backend.ctypes, "memmove"), patch.object(backend.subprocess, "run") as run:
            self.assertTrue(backend.set_clipboard("text"))
        user.OpenClipboard.assert_called_once_with(333)
        user.CloseClipboard.assert_called_once()
        kernel.GlobalFree.assert_not_called()
        run.assert_not_called()

    def test_copy_error_releases_global_lock_and_memory(self):
        user, kernel = self.clipboard_apis()
        with patch.object(backend, "_clipboard_apis", return_value=(user, kernel)), patch.object(backend.ctypes, "memmove", side_effect=OSError("copy failed")), patch.object(backend.subprocess, "run", return_value=Mock(returncode=1)):
            self.assertFalse(backend.set_clipboard("text"))
        kernel.GlobalUnlock.assert_called_once_with(111)
        kernel.GlobalFree.assert_called_once_with(111)
        user.OpenClipboard.assert_not_called()

    def test_read_error_always_unlocks_and_closes_clipboard(self):
        user, kernel = self.clipboard_apis()
        with patch.object(backend, "_clipboard_apis", return_value=(user, kernel)), patch.object(backend.ctypes, "wstring_at", side_effect=OSError("read failed")), patch.object(backend.subprocess, "run", return_value=Mock(returncode=1, stdout="invalid")):
            self.assertEqual(backend.get_clipboard(), "")
        kernel.GlobalUnlock.assert_called_once_with(111)
        user.CloseClipboard.assert_called_once()

    def test_window_handle_signature_preserves_64_bit_value(self):
        user = Mock()
        user.FindWindowW.return_value = 2 ** 40
        executable = os.path.join(backend.ODX_ROOT, "bin", "odx-app.exe")
        with patch.object(backend.ctypes, "WinDLL", return_value=user), patch.object(backend, "_window_process_path", return_value=(123, executable)):
            self.assertEqual(backend.find_odx_window(), 2 ** 40)
        self.assertEqual(ctypes.sizeof(user.FindWindowW.restype), ctypes.sizeof(ctypes.c_void_p))

    def test_window_from_another_installation_is_rejected(self):
        user = Mock()
        user.FindWindowW.return_value = 2 ** 40
        with patch.object(backend.ctypes, "WinDLL", return_value=user), patch.object(backend, "_window_process_path", return_value=(123, "C:/unrelated-install/bin/odx-app.exe")):
            self.assertEqual(backend.find_odx_window(), 0)

    def test_selected_installation_also_requires_managed_process_ownership(self):
        executable = os.path.join(backend.ODX_ROOT, "bin", "odx-app.exe")
        with patch.object(backend, "_window_process_path", return_value=(123, executable)), patch.object(backend._runtime, "owns_pid", return_value=False):
            self.assertFalse(backend.is_managed_odx_window(1234))
        with patch.object(backend, "_window_process_path", return_value=(123, executable)), patch.object(backend._runtime, "owns_pid", return_value=True):
            self.assertTrue(backend.is_managed_odx_window(1234))

    def test_image_path_query_closes_64_bit_process_handle(self):
        user, kernel = Mock(), Mock()
        kernel.OpenProcess.return_value = 2 ** 40

        def get_pid(hwnd, pointer):
            pointer._obj.value = 123
            return 1

        def get_path(handle, flags, buffer, size):
            buffer.value = "C:/selected/bin/odx-app.exe"
            return 1

        user.GetWindowThreadProcessId.side_effect = get_pid
        kernel.QueryFullProcessImageNameW.side_effect = get_path
        with patch.object(backend.ctypes, "WinDLL", side_effect=lambda name, **kwargs: user if name == "user32" else kernel):
            self.assertEqual(backend._window_process_path(2 ** 41), (123, "C:/selected/bin/odx-app.exe"))
        kernel.CloseHandle.assert_called_once_with(2 ** 40)

    def test_process_check_matches_executable_not_window_title(self):
        kernel = Mock()
        kernel.CreateToolhelp32Snapshot.return_value = 2 ** 40

        def first(handle, pointer):
            pointer._obj.szExeFile = "ArmaReforgerSteam.exe"
            return True

        kernel.Process32FirstW.side_effect = first
        kernel.Process32NextW.return_value = False
        with patch.object(backend.ctypes, "WinDLL", return_value=kernel) as dll, patch.object(backend.subprocess, "run") as run:
            self.assertFalse(backend.is_game_running("CustomGame.exe"))
        self.assertEqual(dll.call_args.args[0], "kernel32")
        kernel.CloseHandle.assert_called_once_with(2 ** 40)
        run.assert_not_called()

    def test_native_process_check_accepts_arma_alias(self):
        kernel = Mock()
        kernel.CreateToolhelp32Snapshot.return_value = 2 ** 40

        def first(handle, pointer):
            pointer._obj.szExeFile = "ArmaReforger.exe"
            return True

        kernel.Process32FirstW.side_effect = first
        with patch.object(backend.ctypes, "WinDLL", return_value=kernel):
            self.assertTrue(backend.is_game_running("ArmaReforgerSteam"))
        kernel.CloseHandle.assert_called_once_with(2 ** 40)

    def test_tasklist_fallback_checks_real_csv_process_and_pid(self):
        csv = 'INFO: looking for CustomGame.exe\n"CustomGame.exe","123","Console","1","1,024 K"\n'
        with patch.object(backend.ctypes, "WinDLL", side_effect=OSError("unavailable")), patch.object(backend.subprocess, "run", return_value=Mock(returncode=0, stdout=csv)):
            self.assertTrue(backend.is_game_running("CustomGame"))
        with patch.object(backend.ctypes, "WinDLL", side_effect=OSError("unavailable")), patch.object(backend.subprocess, "run", return_value=Mock(returncode=0, stdout='"CustomGame.exe","not-a-pid"\n')):
            self.assertFalse(backend.is_game_running("CustomGame"))


class UnmanagedOdxWindowTests(unittest.TestCase):
    def find_window(self, processes, owned_pids=()):
        user = Mock()
        # Genuine Odx must also be found when minimized, hidden, or off screen.
        user.IsWindowVisible.return_value = False
        user.IsIconic.return_value = True

        def window_text(hwnd, buffer, size):
            buffer.value = "Odx Capture"
            return len(buffer.value)

        def enumerate_windows(callback, parameter):
            for hwnd in processes:
                if not callback(hwnd, parameter):
                    break
            return True

        def process_path(hwnd):
            identity = processes[hwnd]
            if isinstance(identity, Exception):
                raise identity
            return identity

        user.GetWindowTextW.side_effect = window_text
        user.EnumWindows.side_effect = enumerate_windows
        with patch.object(backend.ctypes, "WinDLL", return_value=user), patch.object(backend, "_window_process_path", side_effect=process_path), patch.object(backend._runtime, "owns_pid", side_effect=lambda pid: pid in owned_pids) as owns_pid:
            result = backend.find_unmanaged_odx_window()
        return result, user, owns_pid

    def test_same_title_powershell_python_and_unrelated_apps_do_not_block_launch(self):
        for executable in (
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            r"C:\Python\python.exe",
            r"C:\Python\pythonw.exe",
            r"C:\other\odx_capture_test.exe",
        ):
            with self.subTest(executable=executable):
                found, _, owns_pid = self.find_window({592568: (32336, executable)})
                self.assertIsNone(found)
                owns_pid.assert_not_called()

    def test_unknown_process_identity_does_not_turn_caption_into_odx(self):
        for identity in ((None, None), (123, None), (123, ""), (None, r"C:\Odx\odx-app.exe"), OSError("access denied")):
            with self.subTest(identity=identity):
                found, _, owns_pid = self.find_window({100: identity})
                self.assertIsNone(found)
                owns_pid.assert_not_called()

    def test_real_unowned_odx_in_selected_installation_is_detected(self):
        for name in ("odx-app.exe", "Odx Capture.exe"):
            with self.subTest(name=name):
                executable = os.path.join(backend.ODX_ROOT, "bin", name)
                found, _, owns_pid = self.find_window({2 ** 40: (123, executable)})
                self.assertEqual(found, 2 ** 40)
                owns_pid.assert_called_once_with(123)

    def test_real_unowned_odx_in_another_installation_is_detected_when_hidden(self):
        for name in ("ODX-APP.EXE", "Odx Capture.exe"):
            with self.subTest(name=name):
                found, user, _ = self.find_window({100: (123, os.path.join("C:/other-install/bin", name))})
                self.assertEqual(found, 100)
                user.IsWindowVisible.assert_not_called()
                user.IsIconic.assert_not_called()

    def test_scan_continues_past_fake_unreadable_and_managed_windows(self):
        executable = os.path.join(backend.ODX_ROOT, "bin", "odx-app.exe")
        found, _, owns_pid = self.find_window({
            100: (11, r"C:\Python\python.exe"),
            101: OSError("window process exited"),
            102: (12, executable),
            103: (13, "C:/other-install/bin/odx-app.exe"),
        }, owned_pids={12})
        self.assertEqual(found, 103)
        self.assertEqual([call.args[0] for call in owns_pid.call_args_list], [12, 13])

    def test_managed_odx_does_not_block_launch(self):
        executable = os.path.join(backend.ODX_ROOT, "bin", "odx-app.exe")
        found, _, owns_pid = self.find_window({100: (123, executable)}, owned_pids={123})
        self.assertIsNone(found)
        owns_pid.assert_called_once_with(123)


@unittest.skipUnless(os.name == "nt", "Native Windows recorder timing")
class RecordingTimingTests(unittest.TestCase):
    def setUp(self):
        self.executable = os.path.join(backend.ODX_ROOT, "bin", "odx.exe")
        self.output = os.path.join(backend.ODX_ROOT, "data", "captures", "task-01")

    def recorder(self, *, pid=123, executable=None, output=None, origin="103000", operation="record", extra=()):
        executable = executable or self.executable
        arguments = [executable, operation, "--out", output or self.output,
                     "--grid-origin-qpc-ms", origin, *extra]
        return {"pid": pid, "path": executable, "command": subprocess.list2cmdline(arguments)}

    def read(self, records, *, owned=True, live=True, qpc=100000, now=1000):
        response = Mock(returncode=0, stdout=json.dumps(records))
        with patch.object(backend, "run_ps_script", return_value=response) as query, \
             patch.object(backend._runtime, "owns_pid", side_effect=owned if isinstance(owned, list) else None,
                          return_value=owned) as ownership, \
             patch.object(backend, "_is_live_recorder_process", side_effect=live if isinstance(live, list) else None,
                          return_value=live) as liveness, \
             patch.object(backend, "_query_performance_counter_ms", return_value=qpc), \
             patch.object(backend.time, "monotonic", return_value=now):
            result = backend.get_recording_start_time("task-01")
        return result, query, ownership, liveness

    def test_future_native_grid_origin_preserves_room_countdown(self):
        result, query, _, _ = self.read([self.recorder()])
        self.assertEqual(result, 1003)
        self.assertEqual(query.call_args.kwargs["timeout"], 4)
        result, _, _, _ = self.read([self.recorder(origin="98000")])
        self.assertEqual(result, 998, "Late observation must retain the native start, not reset the clock")

    def test_exact_owned_recorder_task_and_installation_are_required(self):
        cases = (
            self.recorder(executable=os.path.join(backend.ODX_ROOT, "other-install", "odx.exe")),
            self.recorder(output=self.output + "-fix1"),
            self.recorder(output=os.path.join(backend.ODX_ROOT, "data", "captures", "task-02")),
            self.recorder(operation="input"),
            self.recorder(pid=True),
            self.recorder(pid=0),
            self.recorder(extra=("--out", self.output)),
            self.recorder(extra=("--grid-origin-qpc-ms", "103000")),
        )
        for record in cases:
            with self.subTest(record=record):
                result, _, _, _ = self.read([record])
                self.assertIsNone(result)
        result, _, _, _ = self.read([self.recorder()], owned=False)
        self.assertIsNone(result)
        result, _, _, _ = self.read([self.recorder()], live=False)
        self.assertIsNone(result)

    def test_recorder_exiting_or_losing_ownership_after_snapshot_is_rejected(self):
        for owned, live in (([True, False], True), (True, [True, False])):
            with self.subTest(owned=owned, live=live):
                result, _, _, _ = self.read([self.recorder()], owned=owned, live=live)
                self.assertIsNone(result)

    def test_missing_invalid_or_ambiguous_origins_never_use_a_countdown_guess(self):
        for origin in ("nan", "inf", "-1", "0", "not-a-number"):
            with self.subTest(origin=origin):
                result, _, _, _ = self.read([self.recorder(origin=origin)])
                self.assertIsNone(result)
        result, _, _, _ = self.read([])
        self.assertIsNone(result)
        result, _, _, _ = self.read([self.recorder(), self.recorder(pid=124)])
        self.assertIsNone(result)
        result, _, _, _ = self.read([self.recorder()], qpc=float("nan"))
        self.assertIsNone(result)

    def test_bad_unrelated_record_does_not_hide_valid_owned_recorder(self):
        result, _, _, _ = self.read([None, {}, self.recorder(origin="bad"), self.recorder()])
        self.assertEqual(result, 1003)

    def test_invalid_task_and_failed_query_never_claim_recording_started(self):
        with patch.object(backend, "run_ps_script") as query:
            for task in (None, "", "../task", "task/other", "x" * 129):
                self.assertIsNone(backend.get_recording_start_time(task))
            query.assert_not_called()
        for response in (None, Mock(returncode=1, stdout="[]"), Mock(returncode=0, stdout="bad JSON"),
                         Mock(returncode=0, stdout='{"pid":123}')):
            with self.subTest(response=response), patch.object(backend, "run_ps_script", return_value=response):
                self.assertIsNone(backend.get_recording_start_time("task-01"))

    def test_windows_argv_parser_preserves_quoted_output_and_never_evaluates_text(self):
        arguments = [r"C:\Program Files\Odx\odx.exe", "record", "--out", r"C:\captures\task 01",
                     "--grid-origin-qpc-ms", "1234.5", "literal$(`whoami`)\"value"]
        self.assertEqual(backend._windows_command_line_arguments(subprocess.list2cmdline(arguments)), arguments)

    def test_native_live_process_check_rejects_exited_and_replaced_images_and_closes_handle(self):
        expected = os.path.normcase(os.path.realpath(self.executable))
        for exit_code, image, success in ((259, self.executable, True), (0, self.executable, False),
                                          (259, r"C:\other\odx.exe", False)):
            with self.subTest(exit_code=exit_code, image=image):
                kernel = Mock()
                kernel.OpenProcess.return_value = 2 ** 40
                def read_exit(handle, value):
                    value._obj.value = exit_code
                    return True
                def read_image(handle, flags, buffer, length):
                    buffer.value = image
                    return True
                kernel.GetExitCodeProcess.side_effect = read_exit
                kernel.QueryFullProcessImageNameW.side_effect = read_image
                with patch.object(backend.ctypes, "WinDLL", return_value=kernel):
                    self.assertEqual(backend._is_live_recorder_process(123, expected), success)
                kernel.CloseHandle.assert_called_once_with(2 ** 40)


class LiveLogFollowerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "capture.log"
        self.path.write_bytes(b"")

    def append(self, data, path=None):
        with (path or self.path).open("ab") as stream:
            stream.write(data.encode("utf-8") if isinstance(data, str) else data)

    def test_partial_line_is_retained_until_newline(self):
        follower = backend.LiveLogFollower(str(self.path))
        self.append("recording task-01 in")
        self.assertEqual(follower.check_new_lines(), [])
        self.assertEqual(follower.last_pos, 0)
        self.append("to C:/capture")
        self.assertEqual(follower.check_new_lines(), [])
        self.assertIsNone(follower.current_recording)
        self.append("\n")
        self.assertEqual(follower.check_new_lines(), ["recording task-01 into C:/capture"])
        self.assertEqual(follower.current_recording, "task-01")
        self.assertEqual(follower.check_new_lines(), [])

    def test_native_stop_timestamp_is_converted_to_monotonic_and_cleared_on_new_capture(self):
        follower = backend.LiveLogFollower(str(self.path))
        native_time = "2026-11-14 13:20:12.057"
        epoch = backend.datetime.strptime(native_time, "%Y-%m-%d %H:%M:%S.%f").timestamp()
        with patch.object(backend.time, "time", return_value=epoch + 7), \
             patch.object(backend.time, "monotonic", return_value=500):
            follower.parse_line(native_time + " stop task-01 (round_end)")
        self.assertEqual(follower.current_stopped, ("task-01", "round_end"))
        self.assertEqual(follower.current_stopped_at, 493)
        follower.parse_line("recording task-02 into C:/capture")
        self.assertIsNone(follower.current_stopped_at)
        follower.parse_line("stop task-02 (user)")
        self.assertIsNone(follower.current_stopped_at, "Missing native time must not be invented")

    def test_native_cancelled_final_result_unlocks_cancel_flow_without_pass(self):
        follower = backend.LiveLogFollower(str(self.path))
        follower.current_recording = "task-01"
        follower.last_all_ok = "task-01"
        path = r"C:\captures\task-01.cancelled-20261114-131847"
        follower.parse_line("2026-11-14 13:18:47.735 task-01: round cancelled, recording kept locally only (" + path + ")")
        self.assertEqual(follower.last_round_result, ("task-01", "cancelled"))
        self.assertEqual(follower.last_kept_path, path)
        self.assertIsNone(follower.current_recording)
        self.assertIsNone(follower.last_all_ok)

    def test_coalesced_arm_record_stop_and_final_result_release_capture_activity(self):
        follower = backend.LiveLogFollower(str(self.path))
        self.append("armed for task-01\nrecording task-01 into C:/capture\n"
                    "stop task-01 (round_end)\nround task-01: all ok\n")
        self.assertEqual(len(follower.check_new_lines()), 4)
        self.assertIsNone(follower.last_armed)
        self.assertIsNone(follower.current_recording)
        self.assertEqual(follower.current_stopped, ("task-01", "round_end"))
        self.assertEqual(follower.last_round_result, ("task-01", "all ok"))

    def test_final_result_or_cancel_releases_matching_activity_without_stop_line(self):
        for terminal in ("round task-01: all ok", "round task-01: RECORD AGAIN · not done",
                         "task-01: round cancelled, recording kept locally only (C:/captures/task-01)"):
            with self.subTest(terminal=terminal):
                follower = backend.LiveLogFollower(str(self.path))
                follower.parse_line("armed for task-01")
                follower.parse_line("recording task-01 into C:/capture")
                follower.parse_line(terminal)
                self.assertIsNone(follower.last_armed)
                self.assertIsNone(follower.current_recording)
                self.assertEqual(follower.last_round_result[0], "task-01")

    def test_local_kept_result_is_not_a_terminal_capture_event(self):
        follower = backend.LiveLogFollower(str(self.path))
        follower.parse_line("armed for task-01")
        follower.parse_line("recording task-01 into C:/capture")
        follower.parse_line("task-01 done: kept in C:/capture/task-01; ok")
        self.assertEqual(follower.last_armed, "task-01")
        self.assertEqual(follower.current_recording, "task-01")

    def test_older_task_terminal_events_do_not_clear_newer_capture_or_armed_task(self):
        for terminal in ("stop task-01 (round_end)", "round task-01: all ok",
                         "task-01: round cancelled, recording kept locally only (C:/captures/task-01)"):
            with self.subTest(terminal=terminal):
                follower = backend.LiveLogFollower(str(self.path))
                follower.parse_line("armed for task-02")
                follower.parse_line("recording task-02 into C:/capture")
                follower.parse_line(terminal)
                self.assertEqual(follower.last_armed, "task-02")
                self.assertEqual(follower.current_recording, "task-02")
                self.assertIsNone(follower.current_stopped)

    def test_older_stop_ends_matching_capture_but_preserves_next_task_armed_flag(self):
        follower = backend.LiveLogFollower(str(self.path))
        follower.parse_line("recording task-01 into C:/capture")
        follower.parse_line("armed for task-02")
        follower.parse_line("stop task-01 (round_end)")
        self.assertIsNone(follower.current_recording)
        self.assertEqual(follower.last_armed, "task-02")
        self.assertEqual(follower.current_stopped, ("task-01", "round_end"))

    def test_concurrent_readers_parse_each_line_once(self):
        follower = backend.LiveLogFollower(str(self.path))
        self.append("".join(f"armed for task-{index:02d}\n" for index in range(20)))
        barrier = threading.Barrier(2)
        original = follower.parse_line

        def slow_parse(line):
            time.sleep(0.001)
            original(line)

        def read():
            barrier.wait(timeout=2)
            return follower.check_new_lines()

        with patch.object(follower, "parse_line", side_effect=slow_parse) as parse:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = pool.submit(read), pool.submit(read)
                lines = first.result(timeout=3) + second.result(timeout=3)
        self.assertEqual(len(lines), 20)
        self.assertEqual(len(set(lines)), 20)
        self.assertEqual(parse.call_count, 20)
        self.assertEqual(follower.last_armed, "task-19")

    def test_truncation_restarts_at_beginning(self):
        self.path.write_text("old line with substantial length\n", encoding="utf-8")
        follower = backend.LiveLogFollower(str(self.path))
        self.path.write_text("armed for new\n", encoding="utf-8")
        self.assertEqual(follower.check_new_lines(), ["armed for new"])
        self.assertEqual(follower.last_armed, "new")

    def test_rewrite_to_larger_size_is_detected_by_cursor_anchor(self):
        self.path.write_text("old\n", encoding="utf-8")
        follower = backend.LiveLogFollower(str(self.path))
        self.path.write_text("armed for longer-replacement\n", encoding="utf-8")
        self.assertEqual(follower.check_new_lines(), ["armed for longer-replacement"])

    def test_file_replacement_is_read_from_start(self):
        self.path.write_text("old contents\n", encoding="utf-8")
        follower = backend.LiveLogFollower(str(self.path))
        replacement = self.path.with_name("replacement.log")
        replacement.write_text("armed for replaced\n", encoding="utf-8")
        replacement.replace(self.path)
        self.assertEqual(follower.check_new_lines(), ["armed for replaced"])

    def test_daily_rotation_preserves_recording_and_reads_new_file(self):
        today = self.path.with_name("today.log")
        current = [str(self.path)]
        with patch.object(backend, "get_latest_log_path", side_effect=lambda: current[0]):
            follower = backend.LiveLogFollower()
            self.append("recording current into C:/capture\n")
            follower.check_new_lines()
            today.write_text("armed for next\n", encoding="utf-8")
            current[0] = str(today)
            self.assertEqual(follower.check_new_lines(), ["armed for next"])
            self.assertEqual(follower.current_recording, "current")
            self.append("stop current (user)\n", today)
            follower.check_new_lines()
        self.assertEqual(follower.current_stopped, ("current", "user"))
        self.assertEqual(follower.log_path, str(today))

    def test_explicit_path_does_not_switch_to_daily_log(self):
        with patch.object(backend, "get_latest_log_path") as latest:
            follower = backend.LiveLogFollower(str(self.path))
            follower.check_new_lines()
        latest.assert_not_called()

    def test_reset_to_end_clears_all_previous_round_state(self):
        follower = backend.LiveLogFollower(str(self.path))
        attributes = ("room_code", "last_armed", "current_recording", "current_stopped", "current_stopped_at",
                      "last_round_result", "last_all_ok", "last_kept_path", "id_conflict",
                      "clock_drift_error", "task_in_use_error")
        for name in attributes:
            setattr(follower, name, "old")
        self.append("recording old into C:/capture\n")
        follower.reset_to_end()
        for name in attributes:
            self.assertIsNone(getattr(follower, name), name)
        self.assertEqual(follower.check_new_lines(), [])
        self.append("recording new into C:/capture\n")
        follower.check_new_lines()
        self.assertEqual(follower.current_recording, "new")

    def test_direct_parse_line_and_explicit_replay_still_work(self):
        self.append("armed for existing\n")
        follower = backend.LiveLogFollower(str(self.path))
        self.assertEqual(follower.check_new_lines(from_start=True), ["armed for existing"])
        follower.parse_line("in room ABC123")
        self.assertEqual(follower.room_code, "ABC123")


class ManagedAutomationTests(unittest.TestCase):
    def test_foreign_window_blocks_automation_before_spawning(self):
        with patch.object(backend, "find_unmanaged_odx_window", return_value=999), patch.object(backend, "find_odx_window") as selected, patch.object(backend.subprocess, "Popen") as launch:
            self.assertIsNone(backend.run_ps_script("Write-Output 'must not run'", timeout=0))
        selected.assert_not_called()
        launch.assert_not_called()

    def test_owned_context_rechecks_process_path_and_ownership(self):
        owned_path = str(Path(backend.ODX_ROOT) / "bin" / "odx-app.exe")
        with patch.object(backend, "find_unmanaged_odx_window", return_value=None), patch.object(backend, "find_odx_window", return_value=123), patch.object(backend, "is_managed_odx_window", return_value=True), patch.object(backend, "_window_process_path", return_value=(321, owned_path)), patch.object(backend._runtime, "owns_pid", return_value=True):
            self.assertEqual(backend._owned_uia_context(0), (123, 321))
        with patch.object(backend, "find_unmanaged_odx_window", return_value=None), patch.object(backend, "find_odx_window", return_value=123), patch.object(backend, "is_managed_odx_window", return_value=True), patch.object(backend, "_window_process_path", return_value=(321, owned_path)), patch.object(backend._runtime, "owns_pid", return_value=False):
            self.assertIsNone(backend._owned_uia_context(0))

    def test_new_owned_window_can_finish_starting_within_timeout(self):
        owned_path = str(Path(backend.ODX_ROOT) / "bin" / "odx-app.exe")
        with patch.object(backend, "find_unmanaged_odx_window", return_value=None), patch.object(backend, "find_odx_window", side_effect=[0, 123]), patch.object(backend, "is_managed_odx_window", return_value=True), patch.object(backend, "_window_process_path", return_value=(321, owned_path)), patch.object(backend._runtime, "owns_pid", return_value=True), patch.object(backend.time, "monotonic", return_value=0), patch.object(backend.time, "sleep") as pause:
            self.assertEqual(backend._owned_uia_context(15), (123, 321))
        pause.assert_called_once_with(0.05)

    def test_worker_is_registered_hidden_and_preserves_unicode_output(self):
        child = Mock(returncode=0)
        child.communicate.return_value = ("Tên người chơi: O'Brien", "")
        user_code = "Write-Output 'O''Brien __ODX_PID__'"
        with patch.object(backend, "_owned_uia_context", return_value=(123, 321)), patch.object(backend.subprocess, "Popen", return_value=child) as launch, patch.object(backend, "register_managed_process") as own:
            result = backend.run_ps_script(user_code, timeout=5)
        own.assert_called_once_with(child)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "Tên người chơi: O'Brien")
        self.assertEqual(launch.call_args.kwargs["encoding"], "utf-8-sig")
        self.assertEqual(launch.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        bootstrap = base64.b64decode(launch.call_args.args[0][-1]).decode("utf-16le")
        self.assertIn("ReadToEnd", bootstrap)
        self.assertEqual(launch.call_args.kwargs["stdin"], subprocess.PIPE)
        code = child.communicate.call_args.kwargs["input"]
        self.assertIn("$script:OdxTargetPid = 321", code)
        self.assertIn("[IntPtr][Int64]123", code)
        self.assertIn("ProcessIdProperty", code)
        self.assertIn(user_code, code)  # User text is not rewritten to inject scope.
        child.kill.assert_not_called()

    def test_timeout_kills_and_reaps_managed_worker(self):
        child = Mock()
        child.poll.return_value = None
        child.communicate.side_effect = [subprocess.TimeoutExpired("powershell", 5), ("partial", "")]
        with patch.object(backend, "_owned_uia_context", return_value=(123, 321)), patch.object(backend.subprocess, "Popen", return_value=child), patch.object(backend, "register_managed_process") as own:
            self.assertIsNone(backend.run_ps_script("Start-Sleep 30", timeout=5))
        own.assert_called_once_with(child)
        child.kill.assert_called_once_with()
        self.assertEqual(child.communicate.call_count, 2)
        self.assertEqual(child.communicate.call_args.kwargs["timeout"], 2)

    def test_long_unicode_script_uses_stdin_instead_of_windows_command_line(self):
        child = Mock(returncode=0)
        child.communicate.return_value = ("Sẵn sàng", "")
        script = "#" + "large script definition " * 4000 + "\nWrite-Output 'Sẵn sàng'"
        with patch.object(backend, "_owned_uia_context", return_value=(123, 321)), patch.object(backend.subprocess, "Popen", return_value=child) as launch, patch.object(backend, "register_managed_process"):
            result = backend.run_ps_script(script)
        self.assertEqual(result.stdout, "Sẵn sàng")
        self.assertLess(sum(len(part) for part in launch.call_args.args[0]), 2048)
        self.assertTrue(child.communicate.call_args.kwargs["input"].endswith(script))
        self.assertNotIn(script, " ".join(launch.call_args.args[0]))

    def test_registration_failure_does_not_leave_untracked_worker(self):
        child = Mock()
        child.poll.return_value = None
        child.communicate.return_value = ("", "")
        with patch.object(backend, "_owned_uia_context", return_value=(123, 321)), patch.object(backend.subprocess, "Popen", return_value=child), patch.object(backend, "register_managed_process", side_effect=OSError("job unavailable")):
            self.assertIsNone(backend.run_ps_script("Write-Output 'ok'"))
        child.kill.assert_called_once_with()
        child.communicate.assert_called_once_with(timeout=2)

    def test_all_static_templates_scope_main_window_dialogs_and_fallbacks(self):
        source = Path(backend.__file__).read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        templates = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                     and isinstance(node.value, str) and ("::RootElement" in node.value or
                          (("ODX_HOST_ROOM:" in node.value or "ODX_PLAYER_READY:" in node.value) and "Get-OwnedOdxMainWindow" in node.value))]
        self.assertGreaterEqual(len(templates), 5)
        self.assertTrue(any('ODX_PLAYER_JOIN:' in code for code in templates))
        for code in templates:
            with self.subTest(code=code[:70]):
                self.assertNotIn("Get-Process -Name", code)
                self.assertNotIn("::Children, $cName)", code)
                self.assertNotIn("::Children, [System.Windows.Automation.Condition]::TrueCondition", code)
                self.assertNotIn("::FromHandle($p.MainWindowHandle)", code)
                for line in code.splitlines():
                    if "$root.Find" in line and "::Children" in line:
                        self.assertIn("$script:OdxProcessCondition", line)
                    if "Get-Process" in line:
                        self.assertIn("-Id $script:OdxTargetPid", line)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell parser")
    def test_scoped_templates_parse_without_executing_automation(self):
        tree = ast.parse(Path(backend.__file__).read_text(encoding="utf-8-sig"))
        templates = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                     and isinstance(node.value, str) and ("::RootElement" in node.value or
                          (("ODX_HOST_ROOM:" in node.value or "ODX_PLAYER_READY:" in node.value) and "Get-OwnedOdxMainWindow" in node.value))]
        with tempfile.TemporaryDirectory(prefix="odx-ps-parser-") as folder:
            for index, code in enumerate(templates):
                path = Path(folder) / f"template-{index}.ps1"
                path.write_text(backend._uia_script_prefix(2 ** 40 + 123, 321) + code, encoding="utf-8-sig")
            parse = "$errors=@(); Get-ChildItem -LiteralPath $args[0] -Filter '*.ps1' | ForEach-Object { $tokens=$null; $err=$null; [System.Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$err) | Out-Null; $errors += $err }; if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
            # Pass the path as a PS literal; parsing creates no window/process automation.
            parse = parse.replace("$args[0]", backend._ps_literal(folder))
            result = subprocess.run(["powershell", "-NoProfile", "-Command", parse], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class HostRoomCreationTests(unittest.TestCase):
    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

    class Cancellation:
        def __init__(self, clock):
            self.clock, self.stopped = clock, False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, seconds):
            self.clock.now += seconds
            return self.stopped

    @staticmethod
    def result(status='waiting', room_code='', can_create=False, clicked=False, error='', returncode=0,
               player_id='host', is_host=True):
        report = dict(status=status, room_code=room_code, can_create=can_create, clicked=clicked, error=error,
                      player_id=player_id, is_host=is_host, hwnd=111, pid=222)
        return types.SimpleNamespace(returncode=returncode, stdout='ODX_HOST_ROOM:' + json.dumps(report))

    def run_flow(self, reports, supplier=None, timeout=2, stopped=False, after_call=None, status=None):
        clock = self.Clock()
        cancellation = self.Cancellation(clock)
        cancellation.stopped = stopped
        queued = iter(reports)
        scripts = []

        def run(script, **kwargs):
            scripts.append(script)
            clock.now += 0.1
            if after_call:
                after_call(script, cancellation)
            return next(queued, self.result())

        statuses = status or Mock()
        with patch.object(backend.threading, 'Thread', ImmediateThread), patch.object(backend.time, 'monotonic', clock.monotonic), patch.object(backend, 'run_ps_script', side_effect=run), \
             patch.object(backend, '_owned_uia_context', return_value=(111, 222)), \
             patch.object(backend, 'fetch_room_status', return_value={'host': 'host', 'members': [{'player_id': 'host'}]}):
            worker = backend.host_auto_create_room(on_status=statuses, cancel_event=cancellation,
                                                   timeout=timeout, room_code_supplier=supplier)
        return worker, scripts, statuses

    def test_creates_once_and_waits_for_room_acknowledgement(self):
        worker, scripts, statuses = self.run_flow([
            self.result('ready', can_create=True),
            self.result('clicked', can_create=True, clicked=True),
            self.result('waiting'),
            self.result('room', room_code='TA3S4Z'),
        ])
        self.assertTrue(worker.success)
        self.assertEqual(worker.room_code, 'TA3S4Z')
        self.assertEqual(worker.error, '')
        self.assertTrue(worker.clicked)
        self.assertEqual(sum('$allowCreate = $true' in script for script in scripts), 1)
        self.assertEqual(statuses.call_args.args[1], 'SUCCESS')
        self.assertIn('TA3S4Z', statuses.call_args.args[0])

    def test_existing_valid_room_does_not_click_create(self):
        worker, scripts, _ = self.run_flow([self.result('room', room_code='ABC123')])
        self.assertTrue(worker.success)
        self.assertFalse(worker.click_attempted)
        self.assertEqual(len(scripts), 1)
        self.assertNotIn('$allowCreate = $true', scripts[0])

    def test_placeholder_room_values_never_report_success(self):
        for placeholder in ('Tạo room...', 'Create room', 'Create', 'abc123', 'ABC12', 'ABC1234', 'ＡＢＣ１２３'):
            with self.subTest(placeholder=placeholder):
                worker, scripts, statuses = self.run_flow([self.result('room', room_code=placeholder)], timeout=0.8)
                self.assertFalse(worker.success)
                self.assertEqual(worker.room_code, '')
                self.assertTrue(worker.error)
                self.assertFalse(any(call.args[1] == 'SUCCESS' for call in statuses.call_args_list))

    def test_fresh_server_code_has_no_arbitrary_word_blacklist(self):
        worker, scripts, _ = self.run_flow([self.result('room', room_code='CREATE')], supplier=lambda: 'CREATE')
        self.assertTrue(worker.success)
        self.assertEqual(worker.room_code, 'CREATE')
        self.assertEqual(len(scripts), 1)

    def test_disabled_or_not_yet_created_button_is_polled_before_one_click(self):
        worker, scripts, _ = self.run_flow([
            self.result('waiting', error='Create room button is not ready.'),
            self.result('ready', can_create=True),
            self.result('room', room_code='XYZ789', clicked=True),
        ])
        self.assertTrue(worker.success)
        self.assertEqual(sum('$allowCreate = $true' in script for script in scripts), 1)
        self.assertIn('$allowCreate = $false', scripts[0])
        self.assertIn('$allowCreate = $false', scripts[1])

    def test_lost_click_result_never_causes_a_second_create_command(self):
        worker, scripts, _ = self.run_flow([
            self.result('ready', can_create=True), None,
            self.result('ready', can_create=True),
            self.result('room', room_code='ABC123'),
        ])
        self.assertTrue(worker.success)
        self.assertTrue(worker.click_attempted)
        self.assertEqual(sum('$allowCreate = $true' in script for script in scripts), 1)

    def test_click_without_room_confirmation_is_not_success(self):
        worker, scripts, statuses = self.run_flow([
            self.result('ready', can_create=True),
            self.result('clicked', clicked=True),
        ], timeout=1)
        self.assertFalse(worker.success)
        self.assertTrue(worker.clicked)
        self.assertTrue(worker.error)
        self.assertEqual(sum('$allowCreate = $true' in script for script in scripts), 1)
        self.assertEqual(statuses.call_args.args[1], 'ERROR')

    def test_fresh_log_supplier_requires_native_host_proof_after_uncertain_ui_result(self):
        values = iter([None, None, 'Q7V8W9'])
        worker, scripts, _ = self.run_flow([
            self.result('ready', can_create=True), None, self.result(), self.result('room', room_code='Q7V8W9'),
        ], supplier=lambda: next(values, 'Q7V8W9'))
        self.assertTrue(worker.success)
        self.assertEqual(worker.room_code, 'Q7V8W9')
        self.assertEqual(sum('$allowCreate = $true' in script for script in scripts), 1)

    def test_log_code_alone_never_unlocks_host_without_native_host_confirmation(self):
        worker, scripts, _ = self.run_flow([self.result('ready', can_create=True), None], supplier=lambda: 'ABC123', timeout=.7)
        self.assertFalse(worker.success)
        self.assertEqual(worker.room_code, '')
        self.assertEqual(sum('$allowCreate = $true' in script for script in scripts), 1)

    def test_cancellation_before_start_does_not_touch_ui(self):
        worker, scripts, _ = self.run_flow([], stopped=True)
        self.assertFalse(worker.success)
        self.assertFalse(scripts)
        self.assertIn('hủy', worker.error)

    def test_cancellation_after_click_attempt_stops_all_followup_work(self):
        def cancel_after_click(script, cancellation):
            if '$allowCreate = $true' in script:
                cancellation.set()
        worker, scripts, _ = self.run_flow([
            self.result('ready', can_create=True), self.result('clicked', clicked=True),
        ], after_call=cancel_after_click)
        self.assertFalse(worker.success)
        self.assertEqual(len(scripts), 2)
        self.assertEqual(sum('$allowCreate = $true' in script for script in scripts), 1)
        self.assertIn('hủy', worker.error)

    def test_nonzero_or_malformed_report_cannot_confirm_room(self):
        for invalid in (None, self.result('room', room_code='ABC123', returncode=1),
                        types.SimpleNamespace(returncode=0, stdout='ODX_HOST_ROOM:bad json'),
                        types.SimpleNamespace(returncode=0, stdout='ODX_HOST_ROOM:[1,2]')):
            with self.subTest(invalid=invalid):
                worker, _, _ = self.run_flow([invalid], timeout=0.5)
                self.assertFalse(worker.success)

    def test_concurrent_create_operation_is_rejected_without_click(self):
        with backend._host_room_creation_lock:
            worker, scripts, statuses = self.run_flow([])
        self.assertFalse(worker.success)
        self.assertFalse(scripts)
        self.assertIn('đang chạy', worker.error)
        self.assertEqual(statuses.call_args.args[1], 'ERROR')

    def test_bad_timeout_reports_error_without_touching_ui(self):
        for timeout in (0, -1, float('inf'), float('nan'), 'not a number'):
            with self.subTest(timeout=timeout):
                worker, scripts, _ = self.run_flow([], timeout=timeout)
                self.assertFalse(worker.success)
                self.assertFalse(scripts)
                self.assertTrue(worker.error)

    def test_status_callback_errors_do_not_change_creation_result(self):
        worker, _, _ = self.run_flow([self.result('room', room_code='ABC123')], status=Mock(side_effect=RuntimeError('closed GUI')))
        self.assertTrue(worker.success)

    def test_static_template_supports_native_ids_and_exact_localized_labels(self):
        script = backend._host_room_script(False)
        self.assertIn("@('CreateButton', 'CreateRoomButton', 'RoomCreateButton')", script)
        self.assertIn("'Tạo room'", script)
        self.assertIn("'Create room'", script)
        self.assertNotIn('.IsOffscreen', script)
        self.assertIn("-cmatch '^[A-Z0-9]{6}$'", script)
        self.assertIn('$button.Current.ProcessId -eq $script:OdxTargetPid', script)
        self.assertEqual(script.count('$invoke.Invoke()'), 1)
        self.assertNotIn('SendKeys', script)
        self.assertNotIn('mouse_event', script)


class PlayerJoinTransactionTests(unittest.TestCase):
    def run_flow(self, statuses, *, collision=False, server_confirms=True, cancelled=False):
        clock = HostRoomCreationTests.Clock()
        cancellation = HostRoomCreationTests.Cancellation(clock)
        cancellation.stopped = cancelled
        queue = iter(statuses)
        actions, entered = [], [False]

        def request(script, **kwargs):
            payload = json.loads(base64.b64decode(re.search(r"FromBase64String\('([^']+)'\)", script).group(1)))
            actions.append(payload['action'])
            status = next(queue, 'dialog')
            if status is None:
                return None
            if status == 'in_room':
                entered[0] = True
            report = dict(status=status, room_code='ABC123', player_id='player1', hwnd=111, pid=222,
                          invoked=payload['action'] in ('open', 'submit'), code_set=payload['action'] == 'submit')
            return Mock(returncode=0, stdout='ODX_PLAYER_JOIN:' + json.dumps(report))

        def room(code):
            return {'host': 'player1' if collision else 'host', 'members': [{'player_id': 'host'}] +
                    ([{'player_id': 'player1'}] if entered[0] and server_confirms else [])}

        with patch.object(backend.threading, 'Thread', ImmediateThread), \
             patch.object(backend.time, 'monotonic', clock.monotonic), \
             patch.object(backend, '_owned_uia_context', return_value=(111, 222)), \
             patch.object(backend, 'run_ps_script', side_effect=request), \
             patch.object(backend, 'fetch_room_status', side_effect=room), \
             patch.object(backend, 'player_ensure_ready_in_room') as ready, \
             patch.object(backend, 'cleanup_all_odx') as cleanup:
            worker = backend.player_auto_join_and_ready('ABC123', 'player1', "O'Brien", timeout=1,
                                                        cancel_event=cancellation, can_ready=lambda: False)
        cleanup.assert_not_called()
        ready.assert_not_called()
        return worker, actions

    def test_settings_gate_does_not_block_actual_join_and_server_confirmation(self):
        worker, actions = self.run_flow(['can_open', 'opened', 'dialog', 'submitted', 'in_room'])
        self.assertTrue(worker.success)
        self.assertTrue(worker.joined)
        self.assertFalse(worker.ready)
        self.assertEqual(worker.status, 'joined_waiting_ready')
        self.assertEqual(actions.count('open'), 1)
        self.assertEqual(actions.count('submit'), 1)

    def test_host_id_collision_fails_before_any_native_ui_call(self):
        worker, actions = self.run_flow([], collision=True)
        self.assertFalse(worker.joined)
        self.assertEqual(worker.status, 'identity_conflict')
        self.assertFalse(actions)

    def test_lost_submit_response_is_not_retried_or_reported_joined(self):
        worker, actions = self.run_flow(['can_open', 'opened', 'dialog', None])
        self.assertFalse(worker.success)
        self.assertFalse(worker.joined)
        self.assertEqual(actions.count('submit'), 1)
        self.assertEqual(worker.status, 'timeout')

    def test_native_room_text_without_server_membership_is_not_a_join(self):
        worker, actions = self.run_flow(['in_room'] * 8, server_confirms=False)
        self.assertFalse(worker.joined)
        self.assertEqual(worker.status, 'timeout')
        self.assertNotIn('open', actions)
        self.assertNotIn('submit', actions)

    def test_wrong_native_player_cannot_adopt_matching_server_player_id(self):
        worker, actions = self.run_flow(['wrong_player'])
        self.assertFalse(worker.success)
        self.assertEqual(worker.status, 'wrong_player')
        self.assertNotIn('open', actions)

    def test_cancel_before_join_does_not_touch_native_ui_or_close_odx(self):
        worker, actions = self.run_flow([], cancelled=True)
        self.assertFalse(worker.success)
        self.assertEqual(worker.status, 'cancelled')
        self.assertFalse(actions)

    def test_cleanup_requires_explicit_tool_closing_flag(self):
        with patch.object(backend._runtime, 'cleanup_managed_processes') as cleanup:
            backend.cleanup_all_odx()
            cleanup.assert_called_once_with(kill_gui=True, tool_closing=False)
            cleanup.reset_mock()
            backend.cleanup_all_odx(tool_closing=True)
            cleanup.assert_called_once_with(kill_gui=True, tool_closing=True)


class PlayerReadyTests(unittest.TestCase):
    class Clock:
        now = 0.0
        def monotonic(self):
            return self.now

    class Cancellation:
        def __init__(self, clock):
            self.clock, self.stopped = clock, False
        def is_set(self):
            return self.stopped
        def set(self):
            self.stopped = True
        def wait(self, seconds):
            self.clock.now += seconds
            return self.stopped

    @staticmethod
    def report(status='ready_action', room='ABC123', player='p1', invoked=False, error=''):
        payload={'status':status,'room_code':room,'player_id':player,
                 'button_state':'ready_action','invoked':invoked,'error':error}
        return subprocess.CompletedProcess([],0,'ODX_PLAYER_READY:'+json.dumps(payload),'')

    @staticmethod
    def room(ready=False, **updates):
        data={'host':'host','members':[{'player_id':'p1','ready':ready}]}
        data.update(updates)
        return data

    def run_ready(self, reports, server=None, player='p1', can_ready=None, cancellation=None, timeout=2, fallback=None):
        clock=self.Clock()
        cancellation=cancellation or self.Cancellation(clock)
        status=Mock()
        reports=iter(reports)
        last=[self.report('already_ready')]
        def script(code, **kwargs):
            clock.now += .05
            try: last[0]=next(reports)
            except StopIteration: pass
            return last[0]
        if server is None: server=self.room(False)
        fetch_kwargs={'side_effect':server} if callable(server) or isinstance(server,list) else {'return_value':server}
        with patch.object(backend.threading,'Thread',ImmediateThread), patch.object(backend.time,'monotonic',clock.monotonic), patch.object(backend,'run_ps_script',side_effect=script) as run, patch.object(backend,'fetch_room_status',**fetch_kwargs) as fetch:
            if fallback is None:
                worker=backend.player_ensure_ready_in_room('ABC123',on_status=status,player_id=player,cancel_event=cancellation,can_ready=can_ready,timeout=timeout)
            else:
                with patch.object(backend,'_temporary_ready_window_access',return_value=fallback):
                    worker=backend.player_ensure_ready_in_room('ABC123',on_status=status,player_id=player,cancel_event=cancellation,can_ready=can_ready,timeout=timeout)
        return worker, run, fetch, status

    def test_invoke_is_not_success_until_fresh_exact_member_ready(self):
        worker,run,fetch,status=self.run_ready(
            [self.report(),self.report('invoked',invoked=True),self.report('already_ready')],
            server=[self.room(False),self.room(True)])
        self.assertTrue(worker.success)
        self.assertEqual(worker.status,'confirmed')
        self.assertTrue(worker.invoked)
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(sum('$allowInvoke = $true' in c.args[0] for c in run.call_args_list),1)
        self.assertEqual(status.call_args.args[1],'SUCCESS')

    def test_lost_ack_never_causes_second_toggle_or_false_success(self):
        worker,run,_,status=self.run_ready([self.report(),None,self.report()])
        self.assertFalse(worker.success)
        self.assertEqual(sum('$allowInvoke = $true' in c.args[0] for c in run.call_args_list),1)
        self.assertFalse(any(c.args[1]=='SUCCESS' for c in status.call_args_list))

    def test_already_ready_server_confirmation_needs_no_invoke(self):
        worker,run,_,_=self.run_ready([self.report('already_ready')],server=self.room(True))
        self.assertTrue(worker.success)
        self.assertFalse(worker.invoked)
        self.assertFalse(any('$allowInvoke = $true' in c.args[0] for c in run.call_args_list))

    def test_ui_already_ready_without_server_confirmation_is_not_success(self):
        worker,run,_,_=self.run_ready([self.report('already_ready')])
        self.assertFalse(worker.success)
        self.assertFalse(worker.invoked)
        self.assertFalse(any('$allowInvoke = $true' in c.args[0] for c in run.call_args_list))

    def test_recording_server_and_recording_button_are_never_toggled(self):
        worker,run,_,status=self.run_ready([],server=self.room(current_session={'task':'task-1'}))
        self.assertEqual(worker.status,'recording')
        run.assert_not_called()
        self.assertEqual(status.call_args.args[1],'INFO')
        worker,run,_,_=self.run_ready([self.report('recording')])
        self.assertEqual(worker.status,'recording')
        self.assertFalse(worker.invoked)
        self.assertEqual(run.call_count,1)

    def test_wrong_room_player_and_solo_are_not_invoked(self):
        for state in ('wrong_room','wrong_player','not_in_room','blocked'):
            with self.subTest(state=state):
                worker,run,_,_=self.run_ready([self.report(state)])
                self.assertFalse(worker.success)
                self.assertEqual(worker.status,state)
                self.assertFalse(any('$allowInvoke = $true' in c.args[0] for c in run.call_args_list))

    def test_other_ready_member_does_not_confirm_this_player(self):
        worker,run,_,_=self.run_ready([self.report()],server=self.room(members=[{'player_id':'other','ready':True}]))
        self.assertFalse(worker.success)
        self.assertEqual(worker.status,'not_in_room')
        self.assertFalse(worker.invoked)

    def test_member_problem_and_host_identity_never_trigger_ready(self):
        for data in (self.room(members=[{'player_id':'p1','ready':False,'problem':'audio missing'}]),self.room(host='p1')):
            with self.subTest(data=data):
                worker,run,_,_=self.run_ready([self.report()],server=data)
                self.assertEqual(worker.status,'blocked')
                self.assertFalse(worker.invoked)

    def test_unavailable_server_is_not_treated_as_pending_or_success(self):
        worker,run,_,_=self.run_ready([self.report()],server={'error':'NETWORK_ERROR','message':'offline'})
        self.assertFalse(worker.success)
        self.assertEqual(worker.status,'unverified')
        self.assertFalse(any('$allowInvoke = $true' in c.args[0] for c in run.call_args_list))

    def test_no_explicit_player_id_is_derived_from_ui_then_verified(self):
        worker,_,fetch,_=self.run_ready([self.report('already_ready')],server=self.room(True),player=None)
        self.assertTrue(worker.success)
        self.assertEqual(worker.player_id,'p1')
        fetch.assert_called_with('ABC123')

    def test_cancel_before_start_and_guard_change_before_invoke(self):
        worker,run,fetch,_=self.run_ready([],can_ready=lambda:False)
        self.assertEqual(worker.status,'cancelled')
        run.assert_not_called()
        fetch.assert_not_called()
        decisions=iter([True,True,True,False])
        worker,run,_,_=self.run_ready([self.report()],can_ready=lambda:next(decisions,False))
        self.assertEqual(worker.status,'cancelled')
        self.assertFalse(any('$allowInvoke = $true' in c.args[0] for c in run.call_args_list))

    def test_provider_fallback_only_once_and_restored_after_confirmation(self):
        fallback=Mock()
        fallback.__enter__=Mock(return_value=True)
        fallback.__exit__=Mock(return_value=False)
        worker,_,_,_=self.run_ready([None,self.report('already_ready')],server=self.room(True),fallback=fallback)
        self.assertTrue(worker.success)
        fallback.__enter__.assert_called_once()
        fallback.__exit__.assert_called_once()

    def test_existing_inflight_operation_does_not_start_second_worker_action(self):
        with patch.object(backend,'_player_ready_lock') as lock:
            lock.acquire.return_value=False
            worker,run,fetch,_=self.run_ready([])
        self.assertEqual(worker.status,'inflight')
        run.assert_not_called()
        fetch.assert_not_called()
        lock.release.assert_not_called()

    def test_restore_fallback_preserves_minimized_state_when_body_fails(self):
        user=Mock()
        def placement(hwnd, pointer):
            pointer._obj.showCmd=2
            return True
        user.GetWindowPlacement.side_effect=placement
        user.IsWindowVisible.return_value=True
        user.IsIconic.return_value=True
        user.MonitorFromWindow.return_value=1
        with patch.object(backend,'find_odx_window',return_value=123), patch.object(backend,'find_unmanaged_odx_window',return_value=None), patch.object(backend,'is_managed_odx_window',return_value=True), patch.object(backend.ctypes,'WinDLL',return_value=user), patch.object(backend.time,'sleep'):
            with self.assertRaisesRegex(OSError,'body error'):
                with backend._temporary_ready_window_access() as restored:
                    self.assertTrue(restored)
                    raise OSError('body error')
        user.ShowWindowAsync.assert_called_once_with(123,4)
        user.SetWindowPlacement.assert_called_once()
        self.assertEqual(user.SetWindowPlacement.call_args.args[1]._obj.showCmd,7)

    def test_shared_script_has_no_visibility_gate_mouse_or_keyboard_fallback(self):
        code=backend._player_ready_script('ABC123','p1',True)
        for forbidden in ('IsOffscreen','BoundingRectangle','SendKeys','SendWait','SetForegroundWindow','mouse_event','keybd_event','SetFocus','F10'):
            self.assertNotIn(forbidden,code)
        for label in ('not ready',"i'm ready",'huy san sang','toi san sang'):
            self.assertIn(label,code)
        self.assertIn("$room -cne $targetRoom",code)
        self.assertIn("$identity -cne $targetPlayer",code)
        self.assertIn('[OdxLegacyReady]::Invoke(',code)
        self.assertIn('DoDefaultAction',code)
        self.assertNotIn('$invoke.Invoke()',code)
        self.assertIn('ODX_PLAYER_READY:',code)

    def test_shared_script_quotes_values_without_interpolating_input_as_code(self):
        code=backend._player_ready_script('ABC123',"O'Brien $HOME",False)
        self.assertIn("$targetPlayer = 'O''Brien $HOME'",code)



class TaskCollisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "odx"
        self.captures = self.root / "data" / "captures"
        self.captures.mkdir(parents=True)
        patcher = patch.object(backend, "ODX_ROOT", str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_nested_or_cancelled_task_names_are_already_reserved(self):
        for name in ("task-01", "TASK-02", "task-03.cancelled-20261114", "task-04"):
            (self.captures / name).mkdir()
        (self.captures / "task-04" / "raw").mkdir()
        for task in ("task-01", "task-02", "task-03", "task-04"):
            with self.subTest(task=task):
                exists, path = backend.is_task_already_recorded(task)
                self.assertTrue(exists)
                self.assertTrue(Path(path).exists())

    def test_file_name_collision_blocks_but_nearby_task_names_do_not(self):
        (self.captures / "task-01").write_text("reserved", encoding="utf-8")
        (self.captures / "task-02-fix1").mkdir()
        (self.captures / "task-020").mkdir()
        self.assertTrue(backend.is_task_already_recorded("task-01")[0])
        self.assertFalse(backend.is_task_already_recorded("task-02")[0])

    def test_legacy_capture_roots_are_checked_even_for_empty_folders(self):
        for parent, task in ((self.root / "captures", "local-01"),
                             (self.root.parent / "captures", "nearby-01")):
            (parent / task).mkdir(parents=True)
            self.assertEqual(backend.is_task_already_recorded(task), (True, str(parent / task)))

    def test_unreadable_root_blocks_instead_of_becoming_an_unused_task(self):
        with patch.object(backend.os, "scandir", side_effect=PermissionError("denied")):
            self.assertEqual(backend.is_task_already_recorded("task-01"), (True, str(self.captures)))
            with self.assertRaisesRegex(RuntimeError, "captures"):
                backend.get_next_available_fix_task_id("task-01")

    def test_broken_root_is_reserved_but_truly_missing_roots_are_safe(self):
        with patch.object(backend.os, "scandir", side_effect=FileNotFoundError), \
             patch.object(backend.os.path, "lexists", return_value=True):
            self.assertTrue(backend.is_task_already_recorded("task-01")[0])
        self.assertEqual(backend.is_task_already_recorded("new-01"), (False, ""))

    def test_fix_generator_checks_beyond_99_and_skips_files_and_cancelled_names(self):
        for number in range(1, 101):
            (self.captures / f"task-fix{number}").mkdir()
        (self.captures / "TASK-fix101").write_text("reserved", encoding="utf-8")
        (self.captures / "task-fix102.cancelled-20261114").mkdir()
        self.assertEqual(backend.get_next_available_fix_task_id("task-FIX7"), "task-fix103")

    def test_exhausted_fix_range_and_overlong_name_never_return_unchecked_id(self):
        entries = [(f"task-fix{number}", str(self.captures / f"task-fix{number}"))
                   for number in range(1, 10001)]
        with patch.object(backend, "_task_capture_entries", return_value=entries):
            with self.assertRaisesRegex(RuntimeError, "10000"):
                backend.get_next_available_fix_task_id("task")
        with self.assertRaises(ValueError):
            backend.get_next_available_fix_task_id("a" * 128)


class DesktopCaptureConfigTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="odx-test-mode-config-")
        self.addCleanup(self.folder.cleanup)
        self.native = Path(self.folder.name) / "native.json"
        self.restore = Path(self.folder.name) / "restore.json"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(backend, "_capture_test_config_paths", return_value=(str(self.native), str(self.restore), "machine-a")))
        self.stack.enter_context(patch.object(backend, "find_odx_window", return_value=None))
        self.stack.enter_context(patch.object(backend, "find_unmanaged_odx_window", return_value=None))
        self.stack.enter_context(patch.object(backend, "_capture_test_snapshots", {}))
        self.original = {"WindowProcess": "ArmaReforgerSteam", "GameProfile": "C:/games/reforger.json",
                         "Fps": 30, "RecordAudio": True, "PlayerId": "local-id", "AccessKey": "test-secret"}
        self.write_native(self.original)

    def write_native(self, value):
        self.native.write_text(json.dumps(value), encoding="utf-8")

    def read_native(self):
        return json.loads(self.native.read_text(encoding="utf-8"))

    def prepare(self, enabled):
        success, error = backend.prepare_capture_mode_launch(enabled)
        self.assertTrue(success, error)

    def test_prelaunch_changes_only_source_profile_and_restores_after_restart(self):
        self.prepare(True)
        changed = dict(self.original, WindowProcess=None, GameProfile=None)
        self.assertEqual(self.read_native(), changed)
        metadata = json.loads(self.restore.read_text(encoding="utf-8"))
        self.assertEqual(metadata, {"version": 1, "identity": "machine-a", "original": {
            key: self.original[key] for key in ("WindowProcess", "GameProfile")}})
        self.assertNotIn("test-secret", self.restore.read_text(encoding="utf-8"))
        backend._capture_test_snapshots.clear()
        self.prepare(True)
        self.assertEqual(json.loads(self.restore.read_text(encoding="utf-8")), metadata)
        self.prepare(False)
        self.assertEqual(self.read_native(), self.original)
        self.assertFalse(self.restore.exists())
        self.assertEqual(list(Path(self.folder.name).glob("*.tmp")), [])

    def test_normal_launch_without_snapshot_leaves_native_bytes_untouched(self):
        self.native.write_bytes(b"even a broken config is not ours to repair")
        self.prepare(False)
        self.assertEqual(self.native.read_bytes(), b"even a broken config is not ours to repair")

    def test_restore_keeps_each_field_that_user_changed_and_all_other_settings(self):
        self.prepare(True)
        manual = self.read_native()
        manual.update(WindowProcess="manual-process", Fps=60, PlayerId="changed-id")
        self.write_native(manual)
        self.prepare(False)
        self.assertEqual(self.read_native(), dict(manual, GameProfile=self.original["GameProfile"]))

    def test_reenable_retains_later_manual_source_as_new_restore_value(self):
        self.prepare(True)
        manual = self.read_native()
        manual["WindowProcess"] = "manual-process"
        self.write_native(manual)
        self.prepare(True)
        self.prepare(False)
        self.assertEqual(self.read_native(), dict(self.original, WindowProcess="manual-process"))

    def test_fields_absent_before_test_remain_absent_after_restore(self):
        self.write_native({"PlayerId": "local-id"})
        self.prepare(True)
        self.assertEqual(self.read_native(), {"PlayerId": "local-id", "WindowProcess": None, "GameProfile": None})
        self.prepare(False)
        self.assertEqual(self.read_native(), {"PlayerId": "local-id"})

    def test_portable_copy_never_restores_settings_from_another_machine(self):
        self.restore.write_text(json.dumps({"version": 1, "identity": "machine-b", "original": {
            "WindowProcess": "foreign-process", "GameProfile": "foreign-profile"}}), encoding="utf-8")
        self.prepare(False)
        self.assertEqual(self.read_native(), self.original)
        self.prepare(True)
        self.prepare(False)
        self.assertEqual(self.read_native(), self.original)

    def test_live_native_window_blocks_config_mutation(self):
        with patch.object(backend, "find_odx_window", return_value=123):
            success, error = backend.prepare_capture_mode_launch(True)
        self.assertFalse(success)
        self.assertTrue(error)
        self.assertEqual(self.read_native(), self.original)
        self.assertFalse(self.restore.exists())

    def test_invalid_config_or_snapshot_blocks_without_replacing_native_data(self):
        for config, metadata in ((b'{"AccessKey":"test-secret",', None),
                                 (json.dumps(self.original).encode(), b'{"version":')):
            with self.subTest(metadata=bool(metadata)):
                self.native.write_bytes(config)
                if metadata is not None:
                    self.restore.write_bytes(metadata)
                success, error = backend.prepare_capture_mode_launch(True)
                self.assertFalse(success)
                self.assertNotIn("test-secret", error)
                self.assertEqual(self.native.read_bytes(), config)

    def test_snapshot_save_failure_never_changes_native_config(self):
        with patch.object(backend, "_write_capture_test_json", side_effect=OSError("disk unavailable")):
            success, error = backend.prepare_capture_mode_launch(True)
        self.assertFalse(success)
        self.assertTrue(error)
        self.assertEqual(self.read_native(), self.original)

    def test_native_write_failure_retains_recoverable_original_metadata(self):
        write_json = backend._write_capture_test_json

        def fail_native(path, value):
            if path == str(self.native):
                raise OSError("disk unavailable")
            return write_json(path, value)

        with patch.object(backend, "_write_capture_test_json", side_effect=fail_native):
            success, _ = backend.prepare_capture_mode_launch(True)
        self.assertFalse(success)
        self.assertTrue(self.restore.exists())
        self.assertEqual(self.read_native(), self.original)
        self.prepare(False)
        self.assertEqual(self.read_native(), self.original)


class DesktopCaptureWorkerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(backend.threading, "Thread", ImmediateThread))
        self.stack.enter_context(patch.object(backend, "_capture_test_snapshots", {}))
        self.context = (111, 222)
        self.stack.enter_context(patch.object(backend, "_owned_uia_context", return_value=self.context))
        self.original = {"source": "ArmaReforgerSteam.exe", "game": "Arma Reforger"}
        self.applied = {"source": backend._desktop_source_names[1], "game": backend._raw_profile_names[1]}

    def response(self, **kwargs):
        return Mock(returncode=0, stdout="CAPTURE_TEST_RESULT:" + json.dumps(
            dict(hwnd=self.context[0], pid=self.context[1], success=True, **kwargs)))

    def test_unowned_window_is_never_automated(self):
        with patch.object(backend, "_owned_uia_context", return_value=None), patch.object(backend, "run_ps_script") as run:
            worker = backend.set_capture_test_mode(True)
        self.assertFalse(worker.success)
        run.assert_not_called()

    def test_success_requires_exact_test_source_and_profile_readback(self):
        with patch.object(backend, "run_ps_script", side_effect=[
            self.response(selection=self.original, target=self.applied),
            self.response(selection=self.original, target=self.applied),
            self.response(selection=self.original, changed=False)]):
            worker = backend.set_capture_test_mode(True)
        self.assertFalse(worker.success)
        self.assertIn(self.context, backend._capture_test_snapshots)

    def test_foreign_hwnd_result_is_rejected(self):
        reply = self.response(selection=self.original, target=self.applied)
        reply.stdout = reply.stdout.replace('"hwnd": 111', '"hwnd": 999')
        with patch.object(backend, "run_ps_script", return_value=reply):
            worker = backend.set_capture_test_mode(True)
        self.assertFalse(worker.success)

    def test_failed_selection_keeps_snapshot_for_recovery(self):
        with patch.object(backend, "run_ps_script", side_effect=[
                self.response(selection=self.original, target=self.applied),
                self.response(selection=self.original, target=self.applied), None]):
            worker = backend.set_capture_test_mode(True)
        self.assertFalse(worker.success)
        self.assertEqual(backend._capture_test_snapshots[self.context]["original"], self.original)

    def test_normal_mode_without_snapshot_does_not_run_any_native_action(self):
        with patch.object(backend, "run_ps_script") as run, patch.object(backend, "_owned_uia_context") as discover:
            worker = backend.set_capture_test_mode(False)
        self.assertTrue(worker.success)
        run.assert_not_called()
        discover.assert_not_called()

    def test_prepared_test_source_has_no_selection_mutation_and_no_snapshot(self):
        with patch.object(backend, "run_ps_script", side_effect=[
            self.response(selection=self.applied, target=self.applied),
            self.response(selection=self.applied, target=self.applied),
            self.response(selection=self.applied, changed=False)]):
            worker = backend.set_capture_test_mode(True)
        self.assertTrue(worker.success, worker.error)
        self.assertFalse(worker.changed)
        self.assertEqual(backend._capture_test_snapshots, {})

    def failure(self, code):
        result = self.response(error=code)
        result.returncode = 1
        result.stdout = result.stdout.replace('"success": true', '"success": false')
        return result

    def test_loading_is_retried_before_applying_only_stable_selections(self):
        ready = self.response(selection=self.applied, target=self.applied)
        with patch.object(backend, "run_ps_script", side_effect=[
                self.failure("CAPTURE_COMBO_MISSING"), self.failure("CAPTURE_SELECTION_UNKNOWN"),
                ready, ready, self.response(selection=self.applied, changed=False)]) as run:
            worker = backend.set_capture_test_mode(True, timeout=10, on_status=Mock())
        self.assertTrue(worker.success, worker.error)
        self.assertEqual(run.call_count, 5)
        self.assertTrue(all(call.kwargs["timeout"] <= 3 for call in run.call_args_list))
        self.assertEqual(backend._capture_test_snapshots, {})

    def test_changed_loading_selection_must_stabilize_before_mutation(self):
        ready = self.response(selection=self.applied, target=self.applied)
        with patch.object(backend, "run_ps_script", side_effect=[
                self.response(selection=self.original, target=self.applied), ready, ready,
                self.response(selection=self.applied, changed=False)]) as run:
            worker = backend.set_capture_test_mode(True)
        self.assertTrue(worker.success, worker.error)
        self.assertEqual(run.call_count, 4)
        self.assertFalse(worker.changed)
        self.assertEqual(backend._capture_test_snapshots, {})

    def test_duplicate_native_choices_fail_without_retries_or_mutation(self):
        with patch.object(backend, "run_ps_script", return_value=self.failure("CAPTURE_CHOICE_NOT_UNIQUE")) as run:
            worker = backend.set_capture_test_mode(True)
        self.assertFalse(worker.success)
        self.assertIn("CAPTURE_CHOICE_NOT_UNIQUE", worker.error)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(backend._capture_test_snapshots, {})

    def test_cancelled_during_wait_does_not_apply_sources(self):
        cancelled = threading.Event()
        with patch.object(backend, "run_ps_script", return_value=self.response(selection=self.original, target=self.applied)) as run:
            worker = backend.set_capture_test_mode(True, cancel_event=cancelled,
                                                   on_status=lambda *_: cancelled.set())
        self.assertFalse(worker.success)
        self.assertIn("hủy", worker.error)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(backend._capture_test_snapshots, {})

    def test_cancellation_before_worker_never_requests_native_ui(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(backend, "run_ps_script") as run:
            worker = backend.set_capture_test_mode(True, cancel_event=cancelled)
        self.assertFalse(worker.success)
        run.assert_not_called()

    def test_replaced_native_window_during_loading_cannot_be_configured(self):
        with patch.object(backend, "_owned_uia_context", side_effect=[self.context, self.context, (999, 333)]), \
             patch.object(backend, "run_ps_script", return_value=self.response(selection=self.original, target=self.applied)) as run:
            worker = backend.set_capture_test_mode(True)
        self.assertFalse(worker.success)
        self.assertIn("ODX_WINDOW_CHANGED", worker.error)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(backend._capture_test_snapshots, {})

    def test_readback_errors_do_not_expose_arbitrary_stderr_or_source_values(self):
        replies = [Mock(returncode=1, stdout="", stderr="private credential and local source path"),
                   Mock(returncode=1, stdout="", stderr="CAPTURE_COMBO_MISSING private credential"),
                   self.failure("unexpected private credential")]
        for reply in replies:
            with self.subTest(stderr=bool(reply.stderr)), patch.object(backend, "run_ps_script", return_value=reply):
                with self.assertRaises(RuntimeError) as error:
                    backend._capture_test_result(self.context, "inspect", 2)
                self.assertNotIn("private credential", str(error.exception))
                self.assertNotIn("local source path", str(error.exception))


if __name__ == "__main__":
    unittest.main()
