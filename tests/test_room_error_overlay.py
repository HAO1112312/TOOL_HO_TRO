"""Changed native member errors stay visible independently of capture HUD ticks."""
import unittest
from unittest.mock import Mock

from odx_overlay import DEFAULT_PREFERENCES
if __package__:
    from . import test_action_overlay as action
else:
    import test_action_overlay as action


class RoomErrorOverlayTests(unittest.TestCase):
    def setUp(self):
        action.ActionOverlayTests.setUp(self)
        self.overlay.room_error_win = Mock()
        self.overlay.room_error_title = Mock()
        self.overlay.room_error_text = Mock()
        self.overlay._action_state()

    commands = action.ActionOverlayTests.commands

    def show(self, message="qhao [round-01]: readback timed out", cue="one"):
        self.assertTrue(self.overlay.show_room_error(message, cue_id=cue))
        command, args = self.commands()[-1]
        self.assertEqual(command, "SHOW_ROOM_ERROR")
        self.assertTrue(self.overlay._show_room_error_ui(*args, preferences=self.overlay._preferences))
        return args

    def test_native_error_has_independent_eight_second_nonactivating_notice(self):
        self.show()
        self.overlay.room_error_win.state.assert_called_once_with("normal")
        self.overlay.room_error_text.config.assert_any_call(text="qhao [round-01]: readback timed out")
        self.overlay.action_win.withdraw.assert_not_called()
        self.overlay.hud_win.withdraw.assert_not_called()
        self.overlay.room_error_win.focus_force.assert_not_called()
        self.assertEqual(next(iter(self.overlay.root.callbacks.values()))[0], 8000)

    def test_same_error_is_deduplicated_without_extending_timeout(self):
        self.show()
        timer = self.overlay._room_error_hide_after
        self.assertFalse(self.overlay.show_room_error("same", cue_id="one"))
        self.assertEqual(self.overlay._room_error_hide_after, timer)
        self.assertEqual(self.commands(), [])

    def test_stale_hide_callback_cannot_hide_new_member_error(self):
        self.show()
        old_callback = self.overlay.root.callbacks[self.overlay._room_error_hide_after][1]
        self.show("nana [round-01]: encoder failed", "two")
        count = self.overlay.room_error_win.withdraw.call_count
        self.assertFalse(old_callback())
        self.assertEqual(self.overlay.room_error_win.withdraw.call_count, count)

    def test_clear_cancels_pending_notice_and_allows_same_error_after_recovery(self):
        self.overlay.show_room_error("qhao failed", cue_id="one")
        args = self.commands()[0][1]
        self.overlay.clear_room_error()
        self.assertFalse(self.overlay._show_room_error_ui(*args, preferences=DEFAULT_PREFERENCES))
        self.assertTrue(self.overlay.show_room_error("qhao failed", cue_id="one"))

    def test_action_option_does_not_disable_errors_but_master_hud_does(self):
        self.overlay._preferences["action_prompt_enabled"] = False
        self.assertTrue(self.overlay.show_room_error("native failure"))
        self.overlay._preferences["hud_enabled"] = False
        self.assertFalse(self.overlay.show_room_error("another failure"))

    def test_normal_capture_ticks_do_not_hide_or_replace_error_notice(self):
        self.show()
        generation = self.overlay._room_error_generation
        self.overlay.update_recording("round-01", 127, 240)
        self.assertEqual(self.overlay._room_error_generation, generation)
        self.assertNotIn("HIDE_ROOM_ERROR", [name for name, _ in self.commands()])

    def test_closed_overlay_rejects_late_notice(self):
        self.overlay.close()
        self.assertFalse(self.overlay.show_room_error("too late"))


if __name__ == "__main__":
    unittest.main()
