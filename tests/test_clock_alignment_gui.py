"""Capture clocks and prepared Host stops without windows, input or networking."""
import queue
import time
import types
import unittest
from unittest.mock import Mock, patch

from odx_overlay import OdxOverlay
from odx_timing import format_recording_clock

if __package__:
    from . import test_gui as gui_fixture
else:
    import test_gui as gui_fixture


class ClockAlignmentGuiTests(unittest.TestCase):
    setUp = gui_fixture.GuiTests.setUp

    def recording(self):
        self.gui.is_recording = True
        self.gui.rec_start_time = 100
        self.gui.target_duration = 240
        self.gui._start_command_sent = True
        self.gui.follower.current_recording = self.gui.active_task_id
        self.gui.btn_host_emergency.config(state="normal")
        self.backend.stop_recording_verified.return_value = False

    def warm_job(self, *, ready=True, key=None):
        job = types.SimpleNamespace(claim=Mock(return_value=ready), cancel=Mock())
        self.gui._prepared_host_stop = job
        self.gui._prepared_host_stop_key = key or (self.gui._transition_token, self.gui.active_task_id)
        self.gui._prepared_host_stop_attempted = True
        return job

    def test_preparation_starts_at_round_beginning_once_without_stopping(self):
        self.recording()
        job = types.SimpleNamespace(claim=Mock(return_value=False), cancel=Mock())
        self.backend.prepare_recording_stop = Mock(return_value=job)
        for elapsed in (0, 1, 100, 232, 233, 239.999, 240):
            self.gui.prepare_host_stop_if_needed(elapsed)
        self.backend.prepare_recording_stop.assert_called_once()
        self.backend.stop_recording_verified.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertFalse(self.gui.is_stopping)
        self.assertIsNone(self.gui._pending_stop_action)
        self.assertEqual(self.gui.btn_host_emergency.options["state"], "normal")
        self.assertFalse(gui_fixture.DeferredThread.created)

    def test_preparation_never_starts_after_deadline(self):
        self.recording()
        self.backend.prepare_recording_stop = Mock()
        self.gui.prepare_host_stop_if_needed(240)
        self.gui.prepare_host_stop_if_needed(241)
        self.backend.prepare_recording_stop.assert_not_called()

    def test_preparation_guard_rejects_changed_connection_task_or_capture(self):
        self.recording()
        self.backend.prepare_recording_stop = Mock(return_value=Mock())
        self.gui.prepare_host_stop_if_needed(235)
        current = self.backend.prepare_recording_stop.call_args.kwargs["can_prepare"]
        self.assertTrue(current())
        for target, attribute, stale in (
            (self.gui, "is_running", False),
            (self.gui, "current_mode", "PLAYER"),
            (self.gui, "_transition_token", 1),
            (self.gui, "active_task_id", "other-task"),
            (self.gui, "is_recording", False),
            (self.gui, "is_stopping", True),
            (self.gui.follower, "current_recording", "other-task"),
        ):
            with self.subTest(attribute=attribute):
                original = getattr(target, attribute)
                setattr(target, attribute, stale)
                self.assertFalse(current())
                setattr(target, attribute, original)
        self.assertTrue(current())

    def test_claimed_job_is_taken_before_transition_and_passed_once_to_backend(self):
        self.recording()
        job = self.warm_job()
        def claim_before_transition():
            self.assertEqual(self.gui._transition_token, 0)
            self.assertFalse(self.gui.is_stopping)
            return True
        job.claim.side_effect = claim_before_transition
        self.gui.request_host_stop("validate")
        self.assertEqual(self.gui._transition_token, 1)
        self.assertIsNone(self.gui._prepared_host_stop)
        self.assertTrue(self.gui.is_stopping)
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        gui_fixture.DeferredThread.created[0].run()
        self.assertIs(self.backend.stop_recording_verified.call_args.kwargs["prepared"], job)
        self.backend.stop_recording_verified.assert_called_once()
        job.claim.assert_called_once()
        job.cancel.assert_called_once()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_unready_job_is_cancelled_and_uses_verified_cold_stop(self):
        self.recording()
        job = self.warm_job(ready=False)
        self.gui.request_host_stop("validate")
        job.cancel.assert_called_once()
        gui_fixture.DeferredThread.created[0].run()
        self.backend.stop_recording_verified.assert_called_once()
        self.assertNotIn("prepared", self.backend.stop_recording_verified.call_args.kwargs)
        job.cancel.assert_called_once()

    def test_job_for_another_task_or_transition_is_never_claimed(self):
        for key in ((0, "old-task"), (-1, "asm-s221-01")):
            with self.subTest(key=key):
                job = self.warm_job(key=key)
                self.assertIsNone(self.gui.take_prepared_host_stop(self.gui.active_task_id))
                job.claim.assert_not_called()
                job.cancel.assert_called_once()
                self.assertIsNone(self.gui._prepared_host_stop)

    def test_new_recording_cancels_previous_preparation(self):
        job = self.warm_job(key=(-1, self.gui.active_task_id))
        with patch.object(time, "monotonic", return_value=101):
            self.gui.begin_confirmed_recording(self.gui.active_task_id, 100)
        job.cancel.assert_called_once()
        self.assertTrue(self.gui.is_recording)
        self.assertIsNone(self.gui._prepared_host_stop)

    def test_reset_cancels_preparation_without_any_native_stop(self):
        job = self.warm_job()
        self.gui.reset_host_task()
        job.cancel.assert_called_once()
        self.backend.stop_recording_verified.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_shutdown_cancels_preparation_and_keeps_cleanup_scoped_to_tool_close(self):
        job = self.warm_job()
        self.gui.shutdown_now()
        job.cancel.assert_called_once()
        self.assertFalse(self.gui.is_running)
        self.backend.cleanup_all_odx.assert_called_once_with(kill_gui=True, tool_closing=True)

    def test_delayed_player_ui_uses_same_elapsed_snapshot_as_hud(self):
        self.gui.current_mode = "PLAYER"
        task, elapsed = self.gui.active_task_id, 4.999
        with patch.object(time, "monotonic", return_value=200):
            self.gui.player_clock_state(0, task, 100, True, elapsed_snapshot=elapsed)
        expected = format_recording_clock(elapsed, 240)
        self.assertEqual(self.gui.lbl_p_timer.options["text"], f"{task}: {expected}")
        self.assertEqual(expected, "00:04.9 / 04:00.0")
        overlay = OdxOverlay.__new__(OdxOverlay)
        overlay.cmd_queue = queue.Queue()
        overlay.update_recording(task, elapsed, 240, is_host=False)
        commands = []
        while not overlay.cmd_queue.empty():
            commands.append(overlay.cmd_queue.get_nowait())
        hud = next(arguments[0] for command, arguments in commands if command == "SHOW_HUD")
        self.assertIn(expected, hud)

    def test_host_timer_keeps_subsecond_precision_and_matches_hud_snapshot(self):
        self.recording()
        with patch.object(time, "monotonic", return_value=104.999):
            self.gui.update_live_timer()
        self.assertEqual(self.gui.lbl_timer.options["text"], "00:04.9 / 04:00.0")
        displayed_elapsed = self.gui.overlay.update_recording.call_args.args[1]
        self.assertAlmostEqual(displayed_elapsed, 4.999)
        self.assertEqual(format_recording_clock(displayed_elapsed, 240), self.gui.lbl_timer.options["text"])

    def test_final_clock_freezes_actual_stop_even_if_observed_late(self):
        self.recording()
        job = self.warm_job()
        task = self.gui.active_task_id
        self.gui.follower.current_stopped = (task, "round_end")
        self.gui.follower.current_stopped_at = 339.95
        with patch.object(time, "monotonic", return_value=500):
            self.assertAlmostEqual(self.gui.capture_elapsed(), 239.95)
            self.gui.finish_host_stop(0, task, True, "emergency")
        self.assertEqual(self.gui.lbl_timer.options["text"], "03:59.9 / 04:00.0")
        self.assertEqual(self.gui.rec_start_time, 0)
        job.cancel.assert_called_once()

    def test_late_stop_from_different_task_cannot_freeze_current_clock(self):
        self.recording()
        self.gui.follower.current_stopped = ("previous-task", "round_end")
        self.gui.follower.current_stopped_at = 150
        with patch.object(time, "monotonic", return_value=339.95):
            self.assertAlmostEqual(self.gui.capture_elapsed(), 239.95)

    def test_host_final_clock_reports_saved_video_duration_instead_of_request_time(self):
        self.gui.is_validating = True
        self.gui._stop_elapsed = 240.011
        self.gui.var_autoincrement.value = False
        self.gui.finish_validation(0, self.gui.active_task_id, True, True, None, 241.3)
        self.assertEqual(self.gui.lbl_timer.options["text"], "04:01.3 / 04:00.0")

    def test_player_final_clock_reports_its_saved_native_video_duration(self):
        self.gui.current_mode = "PLAYER"
        self.gui.is_validating = True
        self.gui._player_round_token = 1
        task = self.gui.active_task_id
        self.gui._player_validation_token = (0, task, 1)
        self.gui.auto_ready_player_next_round = Mock()
        self.gui.finish_player_validation(0, task, 1, True, duration_seconds=241.3)
        self.assertIn("04:01.3 / 04:00.0", self.gui.lbl_p_timer.options["text"])
        self.gui.auto_ready_player_next_round.assert_called_once()


if __name__ == "__main__":
    unittest.main()
