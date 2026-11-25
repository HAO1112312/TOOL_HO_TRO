"""One Host-chosen cue follows the native recording clock on every machine."""
import unittest
from unittest.mock import patch

if __package__:
    from . import test_gui as fixture
else:
    import test_gui as fixture


class ActionPromptGuiTests(unittest.TestCase):
    setUp = fixture.GuiTests.setUp

    def recording(self, mode="HOST", at=127):
        self.gui.current_mode = mode
        self.gui.is_recording = mode == "HOST"
        self.gui.is_player_recording = mode == "PLAYER"
        self.gui.follower.current_recording = "round-01"
        self.gui.remember_shared_action_prompt(dict(
            task_id="round-01", action_prompt_at_seconds=at, start_request_id="request-1"))

    def test_host_draws_once_and_snapshots_keep_same_plan_for_player_ack(self):
        self.gui._sync_start_task = self.gui.active_task_id
        self.gui._sync_start_nonce = "request-1"
        with patch.object(fixture.random, "randint", return_value=128) as draw:
            self.assertEqual(self.gui.plan_action_prompt(self.gui.active_task_id), 128)
            for _ in range(3):
                snapshot = self.gui.host_settings_snapshot()
                self.assertEqual(snapshot["action_prompt_at_seconds"], 128)
                self.assertEqual(snapshot["start_request_id"], "request-1")
                self.gui.plan_action_prompt(self.gui.active_task_id)
            draw.assert_called_once_with(125, 130)

    def test_short_task_has_no_cue_and_deadline_cannot_cut_off_cue_start(self):
        self.gui.config["round_duration_seconds"] = 125
        with patch.object(fixture.random, "randint") as draw:
            self.assertIsNone(self.gui.plan_action_prompt("round-01"))
            draw.assert_not_called()
        self.gui.config["round_duration_seconds"] = 128
        with patch.object(fixture.random, "randint", return_value=127) as draw:
            self.assertEqual(self.gui.plan_action_prompt("round-01"), 127)
            draw.assert_called_once_with(125, 127)

    def test_pending_duration_change_publishes_adjusted_cue_before_ack(self):
        task = self.gui.active_task_id
        self.gui._sync_start_task, self.gui._sync_start_nonce = task, "a" * 32
        self.gui._sync_start_after = 1
        with patch.object(fixture.random, "randint", return_value=130):
            self.gui.plan_action_prompt(task)
        self.gui.config["round_duration_seconds"] = 128
        with patch.object(fixture.random, "randint", return_value=127) as draw:
            snapshot = self.gui.host_settings_snapshot()
            self.assertEqual(snapshot["target_duration"], 128)
            self.assertEqual(snapshot["action_prompt_at_seconds"], 127)
            self.assertEqual(self.gui.plan_action_prompt(task), 127)
            draw.assert_called_once_with(125, 127)

    def test_player_uses_host_plan_and_never_draws_random_time(self):
        self.recording("PLAYER")
        with patch.object(fixture.random, "randint") as draw:
            self.assertIsNone(self.gui.plan_action_prompt("round-01"))
            self.assertEqual(self.gui.action_prompt_at("round-01"), 127)
            self.assertFalse(self.gui.maybe_show_action_prompt("round-01", 126.9, 240))
            self.assertTrue(self.gui.maybe_show_action_prompt("round-01", 127.1, 240))
            draw.assert_not_called()
        self.gui.overlay.show_action_change.assert_called_once_with(
            "round-01", duration_sec=5, cue_id="round-01:request-1")

    def test_countdown_does_not_show_and_active_ticks_show_only_once(self):
        self.recording()
        self.gui.is_recording = False
        self.gui.is_counting_down = True
        self.assertFalse(self.gui.maybe_show_action_prompt("round-01", 127, 240))
        self.gui.is_counting_down = False
        self.gui.is_recording = True
        self.assertTrue(self.gui.maybe_show_action_prompt("round-01", 127, 240))
        self.assertFalse(self.gui.maybe_show_action_prompt("round-01", 127.2, 240))
        self.assertFalse(self.gui.maybe_show_action_prompt("round-01", 129, 240))
        self.gui.overlay.show_action_change.assert_called_once()

    def test_stop_audit_and_stale_task_cannot_show_cue(self):
        self.recording()
        for field in ("is_stopping", "is_validating"):
            setattr(self.gui, field, True)
            self.assertFalse(self.gui.maybe_show_action_prompt("round-01", 127, 240))
            setattr(self.gui, field, False)
        self.gui.follower.current_recording = "round-02"
        self.assertFalse(self.gui.maybe_show_action_prompt("round-01", 127, 240))
        self.gui.overlay.show_action_change.assert_not_called()

    def test_suspended_vm_drops_late_cue_instead_of_showing_after_target(self):
        self.recording(at=130)
        self.assertFalse(self.gui.maybe_show_action_prompt("round-01", 151, 240))
        self.assertFalse(self.gui.maybe_show_action_prompt("round-01", 130, 240))
        self.gui.overlay.show_action_change.assert_not_called()

    def test_player_callback_uses_current_clock_and_rejects_old_round(self):
        self.recording("PLAYER")
        with patch.object(fixture.time, "monotonic", return_value=400):
            self.gui.player_action_prompt_tick(1, "round-01", 0, 273, 240)
            self.gui.player_action_prompt_tick(0, "round-01", 1, 273, 240)
            self.gui.overlay.show_action_change.assert_not_called()
            self.gui.player_action_prompt_tick(0, "round-01", 0, 273, 240)
        self.gui.overlay.show_action_change.assert_called_once()

    def test_host_advance_does_not_remove_current_player_cue(self):
        self.recording("PLAYER")
        self.gui.remember_shared_action_prompt(dict(
            task_id="round-02", action_prompt_at_seconds=None, start_request_id=None))
        self.assertTrue(self.gui.maybe_show_action_prompt("round-01", 127, 240))

    def test_host_hud_preferences_can_disable_cue(self):
        self.recording()
        for key in ("hud_enabled", "action_prompt_enabled"):
            self.gui.config[key] = False
            self.assertFalse(self.gui.maybe_show_action_prompt("round-01", 127, 240))
            self.gui.config[key] = True
        self.gui.overlay.show_action_change.assert_not_called()


if __name__ == "__main__":
    unittest.main()
