"""Host duration boundaries and stop confirmation without native input.

Only the GUI state fixture and mocked backend are used. No Odx window, room,
recording, foreground change or keyboard input is created by these tests.
"""
import time
import unittest
from unittest.mock import Mock, patch

if __package__:
    from . import test_gui as gui_fixture
else:
    import test_gui as gui_fixture


class HostAutomaticStopTests(unittest.TestCase):
    setUp = gui_fixture.GuiTests.setUp

    def recording(self, target=239):
        self.gui.is_recording = True
        self.gui.rec_start_time = 100
        self.gui.target_duration = target
        self.gui.follower.current_recording = self.gui.active_task_id
        self.gui._start_command_sent = True

    def tick(self, native_time):
        with patch.object(time, "monotonic", return_value=native_time):
            self.gui.update_live_timer()

    def test_native_duration_deadline_dispatches_one_stop_across_later_ticks(self):
        self.recording()
        self.tick(338.99)
        self.assertFalse(gui_fixture.DeferredThread.created)
        self.assertFalse(self.gui.is_stopping)
        self.tick(339)
        self.assertTrue(self.gui.is_stopping)
        self.assertEqual(self.gui._pending_stop_action, "validate")
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        self.tick(340)
        self.tick(357)
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        self.backend.stop_recording_verified.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_target_stop_uses_owned_window_guard_without_a_gui_global_toggle(self):
        self.recording()
        self.backend.stop_recording_verified.return_value = False
        self.tick(339)
        gui_fixture.DeferredThread.created[0].run()
        call = self.backend.stop_recording_verified.call_args
        self.assertEqual(call.args, (self.gui.follower, self.gui.active_task_id))
        self.assertEqual(call.kwargs["hwnd"], 123)
        self.assertTrue(call.kwargs["can_stop"]())
        self.backend.is_managed_odx_window.return_value = False
        self.assertFalse(call.kwargs["can_stop"]())
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_unconfirmed_stop_keeps_recording_and_does_not_mark_completion(self):
        self.recording()
        self.backend.stop_recording_verified.return_value = False
        self.tick(339)
        gui_fixture.DeferredThread.created[0].run()
        self.assertTrue(self.gui.is_recording)
        self.assertTrue(self.gui.capture_state_unknown)
        self.assertTrue(self.gui._stop_confirmation_failed)
        self.assertFalse(self.gui.is_validating)
        self.assertEqual(self.gui._pending_stop_action, "validate")
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})
        self.assertIsNone(self.gui.abort_state)
        self.tick(360)
        self.gui.host_trigger_emergency_stop()
        self.backend.stop_recording_verified.assert_called_once()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_late_native_completion_preserves_automatic_validation_without_second_stop(self):
        self.recording()
        self.backend.stop_recording_verified.return_value = False
        self.tick(339)
        gui_fixture.DeferredThread.created[0].run()
        task = self.gui.active_task_id
        self.gui.follower.current_recording = None
        self.gui.follower.current_stopped = (task, "round_end")
        self.gui.follower.current_stopped_at = 339
        self.gui.follower.last_round_result = (task, "all ok")
        self.tick(357)
        self.assertFalse(self.gui.is_recording)
        self.assertFalse(self.gui.capture_state_unknown)
        self.assertFalse(self.gui._stop_confirmation_failed)
        self.assertTrue(self.gui.is_validating)
        self.assertIsNone(self.gui.abort_state)
        self.assertIsNone(self.gui._pending_stop_action)
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})
        self.backend.stop_recording_verified.assert_called_once()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_other_task_activity_cannot_cause_automatic_stop_for_previous_task(self):
        self.recording()
        self.gui.follower.current_recording = "different-task-02"
        self.tick(360)
        self.assertFalse(gui_fixture.DeferredThread.created)
        self.assertFalse(self.gui.is_stopping)
        self.backend.stop_recording_verified.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_local_kept_result_does_not_mean_room_finished(self):
        self.recording()
        task = self.gui.active_task_id
        self.gui.follower.current_recording = None
        self.gui.follower.last_round_result = (task, "ok")
        self.assertFalse(self.gui.has_round_finished(task))
        self.assertEqual(self.gui.progress, {"schema": 1, "batches": {}})
        self.backend.cleanup_all_odx.assert_not_called()

    def test_local_stop_without_room_final_requests_end_for_remaining_players_once(self):
        self.recording()
        task = self.gui.active_task_id
        self.gui.follower.current_recording = None
        self.gui.follower.current_stopped = (task, "round_end")
        self.gui.follower.current_stopped_at = 339
        self.gui.follower.last_round_result = (task, "ok")
        self.tick(357)
        self.assertTrue(self.gui.is_stopping)
        self.assertEqual(self.gui._pending_stop_action, "validate")
        self.assertEqual(self.gui._pending_stop_task, task)
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        self.assertFalse(self.gui.is_validating)
        self.tick(360)
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_stop_progress_only_updates_current_pending_task_transition(self):
        self.recording()
        self.tick(339)

        def report_progress(follower, task, *, on_status, **kwargs):
            on_status("Đã gửi kết thúc đúng task", "WAIT")
            self.assertEqual(self.gui.lbl_host_status.options["text"], "Đã gửi kết thúc đúng task")
            self.gui._transition_token += 1
            on_status("stale transition must be ignored", "WAIT")
            self.assertEqual(self.gui.lbl_host_status.options["text"], "Đã gửi kết thúc đúng task")
            self.gui._transition_token -= 1
            self.gui._pending_stop_task = "different-task-02"
            on_status("different pending task must be ignored", "WAIT")
            self.assertEqual(self.gui.lbl_host_status.options["text"], "Đã gửi kết thúc đúng task")
            self.gui._pending_stop_task = task
            self.gui.is_stopping = False
            on_status("completed stop must be ignored", "WAIT")
            self.assertEqual(self.gui.lbl_host_status.options["text"], "Đã gửi kết thúc đúng task")
            self.gui.is_stopping = True
            return False

        self.backend.stop_recording_verified.side_effect = report_progress
        gui_fixture.DeferredThread.created[0].run()
        self.assertTrue(self.gui.capture_state_unknown)
        self.assertFalse(self.gui.is_validating)
        self.backend.cleanup_all_odx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
