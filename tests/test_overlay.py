"""Capture HUD states without creating Tk windows or emitting input."""
import queue
import unittest
from unittest.mock import Mock, patch

from odx_overlay import OdxOverlay


class OverlayStateTests(unittest.TestCase):
    def setUp(self):
        self.overlay = OdxOverlay.__new__(OdxOverlay)
        self.overlay.cmd_queue = queue.Queue()

    def commands(self):
        commands = []
        while not self.overlay.cmd_queue.empty():
            commands.append(self.overlay.cmd_queue.get_nowait())
        return commands

    def hud_text(self):
        commands = self.commands()
        self.assertEqual([command for command, _ in commands],
                         ["HIDE_CD", "HIDE_BANNER", "SHOW_HUD"])
        return commands[-1][1][0]

    def test_pending_native_start_has_no_recording_clock_or_f11(self):
        for remaining in (None, 3, 0.25, 0):
            with self.subTest(remaining=remaining):
                self.overlay.show_pending_recording("task-01", remaining)
                text = self.hud_text()
                self.assertIn("CHO QUAY", text)
                self.assertNotIn("REC", text)
                self.assertNotIn("00:00", text)
                self.assertNotIn("F11", text)
                self.assertIn("F10: DUNG KHAN CAP", text)

    def test_host_recording_uses_f10_throughout_active_capture(self):
        for elapsed in (0, 30, 229, 230, 239):
            with self.subTest(elapsed=elapsed):
                self.overlay.update_recording("task-01", elapsed, target_sec=240)
                text = self.hud_text()
                self.assertIn("F10: DUNG KHAN CAP", text)
                self.assertNotIn("F11", text)
        self.overlay.update_recording("task-01", 240, target_sec=240)
        text = self.hud_text()
        self.assertIn("CHO ODX KET THUC", text)
        self.assertNotIn("F11", text)

    def test_player_hud_does_not_offer_host_controls(self):
        for elapsed in (-3, 0, 235, 240):
            with self.subTest(elapsed=elapsed):
                self.overlay.update_recording("task-01", elapsed, is_host=False)
                text = self.hud_text()
                self.assertNotIn("F10", text)
                self.assertNotIn("F11", text)

    def test_negative_elapsed_stays_in_pending_state(self):
        self.overlay.update_recording("task-01", -2.5)
        text = self.hud_text()
        self.assertIn("ODX DEM NGUOC: 3s", text)
        self.assertNotIn("[REC]", text)

    def test_preferences_are_queued_once_without_touching_tk_from_caller(self):
        self.overlay.hud_win = Mock()
        settings = {"hud_scale": 1.5, "hud_opacity": .6, "hud_show_timer": False}
        self.assertTrue(self.overlay.set_preferences(settings))
        self.assertFalse(self.overlay.set_preferences(dict(settings, player_id="private-local-id")))
        commands = self.commands()
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], "PREFERENCES")
        self.assertEqual(commands[0][1][0]["hud_scale"], 1.5)
        self.overlay.hud_win.attributes.assert_not_called()
        self.overlay.hud_win.geometry.assert_not_called()

    def test_invalid_local_preferences_do_not_replace_valid_preferences(self):
        for settings in ({"hud_scale": float("nan")}, {"hud_opacity": .01},
                         {"hud_enabled": "false"}, {"hud_language": "unsupported"},
                         {"test_mode": "true"}, {"test_mode": 1}):
            self.assertFalse(self.overlay.set_preferences(settings))
        self.assertEqual(self.commands(), [])

    def test_disabled_overlay_never_publishes_new_capture_or_roster_content(self):
        self.overlay.set_preferences({"hud_enabled": False})
        self.commands()
        self.overlay.show_pending_recording("task-01", 3)
        self.overlay.update_recording("task-01", 12)
        self.overlay.show_roster_hud("roster", roster=True)
        self.overlay.show_roster_hud("status")
        self.assertNotIn("SHOW_HUD", [name for name, _ in self.commands()])

    def test_hide_roster_keeps_status_and_choice_messages_available(self):
        self.overlay.set_preferences({"hud_show_roster": False})
        self.commands()
        self.overlay.show_roster_hud("room member list", roster=True)
        self.overlay.show_roster_hud("F11: FIX | F10: NEXT | F9: BASE")
        commands = self.commands()
        self.assertEqual([name for name, _ in commands], ["SHOW_HUD"])
        self.assertIn("FIX", commands[0][1][0])

    def test_host_preferences_control_language_task_and_clock_in_all_capture_states(self):
        self.overlay.set_preferences({"hud_language": "en", "hud_show_task": False,
                                      "hud_show_timer": False})
        self.commands()
        for elapsed in (-3, 12, 235, 240):
            with self.subTest(elapsed=elapsed):
                self.overlay.update_recording("private-task-id", elapsed)
                text = self.hud_text()
                self.assertNotIn("private-task-id", text)
                self.assertNotIn("00:", text)
                self.assertNotIn("3s", text)
                self.assertNotIn("F11", text)
                self.assertNotIn("CHO ", text)
                self.assertNotIn("DUNG ", text)
                if elapsed < 240:
                    self.assertIn("F10: EMERGENCY STOP", text)

    def test_queued_window_preferences_apply_scale_and_opacity_to_all_windows(self):
        for name in ("cd_win", "hud_win", "banner_win", "hud_lbl", "cd_num_lbl",
                     "cd_sub_lbl", "banner_l1", "banner_l2"):
            setattr(self.overlay, name, Mock())
        self.overlay.hud_lbl.cget.return_value = "Current HUD"
        self.overlay.set_preferences({"hud_enabled": False, "hud_scale": 1.5, "hud_opacity": .6})
        preferences = self.commands()[0][1][0]
        self.overlay._apply_window_preferences(preferences, 800, 600)
        for window in (self.overlay.cd_win, self.overlay.hud_win, self.overlay.banner_win):
            window.attributes.assert_called_once_with("-alpha", .6)
            window.withdraw.assert_called_once_with()
        self.overlay.hud_lbl.config.assert_called_once_with(font=("Consolas", 16, "bold"))
        self.assertTrue(self.overlay.hud_win.geometry.call_args.args[0].startswith("750x69+"))
        self.assertTrue(self.overlay.banner_win.geometry.call_args.args[0].startswith("760x112+"))

    def test_test_mode_labels_host_and_player_capture_states_without_changing_task(self):
        self.overlay.set_preferences({"test_mode": True})
        self.commands()
        for is_host in (True, False):
            for elapsed in (-3, 0, 235, 240):
                with self.subTest(is_host=is_host, elapsed=elapsed):
                    self.overlay.update_recording("test-task-01", elapsed, is_host=is_host)
                    text = self.hud_text()
                    self.assertTrue(text.startswith("[TEST] "))
                    self.assertEqual(text.count("[TEST]"), 1)
                    self.assertIn("[test-task-01]", text)

    def test_test_mode_labels_roster_choices_countdown_and_result_banners(self):
        self.overlay.set_preferences({"test_mode": True})
        self.commands()
        self.overlay.show_roster_hud("Host + Player ready", roster=True)
        self.overlay.show_roster_hud("F11: FIX | F10: NEXT | F9: BASE")
        self.overlay.show_roster_hud("[TEST] Host already labeled")
        with patch("odx_overlay.time.sleep"):
            self.overlay.countdown(1)
            self.overlay.notify_cancelled("test-01", "test-01-fix1", display_sec=0)
            self.overlay.notify_completed("test-01", "test-02", cooldown_sec=1)
        displays = [(name, args) for name, args in self.commands() if name.startswith("SHOW_")]
        self.assertEqual({name for name, _ in displays},
                         {"SHOW_ROSTER", "SHOW_HUD", "SHOW_CD", "SHOW_BANNER"})
        for name, args in displays:
            text = args[1] if name == "SHOW_CD" else args[0]
            with self.subTest(name=name, text=text):
                self.assertTrue(text.startswith("[TEST] "))
                self.assertEqual(text.count("[TEST]"), 1)

    def test_test_mode_label_can_be_removed_and_is_visible_when_task_is_hidden(self):
        self.overlay.set_preferences({"test_mode": True, "hud_show_task": False})
        self.commands()
        self.overlay.update_recording("private-test-task", 10)
        text = self.hud_text()
        self.assertTrue(text.startswith("[TEST] "))
        self.assertNotIn("private-test-task", text)
        self.overlay.set_preferences({"test_mode": False})
        self.commands()
        self.overlay.update_recording("normal-task", 10)
        self.assertNotIn("[TEST]", self.hud_text())


if __name__ == "__main__":
    unittest.main()
