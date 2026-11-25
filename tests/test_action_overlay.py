"""Centered action cues use a cancellable Tk timer, never sleep or capture input."""
import queue
import unittest
from unittest.mock import Mock

from odx_overlay import DEFAULT_PREFERENCES, OdxOverlay


class FakeRoot:
    def __init__(self):
        self.callbacks = {}
        self.cancelled = []
        self.sequence = 0

    def after(self, delay, callback):
        self.sequence += 1
        token = f"after-{self.sequence}"
        self.callbacks[token] = (delay, callback)
        return token

    def after_cancel(self, token):
        self.cancelled.append(token)
        self.callbacks.pop(token, None)

    def winfo_screenwidth(self):
        return 1024

    def winfo_screenheight(self):
        return 768


class ActionOverlayTests(unittest.TestCase):
    def setUp(self):
        self.overlay = OdxOverlay.__new__(OdxOverlay)
        self.overlay.cmd_queue = queue.Queue()
        self.overlay._preferences = dict(DEFAULT_PREFERENCES)
        self.overlay.root = FakeRoot()
        self.overlay.action_win = Mock()
        self.overlay.action_canvas = Mock()
        self.overlay._action_work_area = Mock(return_value=(0, 0, 1024, 728))
        self.overlay.hud_win = Mock()

    def commands(self):
        result = []
        while not self.overlay.cmd_queue.empty():
            result.append(self.overlay.cmd_queue.get_nowait())
        return result

    def show(self, task="round-01", cue="a"):
        self.assertTrue(self.overlay.show_action_change(task, cue_id=cue))
        command, args = self.commands()[-1]
        self.assertEqual(command, "SHOW_ACTION")
        self.assertTrue(self.overlay._show_action_ui(*args, preferences=self.overlay._preferences))
        return args

    def test_public_request_is_nonblocking_and_never_touches_tk(self):
        self.overlay.root = Mock(side_effect=AssertionError("No caller-side Tk"))
        self.assertTrue(self.overlay.show_action_change("round-01", cue_id="start-1"))
        self.overlay.action_win.deiconify.assert_not_called()
        self.overlay.action_canvas.itemconfigure.assert_not_called()
        self.assertEqual(self.commands()[0][:1], ("SHOW_ACTION",))

    def test_center_prompt_uses_exact_message_and_keeps_recording_hud(self):
        self.show()
        texts = {call.args[0]: call.kwargs["text"] for call in self.overlay.action_canvas.itemconfigure.call_args_list}
        self.assertEqual(texts["action_label"], "THAY ĐỔI HÀNH ĐỘNG")
        self.assertEqual(texts["task_label"], "Task: round-01")
        self.overlay.action_win.state.assert_called_once_with("normal")
        self.overlay.action_win.deiconify.assert_not_called()
        self.overlay.hud_win.withdraw.assert_not_called()
        self.overlay.action_win.focus_set.assert_not_called()
        self.overlay.action_win.focus_force.assert_not_called()
        self.assertEqual(next(iter(self.overlay.root.callbacks.values()))[0], 5000)

    def test_repeated_cue_does_not_extend_its_timer(self):
        self.show()
        token = self.overlay._action_hide_after
        for _ in range(15):
            self.assertFalse(self.overlay.show_action_change("round-01", cue_id="a"))
        self.assertEqual(self.overlay._action_hide_after, token)
        self.assertEqual(len(self.overlay.root.callbacks), 1)
        self.assertEqual(self.commands(), [])

    def test_old_hide_callback_cannot_hide_new_round_cue(self):
        self.show()
        old_token = self.overlay._action_hide_after
        stale_callback = self.overlay.root.callbacks[old_token][1]
        self.show("round-02", "b")
        withdrawals = self.overlay.action_win.withdraw.call_count
        self.assertFalse(stale_callback())
        self.assertEqual(self.overlay.action_win.withdraw.call_count, withdrawals)
        self.assertIn(old_token, self.overlay.root.cancelled)
        new_callback = self.overlay.root.callbacks[self.overlay._action_hide_after][1]
        self.assertTrue(new_callback())
        self.assertEqual(self.overlay.action_win.withdraw.call_count, withdrawals + 1)

    def test_clear_before_ui_delivery_invalidates_pending_show(self):
        self.overlay.show_action_change("round-01", cue_id="a")
        show_args = self.commands()[0][1]
        self.overlay.clear()
        self.assertFalse(self.overlay._show_action_ui(*show_args, preferences=self.overlay._preferences))
        self.overlay.action_win.deiconify.assert_not_called()
        self.assertFalse(self.overlay.show_action_change("round-01", cue_id="a"))

    def test_stop_clear_cancels_visible_timer(self):
        self.show()
        timer = self.overlay._action_hide_after
        self.overlay.clear()
        command, args = self.commands()[0]
        self.assertEqual(command, "CLEAR_ALL")
        self.assertTrue(self.overlay._hide_action_ui(*args))
        self.assertIn(timer, self.overlay.root.cancelled)
        self.assertIsNone(self.overlay._action_hide_after)

    def test_regular_recording_updates_do_not_cancel_action_cue(self):
        self.show()
        generation = self.overlay._action_generation
        self.overlay.update_recording("round-01", 129, 240)
        self.assertEqual(self.overlay._action_generation, generation)
        self.assertNotIn("HIDE_ACTION", [name for name, _ in self.commands()])

    def test_pending_or_different_task_invalidates_visible_prompt(self):
        self.show()
        self.overlay.show_pending_recording("round-02", 2)
        self.assertIn("HIDE_ACTION", [name for name, _ in self.commands()])
        self.show("round-02", "b")
        self.overlay.update_recording("round-03", 0)
        self.assertIn("HIDE_ACTION", [name for name, _ in self.commands()])

    def test_repeated_pending_updates_while_tk_is_busy_still_hide_old_prompt(self):
        self.show()
        old_callback = self.overlay.root.callbacks[self.overlay._action_hide_after][1]
        self.overlay.show_pending_recording("round-02", 3)
        self.overlay.show_pending_recording("round-02", 2)
        self.overlay.show_pending_recording("round-02", 1)
        hidden = []
        for command, args in self.commands():
            if command == "HIDE_ACTION":
                hidden.append(self.overlay._hide_action_ui(*args))
        self.assertIn(True, hidden)
        self.assertIsNone(self.overlay._action_hide_after)
        self.assertFalse(old_callback())

    def test_master_hud_and_action_toggle_disable_requests(self):
        for preferences in ({"hud_enabled": False}, {"action_prompt_enabled": False}):
            with self.subTest(preferences=preferences):
                self.overlay._preferences = dict(DEFAULT_PREFERENCES, **preferences)
                self.assertFalse(self.overlay.show_action_change("round-01", cue_id="a"))
        self.assertEqual(self.commands(), [])

    def test_preference_disable_invalidates_already_queued_action(self):
        self.overlay.show_action_change("round-01", cue_id="a")
        args = self.commands()[0][1]
        self.overlay.set_preferences({"action_prompt_enabled": False})
        self.assertFalse(self.overlay._show_action_ui(*args, preferences=self.overlay._preferences))
        self.overlay.action_win.deiconify.assert_not_called()

    def test_close_invalidates_timer_and_rejects_future_cues(self):
        self.show()
        old_callback = self.overlay.root.callbacks[self.overlay._action_hide_after][1]
        self.overlay.close()
        self.assertFalse(old_callback())
        self.assertFalse(self.overlay.show_action_change("round-02", cue_id="b"))
        self.assertEqual(self.commands()[-1][0], "QUIT")

    def test_geometry_fits_small_and_negative_position_work_areas(self):
        for area in ((0, 0, 1024, 728), (-1280, 0, 0, 984), (0, 0, 480, 320)):
            for scale in (.5, 1, 2):
                with self.subTest(area=area, scale=scale):
                    width, height, x, y = self.overlay._fit_action_bounds(area, scale)
                    left, top, right, bottom = area
                    self.assertGreater(width, 0)
                    self.assertLess(width, right - left)
                    self.assertLess(height, bottom - top)
                    self.assertEqual(x, left + ((right - left) - width) // 2)
                    self.assertEqual(y, top + ((bottom - top) - height) // 2)

    def test_task_toggle_hides_private_task_without_changing_action_message(self):
        self.overlay._preferences.update(hud_show_task=False, test_mode=True)
        self.show("private-task-01", "nonce")
        texts = [call.kwargs["text"] for call in self.overlay.action_canvas.itemconfigure.call_args_list
                 if "text" in call.kwargs]
        self.assertEqual(texts, ["THAY ĐỔI HÀNH ĐỘNG", ""])

    def test_invalid_or_unbounded_requests_never_reach_ui(self):
        for duration in (0, -1, True, "5", float("nan"), float("inf")):
            with self.subTest(duration=duration):
                self.assertFalse(self.overlay.show_action_change("round-01", duration_sec=duration))
        self.assertFalse(self.overlay.show_action_change(""))
        self.assertFalse(self.overlay.show_action_change("x" * 129))
        self.assertFalse(self.overlay.show_action_change("round-01", cue_id="x" * 257))
        self.assertEqual(self.commands(), [])


if __name__ == "__main__":
    unittest.main()
