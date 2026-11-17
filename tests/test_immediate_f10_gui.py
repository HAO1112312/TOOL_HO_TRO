"""F10 stop dispatch precedes Tk work and belongs to the exact key-down round."""
import time
import types
import unittest
from unittest.mock import Mock, patch

from odx_hotkeys import route_physical_hotkey

if __package__:
    from . import test_gui as gui_fixture
else:
    import test_gui as gui_fixture


class ImmediateF10GuiTests(unittest.TestCase):
    setUp = gui_fixture.GuiTests.setUp

    def recording(self, *, target=240):
        self.gui.is_recording = True
        self.gui.rec_start_time = 100
        self.gui.target_duration = target
        self.gui._start_command_sent = True
        self.gui.follower.current_recording = self.gui.active_task_id
        self.gui._ui_thread_id = -1  # Simulate hook/worker calls while Tk is busy.
        self.backend.stop_recording_verified.return_value = False

    def run_workers(self):
        index = 0
        while index < len(gui_fixture.DeferredThread.created):
            worker = gui_fixture.DeferredThread.created[index]
            index += 1
            worker.run()

    def keydown(self, *, at=110):
        with patch.object(time, "monotonic", return_value=at):
            self.gui.queue_host_hotkey(0x79)

    def test_busy_ui_does_not_delay_backend_stop_or_touch_tk(self):
        self.recording()
        blocked_ui = Mock(side_effect=AssertionError("Tk backlog must not run first"))
        self.gui.ui_queue.put((blocked_ui, (), {}))
        self.gui.handle_host_hotkey = Mock(side_effect=AssertionError("Slow UI hotkey path"))
        self.gui.btn_host_start.config = Mock(side_effect=AssertionError("Tk before drain"))
        self.gui.btn_host_emergency.config = Mock(side_effect=AssertionError("Tk before drain"))
        self.gui.lbl_host_status.config = Mock(side_effect=AssertionError("Tk before drain"))
        self.keydown()
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        self.run_workers()
        self.backend.stop_recording_verified.assert_called_once()
        self.assertTrue(self.gui.is_stopping)
        blocked_ui.assert_not_called()
        self.gui.handle_host_hotkey.assert_not_called()
        self.gui.btn_host_start.config.assert_not_called()
        self.gui.btn_host_emergency.config.assert_not_called()
        self.gui.lbl_host_status.config.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_hook_queue_does_not_acquire_action_lock_or_inspect_native_window(self):
        self.recording()
        class ForbiddenLock:
            def __enter__(self):
                raise AssertionError("Hook must not wait for the action lock")
            def __exit__(self, *_):
                pass
        self.gui._action_lock = ForbiddenLock()
        self.keydown()
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        self.backend.find_odx_window.assert_not_called()
        self.backend.fetch_room_status.assert_not_called()
        self.backend.stop_recording_verified.assert_not_called()

    def test_held_key_repeat_and_release_produce_one_stop_intent(self):
        self.recording()
        pressed = set()
        def route(message):
            return route_physical_hotkey(pressed, message, 0x79,
                                         should_capture=self.gui.should_capture_hotkey,
                                         dispatch=self.gui.queue_host_hotkey)
        with patch.object(time, "monotonic", return_value=110):
            self.assertTrue(route(0x0100))
            for _ in range(20):
                self.assertTrue(route(0x0100))
            self.assertTrue(route(0x0101))
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        self.run_workers()
        self.backend.stop_recording_verified.assert_called_once()
        self.assertFalse(pressed)

    def test_old_intent_after_new_round_token_cannot_stop_or_start(self):
        self.recording()
        self.keydown()
        self.gui._transition_token += 1
        self.run_workers()
        self.assertFalse(self.gui.is_stopping)
        self.backend.stop_recording_verified.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertTrue(self.gui.ui_queue.empty())

    def test_held_stop_key_cannot_restart_after_round_finishes_or_on_release(self):
        self.recording()
        pressed = set()
        def route(message):
            return route_physical_hotkey(pressed, message, 0x79,
                                         should_capture=self.gui.should_capture_hotkey,
                                         dispatch=self.gui.queue_host_hotkey)
        self.assertTrue(route(0x0100))
        self.run_workers()
        self.gui.is_recording = False
        self.gui.is_stopping = False
        self.gui._transition_token += 1
        self.gui.active_task_id = "new-round-02"
        queued_before = self.gui.ui_queue.qsize()
        workers_before = len(gui_fixture.DeferredThread.created)
        self.assertTrue(route(0x0100))  # Autorepeat arrives after native stop.
        self.assertTrue(route(0x0101))
        self.assertEqual(len(gui_fixture.DeferredThread.created), workers_before)
        self.assertEqual(self.gui.ui_queue.qsize(), queued_before)
        self.backend.stop_recording_verified.assert_called_once()
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_old_intent_after_task_change_cannot_stop_or_start(self):
        self.recording()
        self.keydown()
        self.gui.active_task_id = "new-round-02"
        self.run_workers()
        self.assertFalse(self.gui.is_stopping)
        self.backend.stop_recording_verified.assert_not_called()
        self.backend.send_toggle_hotkey.assert_not_called()

    def test_automatic_stop_reserves_first_and_discards_older_f10_intent(self):
        self.recording()
        self.keydown(at=339.9)
        with patch.object(time, "monotonic", return_value=340):
            self.gui.request_host_stop("validate")
        self.run_workers()
        self.backend.stop_recording_verified.assert_called_once()
        self.assertEqual(self.gui._pending_stop_action, "validate")
        self.assertEqual(self.gui._transition_token, 1)

    def test_manual_stop_reserves_first_and_automatic_timer_cannot_duplicate_it(self):
        self.recording()
        self.keydown(at=339.9)
        with patch.object(time, "monotonic", return_value=339.95):
            gui_fixture.DeferredThread.created[0].run()
        self.gui.request_host_stop("validate")
        self.assertEqual(len(gui_fixture.DeferredThread.created), 2)
        gui_fixture.DeferredThread.created[1].run()
        self.backend.stop_recording_verified.assert_called_once()
        self.assertEqual(self.gui._pending_stop_action, "emergency")

    def test_early_keydown_remains_emergency_if_worker_runs_after_target(self):
        self.recording()
        self.keydown(at=339.9)
        with patch.object(time, "monotonic", return_value=341):
            self.run_workers()
        self.backend.stop_recording_verified.assert_called_once()
        self.assertEqual(self.gui._pending_stop_action, "emergency")

    def test_countdown_preparation_is_retained_when_recording_reaches_native_anchor(self):
        task = self.gui.active_task_id
        job = types.SimpleNamespace(claim=Mock(return_value=True), cancel=Mock())
        self.backend.prepare_recording_stop = Mock(return_value=job)
        self.gui.follower.current_recording = task
        self.gui._start_command_sent = True
        with patch.object(time, "monotonic", return_value=100):
            self.gui.begin_confirmed_recording(task, 103)
        self.assertTrue(self.gui.is_counting_down)
        self.backend.prepare_recording_stop.assert_called_once()
        guard = self.backend.prepare_recording_stop.call_args.kwargs["can_prepare"]
        self.assertTrue(guard())
        with patch.object(time, "monotonic", return_value=103.1):
            self.gui.begin_confirmed_recording(task, 103)
        self.assertTrue(self.gui.is_recording)
        self.assertFalse(self.gui.is_counting_down)
        self.backend.prepare_recording_stop.assert_called_once()
        job.cancel.assert_not_called()
        self.assertTrue(guard())

    def test_early_f10_uses_already_prepared_action_without_waiting_for_ui(self):
        self.recording()
        job = types.SimpleNamespace(claim=Mock(return_value=True), cancel=Mock())
        self.backend.prepare_recording_stop = Mock(return_value=job)
        self.gui.prepare_host_stop_if_needed(0)
        self.keydown(at=110)
        with patch.object(time, "monotonic", return_value=110.01):
            self.run_workers()
        self.assertIs(self.backend.stop_recording_verified.call_args.kwargs["prepared"], job)
        job.claim.assert_called_once()
        job.cancel.assert_called_once()
        self.assertFalse(self.gui.ui_queue.empty())

    def test_non_recording_hotkey_keeps_the_normal_ui_start_or_selector_path(self):
        self.gui._ui_thread_id = -1
        self.gui.queue_host_hotkey(0x79)
        self.gui.queue_host_hotkey(0x7A)
        self.assertFalse(gui_fixture.DeferredThread.created)
        pending = []
        while not self.gui.ui_queue.empty():
            callback, args, _ = self.gui.ui_queue.get_nowait()
            self.assertEqual(callback, self.gui.handle_host_hotkey)
            pending.append(args)
        self.assertEqual(pending, [(0x79,), (0x7A,)])
        self.backend.stop_recording_verified.assert_not_called()

    def test_dispatch_start_failure_is_consumed_and_reported_without_global_toggle(self):
        self.recording()
        self.ns["threading"].Thread = Mock(side_effect=OSError("worker unavailable"))
        consumed = route_physical_hotkey(set(), 0x0100, 0x79,
                                         should_capture=self.gui.should_capture_hotkey,
                                         dispatch=self.gui.queue_host_hotkey)
        self.assertTrue(consumed)
        self.assertFalse(self.gui.ui_queue.empty())
        self.backend.send_toggle_hotkey.assert_not_called()
        self.backend.stop_recording_verified.assert_not_called()


if __name__ == "__main__":
    unittest.main()
