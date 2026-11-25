"""A finished round may re-arm Player readiness without authorizing a retake.

The GUI fixture executes no Tk, Odx, network, keyboard or process actions.
Native Ready is represented by a worker result and a captured safety callback.
"""
import time
import types
import unittest
from unittest.mock import Mock

if __package__:
    from . import test_gui as gui_fixture
else:
    import test_gui as gui_fixture


class PostRoundReadyTests(unittest.TestCase):
    setUp = gui_fixture.GuiTests.setUp

    def connected_player(self, phase="choices", task="round-01", exists=True):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui.player_connection_active = True
        self.gui.room_code = "ABC123"
        self.gui.config.update(player_id="p1", test_mode=False)
        self.gui._room_test_mode = False
        self.gui._sync_required = True
        self.gui._player_round_token = 1
        self.gui._player_ready_after_round = None
        key = ("PLAYER", "ABC123", "host", "")
        client = Mock(last_error=None, last_seen_at=time.monotonic(),
                      current_revision=5, instance_id="host-instance")
        self.gui._sync_key, self.gui._sync_client = key, client
        self.gui._sync_applied_revision = 5
        self.gui._sync_applied_instance = "host-instance"
        self.gui._shared_settings = {
            "task_id": task, "target_duration": 240, "phase": phase,
            "test_mode": False,
        }
        self.backend.is_task_already_recorded.return_value = (exists, "existing-capture" if exists else None)
        self.ready_worker = types.SimpleNamespace(
            join=Mock(), success=True, status="confirmed", error="")
        self.backend.player_ensure_ready_in_room = Mock(return_value=self.ready_worker)
        return key, client

    def completed_round(self, *, ok=True, cancelled=False, task="round-01"):
        self.gui.is_validating = True
        self.gui._player_validation_token = (0, task, 1)
        self.gui.follower.last_round_result = (task, "cancelled" if cancelled else "all ok")
        self.gui.finish_player_validation(0, task, 1, ok, cancelled)

    def run_pending_workers(self):
        # Tk's after(0) callbacks run after the settings callback returns.
        # Keep that boundary rather than re-entering Ready while applying ACKs.
        callbacks = list(self.gui.root.after.call_args_list)
        self.gui.root.after.reset_mock()
        for call in callbacks:
            if len(call.args) >= 2 and call.args[0] == 0:
                call.args[1](*call.args[2:])
        for worker in list(gui_fixture.DeferredThread.created):
            worker.run()
        gui_fixture.DeferredThread.created.clear()

    def test_early_stop_readies_player_while_host_is_choosing_existing_task(self):
        self.connected_player()
        self.completed_round(cancelled=True)
        self.assertEqual(self.gui._player_ready_after_round, (0, 1, "round-01"))
        self.assertFalse(self.gui.synced_settings_ready(), "The old task stays reserved for start authorization")
        self.assertTrue(self.gui.synced_settings_ready(allow_finished_task=True))
        self.run_pending_workers()
        self.backend.player_ensure_ready_in_room.assert_called_once()
        self.assertTrue(self.backend.player_ensure_ready_in_room.call_args.kwargs["can_ready"]())
        self.assertIn("SẴN SÀNG", self.gui.lbl_p_status.options["text"])
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_full_pass_readies_same_task_when_host_keeps_autoincrement_off(self):
        self.connected_player(phase="idle")
        self.gui._shared_settings["auto_increment"] = False
        self.completed_round()
        self.assertFalse(self.gui.synced_settings_ready())
        self.assertTrue(self.gui.player_ready_request_allowed(0, 1, "p1", self.gui._ready_operation_token))
        self.run_pending_workers()
        self.backend.player_ensure_ready_in_room.assert_called_once()
        self.assertFalse(self.gui._player_validation_failed)

    def test_failed_local_audit_does_not_allow_ready_for_terminal_task(self):
        self.connected_player()
        self.gui._player_ready_after_round = (0, 1, "round-01")
        self.completed_round(ok=False)
        self.gui.auto_ready_player_next_round()
        self.run_pending_workers()
        self.assertTrue(self.gui._player_validation_failed)
        self.assertIsNone(self.gui._player_ready_after_round)
        self.backend.player_ensure_ready_in_room.assert_not_called()

    def test_completed_task_proof_does_not_adopt_an_unrelated_existing_task(self):
        self.connected_player(task="new-task-02")
        self.gui._player_ready_after_round = (0, 1, "round-01")
        self.assertFalse(self.gui.synced_settings_ready(allow_finished_task=True))
        self.assertFalse(self.gui.player_ready_request_allowed(0, 1, "p1", 0))

    def test_stale_connection_or_round_proof_cannot_bypass_existing_task_check(self):
        self.connected_player()
        for proof in ((-1, 1, "round-01"), (0, 0, "round-01"), (0, 2, "round-01")):
            with self.subTest(proof=proof):
                self.gui._player_ready_after_round = proof
                self.assertFalse(self.gui.synced_settings_ready(allow_finished_task=True))
                self.assertFalse(self.gui.player_ready_request_allowed(0, 1, "p1", 0))

    def test_old_validation_callback_cannot_create_finished_task_proof(self):
        self.connected_player()
        self.gui._player_round_token = 2
        self.gui._player_validation_token = (0, "new-task-02", 2)
        self.gui.is_validating = True
        self.gui.finish_player_validation(0, "round-01", 1, True)
        self.assertIsNone(self.gui._player_ready_after_round)
        self.assertTrue(self.gui.is_validating)
        self.backend.player_ensure_ready_in_room.assert_not_called()

    def test_finished_task_proof_still_requires_recent_applied_host_settings(self):
        _, client = self.connected_player()
        self.gui._player_ready_after_round = (0, 1, "round-01")
        client.last_seen_at = time.monotonic() - 10
        self.assertFalse(self.gui.synced_settings_ready(allow_finished_task=True))
        client.last_seen_at = time.monotonic()
        self.gui._sync_applied_revision = 4
        self.assertFalse(self.gui.synced_settings_ready(allow_finished_task=True))
        self.gui._sync_applied_revision = 5
        self.gui._sync_applied_instance = "previous-host"
        self.assertFalse(self.gui.synced_settings_ready(allow_finished_task=True))

    def test_finished_task_proof_does_not_bypass_test_mode_or_identity_guards(self):
        self.connected_player()
        self.gui._player_ready_after_round = (0, 1, "round-01")
        self.gui._room_test_mode = True
        self.assertFalse(self.gui.synced_settings_ready(allow_finished_task=True))
        self.gui._room_test_mode = False
        self.gui.config["player_id"] = "another-player"
        self.assertFalse(self.gui.player_ready_request_allowed(0, 1, "p1", 0))

    def test_finished_task_never_readies_during_capture_armed_or_validation(self):
        self.connected_player()
        self.gui._player_ready_after_round = (0, 1, "round-01")
        for flag in ("is_player_recording", "p_counting_down", "is_validating", "_player_validation_failed"):
            with self.subTest(flag=flag):
                setattr(self.gui, flag, True)
                self.assertFalse(self.gui.player_ready_request_allowed(0, 1, "p1", 0))
                setattr(self.gui, flag, False)
        self.gui.follower.last_armed = "new-task-02"
        self.assertFalse(self.gui.player_ready_request_allowed(0, 1, "p1", 0))
        self.gui.follower.last_armed = None
        self.gui.follower.current_recording = "new-task-02"
        self.assertFalse(self.gui.player_ready_request_allowed(0, 1, "p1", 0))

    def test_player_does_not_touch_unowned_window_after_terminal_round(self):
        self.connected_player()
        self.backend.is_managed_odx_window.return_value = False
        self.completed_round(cancelled=True)
        self.run_pending_workers()
        self.backend.player_ensure_ready_in_room.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_room_finish_wait_is_retried_after_current_session_clears(self):
        self.connected_player()
        self.gui.tracker.latest_data["current_session"] = "ending-round"
        self.completed_round(cancelled=True)
        self.run_pending_workers()
        self.backend.player_ensure_ready_in_room.assert_not_called()
        self.gui.tracker.latest_data["current_session"] = None
        self.gui.auto_ready_player_next_round()
        self.run_pending_workers()
        self.backend.player_ensure_ready_in_room.assert_called_once()

    def test_ready_after_stop_never_acknowledges_an_existing_startable_task(self):
        key, client = self.connected_player()
        self.gui._player_ready_after_round = (0, 1, "round-01")
        for phase in ("idle", "preparing", "countdown"):
            with self.subTest(phase=phase):
                settings = dict(self.gui._shared_settings, phase=phase,
                                start_request_id="1" * 32)
                self.gui.finish_settings_fetch(key, 0, client, {
                    "revision": 5, "instance_id": "host-instance", "settings": settings})
                self.run_pending_workers()
                client.acknowledge.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_choices_can_ack_metadata_without_granting_retake_permission(self):
        key, client = self.connected_player()
        self.gui._player_ready_after_round = (0, 1, "round-01")
        self.gui.finish_settings_fetch(key, 0, client, {
            "revision": 5, "instance_id": "host-instance",
            "settings": dict(self.gui._shared_settings)})
        self.run_pending_workers()
        client.acknowledge.assert_called_once_with(5, "p1")
        self.assertFalse(self.gui.synced_settings_ready())

    def test_new_unused_task_can_be_applied_acknowledged_and_readied(self):
        key, client = self.connected_player(task="new-task-02", phase="idle", exists=False)
        self.gui._player_ready_after_round = (0, 1, "round-01")
        self.gui.finish_settings_fetch(key, 0, client, {
            "revision": 5, "instance_id": "host-instance",
            "settings": dict(self.gui._shared_settings)})
        self.run_pending_workers()
        client.acknowledge.assert_called_once_with(5, "p1")
        self.assertTrue(self.gui.synced_settings_ready())
        self.backend.player_ensure_ready_in_room.assert_called_once()

    def test_queued_ready_rechecks_capture_state_before_native_invocation(self):
        key, client = self.connected_player(task="new-task-02", phase="idle", exists=False)
        self.gui.finish_settings_fetch(key, 0, client, {
            "revision": 5, "instance_id": "host-instance",
            "settings": dict(self.gui._shared_settings)})
        self.backend.player_ensure_ready_in_room.assert_not_called()
        self.assertTrue(any(call.args[0] == 0 for call in self.gui.root.after.call_args_list))
        self.gui.player_countdown_state(0, "new-task-02")
        self.run_pending_workers()
        self.backend.player_ensure_ready_in_room.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_post_round_ready_does_not_bypass_hosts_fresh_start_acknowledgments(self):
        self.connected_player()
        self.completed_round(cancelled=True)
        self.run_pending_workers()
        self.gui.current_mode = self.gui.view_mode = "HOST"
        self.backend.is_task_already_recorded.return_value = (False, None)
        server = Mock(room_code="ABC123", host_id="host", port=45455)
        server.publish.return_value = 7
        server.applied_players.return_value = set()
        self.gui._sync_server = server
        self.gui._sync_key = ("HOST", "ABC123", "host", "")
        self.gui.host_trigger_start()
        self.assertEqual(self.gui._pending_sync_start_task, self.gui.active_task_id)
        self.assertFalse(self.gui.is_counting_down)
        self.backend.send_toggle_hotkey.assert_not_called()


if __name__ == "__main__":
    unittest.main()
