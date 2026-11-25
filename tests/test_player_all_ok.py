"""Ready needs both the stable local audit and the exact native round outcome."""
import types
import unittest
from unittest.mock import Mock

if __package__:
    from . import test_gui as gui_fixture
else:
    import test_gui as gui_fixture


class PlayerAllOkTests(unittest.TestCase):
    setUp = gui_fixture.GuiTests.setUp

    def player(self, local_pass=True):
        task = "round-01"
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui.player_connection_active = True
        self.gui._player_round_token = 1
        self.gui._player_validation_token = (0, task, 1)
        self.gui._player_validation_failed = False
        self.gui._player_failed_audit = None
        self.gui._player_ready_after_round = None
        self.gui._shared_settings = dict(task_id=task, phase="choices")
        self.gui.is_validating = True
        self.gui.auto_ready_player_next_round = Mock()
        self.backend.validate_finalized_capture = Mock(return_value=types.SimpleNamespace(
            passed=local_pass, error="invalid frames" if not local_pass else "",
            cancelled=False, duration_seconds=None))
        self.clock = 0
        self.on_sleep = None
        def sleep(seconds):
            self.clock += seconds
            if self.on_sleep is not None:
                self.on_sleep()
        self.ns["time"] = types.SimpleNamespace(monotonic=lambda: self.clock, sleep=sleep)
        return task

    def test_local_pass_waits_for_full_round_all_ok_then_readies(self):
        task = self.player()
        self.gui.follower.last_round_result = (task, "ok")
        observed = []
        def rendered():
            observed.append(self.gui._player_ready_after_round)
            if self.clock >= 0.4:
                self.gui.follower.last_round_result = (task, "all ok")
        self.on_sleep = rendered
        self.gui.post_player_round(0, task, 1)
        self.assertEqual(observed, [None, None])
        self.assertEqual(self.gui._player_ready_after_round, (0, 1, task))
        self.gui.auto_ready_player_next_round.assert_called_once()
        self.assertIn("ODX ALL OK", self.gui.lbl_p_status.options["text"])
        self.backend.cleanup_all_odx.assert_not_called()

    def test_another_tasks_all_ok_times_out_without_busy_network_loop(self):
        task = self.player()
        self.gui.follower.last_round_result = ("another-task", "all ok")
        self.gui.post_player_round(0, task, 1)
        self.assertTrue(self.gui._player_validation_failed)
        self.assertIn("120 giây", self.gui.lbl_p_status.options["text"])
        self.assertIn("ALL OK", self.gui.lbl_p_status.options["text"])
        self.assertLess(self.gui.follower.check_new_lines.call_count, 605)
        self.backend.fetch_room_status.assert_not_called()
        self.gui.auto_ready_player_next_round.assert_not_called()

    def test_native_failure_blocks_local_pass_and_cancellation_without_false_pass(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                task = self.player()
                detail = "RECORD AGAIN · qhao: failed: callback readback timed out"
                self.gui.follower.last_round_result = (task, detail)
                self.gui.post_player_round(0, task, 1, cancelled=cancelled)
                self.assertTrue(self.gui._player_validation_failed)
                self.assertIsNone(self.gui._player_ready_after_round)
                self.assertIn(detail, self.gui.lbl_p_status.options["text"])
                self.gui.auto_ready_player_next_round.assert_not_called()
                if cancelled:
                    self.backend.validate_finalized_capture.assert_not_called()

    def test_all_ok_does_not_override_local_failure(self):
        task = self.player(local_pass=False)
        self.gui.follower.last_round_result = (task, "all ok")
        self.gui.post_player_round(0, task, 1)
        self.assertTrue(self.gui._player_validation_failed)
        self.assertIn("invalid frames", self.gui.lbl_p_status.options["text"])
        self.gui.auto_ready_player_next_round.assert_not_called()

    def test_current_member_failure_blocks_even_when_full_round_all_ok(self):
        task = self.player()
        self.gui.follower.last_round_result = (task, "all ok")
        self.gui.tracker.latest_data["members"][1].update(name="qhao", problem="capture readback failed")
        self.gui.post_player_round(0, task, 1)
        self.assertTrue(self.gui._player_validation_failed)
        self.assertIn("qhao", self.gui.lbl_p_status.options["text"])
        self.assertIn("capture readback failed", self.gui.lbl_p_status.options["text"])
        self.gui.auto_ready_player_next_round.assert_not_called()

    def test_generation_change_while_waiting_discards_old_ready(self):
        task = self.player()
        def reconnect():
            self.gui._player_connection_generation += 1
        self.on_sleep = reconnect
        self.gui.post_player_round(0, task, 1)
        self.assertIsNone(self.gui._player_ready_after_round)
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.assertTrue(self.gui.is_validating, "The stale worker must not mutate the new UI state")

    def test_late_error_between_worker_and_ui_callback_is_rechecked(self):
        task = self.player()
        self.gui.follower.last_round_result = (task, "all ok")
        self.gui._ui_thread_id = -1
        self.gui.post_player_round(0, task, 1)
        callback, args, kwargs = self.gui.ui_queue.get_nowait()
        self.gui.follower.last_round_result = (task, "RECORD AGAIN · qhao: not done")
        callback(*args, **kwargs)
        self.assertTrue(self.gui._player_validation_failed)
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.assertIn("qhao", self.gui.lbl_p_status.options["text"])

    def test_cancelled_round_requires_native_confirmation_and_never_advertises_pass(self):
        task = self.player()
        self.gui.follower.last_round_result = (task, "ok")
        def cancellation_arrives():
            self.gui.follower.last_round_result = (task, "cancelled")
        self.on_sleep = cancellation_arrives
        self.gui.post_player_round(0, task, 1, cancelled=True)
        self.assertGreater(self.clock, 0)
        self.assertEqual(self.gui._player_ready_after_round, (0, 1, task))
        self.assertIn("ĐÃ HỦY", self.gui.lbl_p_status.options["text"])
        self.assertNotIn("PASS", self.gui.lbl_p_status.options["text"])
        self.backend.validate_finalized_capture.assert_not_called()

    def test_new_unused_fix_recovers_without_changing_failed_round(self):
        task = self.player()
        self.gui.follower.last_round_result = (task, "RECORD AGAIN · qhao: failed")
        self.gui.post_player_round(0, task, 1)
        self.assertTrue(self.gui._player_validation_failed)
        settings = dict(task_id=task + "-fix1", phase="idle")
        self.assertTrue(self.gui.consider_player_round_recovery(settings))
        self.assertFalse(self.gui._player_validation_failed)
        self.assertIsNone(self.gui._player_ready_after_round)
        self.assertEqual(self.gui.player_round_outcome(task)[0], "failed")
        self.assertEqual(self.gui.player_blocking_room_error(settings["task_id"]), "")
        self.backend.cleanup_all_odx.assert_not_called()

    def test_recovery_refuses_same_existing_or_busy_task(self):
        task = self.player()
        self.gui.follower.last_round_result = (task, "RECORD AGAIN · qhao: failed")
        self.gui.post_player_round(0, task, 1)
        self.assertFalse(self.gui.consider_player_round_recovery(dict(task_id=task, phase="choices")))
        self.backend.is_task_already_recorded.return_value = (True, "capture")
        settings = dict(task_id=task + "-fix1", phase="idle")
        self.assertFalse(self.gui.consider_player_round_recovery(settings))
        self.backend.is_task_already_recorded.return_value = (False, None)
        self.gui.follower.last_armed = settings["task_id"]
        self.assertFalse(self.gui.consider_player_round_recovery(settings))
        self.assertTrue(self.gui._player_validation_failed)

    def test_room_errors_show_exact_details_deduplicate_and_clear_current_notice(self):
        self.gui.current_mode = "PLAYER"
        self.gui.p_slot_labels = [gui_fixture.Widget() for _ in range(4)]
        self.gui.overlay.show_room_error = Mock()
        self.gui.overlay.clear_room_error = Mock()
        member = self.gui.tracker.latest_data["members"][1]
        member.update(name="qhao", ready=True)
        error = dict(task_id="round-01", player_id="p1", name="qhao", message="failed: callback readback >2 s; last 398 frames frozen >3 s")
        self.gui.tracker.latest_data["_round_errors"] = [error]
        members = self.gui.tracker.latest_data["members"]
        self.gui.update_player_roster(0, "host", members)
        self.gui.update_player_roster(0, "host", members)
        self.gui.overlay.show_room_error.assert_called_once()
        self.assertEqual(self.gui.log.call_count, 1)
        self.assertIn(error["message"], self.gui.lbl_p_status.options["text"])
        self.assertIn("lỗi vòng trước", self.gui.p_slot_labels[1].options["text"])
        self.assertEqual(self.gui.p_slot_labels[1].options["fg"], self.ns["ModernTheme"].ACCENT_RED)
        self.gui.tracker.latest_data["_round_errors"] = []
        self.gui.update_player_roster(0, "host", members)
        self.gui.overlay.clear_room_error.assert_called_once()
        self.assertNotIn("lỗi", self.gui.p_slot_labels[1].options["text"])
        self.messages.showerror.assert_not_called()

    def test_queued_ready_cannot_run_after_new_member_problem(self):
        self.gui.current_mode = "PLAYER"
        self.gui.player_connection_active = True
        self.gui.synced_settings_ready = Mock(return_value=True)
        self.gui.tracker.latest_data["members"][1]["problem"] = "capture stopped unexpectedly"
        self.assertFalse(self.gui.player_ready_request_allowed(0, 0, "p1", 0))
        self.gui.auto_ready_player_next_round()
        self.assertFalse(gui_fixture.DeferredThread.created)

    def test_host_roster_shows_error_during_recording_without_modal_or_shutdown(self):
        self.gui.slot_labels = [gui_fixture.Widget() for _ in range(4)]
        self.gui.is_recording = True
        self.gui.overlay.show_room_error = Mock()
        data = dict(host="host", members=[dict(player_id="host", name="Host", ready=True),
                    dict(player_id="p1", name="qhao", ready=True, problem="capture callback readback failed")])
        tracker = types.SimpleNamespace(latest_data=data, start=Mock(), stop=Mock())
        self.backend.RoomTracker = Mock(return_value=tracker)
        self.gui.start_host_room_tracking("ABC123")
        callback = self.backend.RoomTracker.call_args.kwargs["on_change_callback"]
        callback("ABC123", "host", data["members"])
        self.assertIn("qhao", self.gui.lbl_host_status.options["text"])
        self.assertIn("readback failed", self.gui.lbl_host_status.options["text"])
        self.assertIn("LỖI", self.gui.slot_labels[1].options["text"])
        self.gui.overlay.show_room_error.assert_called_once()
        self.assertIs(self.backend.RoomTracker.call_args.kwargs["follower"], self.gui.follower)
        self.messages.showerror.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
