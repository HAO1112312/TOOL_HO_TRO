"""GUI state regression tests; no Tk windows, hooks, games or real Odx processes."""
import ast
import json
import os
from pathlib import Path
import queue
import re
import tempfile
import sys
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "odx_gui.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
           and node.name in ("ModernTheme", "OdxAutoPilotGUI")]


class Widget:
    def __init__(self, value=""):
        self.value = value
        self.options = {}
        self.visible = False

    def get(self):
        return self.value

    def delete(self, *_):
        self.value = ""

    def insert(self, _, value):
        self.value = value

    def config(self, **options):
        self.options.update(options)

    def pack(self, **_):
        self.visible = True

    def pack_forget(self):
        self.visible = False


class DeferredThread:
    created = []

    def __init__(self, target, args=(), **_):
        self.target, self.args = target, args
        self.created.append(self)

    def start(self):
        pass

    def run(self):
        self.target(*self.args)


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        DeferredThread.created = []
        injection = types.SimpleNamespace(success=True, join=Mock(), is_alive=lambda: False)
        self.room_worker = types.SimpleNamespace(success=True, room_code="ABC123", error="", join=Mock())
        self.backend = types.SimpleNamespace(
            TOOL_DIR=self.temp.name, ODX_ROOT=self.temp.name,
            set_task_box=Mock(return_value=injection), set_clipboard=Mock(),
            set_capture_test_mode=Mock(return_value=injection),
            prepare_capture_mode_launch=Mock(return_value=(True, "")),
            get_next_available_fix_task_id=lambda task: re.sub(r"-fix\d+$", "", task) + "-fix1",
            is_task_already_recorded=Mock(return_value=(False, None)),
            get_target_duration_for_task=lambda task: 240,
            get_recording_start_time=Mock(return_value=None),
            find_odx_window=Mock(return_value=123), is_game_running=Mock(return_value=True),
            _window_process_path=Mock(return_value=(321, str(Path(self.temp.name) / "bin" / "odx-app.exe"))),
            is_managed_odx_window=Mock(return_value=True),
            find_unmanaged_odx_window=Mock(return_value=None),
            fetch_room_status=Mock(return_value={"members": []}), set_player_info=Mock(),
            send_toggle_hotkey=Mock(return_value=True),
            stop_recording_verified=Mock(return_value=True), cleanup_all_odx=Mock(),
            host_auto_create_room=Mock(return_value=self.room_worker), register_managed_process=Mock(side_effect=lambda process: process))
        self.messages = types.SimpleNamespace(showwarning=Mock(), showerror=Mock(),
                                              showinfo=Mock(), askyesno=Mock(return_value=True))
        self.ns = dict(be=self.backend, re=re, time=time, os=os, json=json,
                       tempfile=tempfile, threading=types.SimpleNamespace(Thread=DeferredThread,
                                                                          get_ident=threading.get_ident,
                                                                          Event=threading.Event),
                       subprocess=types.SimpleNamespace(Popen=Mock()),
                       messagebox=self.messages, queue=queue,
                       HostSettingsServer=Mock(), PlayerSettingsClient=Mock(),
                       get_current_version_info=lambda: {"version": "3.1.2"},
                       check_for_updates_async=Mock(), download_and_apply_update=Mock(return_value=(True, None)))
        exec(compile(ast.Module(body=classes, type_ignores=[]), str(SOURCE), "exec"), self.ns)
        self.gui = self.ns["OdxAutoPilotGUI"].__new__(self.ns["OdxAutoPilotGUI"])
        values = dict(odx_opened=True, is_recording=False, is_counting_down=False,
                      is_in_cooldown=False, is_validating=False, is_stopping=False,
                      is_updating=False, is_player_recording=False, p_counting_down=False,
                      capture_state_unknown=False, task_id_override=None, current_mode="HOST", view_mode="HOST",
                      is_running=True, hotkeys_ready=True, _transition_token=0,
                      _hotkey_precedence_required=False,
                      _hotkey_owner_identity=None, _hotkey_refresh_target=None,
                      _hotkey_refresh_generation=0, _hotkey_refresh_retry_at=0,
                      _player_connection_generation=0, player_connection_active=False,
                      is_connecting=False, _connection_operation_token=0,
                      _connection_cancel_event=None,
                      is_host_opening=False, _host_launch_generation=0, _host_room_cancel_event=None,
                      _host_process=None, room_code="",
                      _ready_inflight=False, _ready_operation_token=0,
                      _ready_cancel_event=None,
                      _ui_thread_id=threading.get_ident(), _closing=False,
                      _action_lock=threading.RLock(), _start_command_sent=False,
                      _stop_confirmation_failed=False, _hook_thread_id=None,
                      _pending_stop_action=None, _pending_stop_task="",
                      _scheduled_start_at=None, _scheduled_start_task="", _stop_elapsed=None,
                      _round_start_epoch=None, _sync_required=False,
                      _sync_server=None, _sync_client=None, _sync_key=None, _sync_polling=False,
                      _sync_applied_revision=None, _sync_applied_instance=None, _pending_sync_start_task=None,
                      _sync_start_after=None, _sync_start_task=None,
                      _shared_settings={}, _shared_targets={}, app_version="3.1.9",
                      _player_round_token=0, _player_validation_token=None, _player_validation_failed=False,
                      _completed_validation_tokens=set(), progress={"schema": 1, "batches": {}},
                      active_task_id="asm-s221-01", original_task_before_abort="asm-s221-01",
                      pending_fix_task="asm-s221-01-fix1", pending_next_task="asm-s221-02",
                      abort_state=None, rec_start_time=0, target_duration=240,
                      announced_all_ok=set(), config={"round_duration_seconds": 240, "random_enabled": False}, batch_list=["asm-s221-"],
                      cfg_file=str(Path(self.temp.name) / "autopilot_config.json"),
                      prog_file=str(Path(self.temp.name) / "autopilot_progress.json"),
                      ui_queue=queue.Queue(), log_queue=queue.Queue())
        for name, value in values.items():
            setattr(self.gui, name, value)
        for name, value in dict(combo_batch="asm-s221-", ent_subtask="01", ent_start_num="1",
                                ent_total="15", ent_full_task="asm-s221-01",
                                ent_p_room="ABC123", ent_p_id="p1", ent_p_name="Player", ent_host_room="").items():
            setattr(self.gui, name, Widget(value))
        for name in ("lbl_task_display", "btn_host_start", "btn_host_emergency", "lbl_host_status",
                     "lbl_timer", "btn_inject_task", "btn_auto_fix", "btn_p_connect", "lbl_p_status",
                     "tab_host", "tab_player", "tab_audit", "btn_nav_host", "btn_nav_player", "btn_nav_audit",
                     "btn_open_odx", "frame_host_choices", "lbl_p_timer", "lbl_host_sync", "lbl_player_sync",
                     "ent_sync_host"):
            setattr(self.gui, name, Widget())
        self.gui.var_autoincrement = Widget(True)
        self.gui.root = types.SimpleNamespace(focus_get=lambda: None, after=Mock(), destroy=Mock())
        self.gui.overlay = types.SimpleNamespace(show_roster_hud=Mock(), clear=Mock(),
                                                 update_recording=Mock(), show_pending_recording=Mock(),
                                                 set_preferences=Mock(), close=Mock())
        self.gui.follower = types.SimpleNamespace(current_recording=None, current_stopped=None,
            current_stopped_at=None,
            last_armed=None, last_all_ok=None, last_kept_path=None, last_round_result=None,
            task_in_use_error=None, id_conflict=None, clock_drift_error=None,
            check_new_lines=Mock(), reset_to_end=Mock(), room_code=None)
        self.gui.tracker = types.SimpleNamespace(latest_data={"host": "host", "members": [
            {"player_id": "host", "ready": True}, {"player_id": "p1", "ready": True},
            {"player_id": "p2", "ready": True}, {"player_id": "p3", "ready": True}]},
            last_error=None, last_success_at=time.monotonic(), is_all_ready=Mock(return_value=True),
            pause_hud=Mock(), resume_hud=Mock(), stop=Mock())
        self.gui.log = Mock()

    def test_host_create_waits_for_room_confirmation_before_unlocking(self):
        self.gui.start_host_room_tracking = Mock()
        self.gui.host_open_odx()
        self.assertTrue(self.gui.is_host_opening)
        self.assertTrue(self.gui.operation_busy())
        self.assertFalse(self.gui.odx_opened)
        self.assertEqual(self.gui.btn_host_start.options["state"], "disabled")
        self.assertEqual(self.gui.btn_open_odx.options["state"], "disabled")
        self.backend.host_auto_create_room.assert_not_called()
        DeferredThread.created[0].run()
        self.room_worker.join.assert_called_once_with()
        self.assertFalse(self.gui.is_host_opening)
        self.assertTrue(self.gui.odx_opened)
        self.assertEqual(self.gui.ent_host_room.get(), "ABC123")
        self.gui.start_host_room_tracking.assert_called_once_with("ABC123")
        self.backend.set_clipboard.assert_called_with("ABC123")
        self.backend.cleanup_all_odx.assert_not_called()
        self.ns["subprocess"].Popen.assert_not_called()

    def test_duplicate_create_click_does_not_start_another_operation(self):
        self.gui.host_open_odx()
        self.gui.host_open_odx()
        self.assertEqual(len(DeferredThread.created), 1)
        self.backend.cleanup_all_odx.assert_not_called()

    def test_failed_room_creation_is_visible_and_retry_reuses_same_window(self):
        self.room_worker.success = False
        self.room_worker.room_code = ""
        self.room_worker.error = "Room service timed out"
        self.gui.start_host_room_tracking = Mock()
        self.gui.host_open_odx()
        DeferredThread.created[0].run()
        self.assertFalse(self.gui.odx_opened)
        self.assertFalse(self.gui.is_host_opening)
        self.assertEqual(self.gui.btn_open_odx.options["state"], "normal")
        self.assertIn("Room service timed out", self.gui.lbl_host_status.options["text"])
        self.room_worker.success = True
        self.room_worker.room_code = "XYZ789"
        self.gui.host_open_odx()
        DeferredThread.created[1].run()
        self.assertEqual(self.gui.room_code, "XYZ789")
        self.assertTrue(self.gui.odx_opened)
        self.ns["subprocess"].Popen.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_new_host_launch_opens_once_and_only_unlocks_after_room(self):
        (Path(self.temp.name) / "Odx Capture.exe").write_bytes(b"fixture")
        self.gui.odx_opened = False
        self.backend.find_odx_window.return_value = None
        process = types.SimpleNamespace(poll=Mock(return_value=None))
        self.ns["subprocess"].Popen.return_value = process
        self.gui.start_host_room_tracking = Mock()
        self.gui.host_open_odx()
        self.ns["subprocess"].Popen.assert_called_once()
        self.backend.register_managed_process.assert_called_once_with(process)
        self.gui.follower.reset_to_end.assert_called_once_with()
        self.assertFalse(self.gui.odx_opened)
        self.backend.find_odx_window.return_value = 123
        DeferredThread.created[0].run()
        self.assertTrue(self.gui.odx_opened)
        self.assertEqual(self.gui.room_code, "ABC123")

    def test_room_placeholder_and_stale_code_do_not_unlock_host(self):
        self.gui.follower.room_code = "OLD123"
        self.room_worker.success = False
        self.room_worker.room_code = "Tạo room..."
        self.room_worker.error = "No room acknowledgement"
        self.gui.host_open_odx()
        self.assertIsNone(self.gui.follower.room_code)
        DeferredThread.created[0].run()
        self.assertFalse(self.gui.odx_opened)
        self.assertEqual(self.gui.room_code, "")
        self.backend.set_clipboard.assert_not_called()

    def test_failed_backend_result_cannot_smuggle_unconfirmed_room(self):
        self.room_worker.success = False
        self.room_worker.room_code = "FAKE12"
        self.room_worker.error = "Not confirmed"
        self.gui.host_open_odx()
        DeferredThread.created[0].run()
        self.assertFalse(self.gui.odx_opened)
        self.assertEqual(self.gui.room_code, "")

    def test_log_room_does_not_override_failed_backend_room_verification(self):
        self.room_worker.success = False
        self.room_worker.room_code = ""
        self.room_worker.join.side_effect = lambda: setattr(self.gui.follower, "room_code", "LOG123")
        self.gui.start_host_room_tracking = Mock()
        self.gui.host_open_odx()
        DeferredThread.created[0].run()
        self.assertFalse(self.gui.odx_opened)
        self.assertEqual(self.gui.room_code, "")
        self.gui.start_host_room_tracking.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_closing_during_create_cancels_before_late_worker_starts(self):
        self.gui.host_open_odx()
        event = self.gui._host_room_cancel_event
        self.gui.on_close_window()
        self.assertTrue(event.is_set())
        self.assertFalse(self.gui.is_running)
        DeferredThread.created[0].run()
        self.backend.host_auto_create_room.assert_not_called()
        self.assertFalse(self.gui.odx_opened)

    def test_reset_rejects_late_room_confirmation(self):
        self.gui.host_open_odx()
        old_generation = self.gui._host_launch_generation
        event = self.gui._host_room_cancel_event
        self.gui.reset_host_to_unopened_state()
        self.gui.finish_host_room_creation(old_generation, "OLD123")
        self.assertTrue(event.is_set())
        self.assertFalse(self.gui.odx_opened)
        self.assertEqual(self.gui.ent_host_room.get(), "")

    def test_room_creation_blocks_recording_cleanup_update_and_role_switch(self):
        self.gui.host_open_odx()
        self.assertTrue(self.gui.should_capture_hotkey(0x79))
        self.gui.host_trigger_start()
        self.gui.run_clean_all()
        self.gui.run_check_update()
        self.gui.switch_mode("PLAYER")
        self.assertEqual(self.gui.current_mode, "HOST")
        self.assertEqual(len(DeferredThread.created), 1)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()
        self.ns["check_for_updates_async"].assert_not_called()

    def test_launch_failure_releases_create_busy_state_and_allows_retry(self):
        (Path(self.temp.name) / "Odx Capture.exe").write_bytes(b"fixture")
        self.gui.odx_opened = False
        self.backend.find_odx_window.return_value = None
        self.ns["subprocess"].Popen.side_effect = OSError("launch denied")
        self.gui.host_open_odx()
        self.assertFalse(self.gui.is_host_opening)
        self.assertFalse(self.gui.odx_opened)
        self.assertIn("launch denied", self.gui.lbl_host_status.options["text"])
        self.assertEqual(self.gui.btn_open_odx.options["state"], "normal")

    def test_copy_room_rejects_pending_placeholder(self):
        self.gui.ent_host_room.value = "ĐANG TẠO..."
        self.gui.copy_room_code()
        self.backend.set_clipboard.assert_not_called()

    def test_fix_button_preserves_generated_task(self):
        self.gui.btn_auto_add_fix()
        self.gui.update_task_id_preview()
        self.assertEqual(self.gui.active_task_id, "asm-s221-01-fix1")
        self.assertEqual(self.gui.ent_full_task.get(), "asm-s221-01-fix1")
        self.assertEqual(self.backend.set_task_box.call_args.args, ("asm-s221-01-fix1",))

    def test_manual_task_survives_start_preparation(self):
        self.gui.ent_full_task.value = "my-custom-42"
        self.gui.on_full_task_manual_edit()
        self.gui.host_trigger_start()
        self.assertEqual(self.gui.active_task_id, "my-custom-42")
        self.assertTrue(self.gui.is_counting_down)
        self.assertFalse(self.gui.is_recording)

    def test_fix_choice_sets_folder_name_then_explicit_f10_starts_verified_task(self):
        self.gui.abort_state = "AWAITING_CHOICE"
        self.gui.watch_start = Mock()
        self.gui.host_choose_option("FIX")
        self.assertEqual(self.gui.active_task_id, "asm-s221-01-fix1")
        self.assertEqual(self.gui.ent_full_task.get(), "asm-s221-01-fix1")
        self.assertEqual(self.gui.config["task_id_override"], "asm-s221-01-fix1")
        self.assertFalse(DeferredThread.created)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.assertFalse(self.gui.is_counting_down)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertIn("F10", self.gui.lbl_host_status.options["text"])

        self.gui.handle_host_hotkey(0x79)
        self.assertEqual(len(DeferredThread.created), 1)
        DeferredThread.created[-1].run()
        self.backend.send_toggle_hotkey.assert_called_once_with(123)
        self.gui.watch_start.assert_called_once_with(1, "asm-s221-01-fix1")

    def test_failed_task_injection_never_sends_start(self):
        self.backend.set_task_box.return_value.success = False
        self.gui.host_trigger_start()
        DeferredThread.created[-1].run()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertFalse(self.gui.is_recording)
        self.assertFalse(self.gui.is_counting_down)

    def test_cancel_keeps_timer_until_stop_confirmation(self):
        self.gui.is_recording = True
        self.gui.rec_start_time = time.monotonic() - 100
        self.gui.follower.current_recording = "asm-s221-01"
        original_time = self.gui.rec_start_time
        self.gui.host_cancel_and_reset()
        self.assertTrue(self.gui.is_stopping)
        self.assertTrue(self.gui.is_recording)
        self.assertEqual(self.gui.rec_start_time, original_time)
        self.gui.wait_capture_finished = Mock(return_value=False)
        DeferredThread.created[-1].run()
        self.backend.stop_recording_verified.assert_called_once()
        self.assertTrue(self.gui.is_recording)
        self.assertEqual(self.gui.rec_start_time, original_time)
        self.assertTrue(self.gui.capture_state_unknown)
        self.gui.update_live_timer()
        self.assertEqual(self.gui.rec_start_time, original_time)

    def test_confirmed_cancel_resets_after_ack(self):
        self.gui.is_recording = True
        self.gui.follower.current_recording = "asm-s221-01"
        self.gui.host_cancel_and_reset()
        self.gui.wait_capture_finished = Mock(return_value=True)
        DeferredThread.created[-1].run()
        self.assertFalse(self.gui.is_recording)
        self.assertFalse(self.gui.is_stopping)
        self.assertEqual(self.gui.rec_start_time, 0)

    def test_stop_does_not_send_keys_to_replacement_unowned_window(self):
        self.gui.is_recording = True
        self.gui.follower.current_recording = "asm-s221-01"
        self.backend.is_managed_odx_window.return_value = False
        self.gui.host_cancel_and_reset()
        DeferredThread.created[-1].run()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertTrue(self.gui.capture_state_unknown)

    def test_start_and_stop_are_not_duplicated(self):
        self.gui.host_trigger_start()
        self.gui.host_trigger_start()
        self.assertEqual(len(DeferredThread.created), 1)
        self.gui.is_counting_down = False
        self.gui.is_recording = True
        self.gui.host_trigger_emergency_stop()
        self.gui.host_trigger_emergency_stop()
        self.assertEqual(len(DeferredThread.created), 2)

    def test_stale_start_callback_cannot_resurrect_cancelled_countdown(self):
        self.gui._transition_token = 2
        self.gui.finish_start(1, "asm-s221-01", True, "")
        self.assertFalse(self.gui.is_recording)

    def test_confirmed_recording_does_not_reset_existing_anchor(self):
        self.gui.begin_confirmed_recording("asm-s221-01")
        anchor = self.gui.rec_start_time
        self.gui.begin_confirmed_recording("asm-s221-01")
        self.assertEqual(self.gui.rec_start_time, anchor)

    def test_native_future_start_keeps_timer_and_hud_pending_until_deadline(self):
        task_id = self.gui.active_task_id
        self.gui.follower.current_recording = task_id
        self.gui.request_host_stop = Mock()
        with patch.object(time, "monotonic", return_value=100):
            self.gui.begin_confirmed_recording(task_id, started_at=103)
        self.assertFalse(self.gui.is_recording)
        self.assertTrue(self.gui.is_counting_down)
        self.assertEqual(self.gui.rec_start_time, 0)
        self.gui.overlay.update_recording.assert_not_called()
        self.gui.overlay.show_pending_recording.assert_called_with(task_id, 3)

        with patch.object(time, "monotonic", return_value=101):
            self.gui.update_live_timer()
        self.assertFalse(self.gui.is_recording)
        self.assertTrue(self.gui.is_counting_down)
        self.gui.overlay.update_recording.assert_not_called()
        self.gui.request_host_stop.assert_not_called()

        with patch.object(time, "monotonic", return_value=103.5):
            self.gui.update_live_timer()
        self.assertTrue(self.gui.is_recording)
        self.assertFalse(self.gui.is_counting_down)
        self.assertEqual(self.gui.rec_start_time, 103)
        recording_call = self.gui.overlay.update_recording.call_args
        self.assertEqual(recording_call.args[:3], (task_id, 0.5, 240))

        with patch.object(time, "monotonic", return_value=106.2):
            self.gui.update_live_timer()
        self.assertTrue(self.gui.lbl_timer.options["text"].startswith("00:03 /"))
        self.gui.request_host_stop.assert_not_called()

    def test_late_recording_confirmation_uses_native_anchor_not_observation_time(self):
        task_id = self.gui.active_task_id
        self.gui.follower.current_recording = task_id
        with patch.object(time, "monotonic", return_value=100):
            self.gui.begin_confirmed_recording(task_id, started_at=95)
            self.gui.update_live_timer()
        self.assertEqual(self.gui.rec_start_time, 95)
        self.assertTrue(self.gui.lbl_timer.options["text"].startswith("00:05 /"))
        self.assertEqual(self.gui.overlay.update_recording.call_args.args[:3], (task_id, 5, 240))

    def test_watch_start_passes_true_native_start_instead_of_early_setup_log(self):
        task_id = self.gui.active_task_id
        self.gui.follower.current_recording = task_id
        self.backend.get_recording_start_time.return_value = 103
        self.gui.finish_start = Mock()
        with patch.object(time, "monotonic", return_value=100):
            self.gui.watch_start(0, task_id)
        self.backend.get_recording_start_time.assert_called_once_with(task_id)
        self.gui.finish_start.assert_called_once_with(0, task_id, True, "", True, 103)

    def test_missing_native_start_never_marks_early_setup_as_recording(self):
        task_id = self.gui.active_task_id
        self.gui.follower.current_recording = task_id
        self.gui.finish_start = Mock()
        with patch.object(time, "monotonic", side_effect=[100, 100, 126]), patch.object(time, "sleep"):
            self.gui.watch_start(0, task_id)
        self.assertFalse(self.gui.finish_start.call_args.args[2])
        self.gui.overlay.update_recording.assert_not_called()
        self.assertFalse(self.gui.is_recording)

    def test_native_early_stop_uses_stop_time_even_if_observed_after_target(self):
        task_id = self.gui.active_task_id
        self.gui.is_recording = True
        self.gui.rec_start_time = 100
        self.gui.follower.current_recording = None
        self.gui.follower.current_stopped = (task_id, "round_end")
        self.gui.follower.current_stopped_at = 339
        self.gui.follower.last_round_result = (task_id, "all ok")
        with patch.object(time, "monotonic", return_value=500):
            self.assertEqual(self.gui.capture_elapsed(), 239)
            self.gui.update_live_timer()
        self.assertEqual(self.gui.abort_state, "AWAITING_CHOICE")
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.assertFalse(self.gui.is_validating)
        self.assertFalse(DeferredThread.created)
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})

    def test_cancelled_countdown_never_flashes_rec_before_showing_choices(self):
        task_id = self.gui.active_task_id
        self.gui.is_counting_down = True
        self.gui._scheduled_start_at = 103
        self.gui._scheduled_start_task = task_id
        self.gui.follower.current_stopped = (task_id, "cancelled")
        self.gui.follower.current_stopped_at = 101
        self.gui.follower.last_round_result = (task_id, "cancelled")
        with patch.object(time, "monotonic", return_value=101):
            self.gui.update_live_timer()
        self.assertFalse(self.gui.is_recording)
        self.assertFalse(self.gui.is_counting_down)
        self.assertIsNone(self.gui._scheduled_start_at)
        self.gui.overlay.update_recording.assert_not_called()
        self.assertEqual(self.gui.abort_state, "AWAITING_CHOICE")
        self.assertTrue(self.gui.frame_host_choices.visible)

    def test_player_uses_same_native_deadline_before_displaying_rec_or_elapsed(self):
        task_id = self.gui.active_task_id
        self.gui.current_mode = "PLAYER"
        self.gui.follower.current_recording = task_id
        self.backend.get_recording_start_time.return_value = 103
        self.gui.player_recording_state = Mock()
        clock = [100.0]
        iterations = []

        def next_frame(_):
            iterations.append(clock[0])
            if len(iterations) <= 2:
                self.gui.player_recording_state.assert_not_called()
                self.gui.overlay.update_recording.assert_not_called()
                clock[0] = 101.0 if len(iterations) == 1 else 104.0
            else:
                self.gui.is_running = False

        with patch.object(time, "monotonic", side_effect=lambda: clock[0]), patch.object(time, "sleep", side_effect=next_frame):
            self.gui.player_follow_worker(0, "ABC123", "p1")
        self.assertEqual(iterations, [100, 101, 104])
        self.backend.get_recording_start_time.assert_called_once_with(task_id)
        self.gui.player_recording_state.assert_called_once_with(0, task_id, True, 1)
        recording_call = self.gui.overlay.update_recording.call_args
        self.assertEqual(recording_call.args[:3], (task_id, 1, 240))
        self.assertFalse(recording_call.kwargs["is_host"])
        self.assertIn("00:01 /", self.gui.lbl_p_timer.options["text"])

    def test_round_randomization_is_stable_and_shared_target_overrides_player_local_settings(self):
        self.gui.config.update(round_duration_seconds=300, random_enabled=True, random_offset_seconds=7)
        task_id = self.gui.active_task_id
        target = self.gui.round_target_duration(task_id)
        self.assertGreaterEqual(target, 293)
        self.assertLessEqual(target, 307)
        self.assertEqual({self.gui.round_target_duration(task_id) for _ in range(5)}, {target})
        self.gui.current_mode = "PLAYER"
        self.assertEqual(self.gui.round_target_duration(task_id), target)
        self.gui._shared_settings = {"task_id": task_id, "target_duration": 397}
        self.assertEqual(self.gui.round_target_duration(task_id), 397)
        self.assertGreaterEqual(self.gui.round_target_duration("another-task"), 293)
        self.assertLessEqual(self.gui.round_target_duration("another-task"), 307)

    def test_player_applies_host_common_settings_before_ack_and_preserves_private_identity(self):
        self.gui.current_mode = "PLAYER"
        self.gui.config.update(player_id="p1", player_name="Private player", odx_root="C:/private-odx",
                               game_process="MyGame", access_key="private-secret")
        key = ("PLAYER", "ABC123", "host", "")
        client = Mock(last_error=None, last_seen_at=100, current_revision=5, instance_id="host-instance")
        self.gui._sync_key, self.gui._sync_client = key, client
        self.gui._sync_polling = True
        settings = dict(task_id="host-task-01", target_duration=397, round_duration_seconds=400,
                        random_offset_seconds=5, random_enabled=True, target_players=3,
                        cooldown_between_rounds_seconds=8, hud_enabled=False,
                        hud_show_timer=True, hud_scale=1.2, hud_opacity=.8)
        envelope = {"revision": 5, "instance_id": "host-instance", "settings": settings}
        self.gui.finish_settings_fetch(key, 0, client, envelope)
        self.assertFalse(self.gui._sync_polling)
        self.assertEqual(self.gui.round_target_duration("host-task-01"), 397)
        self.assertEqual(self.gui.config["target_players"], 3)
        self.assertEqual(self.gui.tracker.expected_players, 3)
        self.assertTrue(self.gui.tracker._force_hud_update)
        self.assertEqual(self.gui.config["hud_scale"], 1.2)
        self.gui.overlay.set_preferences.assert_called_once_with(settings)
        for field, value in dict(player_id="p1", player_name="Private player", odx_root="C:/private-odx",
                                 game_process="MyGame", access_key="private-secret").items():
            self.assertEqual(self.gui.config[field], value)
        client.acknowledge.assert_not_called()
        self.assertEqual(len(DeferredThread.created), 1)
        DeferredThread.created[0].run()
        client.acknowledge.assert_called_once_with(5, "p1")

        # A new Host task must not change the planned duration of the old round.
        client.current_revision = 6
        self.gui.finish_settings_fetch(key, 0, client, {
            "revision": 6, "instance_id": "host-instance", "settings": {"task_id": "host-task-02", "target_duration": 402}})
        self.assertEqual(self.gui.round_target_duration("host-task-01"), 397)
        self.assertEqual(self.gui.round_target_duration("host-task-02"), 402)

    def test_stale_room_settings_cannot_mutate_config_ack_or_release_new_poll(self):
        self.gui.current_mode = "PLAYER"
        current_key = ("PLAYER", "NEW123", "new-host", "")
        current_client = Mock(last_error=None, last_seen_at=100)
        self.gui._sync_key, self.gui._sync_client = current_key, current_client
        self.gui._player_connection_generation = 2
        old_client = Mock(last_error=None, last_seen_at=100)
        envelope = {"revision": 3, "settings": {"task_id": "old-task", "target_duration": 999}}
        for key, generation, client in (
            (("PLAYER", "OLD123", "old-host", ""), 1, old_client),
            (current_key, 1, current_client),
            (current_key, 2, old_client),
        ):
            with self.subTest(key=key, generation=generation, old_client=client is old_client):
                self.gui._sync_polling = True
                self.gui.finish_settings_fetch(key, generation, client, envelope)
                self.assertTrue(self.gui._sync_polling)
                self.assertEqual(self.gui._shared_settings, {})
                self.assertEqual(self.gui._shared_targets, {})
                self.gui.overlay.set_preferences.assert_not_called()
                self.assertFalse(DeferredThread.created)
                client.acknowledge.assert_not_called()

    def test_host_snapshot_shares_round_and_hud_settings_without_machine_private_fields(self):
        self.gui.config.update(round_duration_seconds=300, random_offset_seconds=7,
                               cooldown_between_rounds_seconds=9, target_players=3,
                               hud_enabled=False, hud_scale=1.3, hud_opacity=.75,
                               player_id="private-id", player_name="private-name", odx_root="C:/private",
                               game_process="PrivateGame", access_key="private-key")
        snapshot = self.gui.host_settings_snapshot()
        self.assertEqual(snapshot["task_id"], self.gui.active_task_id)
        self.assertEqual(snapshot["target_duration"], 300)
        self.assertEqual(snapshot["random_offset_seconds"], 7)
        self.assertEqual(snapshot["target_players"], 3)
        self.assertEqual(snapshot["cooldown_between_rounds_seconds"], 9)
        self.assertFalse(snapshot["hud_enabled"])
        self.assertEqual(snapshot["hud_scale"], 1.3)
        self.assertEqual(snapshot["hud_opacity"], .75)
        for field in ("player_id", "player_name", "odx_root", "game_process", "access_key"):
            self.assertNotIn(field, snapshot)

    def test_player_sync_requires_recent_success_and_applied_settings(self):
        self.gui._sync_required = True
        client = Mock(last_error=None, last_seen_at=99, current_revision=5, instance_id="host-instance")
        self.gui._sync_client = client
        with patch.object(time, "monotonic", return_value=100):
            self.assertFalse(self.gui.synced_settings_ready())
            self.gui._shared_settings = {"task_id": "task-01", "target_duration": 237}
            self.gui._sync_applied_revision = 5
            self.gui._sync_applied_instance = "host-instance"
            self.assertTrue(self.gui.synced_settings_ready())
            client.last_seen_at = 90
            self.assertFalse(self.gui.synced_settings_ready())
            client.last_seen_at = 99
            client.last_error = "Connection lost"
            self.assertFalse(self.gui.synced_settings_ready())

    def test_player_existing_task_blocks_ready_despite_fresh_applied_host_settings(self):
        self.gui.current_mode = "PLAYER"
        self.gui._sync_required = True
        self.gui.config["player_id"] = "p1"
        self.gui._sync_client = Mock(last_error=None, last_seen_at=99,
                                     current_revision=5, instance_id="host-instance")
        self.gui._shared_settings = {"task_id": "host-task-01", "target_duration": 240}
        self.gui._sync_applied_revision = 5
        self.gui._sync_applied_instance = "host-instance"
        with patch.object(time, "monotonic", return_value=100):
            self.assertTrue(self.gui.synced_settings_ready())
            for blocked_path in ("existing-player-capture", "unreadable-captures-root"):
                with self.subTest(blocked_path=blocked_path):
                    self.backend.is_task_already_recorded.return_value = (True, blocked_path)
                    self.assertFalse(self.gui.synced_settings_ready())
                    self.assertFalse(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
            self.backend.is_task_already_recorded.assert_called_with("host-task-01")
            self.backend.is_task_already_recorded.return_value = (False, None)
            self.gui._shared_settings = {"target_duration": 240}
            self.assertFalse(self.gui.synced_settings_ready(), "No task identity cannot authorize Ready")
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.stop_recording_verified.assert_not_called()

    def test_player_collision_refuses_ack_for_new_startable_host_snapshot(self):
        self.gui.current_mode = "PLAYER"
        self.gui._sync_required = True
        self.gui.config.update(player_id="p1", player_name="Private player", odx_root="C:/private")
        key = ("PLAYER", "ABC123", "host", "")
        client = Mock(last_error=None, last_seen_at=99, current_revision=5,
                      instance_id="host-instance")
        self.gui._sync_key, self.gui._sync_client = key, client
        self.backend.is_task_already_recorded.return_value = (True, "existing-player-capture")
        for phase in ("idle", "preparing", "countdown"):
            with self.subTest(phase=phase):
                self.gui.finish_settings_fetch(key, 0, client, {
                    "revision": 5, "instance_id": "host-instance", "settings": {
                        "task_id": "host-task-01", "target_duration": 240, "phase": phase}})
                self.assertEqual(self.gui._shared_settings["task_id"], "host-task-01")
                self.assertEqual(self.gui.config["player_id"], "p1")
                self.assertEqual(self.gui.config["odx_root"], "C:/private")
                self.assertFalse(DeferredThread.created, "A colliding task must never be acknowledged")
                client.acknowledge.assert_not_called()
                self.assertIsNone(self.gui._pending_sync_start_task)
                self.assertFalse(self.gui.is_counting_down)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_player_rechecks_collision_before_renewing_ack_for_unchanged_settings(self):
        self.gui.current_mode = "PLAYER"
        self.gui._sync_required = True
        self.gui.config["player_id"] = "p1"
        key = ("PLAYER", "ABC123", "host", "")
        client = Mock(last_error=None, last_seen_at=99, current_revision=5,
                      instance_id="host-instance")
        self.gui._sync_key, self.gui._sync_client = key, client
        self.gui.finish_settings_fetch(key, 0, client, {
            "revision": 5, "instance_id": "host-instance", "settings": {
                "task_id": "host-task-01", "target_duration": 240, "phase": "idle"}})
        self.assertEqual(len(DeferredThread.created), 1)
        DeferredThread.created[0].run()
        client.acknowledge.assert_called_once_with(5, "p1")
        client.acknowledge.reset_mock()
        DeferredThread.created = []

        # The Host has not published a new revision, but the local task name
        # has become occupied since the Player acknowledged it.
        self.backend.is_task_already_recorded.return_value = (True, "new-local-capture")
        self.gui.finish_settings_fetch(key, 0, client, None)
        self.assertFalse(DeferredThread.created)
        client.acknowledge.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_player_can_ack_current_owned_recording_metadata_without_false_collision_alarm(self):
        self.gui.current_mode = "PLAYER"
        self.gui._sync_required = True
        self.gui.config["player_id"] = "p1"
        self.gui.is_player_recording = True
        self.gui.follower.current_recording = "host-task-01"
        key = ("PLAYER", "ABC123", "host", "")
        client = Mock(last_error=None, last_seen_at=99, current_revision=5,
                      instance_id="host-instance")
        self.gui._sync_key, self.gui._sync_client = key, client
        self.backend.is_task_already_recorded.return_value = (True, "current-inflight-capture")
        self.gui.finish_settings_fetch(key, 0, client, {
            "revision": 5, "instance_id": "host-instance", "settings": {
                "task_id": "host-task-01", "target_duration": 240, "phase": "recording"}})
        self.assertEqual(len(DeferredThread.created), 1)
        DeferredThread.created[0].run()
        client.acknowledge.assert_called_once_with(5, "p1")
        self.assertTrue(self.gui.is_player_recording)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_host_never_starts_until_every_player_applied_current_config_revision(self):
        self.gui._sync_required = True
        self.gui.room_code = "ABC123"
        server = Mock(room_code="ABC123", host_id="host")
        server.publish.return_value = 7
        server.applied_players.return_value = {"p1", "p2"}
        self.gui._sync_server = server
        self.gui.watch_start = Mock()

        self.gui.host_trigger_start()
        self.assertFalse(DeferredThread.created)
        self.assertFalse(self.gui.is_counting_down)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertIsNotNone(self.gui._sync_start_after)
        server.applied_players.assert_called_once_with(7, after=self.gui._sync_start_after)
        self.assertIn("p3", self.gui.log.call_args.args[0])
        snapshot = server.publish.call_args.args[0]
        self.assertEqual(snapshot["task_id"], self.gui.active_task_id)
        self.assertEqual(snapshot["target_duration"], 240)

        server.applied_players.return_value = {"p1", "p2", "p3"}
        self.gui.host_trigger_start()
        self.assertTrue(self.gui.is_counting_down)
        self.assertEqual(len(DeferredThread.created), 1)
        self.backend.send_toggle_hotkey.assert_not_called()
        DeferredThread.created[0].run()
        self.backend.send_toggle_hotkey.assert_called_once_with(123)

    def test_player_ready_guard_rejects_missing_stale_or_failed_host_sync(self):
        self.gui.current_mode = "PLAYER"
        self.gui._sync_required = True
        self.gui.config["player_id"] = "p1"
        self.assertFalse(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
        client = Mock(last_error=None, last_seen_at=99, current_revision=5, instance_id="host-instance")
        self.gui._sync_client = client
        self.gui._shared_settings = {"task_id": "task-01", "target_duration": 237}
        self.gui._sync_applied_revision = 5
        self.gui._sync_applied_instance = "host-instance"
        with patch.object(time, "monotonic", return_value=100):
            self.assertTrue(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
            client.last_seen_at = 90
            self.assertFalse(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
            client.last_seen_at = 99
            client.last_error = "Host unreachable"
            self.assertFalse(self.gui.player_ready_request_allowed(0, 0, "p1", 0))

    def test_player_cannot_ready_between_fetching_new_revision_and_applying_it_on_ui(self):
        self.gui.current_mode = "PLAYER"
        self.gui._sync_required = True
        self.gui.config["player_id"] = "p1"
        key = ("PLAYER", "ABC123", "host", "")
        client = Mock(last_error=None, last_seen_at=99, current_revision=5, instance_id="host-instance")
        self.gui._sync_key, self.gui._sync_client = key, client
        self.gui._shared_settings = {"task_id": "task-01", "target_duration": 237}
        self.gui._sync_applied_revision = 5
        self.gui._sync_applied_instance = "host-instance"
        with patch.object(time, "monotonic", return_value=100):
            self.assertTrue(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
            # The network worker has accepted a newer Host packet, but Tk has
            # not applied its target duration and display preferences yet.
            client.current_revision = 6
            self.assertFalse(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
            self.gui.finish_settings_fetch(key, 0, client, {
                "revision": 6, "instance_id": "host-instance",
                "settings": {"task_id": "task-02", "target_duration": 242}})
            self.assertTrue(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
            self.assertEqual(self.gui.round_target_duration("task-02"), 242)
            client.instance_id = "restarted-host"
            self.assertFalse(self.gui.player_ready_request_allowed(0, 0, "p1", 0))

    def test_fix_selection_never_auto_starts_after_ack_until_f10_is_requested(self):
        self.gui._sync_required = True
        self.gui.room_code = "ABC123"
        self.gui._sync_key = ("HOST", "ABC123", "host", "")
        server = Mock(room_code="ABC123", host_id="host", port=45455)
        server.publish.return_value = 7
        server.applied_players.return_value = set()
        self.gui._sync_server = server
        self.gui.watch_start = Mock()
        self.gui.show_abort_choices()
        self.gui.handle_host_hotkey(0x7A)
        self.assertEqual(self.gui.active_task_id, "asm-s221-01-fix1")
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.assertFalse(self.gui.is_counting_down)
        self.assertFalse(DeferredThread.created)

        self.gui.sync_settings_tick()
        self.assertFalse(DeferredThread.created)
        self.backend.send_toggle_hotkey.assert_not_called()
        server.applied_players.return_value = {"p1", "p2", "p3"}
        self.gui.sync_settings_tick()
        self.assertFalse(DeferredThread.created)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.backend.send_toggle_hotkey.assert_not_called()

        server.applied_players.return_value = set()
        self.gui.handle_host_hotkey(0x79)
        self.assertEqual(self.gui._pending_sync_start_task, "asm-s221-01-fix1")
        self.assertFalse(DeferredThread.created)
        self.gui.sync_settings_tick()
        self.assertFalse(DeferredThread.created)
        server.applied_players.return_value = {"p1", "p2", "p3"}
        self.gui.sync_settings_tick()
        self.assertEqual(self.gui.active_task_id, "asm-s221-01-fix1")
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.assertTrue(self.gui.is_counting_down)
        self.assertEqual(len(DeferredThread.created), 1)

        self.gui.sync_settings_tick()
        self.assertEqual(len(DeferredThread.created), 1)
        DeferredThread.created[0].run()
        self.backend.send_toggle_hotkey.assert_called_once_with(123)
        self.gui.sync_settings_tick()
        self.assertEqual(len(DeferredThread.created), 1)

    def test_f10_cancels_pending_sync_start_without_sending_any_native_key(self):
        self.gui._sync_required = True
        self.gui.room_code = "ABC123"
        self.gui._sync_key = ("HOST", "ABC123", "host", "")
        server = Mock(room_code="ABC123", host_id="host", port=45455)
        server.publish.return_value = 7
        server.applied_players.return_value = set()
        self.gui._sync_server = server
        self.gui.host_trigger_start()
        self.assertEqual(self.gui._pending_sync_start_task, self.gui.active_task_id)
        self.gui.handle_host_hotkey(0x79)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.assertFalse(self.gui.is_recording)
        self.assertFalse(self.gui.is_counting_down)

        server.applied_players.return_value = {"p1", "p2", "p3"}
        self.gui.sync_settings_tick()
        self.assertFalse(DeferredThread.created)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.stop_recording_verified.assert_not_called()

    def test_busy_role_switch_is_blocked(self):
        self.gui.is_recording = True
        self.gui.switch_mode("PLAYER")
        self.assertEqual(self.gui.current_mode, "HOST")

    def test_audit_view_preserves_operational_host_and_key_interception(self):
        self.gui.switch_mode("AUDIT")
        self.assertEqual(self.gui.view_mode, "AUDIT")
        self.assertEqual(self.gui.current_mode, "HOST")
        self.assertTrue(self.gui.should_capture_hotkey(0x79))

    def test_open_session_blocks_cross_role_switch(self):
        self.gui.switch_mode("PLAYER")
        self.assertEqual(self.gui.current_mode, "HOST")
        self.messages.showwarning.assert_called_once()

    def test_reset_during_task_preparation_does_not_send_f10_or_leave_unknown_state(self):
        self.gui.host_trigger_start()
        self.gui.host_cancel_and_reset()
        self.assertFalse(self.gui.is_counting_down)
        self.assertFalse(self.gui.capture_state_unknown)
        DeferredThread.created[0].run()
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_physical_host_keys_consumed_and_injected_keys_pass(self):
        self.assertTrue(self.gui.should_capture_hotkey(0x79))
        self.assertFalse(self.gui.should_capture_hotkey(0x79, injected=True))
        self.gui.current_mode = "PLAYER"
        self.assertFalse(self.gui.should_capture_hotkey(0x79))

    def test_f11_choice_and_f10_stop_are_routed_without_real_keys(self):
        self.gui.host_choose_option = Mock()
        self.gui.abort_state = "AWAITING_CHOICE"
        self.gui.handle_host_hotkey(0x7A)
        self.gui.host_choose_option.assert_called_once_with("FIX")
        self.gui.abort_state = None
        self.gui.is_recording = True
        self.gui.host_trigger_emergency_stop = Mock()
        self.gui.handle_host_hotkey(0x79)
        self.gui.host_trigger_emergency_stop.assert_called_once_with()

    def test_manual_stop_then_f11_only_selects_fix_until_next_f10(self):
        self.gui.watch_start = Mock()
        self.gui.handle_host_hotkey(0x79)
        self.assertTrue(self.gui.is_counting_down)
        self.assertEqual(len(DeferredThread.created), 1)
        DeferredThread.created[0].run()
        self.backend.send_toggle_hotkey.assert_called_once_with(123)
        self.gui.follower.current_recording = self.gui.active_task_id
        self.gui.finish_start(self.gui._transition_token, self.gui.active_task_id, True, "")
        self.assertTrue(self.gui.is_recording)
        self.assertIn("F10: DỪNG KHẨN CẤP", self.gui.lbl_host_status.options["text"])

        def finish_native(task_id):
            self.gui.follower.current_recording = None
            self.gui.follower.current_stopped = (task_id, "round_end")
            self.assertTrue(self.gui.is_stopping)
            self.assertIsNone(self.gui.abort_state)
            self.assertFalse(self.gui.frame_host_choices.visible)
            self.gui.handle_host_hotkey(0x7A)
            self.gui.handle_host_hotkey(0x79)
            self.assertEqual(len(DeferredThread.created), 2)
            self.assertFalse(self.gui.is_validating)
            self.gui.follower.last_round_result = (task_id, "all ok")
            return True

        self.gui.wait_capture_finished = Mock(side_effect=finish_native)

        self.gui.handle_host_hotkey(0x79)
        self.assertTrue(self.gui.is_stopping)
        self.assertEqual(len(DeferredThread.created), 2)
        self.gui.handle_host_hotkey(0x79)
        self.assertEqual(len(DeferredThread.created), 2)
        DeferredThread.created[1].run()
        self.backend.stop_recording_verified.assert_called_once()
        stop_call = self.backend.stop_recording_verified.call_args
        self.assertEqual(stop_call.args, (self.gui.follower, self.gui.active_task_id))
        self.assertEqual(stop_call.kwargs["hwnd"], 123)
        self.assertEqual(stop_call.kwargs["timeout"], 30)
        self.assertTrue(stop_call.kwargs["allow_armed"])
        self.assertTrue(callable(stop_call.kwargs["can_stop"]))
        self.assertFalse(self.gui.is_recording)
        self.assertFalse(self.gui.is_validating)
        self.assertEqual(self.gui.abort_state, "AWAITING_CHOICE")
        self.assertEqual(self.gui.active_task_id, "asm-s221-01")
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.assertEqual(len(DeferredThread.created), 2)
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})

        self.gui.handle_host_hotkey(0x7A)
        self.assertEqual(len(DeferredThread.created), 2)
        self.assertEqual(self.gui.active_task_id, "asm-s221-01-fix1")
        self.assertFalse(self.gui.frame_host_choices.visible)
        self.assertFalse(self.gui.is_counting_down)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.backend.send_toggle_hotkey.assert_called_once_with(123)
        self.gui.handle_host_hotkey(0x79)
        self.assertEqual(len(DeferredThread.created), 3)
        DeferredThread.created[2].run()
        self.assertEqual(self.backend.send_toggle_hotkey.call_count, 2)
        self.gui.watch_start.assert_called_with(3, "asm-s221-01-fix1")

    def test_f11_without_fix_choices_never_stops_or_starts_odx(self):
        for state in ("idle", "preparing", "recording", "stopping", "validating"):
            with self.subTest(state=state):
                self.gui.is_counting_down = state == "preparing"
                self.gui.is_recording = state == "recording"
                self.gui.is_stopping = state == "stopping"
                self.gui.is_validating = state == "validating"
                self.gui.handle_host_hotkey(0x7A)
                self.assertFalse(DeferredThread.created)
                self.assertIsNone(self.gui.abort_state)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.stop_recording_verified.assert_not_called()

    def test_repeated_f11_after_fix_selection_never_starts_or_changes_the_selected_task(self):
        self.gui.show_abort_choices()
        self.gui.handle_host_hotkey(0x7A)
        selected = self.gui.active_task_id
        self.gui.handle_host_hotkey(0x7A)
        self.assertEqual(selected, "asm-s221-01-fix1")
        self.assertEqual(self.gui.active_task_id, selected)
        self.assertFalse(DeferredThread.created)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.stop_recording_verified.assert_not_called()

    def test_f10_in_choices_selects_next_and_starts_because_start_was_explicitly_requested(self):
        self.gui.show_abort_choices()
        self.gui.watch_start = Mock()
        self.gui.handle_host_hotkey(0x79)
        self.assertEqual(self.gui.active_task_id, "asm-s221-02")
        self.assertTrue(self.gui.is_counting_down)
        self.assertEqual(len(DeferredThread.created), 1)
        DeferredThread.created[0].run()
        self.backend.send_toggle_hotkey.assert_called_once_with(123)

    def test_stopping_fix1_then_f10_prepares_next_original_task_before_native_start(self):
        self.gui.combo_batch.value = "zukumi"
        self.gui.select_task_id("zukumi-03-fix1")
        self.gui.begin_confirmed_recording("zukumi-03-fix1")
        self.gui.rec_start_time = time.monotonic() - 12
        self.gui.follower.current_recording = "zukumi-03-fix1"
        self.gui.wait_capture_finished = Mock(return_value=True)
        self.gui.host_trigger_emergency_stop()
        DeferredThread.created[0].run()
        self.assertEqual(self.gui.abort_state, "AWAITING_CHOICE")
        self.assertEqual(self.gui.pending_next_task, "zukumi-04")
        self.backend.set_task_box.reset_mock()
        self.gui.watch_start = Mock()

        def verify_native_start(window):
            self.assertEqual(window, 123)
            self.assertEqual(self.gui.active_task_id, "zukumi-04")
            self.assertEqual(self.backend.set_task_box.call_args.args, ("zukumi-04",))
            return True

        self.backend.send_toggle_hotkey.side_effect = verify_native_start
        self.gui.handle_host_hotkey(0x79)
        self.assertEqual(self.gui.active_task_id, "zukumi-04")
        self.assertEqual(self.gui.task_id_override, "zukumi-04")
        self.assertTrue(self.gui.is_counting_down)
        self.assertFalse(self.gui.frame_host_choices.visible)
        DeferredThread.created[1].run()
        self.backend.send_toggle_hotkey.assert_called_once_with(123)
        self.gui.watch_start.assert_called_once_with(self.gui._transition_token, "zukumi-04")

    def test_start_waits_until_tool_reclaims_priority_from_the_native_odx_hook(self):
        self.gui._hotkey_precedence_required = True
        self.gui.ensure_host_hotkey_priority = Mock(return_value=False)
        self.gui.host_trigger_start()
        self.gui.ensure_host_hotkey_priority.assert_called_once_with()
        self.assertFalse(DeferredThread.created)
        self.assertFalse(self.gui.is_counting_down)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_hook_priority_is_rechecked_after_task_injection_before_f10(self):
        self.gui._hotkey_precedence_required = True
        self.gui.ensure_host_hotkey_priority = Mock(return_value=True)
        self.gui.watch_start = Mock()
        self.gui.host_trigger_start()
        self.assertTrue(self.gui.is_counting_down)
        self.gui.ensure_host_hotkey_priority.return_value = False
        DeferredThread.created[0].run()
        self.assertGreaterEqual(self.gui.ensure_host_hotkey_priority.call_count, 2)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.gui.watch_start.assert_not_called()
        self.assertFalse(self.gui.is_counting_down)
        self.assertFalse(self.gui.capture_state_unknown)

    def enable_hook_priority_fixture(self):
        self.gui._hotkey_precedence_required = True
        self.gui._hook_thread_id = 77
        user = types.SimpleNamespace(PostThreadMessageW=Mock(return_value=True))
        self.ns["ctypes"] = types.SimpleNamespace(windll=types.SimpleNamespace(user32=user))
        self.ns["wintypes"] = types.SimpleNamespace(DWORD=int, UINT=int, WPARAM=int, LPARAM=int, BOOL=bool)
        return user

    def test_owned_odx_requires_one_hook_refresh_before_keys_are_ready(self):
        user = self.enable_hook_priority_fixture()
        self.assertFalse(self.gui.ensure_host_hotkey_priority())
        self.assertEqual(self.gui._hotkey_refresh_target, (123, 321))
        self.assertFalse(self.gui.hotkeys_ready)
        user.PostThreadMessageW.assert_called_once_with(77, 0x8001, 1, 0)
        self.assertFalse(self.gui.ensure_host_hotkey_priority())
        user.PostThreadMessageW.assert_called_once()

        self.gui.finish_hotkey_refresh(1, 123, 321, True)
        self.assertTrue(self.gui.hotkeys_ready)
        self.assertEqual(self.gui._hotkey_owner_identity, (123, 321))
        self.assertIsNone(self.gui._hotkey_refresh_target)
        self.assertTrue(self.gui.ensure_host_hotkey_priority())
        user.PostThreadMessageW.assert_called_once()
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_new_odx_window_or_pid_requires_a_new_priority_confirmation(self):
        user = self.enable_hook_priority_fixture()
        self.gui._hotkey_owner_identity = (123, 321)
        self.backend.find_odx_window.return_value = 456
        self.backend._window_process_path.return_value = (654, "selected-odx-app.exe")
        self.assertFalse(self.gui.ensure_host_hotkey_priority())
        self.assertEqual(self.gui._hotkey_refresh_target, (456, 654))
        self.gui.finish_hotkey_refresh(1, 456, 654, True)
        self.assertTrue(self.gui.ensure_host_hotkey_priority())

        self.backend._window_process_path.return_value = (655, "selected-odx-app.exe")
        self.assertFalse(self.gui.ensure_host_hotkey_priority())
        self.assertEqual(self.gui._hotkey_refresh_target, (456, 655))
        self.assertFalse(self.gui.hotkeys_ready)
        self.assertEqual(user.PostThreadMessageW.call_count, 2)
        self.gui.finish_hotkey_refresh(1, 456, 654, True)
        self.assertFalse(self.gui.hotkeys_ready)
        self.assertEqual(self.gui._hotkey_refresh_target, (456, 655))
        self.gui.finish_hotkey_refresh(2, 456, 655, True)
        self.assertTrue(self.gui.ensure_host_hotkey_priority())

    def test_stale_generation_identity_or_shutdown_cannot_mark_hook_ready(self):
        self.enable_hook_priority_fixture()
        self.gui.ensure_host_hotkey_priority()
        self.gui.ensure_host_hotkey_priority(force=True)
        self.assertEqual(self.gui._hotkey_refresh_generation, 2)
        for generation, hwnd, pid in ((1, 123, 321), (2, 456, 321), (2, 123, 654)):
            with self.subTest(generation=generation, hwnd=hwnd, pid=pid):
                self.gui.finish_hotkey_refresh(generation, hwnd, pid, True)
                self.assertFalse(self.gui.hotkeys_ready)
                self.assertEqual(self.gui._hotkey_refresh_target, (123, 321))
        self.gui.is_running = False
        self.gui.finish_hotkey_refresh(2, 123, 321, True)
        self.assertFalse(self.gui.hotkeys_ready)

    def test_failed_refresh_request_stays_blocked_and_retries_with_backoff(self):
        user = self.enable_hook_priority_fixture()
        user.PostThreadMessageW.return_value = False
        with patch.object(time, "monotonic", return_value=100):
            self.assertFalse(self.gui.ensure_host_hotkey_priority())
        self.assertFalse(self.gui.hotkeys_ready)
        self.assertIsNone(self.gui._hotkey_owner_identity)
        self.assertEqual(self.gui._hotkey_refresh_retry_at, 102)
        user.PostThreadMessageW.return_value = True
        with patch.object(time, "monotonic", return_value=101):
            self.assertFalse(self.gui.ensure_host_hotkey_priority())
        user.PostThreadMessageW.assert_called_once()
        with patch.object(time, "monotonic", return_value=103):
            self.assertFalse(self.gui.ensure_host_hotkey_priority())
        self.assertEqual(user.PostThreadMessageW.call_count, 2)
        self.assertFalse(self.gui.hotkeys_ready)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_room_confirmation_forces_refresh_and_blocks_start_until_acknowledged(self):
        user = self.enable_hook_priority_fixture()
        self.gui.start_host_room_tracking = Mock()
        self.gui._hotkey_owner_identity = (123, 321)
        self.gui.finish_host_room_creation(0, "ABC123")
        user.PostThreadMessageW.assert_called_once_with(77, 0x8001, 1, 0)
        self.assertTrue(self.gui.odx_opened)
        self.assertFalse(self.gui.hotkeys_ready)
        self.gui.host_trigger_start()
        self.assertFalse(DeferredThread.created)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.gui.finish_hotkey_refresh(1, 123, 321, True)
        self.gui.host_trigger_start()
        self.assertTrue(self.gui.is_counting_down)
        self.assertEqual(len(DeferredThread.created), 1)

    def test_f9_in_choices_selects_original_folder_without_starting(self):
        self.gui.select_task_id("asm-s221-01-fix3")
        self.gui.show_abort_choices()
        self.gui.handle_host_hotkey(0x78)
        self.assertEqual(self.gui.active_task_id, "asm-s221-01")
        self.assertFalse(DeferredThread.created)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_second_f10_before_start_command_cancels_preparation_without_a_key(self):
        self.gui.handle_host_hotkey(0x79)
        self.assertTrue(self.gui.is_counting_down)
        self.assertFalse(self.gui._start_command_sent)
        self.gui.handle_host_hotkey(0x79)
        self.assertFalse(self.gui.is_counting_down)
        self.assertFalse(self.gui.capture_state_unknown)
        self.assertEqual(self.gui.abort_state, "AWAITING_CHOICE")
        self.assertEqual(len(DeferredThread.created), 1)
        DeferredThread.created[0].run()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.stop_recording_verified.assert_not_called()

    def test_early_stop_shows_three_choices_without_advancing_progress(self):
        self.gui.finish_host_stop(0, self.gui.active_task_id, True, "emergency")
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.assertEqual(self.gui.abort_state, "AWAITING_CHOICE")
        self.assertEqual(self.gui.pending_fix_task, "asm-s221-01-fix1")
        self.assertEqual(self.gui.pending_next_task, "asm-s221-02")
        choice_text = self.gui.lbl_host_status.options["text"]
        for shortcut in ("F11", "F10", "F9"):
            self.assertIn(shortcut, choice_text)
        self.assertFalse(self.gui.is_validating)
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})

    def test_next_choice_hides_panel_and_selects_next_task_without_starting(self):
        self.gui.show_abort_choices()
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.gui.host_choose_option("NEXT")
        self.assertFalse(self.gui.frame_host_choices.visible)
        self.assertFalse(self.gui.is_counting_down)
        self.assertEqual(self.gui.active_task_id, "asm-s221-02")
        self.assertFalse(DeferredThread.created)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_base_choice_cannot_overwrite_original_task_even_after_explicit_f10(self):
        self.gui.select_task_id("asm-s221-01-fix3")
        self.gui.show_abort_choices()
        self.backend.is_task_already_recorded.return_value = (True, "existing-original-capture")
        self.gui.watch_start = Mock()
        self.gui.host_choose_option("CANCEL")
        self.assertEqual(self.gui.active_task_id, "asm-s221-01")
        self.assertFalse(self.gui.frame_host_choices.visible)
        self.assertFalse(self.gui.is_counting_down)
        self.assertFalse(DeferredThread.created)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.gui.handle_host_hotkey(0x79)
        self.assertFalse(self.gui.is_counting_down)
        self.assertFalse(DeferredThread.created)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.assertEqual(self.gui.active_task_id, "asm-s221-01")
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.assertEqual(self.gui.abort_state, "AWAITING_CHOICE")
        self.backend.send_toggle_hotkey.assert_not_called()
        self.gui.watch_start.assert_not_called()

    def test_stale_retake_permission_never_bypasses_the_same_existing_task(self):
        # Older releases kept this field after selecting the original task.
        self.gui._retake_task_once = self.gui.active_task_id
        self.backend.is_task_already_recorded.return_value = (True, "existing-capture")
        self.gui.host_trigger_start()
        self.assertFalse(DeferredThread.created)
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.assertEqual(self.gui.abort_state, "AWAITING_CHOICE")
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_existing_task_is_rejected_before_waiting_for_settings_ack(self):
        self.gui._sync_required = True
        self.gui.room_code = "ABC123"
        server = Mock(room_code="ABC123", host_id="host", port=45455)
        server.publish.return_value = 7
        server.applied_players.return_value = set()
        self.gui._sync_server = server
        self.backend.is_task_already_recorded.return_value = (True, "existing-capture")

        self.gui.host_trigger_start()

        self.assertFalse(DeferredThread.created)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.assertFalse(self.gui.is_counting_down)
        server.publish.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_task_created_during_injection_cancels_start_without_capture_unknown_state(self):
        self.gui.watch_start = Mock()
        self.gui.host_trigger_start()
        self.assertTrue(self.gui.is_counting_down)

        def task_became_occupied(**kwargs):
            self.backend.is_task_already_recorded.return_value = (True, "created-during-injection")

        self.backend.set_task_box.return_value.join.side_effect = task_became_occupied
        DeferredThread.created[0].run()

        self.assertFalse(self.gui.is_counting_down)
        self.assertFalse(self.gui.is_recording)
        self.assertFalse(self.gui.capture_state_unknown)
        self.assertFalse(self.gui._start_command_sent)
        self.assertIsNone(self.gui._pending_sync_start_task)
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})
        self.backend.send_toggle_hotkey.assert_not_called()
        self.gui.watch_start.assert_not_called()

    def test_task_created_at_final_action_lock_is_not_started(self):
        self.gui.watch_start = Mock()
        self.gui.host_trigger_start()
        backend = self.backend

        class CollisionLock:
            def __enter__(self):
                backend.is_task_already_recorded.return_value = (True, "created-before-f10")
                return self

            def __exit__(self, *_):
                return False

        self.gui._action_lock = CollisionLock()
        DeferredThread.created[0].run()

        self.assertFalse(self.gui.is_counting_down)
        self.assertFalse(self.gui.is_recording)
        self.assertFalse(self.gui.capture_state_unknown)
        self.assertFalse(self.gui._start_command_sent)
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.gui.watch_start.assert_not_called()

    def test_task_created_while_waiting_for_ack_cancels_queued_start(self):
        self.gui._sync_required = True
        self.gui.room_code = "ABC123"
        self.gui._sync_key = ("HOST", "ABC123", "host", "")
        server = Mock(room_code="ABC123", host_id="host", port=45455)
        server.publish.return_value = 7
        server.applied_players.return_value = set()
        self.gui._sync_server = server
        self.gui.host_trigger_start()
        self.assertEqual(self.gui._pending_sync_start_task, self.gui.active_task_id)

        self.backend.is_task_already_recorded.return_value = (True, "created-while-waiting")
        server.applied_players.return_value = {"p1", "p2", "p3"}
        self.gui.sync_settings_tick()
        self.gui.sync_settings_tick()

        self.assertIsNone(self.gui._pending_sync_start_task)
        self.assertFalse(DeferredThread.created)
        self.assertFalse(self.gui.is_counting_down)
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_late_collision_callback_cannot_reopen_choices_for_a_new_start(self):
        self.gui._transition_token = 2
        self.gui.is_counting_down = True
        self.gui.active_task_id = "new-task-02"

        self.gui.finish_start_collision(1, "old-task-01", "existing-old-capture")

        self.assertTrue(self.gui.is_counting_down)
        self.assertEqual(self.gui.active_task_id, "new-task-02")
        self.assertFalse(self.gui.frame_host_choices.visible)
        self.assertIsNone(self.gui.abort_state)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_f10_during_armed_countdown_uses_verified_stop_and_waits_for_completion(self):
        self.gui.watch_start = Mock()
        self.gui.handle_host_hotkey(0x79)
        DeferredThread.created[0].run()
        self.gui.follower.last_armed = self.gui.active_task_id
        self.gui.wait_capture_finished = Mock(return_value=True)
        self.assertTrue(self.gui.is_counting_down)
        self.assertTrue(self.gui._start_command_sent)
        self.gui.handle_host_hotkey(0x79)
        DeferredThread.created[1].run()
        self.backend.stop_recording_verified.assert_called_once()
        self.assertTrue(self.backend.stop_recording_verified.call_args.kwargs["allow_armed"])
        self.gui.wait_capture_finished.assert_called_once_with("asm-s221-01")
        self.assertEqual(self.gui.abort_state, "AWAITING_CHOICE")
        self.assertFalse(self.gui.is_validating)

    def test_start_clears_old_armed_task_before_any_new_command(self):
        self.gui.follower.last_armed = "asm-s221-01"
        self.gui.host_trigger_start()
        self.assertIsNone(self.gui.follower.last_armed)
        self.assertTrue(self.gui.is_counting_down)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_busy_room_blocks_start_and_is_rechecked_before_sending_f10(self):
        self.gui.tracker.latest_data["current_session"] = {"id": "previous-round"}
        self.gui.handle_host_hotkey(0x79)
        self.assertFalse(DeferredThread.created)
        self.backend.send_toggle_hotkey.assert_not_called()

        self.gui.tracker.latest_data["current_session"] = None
        self.gui.host_trigger_start()
        self.gui.tracker.latest_data["current_session"] = {"id": "other-round"}
        DeferredThread.created[0].run()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertFalse(self.gui.is_counting_down)
        self.assertFalse(self.gui.capture_state_unknown)

    def test_waiting_countdown_stop_rechecks_ownership_and_operation_generation(self):
        self.gui.is_counting_down = True
        self.gui._start_command_sent = True

        def verify_stop(*args, can_stop, **kwargs):
            self.assertTrue(can_stop())
            self.backend.is_managed_odx_window.return_value = False
            self.assertFalse(can_stop())
            self.backend.is_managed_odx_window.return_value = True
            self.gui._transition_token += 1
            self.assertFalse(can_stop())
            self.gui._transition_token -= 1
            self.gui.is_running = False
            self.assertFalse(can_stop())
            self.gui.is_running = True
            return False

        self.backend.stop_recording_verified.side_effect = verify_stop
        self.gui.host_trigger_emergency_stop()
        DeferredThread.created[0].run()
        self.backend.stop_recording_verified.assert_called_once()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertTrue(self.gui.capture_state_unknown)

    def test_automatic_duration_stop_keeps_normal_validation_flow(self):
        self.gui.begin_confirmed_recording(self.gui.active_task_id)
        self.gui.follower.current_recording = self.gui.active_task_id
        self.gui.rec_start_time = time.monotonic() - 241
        self.gui.wait_capture_finished = Mock()
        self.gui.update_live_timer()
        self.assertEqual(self.gui._pending_stop_action, "validate")
        self.assertTrue(self.gui.is_stopping)
        DeferredThread.created[0].run()
        self.backend.stop_recording_verified.assert_called_once()
        self.gui.wait_capture_finished.assert_not_called()
        self.assertTrue(self.gui.is_validating)
        self.assertIsNone(self.gui.abort_state)
        self.assertEqual(len(DeferredThread.created), 2)

    def test_late_emergency_ack_waits_for_final_result_and_keeps_fix_intent(self):
        self.gui.is_recording = True
        self.gui.rec_start_time = time.monotonic() - 100
        self.gui.capture_state_unknown = True
        self.gui._stop_confirmation_failed = True
        self.gui._pending_stop_action = "emergency"
        self.gui._pending_stop_task = self.gui.active_task_id
        self.gui.follower.current_recording = None
        self.gui.follower.current_stopped = (self.gui.active_task_id, "round_end")
        self.gui.follower.last_round_result = ("old-task", "all ok")
        self.gui.update_live_timer()
        self.assertIsNone(self.gui.abort_state)
        self.assertTrue(self.gui.is_recording)
        self.assertFalse(self.gui.is_validating)
        self.assertEqual(self.gui._pending_stop_action, "emergency")

        # Local capture files can be kept while the room is still finalizing.
        self.gui.follower.last_round_result = (self.gui.active_task_id, "ok")
        self.gui.update_live_timer()
        self.assertIsNone(self.gui.abort_state)
        self.assertTrue(self.gui.is_recording)
        self.assertTrue(self.gui.capture_state_unknown)
        self.assertFalse(self.gui.is_validating)
        self.assertEqual(self.gui._pending_stop_action, "emergency")
        self.assertFalse(DeferredThread.created)

        self.gui.follower.last_round_result = (self.gui.active_task_id, "all ok")
        self.gui.update_live_timer()
        self.assertFalse(self.gui.is_recording)
        self.assertFalse(self.gui.capture_state_unknown)
        self.assertFalse(self.gui._stop_confirmation_failed)
        self.assertFalse(self.gui.is_validating)
        self.assertEqual(self.gui.abort_state, "AWAITING_CHOICE")
        self.assertEqual(self.gui.active_task_id, "asm-s221-01")
        self.assertFalse(DeferredThread.created)
        self.backend.stop_recording_verified.assert_not_called()

    def test_wait_capture_finished_waits_past_local_kept_until_room_result(self):
        task_id = self.gui.active_task_id
        results = iter(((task_id, "ok"), (task_id, "all ok")))
        self.gui.follower.check_new_lines.side_effect = lambda: setattr(
            self.gui.follower, "last_round_result", next(results))
        with patch.object(time, "sleep"):
            self.assertTrue(self.gui.wait_capture_finished(task_id))
        self.assertEqual(self.gui.follower.check_new_lines.call_count, 2)

    def test_player_f11_is_consumed_for_managed_session_while_f10_still_passes(self):
        self.gui.current_mode = "PLAYER"
        self.gui.player_connection_active = True
        self.assertTrue(self.gui.should_capture_hotkey(0x7A))
        self.assertFalse(self.gui.should_capture_hotkey(0x79))
        self.gui.handle_host_hotkey(0x7A)
        self.assertFalse(DeferredThread.created)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.stop_recording_verified.assert_not_called()
        self.gui.player_connection_active = False
        self.assertFalse(self.gui.should_capture_hotkey(0x7A))

    def test_f10_in_unknown_state_never_sends_a_new_toggle(self):
        self.gui.capture_state_unknown = True
        self.gui.handle_host_hotkey(0x79)
        self.assertFalse(DeferredThread.created)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertIn("kiểm tra Odx", self.gui.log.call_args.args[0])

    def test_unready_problem_stale_room_or_missing_game_blocks_f10(self):
        self.gui.tracker.is_all_ready.return_value = False
        self.gui.host_trigger_start()
        self.assertFalse(DeferredThread.created)
        self.gui.tracker.is_all_ready.return_value = True
        self.gui.tracker.last_success_at -= 20
        self.gui.host_trigger_start()
        self.assertFalse(DeferredThread.created)
        self.gui.tracker.last_success_at = time.monotonic()
        self.backend.is_game_running.return_value = False
        self.gui.host_trigger_start()
        self.assertFalse(DeferredThread.created)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_task_path_characters_never_reach_backend(self):
        self.gui.ent_full_task.value = "../escape"
        self.gui.on_full_task_manual_edit()
        self.gui.host_trigger_start()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.set_task_box.assert_not_called()

    def test_atomic_config_failure_preserves_previous_file_and_reports_error(self):
        Path(self.gui.cfg_file).write_text('{"previous": true}', encoding="utf-8")
        with patch.object(os, "replace", side_effect=OSError("disk error")):
            self.assertFalse(self.gui.save_config())
        self.assertEqual(json.loads(Path(self.gui.cfg_file).read_text()), {"previous": True})
        self.assertIn("disk error", self.gui.log.call_args.args[0])
        self.assertEqual(list(Path(self.temp.name).glob("*.tmp")), [])

    def test_config_is_not_written_while_recording(self):
        self.gui.is_recording = True
        self.assertFalse(self.gui.save_config())
        self.assertFalse(Path(self.gui.cfg_file).exists())

    def test_update_download_runs_in_worker_and_blocks_recording(self):
        def result(callback):
            callback(True, {"version": "9.0.0", "download_url": "https://example.invalid/x.zip", "sha256": "abc"}, None)
        self.ns["check_for_updates_async"].side_effect = result
        self.gui.shutdown_now = Mock()
        self.gui.run_check_update()
        self.assertTrue(self.gui.is_updating)
        self.ns["download_and_apply_update"].assert_not_called()
        self.gui.host_trigger_start()
        self.assertEqual(len(DeferredThread.created), 1)
        DeferredThread.created[0].run()
        self.assertEqual(self.ns["download_and_apply_update"].call_args.kwargs["expected_sha256"], "abc")
        self.gui.shutdown_now.assert_called_once()

    def test_duplicate_player_connection_is_blocked(self):
        self.gui.current_mode = "PLAYER"
        self.gui.player_connection_active = True
        self.gui.player_start_connection()
        self.assertFalse(DeferredThread.created)

    def test_uncertain_stop_is_never_retried_by_timer_or_f10(self):
        self.gui.is_recording = True
        self.gui.rec_start_time = time.monotonic() - 300
        self.gui.capture_state_unknown = True
        self.gui._stop_confirmation_failed = True
        self.gui.follower.current_recording = self.gui.active_task_id
        self.gui.request_host_stop = Mock()
        self.gui.update_live_timer()
        self.gui.host_trigger_emergency_stop()
        self.gui.request_host_stop.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertTrue(self.gui.is_recording)
        self.assertIn("kiểm tra Odx", self.gui.log.call_args.args[0])

    def test_failed_stop_disables_emergency_toggle_and_f11_cannot_retry_it(self):
        self.gui.is_recording = True
        self.gui.follower.current_recording = self.gui.active_task_id
        self.gui.finish_host_stop(0, self.gui.active_task_id, False, "validate")
        self.assertEqual(self.gui.btn_host_emergency.options["state"], "disabled")
        self.gui.request_host_stop = Mock()
        self.gui.host_trigger_emergency_stop()
        self.gui.handle_host_hotkey(0x7A)
        self.gui.request_host_stop.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_late_confirmed_stop_clears_uncertain_recording(self):
        self.gui.is_recording = True
        self.gui.rec_start_time = time.monotonic() - 300
        self.gui.capture_state_unknown = True
        self.gui._stop_confirmation_failed = True
        self.gui.follower.current_recording = None
        self.gui.follower.current_stopped = (self.gui.active_task_id, "user")
        self.gui.update_live_timer()
        self.assertFalse(self.gui.is_recording)
        self.assertFalse(self.gui.capture_state_unknown)
        self.assertFalse(self.gui._stop_confirmation_failed)
        self.assertTrue(self.gui.is_validating)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_connecting_remains_busy_until_backend_join_finishes(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui.install_player_tracking = Mock()
        self.gui.shutdown_now = Mock()
        joining = types.SimpleNamespace(join=Mock(), success=True, joined=True, ready=False, error="")
        self.backend.player_auto_join_and_ready = Mock(return_value=joining)
        def while_joining():
            self.assertTrue(self.gui.is_connecting)
            self.assertTrue(self.gui.operation_busy())
            self.gui.run_clean_all()
            self.gui.run_check_update()
            self.gui.switch_mode("HOST")
            self.backend.cleanup_all_odx.assert_not_called()
            self.ns["check_for_updates_async"].assert_not_called()
            self.assertEqual(self.gui.current_mode, "PLAYER")
            self.gui.shutdown_now.assert_not_called()
        joining.join.side_effect = while_joining
        self.gui.player_start_connection()
        self.assertTrue(self.gui.is_connecting)
        DeferredThread.created[-1].run()
        joining.join.assert_called_once_with()
        self.assertFalse(self.gui.is_connecting)

    def test_connection_error_does_not_release_busy_until_worker_exits(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui.install_player_tracking = Mock()
        joining = types.SimpleNamespace(join=Mock(), success=True, joined=True, ready=False, error="")
        self.backend.player_auto_join_and_ready = Mock(return_value=joining)
        def error_then_finish():
            self.gui.player_connection_failed(1, "join rejected")
            self.assertEqual(self.gui._player_connection_generation, 2)
            self.assertTrue(self.gui.is_connecting)
        joining.join.side_effect = error_then_finish
        self.gui.player_start_connection()
        DeferredThread.created[-1].run()
        self.assertFalse(self.gui.is_connecting)
        self.assertFalse(self.gui.player_connection_active)
        self.assertFalse(self.gui.operation_busy())

    def test_join_exception_clears_current_connection_operation(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui.install_player_tracking = Mock()
        joining = types.SimpleNamespace(join=Mock(side_effect=RuntimeError("join failed")))
        self.backend.player_auto_join_and_ready = Mock(return_value=joining)
        self.gui.player_start_connection()
        DeferredThread.created[-1].run()
        self.assertFalse(self.gui.is_connecting)
        self.assertFalse(self.gui.player_connection_active)
        self.assertIn("join failed", self.gui.lbl_p_status.options["text"])

    def test_stale_connection_operation_cannot_release_new_worker(self):
        self.gui.is_connecting = True
        self.gui._connection_operation_token = 2
        self.gui.finish_player_connection_operation(1)
        self.assertTrue(self.gui.is_connecting)
        self.gui.finish_player_connection_operation(2)
        self.assertFalse(self.gui.is_connecting)

    def test_ready_stays_busy_until_actual_worker_finishes(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui.shutdown_now = Mock()
        readying = types.SimpleNamespace(join=Mock())
        self.backend.player_ensure_ready_in_room = Mock(return_value=readying)
        def while_readying():
            self.assertTrue(self.gui.operation_busy())
            self.gui.run_clean_all()
            self.gui.run_check_update()
            self.backend.cleanup_all_odx.assert_not_called()
            self.ns["check_for_updates_async"].assert_not_called()
            self.gui.shutdown_now.assert_not_called()
        readying.join.side_effect = while_readying
        self.gui.auto_ready_player_next_round()
        self.assertTrue(self.gui._ready_inflight)
        DeferredThread.created[-1].run()
        readying.join.assert_called_once_with()
        self.assertFalse(self.gui._ready_inflight)

    def test_stale_ready_completion_cannot_release_new_worker(self):
        self.gui._ready_inflight = True
        self.gui._ready_operation_token = 2
        self.gui.finish_player_ready_operation(0, 1)
        self.assertTrue(self.gui._ready_inflight)
        self.gui.finish_player_ready_operation(1, 2)
        self.assertFalse(self.gui._ready_inflight)

    def test_connection_thread_start_failure_does_not_leave_busy(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        with patch.object(DeferredThread, "start", side_effect=RuntimeError("thread unavailable")):
            self.gui.player_start_connection()
        self.assertFalse(self.gui.is_connecting)
        self.assertFalse(self.gui.player_connection_active)

    def test_ready_thread_start_failure_does_not_leave_busy(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        with patch.object(DeferredThread, "start", side_effect=RuntimeError("thread unavailable")):
            self.gui.auto_ready_player_next_round()
        self.assertFalse(self.gui._ready_inflight)

    def test_ready_snapshots_exact_player_and_never_reads_widgets_from_worker(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui.config["player_id"] = "p1"
        readying = types.SimpleNamespace(join=Mock(), success=True, status="ready", error="")
        self.backend.player_ensure_ready_in_room = Mock(return_value=readying)
        self.gui.auto_ready_player_next_round()
        self.gui.ent_p_id.get = Mock(side_effect=AssertionError("worker read Tk ID"))
        self.gui.ent_p_room.get = Mock(side_effect=AssertionError("worker read Tk room"))
        DeferredThread.created[-1].run()
        args, kwargs = self.backend.player_ensure_ready_in_room.call_args
        self.assertEqual(args, ("ABC123",))
        self.assertEqual(kwargs["player_id"], "p1")
        self.assertIsInstance(kwargs["cancel_event"], threading.Event)
        self.assertIn("MÁY CHỦ XÁC NHẬN", self.gui.lbl_p_status.options["text"])
        self.assertFalse(self.gui._ready_inflight)

    def test_unconfirmed_ready_result_is_not_success_and_can_retry(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        readying = types.SimpleNamespace(join=Mock(), success=False,
                                        status="unconfirmed", error="server has not confirmed")
        self.backend.player_ensure_ready_in_room = Mock(return_value=readying)
        self.gui.auto_ready_player_next_round()
        DeferredThread.created[-1].run()
        self.assertFalse(self.gui._ready_inflight)
        self.assertIn("CHƯA XÁC NHẬN READY", self.gui.lbl_p_status.options["text"])
        self.assertIn("server has not confirmed", self.gui.lbl_p_status.options["text"])
        self.assertFalse(self.gui._player_validation_failed)
        self.gui.auto_ready_player_next_round()
        self.assertTrue(self.gui._ready_inflight)
        self.assertEqual(len(DeferredThread.created), 2)

    def test_round_start_cancels_queued_ready_before_backend_invocation(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.backend.player_ensure_ready_in_room = Mock()
        self.gui.auto_ready_player_next_round()
        pending_event = self.gui._ready_cancel_event
        self.gui.player_recording_state(0, "asm-s221-01", True, 1)
        self.assertTrue(pending_event.is_set())
        DeferredThread.created[-1].run()
        self.backend.player_ensure_ready_in_room.assert_not_called()
        self.assertFalse(self.gui._ready_inflight)
        self.assertTrue(self.gui.is_player_recording)
        self.assertIn("ODX ĐÃ XÁC NHẬN QUAY", self.gui.lbl_p_status.options["text"])

    def test_ready_guard_reads_follower_before_countdown_callback_reaches_ui(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        readying = types.SimpleNamespace(join=Mock(), success=False, status="cancelled", error="")
        self.backend.player_ensure_ready_in_room = Mock(return_value=readying)
        def native_armed_before_ui():
            callback = self.backend.player_ensure_ready_in_room.call_args.kwargs["can_ready"]
            self.assertTrue(callback())
            self.gui.follower.last_armed = "asm-s221-01"
            self.assertFalse(callback())
            self.assertFalse(self.gui.p_counting_down)
        readying.join.side_effect = native_armed_before_ui
        self.gui.auto_ready_player_next_round()
        DeferredThread.created[-1].run()
        self.assertFalse(self.gui._ready_inflight)

    def test_old_ready_status_and_confirmation_cannot_overwrite_disconnected_ui(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        readying = types.SimpleNamespace(join=Mock(), success=True, status="ready", error="")
        self.backend.player_ensure_ready_in_room = Mock(return_value=readying)
        def disconnect_before_confirmation():
            callback = self.backend.player_ensure_ready_in_room.call_args.kwargs["on_status"]
            self.gui.player_connection_failed(0, "room disconnected")
            self.gui.log.reset_mock()
            callback("late Ready success", "SUCCESS")
            self.gui.log.assert_not_called()
        readying.join.side_effect = disconnect_before_confirmation
        self.gui.auto_ready_player_next_round()
        DeferredThread.created[-1].run()
        self.assertFalse(self.gui._ready_inflight)
        self.assertIn("room disconnected", self.gui.lbl_p_status.options["text"])
        self.assertNotIn("MÁY CHỦ XÁC NHẬN", self.gui.lbl_p_status.options["text"])

    def test_join_passes_guard_and_cancels_initial_ready_as_host_arms(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui.install_player_tracking = Mock()
        joining = types.SimpleNamespace(join=Mock(), success=True, joined=True, ready=False, error="")
        self.backend.player_auto_join_and_ready = Mock(return_value=joining)
        def host_arms_during_join():
            kwargs = self.backend.player_auto_join_and_ready.call_args.kwargs
            self.assertTrue(kwargs["can_ready"]())
            self.assertTrue(self.gui.is_connecting)
            self.gui.player_countdown_state(1, "asm-s221-01")
            self.assertTrue(kwargs["cancel_event"].is_set())
            self.assertFalse(kwargs["can_ready"]())
            kwargs["on_status"]("late Ready success", "SUCCESS")
            self.assertIn("ODX ĐANG CHUẨN BỊ", self.gui.lbl_p_status.options["text"])
        joining.join.side_effect = host_arms_during_join
        self.gui.player_start_connection()
        DeferredThread.created[-1].run()
        self.assertFalse(self.gui.is_connecting)
        self.assertTrue(self.gui.p_counting_down)

    def test_shutdown_cancels_both_pending_ready_paths(self):
        self.gui._ready_cancel_event = threading.Event()
        self.gui._connection_cancel_event = threading.Event()
        self.gui.shutdown_now()
        self.assertTrue(self.gui._ready_cancel_event.is_set())
        self.assertTrue(self.gui._connection_cancel_event.is_set())

    def test_ready_guard_rejects_save_failed_validation_and_changed_identity(self):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.assertTrue(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
        self.gui.is_validating = True
        self.assertFalse(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
        self.gui.is_validating = False
        self.gui._player_validation_failed = True
        self.assertFalse(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
        self.gui._player_validation_failed = False
        self.gui.config["player_id"] = "other-player"
        self.assertFalse(self.gui.player_ready_request_allowed(0, 0, "p1", 0))

    def test_reconnect_resets_player_round_tokens(self):
        self.gui.current_mode = "PLAYER"
        self.gui._player_round_token = 5
        self.gui._player_validation_token = (0, "old-task", 5)
        self.gui.player_start_connection()
        self.assertEqual(self.gui._player_round_token, 0)
        self.assertIsNone(self.gui._player_validation_token)
        self.gui.player_recording_state(1, "new-task", True, 1)
        self.assertTrue(self.gui.is_player_recording)

    def test_untracked_existing_host_is_not_reset_or_reused(self):
        self.backend.is_managed_odx_window.return_value = False
        self.gui.host_open_odx()
        self.backend.cleanup_all_odx.assert_not_called()
        self.gui.follower.reset_to_end.assert_not_called()
        self.messages.showwarning.assert_called_once()

    def test_foreign_odx_from_other_installation_blocks_global_keys_and_ready(self):
        self.backend.find_unmanaged_odx_window.return_value = 456
        self.gui.host_trigger_start()
        self.assertFalse(DeferredThread.created)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.gui.is_recording = True
        self.gui.follower.current_recording = "asm-s221-01"
        self.gui.host_cancel_and_reset()
        DeferredThread.created[-1].run()
        self.assertTrue(self.gui.capture_state_unknown)

    def test_startup_preview_never_injects_into_unowned_odx(self):
        self.gui.odx_opened = False
        self.backend.is_managed_odx_window.return_value = False
        self.gui.update_task_id_preview()
        self.backend.set_task_box.assert_not_called()

    def test_player_unowned_window_guard_precedes_shared_identity_write(self):
        self.gui.current_mode = "PLAYER"
        self.backend.is_managed_odx_window.return_value = False
        self.gui.player_start_connection()
        DeferredThread.created[-1].run()
        self.backend.set_player_info.assert_not_called()
        self.assertFalse(self.gui.player_connection_active)

    def test_declining_force_close_preserves_uncertain_capture(self):
        self.gui._stop_confirmation_failed = True
        self.gui.capture_state_unknown = True
        self.gui.is_recording = True
        self.messages.askyesno.return_value = False
        self.gui.on_close_window()
        self.assertTrue(self.gui.is_recording)
        self.assertTrue(self.gui.is_running)
        self.backend.cleanup_all_odx.assert_not_called()

    def test_accepting_force_close_only_invokes_managed_cleanup(self):
        self.gui._stop_confirmation_failed = True
        self.gui.capture_state_unknown = True
        self.gui.on_close_window()
        self.assertFalse(self.gui.is_running)
        self.backend.cleanup_all_odx.assert_called_once_with(kill_gui=True, tool_closing=True)
        self.gui.root.destroy.assert_called_once()

    def test_player_ready_is_blocked_while_validating_or_after_failure(self):
        self.gui.current_mode = "PLAYER"
        self.gui.is_validating = True
        self.gui.auto_ready_player_next_round()
        self.assertFalse(DeferredThread.created)
        self.gui.is_validating = False
        self.gui._player_validation_failed = True
        self.gui.auto_ready_player_next_round()
        self.assertFalse(DeferredThread.created)

    def test_old_player_validation_cannot_clear_new_round_state(self):
        self.gui.current_mode = "PLAYER"
        self.gui.is_validating = True
        self.gui._player_round_token = 2
        self.gui._player_validation_token = (0, "asm-s221-02", 2)
        self.gui.auto_ready_player_next_round = Mock()
        self.gui.finish_player_validation(0, "asm-s221-01", 1, True)
        self.assertTrue(self.gui.is_validating)
        self.gui.auto_ready_player_next_round.assert_not_called()

    def test_failed_player_validation_does_not_advertise_ready(self):
        self.gui.current_mode = "PLAYER"
        self.gui.is_validating = True
        self.gui._player_round_token = 1
        self.gui._player_validation_token = (0, "asm-s221-01", 1)
        self.gui.auto_ready_player_next_round = Mock()
        self.gui.finish_player_validation(0, "asm-s221-01", 1, False)
        self.assertTrue(self.gui._player_validation_failed)
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.assertEqual(self.gui.btn_p_connect.options["state"], "normal")

    def test_cancelled_player_round_skips_pass_audit(self):
        self.gui.current_mode = "PLAYER"
        self.gui._player_round_token = 1
        self.gui._player_validation_token = (0, "asm-s221-01", 1)
        self.gui.auto_ready_player_next_round = Mock()
        self.gui.post_player_round(0, "asm-s221-01", 1, cancelled=True)
        self.assertFalse(self.gui._player_validation_failed)
        self.assertIn("ĐÃ HỦY", self.gui.lbl_p_status.options["text"])
        self.assertNotIn("PASS", self.gui.lbl_p_status.options["text"])

    def test_local_failure_cannot_be_overridden_by_all_ok(self):
        self.gui.is_validating = True
        self.gui.increment_task_number = Mock()
        self.gui.finish_validation(0, "asm-s221-01", False, True, None)
        self.gui.increment_task_number.assert_not_called()
        self.assertFalse(self.gui.is_in_cooldown)

    def test_success_journal_committed_once_before_advancement(self):
        self.gui.finish_validation(0, "asm-s221-01", True, True, None)
        self.assertEqual(self.gui.active_task_id, "asm-s221-02")
        self.assertEqual(self.gui.batch_progress()["completed"], ["asm-s221-01"])
        self.assertEqual(json.loads(Path(self.gui.prog_file).read_text())["batches"]["asm-s221-"]["next_task"], "asm-s221-02")
        self.gui.finish_validation(0, "asm-s221-01", True, True, None)
        self.assertEqual(self.gui.active_task_id, "asm-s221-02")
        self.assertEqual(len(self.gui.batch_progress()["completed"]), 1)
        result, _ = self.gui.record_task_completion("asm-s221-01-fix1")
        self.assertEqual(result, "duplicate")
        self.assertEqual(len(self.gui.batch_progress()["completed"]), 1)

    def test_journal_recovers_next_task_after_config_commit_interrupted(self):
        result, _ = self.gui.record_task_completion("asm-s221-01")
        self.assertEqual(result, "saved")
        self.gui.progress = self.gui.load_progress()
        self.gui.restore_progress_task()
        self.assertEqual(self.gui.active_task_id, "asm-s221-02")
        self.assertEqual(self.gui.ent_subtask.get(), "02")

    def test_batch_total_blocks_new_capture_until_total_raised(self):
        self.gui.ent_total.value = "1"
        self.gui.finish_validation(0, "asm-s221-01", True, True, None)
        self.assertTrue(self.gui.batch_complete())
        self.gui.host_trigger_start()
        self.assertFalse(DeferredThread.created)
        self.gui.ent_total.value = "2"
        self.gui.host_trigger_start()
        self.assertTrue(self.gui.is_counting_down)

    def test_progress_disk_failure_prevents_advancement(self):
        with patch.object(os, "replace", side_effect=OSError("journal disk error")):
            self.gui.finish_validation(0, "asm-s221-01", True, True, None)
        self.assertEqual(self.gui.active_task_id, "asm-s221-01")
        self.assertEqual(self.gui.batch_progress().get("completed", []), [])
        self.assertFalse(self.gui.is_in_cooldown)

    def test_failed_update_status_is_visible(self):
        Path(self.temp.name, "update_status.json").write_text(json.dumps({"state": "failed", "message": "rollback done"}), encoding="utf-8")
        self.gui.show_update_status()
        self.messages.showwarning.assert_called_once_with("Trạng thái cập nhật", "rollback done")

    def test_worker_ui_callbacks_are_queued_and_not_run_after_shutdown(self):
        callback = Mock()
        self.gui._ui_thread_id = -1
        self.gui.post_ui(callback, 1)
        callback.assert_not_called()
        self.assertEqual(self.gui.ui_queue.qsize(), 1)
        self.gui.is_running = False
        self.gui.post_ui(callback, 2)
        self.assertEqual(self.gui.ui_queue.qsize(), 1)

    def test_refresh_host_keeps_owned_odx_alive(self):
        self.gui.host_open_odx = Mock()
        self.gui.run_clean_all()
        self.gui.host_open_odx.assert_called_once()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_refresh_player_keeps_odx_alive_and_retries_connection(self):
        self.gui.current_mode = "PLAYER"
        self.gui.player_connection_active = True
        self.gui.player_start_connection = Mock()
        self.gui.run_clean_all()
        self.gui.player_start_connection.assert_called_once()
        self.assertFalse(self.gui.player_connection_active)
        self.backend.cleanup_all_odx.assert_not_called()

    def test_close_during_join_cancels_worker_and_closes_owned_processes(self):
        self.gui.current_mode = "PLAYER"
        self.gui.is_connecting = True
        self.gui._connection_cancel_event = threading.Event()
        self.gui.on_close_window()
        self.assertFalse(self.gui.is_running)
        self.assertTrue(self.gui._connection_cancel_event.is_set())
        self.backend.cleanup_all_odx.assert_called_once_with(kill_gui=True, tool_closing=True)
        self.gui.root.destroy.assert_called_once()

    def test_close_during_ready_cancels_worker_and_closes_owned_processes(self):
        self.gui.current_mode = "PLAYER"
        self.gui._ready_inflight = True
        self.gui._ready_cancel_event = threading.Event()
        self.gui.on_close_window()
        self.assertTrue(self.gui._ready_cancel_event.is_set())
        self.backend.cleanup_all_odx.assert_called_once_with(kill_gui=True, tool_closing=True)

    def test_failed_window_save_does_not_prevent_final_process_cleanup(self):
        self.gui.window_preferences = types.SimpleNamespace(close=Mock(side_effect=OSError("window settings locked")))
        self.gui._connection_cancel_event = threading.Event()
        self.gui.shutdown_now()
        self.assertTrue(self.gui._connection_cancel_event.is_set())
        self.backend.cleanup_all_odx.assert_called_once_with(kill_gui=True, tool_closing=True)
        self.gui.root.destroy.assert_called_once()
        self.gui.shutdown_now()
        self.backend.cleanup_all_odx.assert_called_once()

    def test_actual_gui_executable_is_preferred_over_bootstrap(self):
        root = Path(self.temp.name)
        (root / "bin").mkdir()
        (root / "Odx Capture.exe").write_bytes(b"bootstrap fixture")
        native = root / "bin" / "odx-app.exe"
        native.write_bytes(b"native gui fixture")
        self.assertEqual(self.gui.odx_gui_executable(), str(native))

    def test_host_launches_actual_gui_process_before_registering_ownership(self):
        root = Path(self.temp.name)
        (root / "bin").mkdir()
        native = root / "bin" / "odx-app.exe"
        native.write_bytes(b"native gui fixture")
        self.backend.find_odx_window.return_value = None
        self.gui.host_open_odx()
        self.ns["subprocess"].Popen.assert_called_once_with([str(native)], cwd=self.temp.name)
        self.backend.register_managed_process.assert_called_once()
        self.backend.cleanup_all_odx.assert_not_called()

    def run_player_follow_ticks(self, updates):
        self.gui.current_mode = "PLAYER"
        changes = iter(updates)

        def tick(_):
            try:
                change = next(changes)
            except StopIteration:
                raise RuntimeError("end of follow fixture")
            change()

        with patch.object(time, "sleep", side_effect=tick):
            with self.assertRaisesRegex(RuntimeError, "end of follow fixture"):
                self.gui.player_follow_worker(0, "ABC123", "p1")

    def test_coalesced_completed_round_is_processed_once_without_false_pass(self):
        task = self.gui.active_task_id
        self.gui._shared_settings = {"task_id": task, "phase": "choices"}
        self.gui.follower.current_stopped = (task, "requested")
        self.gui.follower.last_round_result = (task, "all ok")
        self.backend.validate_and_inspect_capture = Mock(return_value=True)
        self.run_player_follow_ticks([lambda: None])
        self.assertEqual(len(DeferredThread.created), 1)
        self.assertEqual(DeferredThread.created[0].args, (0, task, 1, True))
        DeferredThread.created[0].run()
        self.assertFalse(self.gui.p_counting_down)
        self.assertEqual(self.gui._player_ready_after_round, (0, 1, task))
        self.backend.validate_and_inspect_capture.assert_not_called()
        self.assertNotIn("PASS", self.gui.lbl_p_status.options["text"])
        self.backend.cleanup_all_odx.assert_not_called()

    def test_countdown_cancel_without_record_start_can_ready_again(self):
        task = self.gui.active_task_id
        self.gui._shared_settings = {"task_id": task, "phase": "choices"}
        self.gui.follower.last_armed = task

        def cancel_before_start():
            self.gui.follower.last_armed = None
            self.gui.follower.last_round_result = (task, "cancelled")

        self.run_player_follow_ticks([cancel_before_start])
        self.assertEqual(len(DeferredThread.created), 1)
        DeferredThread.created[0].run()
        self.assertFalse(self.gui.p_counting_down)
        self.assertEqual(self.gui._player_ready_after_round, (0, 1, task))
        self.assertIn("ĐÃ HỦY", self.gui.lbl_p_status.options["text"])
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_local_kept_result_alone_does_not_count_as_room_ended(self):
        task = self.gui.active_task_id
        self.gui._shared_settings = {"task_id": task, "phase": "choices"}
        self.gui.follower.last_round_result = (task, "ok")
        self.run_player_follow_ticks([])
        self.assertFalse(DeferredThread.created)
        self.assertIsNone(getattr(self.gui, "_player_ready_after_round", None))

    def test_old_terminal_does_not_cancel_a_new_task_being_prepared(self):
        self.gui._shared_settings = {"task_id": "new-task-02", "phase": "countdown"}
        self.gui.follower.current_recording = "new-task-02"
        self.gui.follower.last_armed = "new-task-02"
        self.gui.follower.last_round_result = ("old-task-01", "all ok")
        self.run_player_follow_ticks([])
        self.assertTrue(self.gui.p_counting_down)
        self.assertFalse(DeferredThread.created)
        self.assertIsNone(getattr(self.gui, "_player_ready_after_round", None))

    def test_main_duplicate_instance_never_initializes_process_protection(self):
        main_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        instance = types.SimpleNamespace(acquire=Mock(return_value=False), release=Mock())
        root = types.SimpleNamespace(withdraw=Mock(), destroy=Mock())
        self.ns["tk"] = types.SimpleNamespace(Tk=Mock(return_value=root))
        self.backend.setup_process_protection = Mock()
        exec(compile(ast.Module(body=[main_node], type_ignores=[]), str(SOURCE), "exec"), self.ns)
        with patch.dict(sys.modules, {"odx_instance": types.SimpleNamespace(SingleInstance=lambda: instance)}):
            self.assertEqual(self.ns["main"](), 1)
        self.backend.setup_process_protection.assert_not_called()
        root.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
