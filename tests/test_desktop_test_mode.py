"""Desktop synchronization mode keeps real capture gates and production progress separate.

Reuse the GUI state fixture without creating Tk windows, network connections,
native Odx processes, keyboard hooks or actual recordings.
"""
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

import odx_sync
if __package__:
    from . import test_gui as gui_fixture
else:
    import test_gui as gui_fixture


class Variable(gui_fixture.Widget):
    def set(self, value):
        self.value = value


class DesktopTestModeTests(unittest.TestCase):
    setUp = gui_fixture.GuiTests.setUp

    def enable_desktop_mode(self):
        self.gui.config["test_mode"] = True
        self.gui._room_test_mode = True
        self.gui._round_test_mode = True
        self.backend.is_game_running.return_value = False

    def player_settings_fixture(self, room_mode):
        self.gui.current_mode = "PLAYER"
        self.gui.config.update(player_id="p1", test_mode=room_mode)
        self.gui._room_test_mode = room_mode
        self.gui._sync_required = True
        key = ("PLAYER", "ABC123", "host", "")
        client = Mock(last_error=None, last_seen_at=gui_fixture.time.monotonic(),
                      current_revision=5, instance_id="host-instance")
        self.gui._sync_key, self.gui._sync_client = key, client
        return key, client

    def player_prejoin_fixture(self, test_mode=True, launch=False):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui._sync_required = True
        self.gui.owned_odx_window = Mock(return_value=123)
        self.gui.install_player_tracking = Mock()
        self.backend.fetch_room_status.return_value = {"host": "host", "members": []}
        settings = odx_sync.validate_settings({
            "test_mode": test_mode, "task_id": "host-task-01", "target_duration": 240})
        client = Mock(last_error=None)
        client.fetch.return_value = {"settings": settings, "revision": 5,
                                     "instance_id": "host-instance"}
        self.ns["PlayerSettingsClient"].return_value = client
        joining = types.SimpleNamespace(join=Mock(), success=True, joined=True, ready=False, error="")
        self.backend.player_auto_join_and_ready = Mock(return_value=joining)
        if launch:
            Path(self.temp.name, "Odx Capture.exe").write_bytes(b"fixture")
            self.backend.find_odx_window.return_value = None
        return client, joining

    def test_player_rejects_host_identity_before_any_native_mutation(self):
        self.player_prejoin_fixture()
        self.backend.fetch_room_status.return_value = {"host": "p1", "members": [{"player_id": "p1", "ready": True}]}
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.backend.set_player_info.assert_not_called()
        self.backend.player_auto_join_and_ready.assert_not_called()
        self.ns["subprocess"].Popen.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()
        self.assertFalse(self.gui.player_connection_active)
        self.assertIn("trùng với Host", self.gui.lbl_p_status.options["text"])

    def test_native_join_failure_keeps_odx_alive_and_releases_connect_button(self):
        self.player_prejoin_fixture()
        worker = self.backend.player_auto_join_and_ready.return_value
        worker.success = False
        worker.joined = False
        worker.error = "Không đọc được CodeBox trong hộp thoại Odx"
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.backend.cleanup_all_odx.assert_not_called()
        self.assertFalse(self.gui.player_connection_active)
        self.assertFalse(self.gui.is_connecting)
        self.assertEqual(self.gui.btn_p_connect.options["state"], "normal")
        self.assertIn("CodeBox", self.gui.lbl_p_status.options["text"])

    def test_joined_player_waits_for_settings_without_false_ready(self):
        self.player_prejoin_fixture()
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.assertTrue(self.gui.player_connection_active)
        self.assertIn("ĐÃ XÁC NHẬN VÀO PHÒNG", self.gui.lbl_p_status.options["text"])
        self.assertIn("CHỜ ĐỒNG BỘ", self.gui.lbl_p_status.options["text"])
        self.backend.cleanup_all_odx.assert_not_called()

    def test_shared_schema_accepts_only_boolean_test_mode(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.assertEqual(odx_sync.validate_settings({"test_mode": enabled}),
                                 {"test_mode": enabled})
        for invalid in (0, 1, None, "true", "false", [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                odx_sync.validate_settings({"test_mode": invalid})

    def test_test_mode_defaults_off_and_requires_accepted_checkbox_callback(self):
        self.assertFalse(self.gui.capture_test_mode())
        self.assertFalse(self.gui.read_shared_controls()["test_mode"])
        self.gui.var_test_mode = Variable(True)
        self.assertFalse(self.gui.read_shared_controls()["test_mode"])
        self.gui.on_test_mode_changed()
        self.assertTrue(self.gui.read_shared_controls()["test_mode"])
        self.gui.var_test_mode.set(False)
        self.assertTrue(self.gui.read_shared_controls()["test_mode"])
        self.gui.on_test_mode_changed()
        self.assertFalse(self.gui.read_shared_controls()["test_mode"])

    def test_host_snapshot_publishes_test_mode_using_actual_schema(self):
        self.enable_desktop_mode()
        snapshot = self.gui.host_settings_snapshot()
        self.assertIs(snapshot["test_mode"], True)
        self.assertEqual(odx_sync.validate_settings(snapshot), snapshot)

    def test_normal_mode_still_requires_game(self):
        self.gui._room_test_mode = False
        self.backend.is_game_running.return_value = False
        self.gui.host_trigger_start()
        self.assertFalse(gui_fixture.DeferredThread.created)
        self.assertFalse(self.gui.is_counting_down)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_confirmed_desktop_room_starts_without_game(self):
        self.enable_desktop_mode()
        self.gui.watch_start = Mock()
        self.gui.host_trigger_start()
        self.assertTrue(self.gui.is_counting_down)
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        gui_fixture.DeferredThread.created[0].run()
        self.backend.send_toggle_hotkey.assert_called_once_with(123)
        self.gui.watch_start.assert_called_once_with(1, "asm-s221-01")

    def test_completed_production_batch_does_not_prevent_separate_desktop_test(self):
        self.enable_desktop_mode()
        self.gui.ent_total.value = "1"
        self.gui.progress["batches"]["asm-s221-"] = {"completed": ["asm-s221-01"]}
        self.gui.host_trigger_start()
        self.assertTrue(self.gui.is_counting_down)
        self.assertEqual(self.gui.progress["batches"]["asm-s221-"]["completed"],
                         ["asm-s221-01"])

    def test_desktop_start_requires_native_room_mode_confirmation(self):
        self.enable_desktop_mode()
        for room_mode in (None, False):
            with self.subTest(room_mode=room_mode):
                self.gui._room_test_mode = room_mode
                self.backend.is_game_running.return_value = True
                self.gui.host_trigger_start()
                self.assertFalse(gui_fixture.DeferredThread.created)
                self.assertFalse(self.gui.is_counting_down)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_room_mode_change_during_task_injection_cancels_start(self):
        self.enable_desktop_mode()
        self.gui.watch_start = Mock()
        self.gui.host_trigger_start()
        self.backend.set_task_box.return_value.join.side_effect = (
            lambda **_: setattr(self.gui, "_room_test_mode", False))
        gui_fixture.DeferredThread.created[0].run()
        self.assertFalse(self.gui.is_counting_down)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.gui.watch_start.assert_not_called()

    def test_desktop_mode_keeps_existing_folder_guard(self):
        self.enable_desktop_mode()
        self.backend.is_task_already_recorded.return_value = (True, "existing-desktop-capture")
        self.gui.host_trigger_start()
        self.assertFalse(gui_fixture.DeferredThread.created)
        self.assertTrue(self.gui.frame_host_choices.visible)
        self.assertFalse(self.gui.is_counting_down)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_desktop_mode_keeps_configured_roster_and_ready_guards(self):
        self.enable_desktop_mode()
        self.gui.config["target_players"] = 2
        self.gui.tracker.latest_data["members"] = [{"player_id": "host", "ready": True}]
        self.gui.host_trigger_start()
        self.assertFalse(gui_fixture.DeferredThread.created)
        self.gui.tracker.latest_data["members"].append({"player_id": "p1", "ready": False})
        self.gui.host_trigger_start()
        self.assertFalse(gui_fixture.DeferredThread.created)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_desktop_mode_still_waits_for_fresh_player_configuration_ack(self):
        self.enable_desktop_mode()
        self.gui._sync_required = True
        self.gui.room_code = "ABC123"
        server = Mock(room_code="ABC123", host_id="host", port=45455)
        server.publish.return_value = 7
        server.applied_players.return_value = set()
        self.gui._sync_server = server
        self.gui.host_trigger_start()
        self.assertFalse(gui_fixture.DeferredThread.created)
        self.assertEqual(self.gui._pending_sync_start_task, "asm-s221-01")
        self.assertIs(server.publish.call_args.args[0]["test_mode"], True)
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_player_must_match_desktop_mode_before_acknowledging_host(self):
        for local_mode, host_mode in ((False, True), (True, False)):
            with self.subTest(local_mode=local_mode, host_mode=host_mode):
                key, client = self.player_settings_fixture(local_mode)
                self.gui.finish_settings_fetch(key, 0, client, {
                    "revision": 5, "instance_id": "host-instance", "settings": {
                        "task_id": "host-task-01", "target_duration": 240,
                        "test_mode": host_mode}})
                self.assertFalse(gui_fixture.DeferredThread.created)
                client.acknowledge.assert_not_called()
                self.assertFalse(self.gui.synced_settings_ready())
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_matching_player_applies_desktop_mode_before_acknowledging(self):
        key, client = self.player_settings_fixture(True)
        self.gui.finish_settings_fetch(key, 0, client, {
            "revision": 5, "instance_id": "host-instance", "settings": {
                "task_id": "host-task-01", "target_duration": 240, "test_mode": True}})
        self.assertTrue(self.gui.config["test_mode"])
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        self.assertTrue(self.gui.synced_settings_ready())
        gui_fixture.DeferredThread.created[0].run()
        client.acknowledge.assert_called_once_with(5, "p1")

    def test_player_fetches_host_mode_before_launch_configure_tracking_and_join(self):
        client, joining = self.player_prejoin_fixture(launch=True)
        order = []
        envelope = client.fetch.return_value
        client.fetch.side_effect = lambda: order.append("fetch") or envelope
        self.backend.prepare_capture_mode_launch.side_effect = (
            lambda enabled: order.append(("prepare", enabled)) or (True, ""))
        self.ns["subprocess"].Popen.side_effect = lambda *args, **kwargs: order.append("launch") or Mock()
        native_worker = self.backend.set_capture_test_mode.return_value
        self.backend.set_capture_test_mode.side_effect = (
            lambda enabled, **kwargs: order.append(("configure", enabled)) or native_worker)
        self.gui.install_player_tracking.side_effect = lambda *args: order.append("tracking")

        def join_native(*args, **kwargs):
            self.assertTrue(self.gui.capture_test_mode())
            self.assertIs(self.gui._room_test_mode, True)
            order.append("join")
            return joining

        self.backend.player_auto_join_and_ready.side_effect = join_native
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.assertEqual(order, ["fetch", ("prepare", True), "launch",
                                 ("configure", True), "tracking", "join"])
        self.ns["PlayerSettingsClient"].assert_called_once_with(
            "ABC123", "host", host_address=None, timeout=1)
        joining.join.assert_called_once_with()
        self.assertFalse(self.gui.is_connecting)

    def test_prejoin_host_mode_overrides_player_previous_desktop_choice(self):
        self.gui.config["test_mode"] = True
        self.gui.var_test_mode = Variable(True)
        self.player_prejoin_fixture(test_mode=False)
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.backend.set_capture_test_mode.assert_called_once()
        self.assertEqual(self.backend.set_capture_test_mode.call_args.args, (False,))
        self.assertIs(self.gui._room_test_mode, False)
        self.assertFalse(self.gui.capture_test_mode())
        self.assertFalse(self.gui.var_test_mode.get())
        self.backend.player_auto_join_and_ready.assert_called_once()

    def test_missing_prejoin_host_identity_blocks_source_changes_launch_and_join(self):
        self.player_prejoin_fixture(launch=True)
        self.backend.fetch_room_status.return_value = {"members": []}
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.ns["PlayerSettingsClient"].assert_not_called()
        self.backend.prepare_capture_mode_launch.assert_not_called()
        self.backend.set_capture_test_mode.assert_not_called()
        self.ns["subprocess"].Popen.assert_not_called()
        self.backend.player_auto_join_and_ready.assert_not_called()
        self.assertFalse(self.gui.player_connection_active)
        self.assertFalse(self.gui.is_connecting)

    def test_prejoin_host_settings_timeout_blocks_launch_and_native_join(self):
        client, _ = self.player_prejoin_fixture(launch=True)
        client.fetch.return_value = None
        self.gui.player_start_connection()
        self.ns["time"] = types.SimpleNamespace(
            monotonic=Mock(side_effect=[100, 101, 111]), sleep=Mock())
        gui_fixture.DeferredThread.created[0].run()
        self.backend.prepare_capture_mode_launch.assert_not_called()
        self.backend.set_capture_test_mode.assert_not_called()
        self.ns["subprocess"].Popen.assert_not_called()
        self.backend.player_auto_join_and_ready.assert_not_called()
        self.assertFalse(self.gui.player_connection_active)
        self.assertFalse(self.gui.is_connecting)

    def test_cancellation_during_prejoin_fetch_prevents_native_side_effects(self):
        client, _ = self.player_prejoin_fixture(launch=True)
        envelope = client.fetch.return_value

        def cancelled_fetch():
            self.gui._connection_cancel_event.set()
            return envelope

        client.fetch.side_effect = cancelled_fetch
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.backend.prepare_capture_mode_launch.assert_not_called()
        self.backend.set_capture_test_mode.assert_not_called()
        self.backend.set_player_info.assert_not_called()
        self.ns["subprocess"].Popen.assert_not_called()
        self.backend.player_auto_join_and_ready.assert_not_called()

    def test_player_prepare_launch_failure_prevents_popen_tracking_and_join(self):
        self.player_prejoin_fixture(launch=True)
        self.backend.prepare_capture_mode_launch.return_value = (False, "native settings unavailable")
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.backend.prepare_capture_mode_launch.assert_called_once_with(True)
        self.ns["subprocess"].Popen.assert_not_called()
        self.backend.set_capture_test_mode.assert_not_called()
        self.gui.install_player_tracking.assert_not_called()
        self.backend.player_auto_join_and_ready.assert_not_called()
        self.assertFalse(self.gui.player_connection_active)
        self.assertIn("native settings unavailable", self.gui.lbl_p_status.options["text"])

    def test_cancellation_during_prepare_launch_prevents_popen_and_join(self):
        self.player_prejoin_fixture(launch=True)

        def cancelled_prepare(_):
            self.gui._connection_cancel_event.set()
            return True, ""

        self.backend.prepare_capture_mode_launch.side_effect = cancelled_prepare
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.ns["subprocess"].Popen.assert_not_called()
        self.backend.set_capture_test_mode.assert_not_called()
        self.gui.install_player_tracking.assert_not_called()
        self.backend.player_auto_join_and_ready.assert_not_called()

    def test_player_native_source_failure_prevents_tracking_and_join(self):
        self.player_prejoin_fixture()
        self.backend.set_capture_test_mode.return_value.success = False
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.gui.install_player_tracking.assert_not_called()
        self.backend.player_auto_join_and_ready.assert_not_called()
        self.assertFalse(self.gui.player_connection_active)
        self.assertFalse(self.gui.is_connecting)

    def test_host_prepare_launch_failure_prevents_popen_and_room_creation(self):
        self.enable_desktop_mode()
        Path(self.temp.name, "Odx Capture.exe").write_bytes(b"fixture")
        self.backend.find_odx_window.return_value = None
        self.backend.prepare_capture_mode_launch.return_value = (False, "native settings unavailable")
        self.gui.host_open_odx()
        self.backend.prepare_capture_mode_launch.assert_called_once_with(True)
        self.ns["subprocess"].Popen.assert_not_called()
        self.backend.set_capture_test_mode.assert_not_called()
        self.backend.host_auto_create_room.assert_not_called()
        self.assertFalse(self.gui.odx_opened)
        self.assertFalse(self.gui.is_host_opening)

    def test_prejoin_does_not_reuse_previous_rooms_fresh_settings_to_enable_ready(self):
        self.player_prejoin_fixture()
        old_client = Mock(last_error=None, last_seen_at=gui_fixture.time.monotonic(),
                          current_revision=4, instance_id="previous-host")
        self.gui._sync_key = ("PLAYER", "OLD123", "previous-host", "")
        self.gui._sync_client = old_client
        self.gui._sync_applied_revision = 4
        self.gui._sync_applied_instance = "previous-host"
        self.gui._shared_settings = {"test_mode": True, "task_id": "old-task-01", "target_duration": 240}
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        can_ready = self.backend.player_auto_join_and_ready.call_args.kwargs["can_ready"]
        self.assertFalse(can_ready(), "Previous room settings cannot authorize a new room's Ready")

    def test_mode_toggle_is_rejected_during_recording_or_open_room(self):
        self.gui.var_test_mode = Variable(True)
        self.gui.config["test_mode"] = False
        for blocker, value in (("is_recording", True), ("room_code", "ABC123"),
                               ("player_connection_active", True)):
            with self.subTest(blocker=blocker):
                setattr(self.gui, blocker, value)
                self.gui.var_test_mode.set(True)
                self.gui.on_test_mode_changed()
                self.assertFalse(self.gui.capture_test_mode())
                self.assertFalse(self.gui.var_test_mode.get())
                setattr(self.gui, blocker, "" if blocker == "room_code" else False)

    def test_capture_mode_configuration_requires_successful_native_worker(self):
        worker = types.SimpleNamespace(success=True, error="", join=Mock(), is_alive=lambda: False)
        self.backend.set_capture_test_mode = Mock(return_value=worker)
        self.gui.configure_capture_mode_for_room(True, lambda: True)
        self.backend.set_capture_test_mode.assert_called_once()
        self.assertEqual(self.backend.set_capture_test_mode.call_args.args, (True,))
        worker.join.assert_called_once()

    def test_failed_native_capture_mode_configuration_cannot_authorize_room(self):
        worker = types.SimpleNamespace(success=False, error="desktop source unavailable", join=Mock(),
                                       is_alive=lambda: False)
        self.backend.set_capture_test_mode = Mock(return_value=worker)
        with self.assertRaisesRegex(RuntimeError, "desktop source unavailable"):
            self.gui.configure_capture_mode_for_room(True, lambda: True)

    def test_host_does_not_create_room_until_native_loading_worker_finishes(self):
        self.enable_desktop_mode()
        loading = types.SimpleNamespace(success=False, error="", attempts=0)
        order = []

        def join(**kwargs):
            loading.attempts += 1
            order.append("waiting")
            if loading.attempts == 3:
                loading.success = True

        loading.join = join
        loading.is_alive = lambda: not loading.success
        self.backend.set_capture_test_mode.return_value = loading
        self.backend.host_auto_create_room.side_effect = lambda **kwargs: order.append("create") or self.room_worker
        self.gui.start_host_room_tracking = Mock()
        self.gui.host_open_odx()
        gui_fixture.DeferredThread.created[0].run()
        self.assertEqual(order, ["waiting", "waiting", "waiting", "create"])
        self.assertTrue(self.gui.odx_opened)

    def test_cancel_during_native_loading_signals_worker_and_never_creates_room(self):
        self.enable_desktop_mode()
        loading = types.SimpleNamespace(success=False, error="", is_alive=lambda: True)
        loading.join = lambda **kwargs: self.gui.cancel_host_room_creation()
        self.backend.set_capture_test_mode.return_value = loading
        self.gui.host_open_odx()
        gui_fixture.DeferredThread.created[0].run()
        self.backend.host_auto_create_room.assert_not_called()
        self.assertFalse(self.gui.odx_opened)
        self.assertTrue(self.backend.set_capture_test_mode.call_args.kwargs["cancel_event"].is_set())

    def test_unfinished_native_capture_mode_worker_cannot_authorize_room(self):
        worker = types.SimpleNamespace(success=True, error="", join=Mock(), is_alive=lambda: True)
        self.backend.set_capture_test_mode = Mock(return_value=worker)
        with patch.object(gui_fixture.time, "monotonic", side_effect=(0, 0, 33)):
            with self.assertRaisesRegex(RuntimeError, "Hết thời gian"):
                self.gui.configure_capture_mode_for_room(True, lambda: True)
        self.assertTrue(self.backend.set_capture_test_mode.call_args.kwargs["cancel_event"].is_set())

    def test_cancelled_room_operation_does_not_change_native_capture_mode(self):
        self.backend.set_capture_test_mode = Mock()
        with self.assertRaises(RuntimeError):
            self.gui.configure_capture_mode_for_room(True, lambda: False)
        self.backend.set_capture_test_mode.assert_not_called()

    def test_desktop_success_advances_without_writing_production_progress(self):
        self.enable_desktop_mode()
        self.gui.record_task_completion = Mock()
        self.gui.finish_validation(0, "asm-s221-01", True, True, None)
        self.gui.record_task_completion.assert_not_called()
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})
        self.assertFalse(Path(self.gui.prog_file).exists())
        self.assertEqual(self.gui.active_task_id, "asm-s221-02")

    def test_desktop_success_uses_frozen_round_mode_after_config_changes(self):
        self.enable_desktop_mode()
        self.gui.config["test_mode"] = False
        self.gui.record_task_completion = Mock()
        self.gui.finish_validation(0, "asm-s221-01-fix1", True, True, None)
        self.gui.record_task_completion.assert_not_called()
        self.assertEqual(self.gui.active_task_id, "asm-s221-02")
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})

    def test_desktop_success_respects_disabled_autoincrement(self):
        self.enable_desktop_mode()
        self.gui.var_autoincrement.value = False
        self.gui.finish_validation(0, "asm-s221-01", True, True, None)
        self.assertEqual(self.gui.active_task_id, "asm-s221-01")
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})

    def test_desktop_failure_needs_local_audit_and_all_ok_without_error(self):
        self.enable_desktop_mode()
        self.gui.record_task_completion = Mock()
        for local_ok, all_ok, error in ((False, True, None), (True, False, None),
                                        (True, True, "capture failed")):
            with self.subTest(local_ok=local_ok, all_ok=all_ok, error=error):
                self.gui.finish_validation(0, "asm-s221-01", local_ok, all_ok, error)
                self.assertEqual(self.gui.active_task_id, "asm-s221-01")
                self.assertFalse(self.gui.is_in_cooldown)
        self.gui.record_task_completion.assert_not_called()
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})


if __name__ == "__main__":
    unittest.main()
