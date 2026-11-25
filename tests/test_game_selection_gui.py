"""Host game choice is applied before room creation and remains machine-local."""
import unittest
from unittest.mock import Mock

import odx_sync
if __package__:
    from . import test_gui as fixture
    from . import test_desktop_test_mode as desktop
else:
    import test_gui as fixture
    import test_desktop_test_mode as desktop


class GameSelectionGuiTests(unittest.TestCase):
    setUp = fixture.GuiTests.setUp
    player_prejoin_fixture = desktop.DesktopTestModeTests.player_prejoin_fixture

    def test_legacy_game_process_migrates_without_changing_identity(self):
        self.gui.config.update(game_process="arma3_x64", player_id="local-player")
        self.assertEqual(self.gui.selected_game(), "arma3")
        self.gui.config["game_selection"] = "arma-reforger"
        self.assertEqual(self.gui.selected_game_name(), "Arma Reforger")
        self.assertEqual(self.gui.config["player_id"], "local-player")

    def test_game_option_configures_owned_odx_with_audio(self):
        self.gui.var_game_selection = desktop.Variable("Arma 3")
        self.gui.on_game_selection_changed()
        self.assertEqual(self.gui.config["game_selection"], "arma3")
        self.assertTrue(self.gui.config["record_audio"])
        self.assertTrue(self.gui.operation_busy())
        fixture.DeferredThread.created[0].run()
        self.games.set_game_capture.assert_called_once()
        self.assertEqual(self.games.set_game_capture.call_args.args, ("arma3",))
        self.assertFalse(self.gui.operation_busy())
        self.backend.cleanup_all_odx.assert_not_called()

    def test_no_native_window_saves_option_for_next_room_without_launching(self):
        self.gui.var_game_selection = desktop.Variable("Arma 3")
        self.gui.owned_odx_window = Mock(return_value=None)
        self.gui.on_game_selection_changed()
        self.assertEqual(self.gui.selected_game(), "arma3")
        self.games.set_game_capture.assert_not_called()
        self.ns["subprocess"].Popen.assert_not_called()

    def test_room_cannot_change_game_or_sources_mid_session(self):
        self.gui.config["game_selection"] = "arma-reforger"
        self.gui.room_code = "ABC123"
        self.gui.var_game_selection = desktop.Variable("Arma 3")
        self.gui.on_game_selection_changed()
        self.assertEqual(self.gui.var_game_selection.get(), "Arma Reforger")
        self.games.set_game_capture.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_desktop_mode_enables_audio_without_choosing_game_window(self):
        self.gui.configure_capture_mode_for_room(True, lambda: True)
        self.backend.set_capture_test_mode.assert_called_once()
        self.games.ensure_capture_audio.assert_called_once()
        self.games.set_game_capture.assert_not_called()

    def test_normal_mode_verifies_source_profile_audio_before_room(self):
        self.gui.configure_capture_mode_for_room(False, lambda: True, "arma3")
        self.games.set_game_capture.assert_called_once()
        self.assertEqual(self.games.set_game_capture.call_args.args, ("arma3",))
        self.games.ensure_capture_audio.assert_not_called()

    def test_failed_game_selection_blocks_room_and_leaves_odx_running(self):
        self.games.set_game_capture.return_value.success = False
        self.games.set_game_capture.return_value.error = "Arma 3 is not running"
        self.gui.host_open_odx()
        fixture.DeferredThread.created[0].run()
        self.backend.host_auto_create_room.assert_not_called()
        self.assertFalse(self.gui.odx_opened)
        self.assertIn("not running", self.gui.lbl_host_status.options["text"])
        self.backend.cleanup_all_odx.assert_not_called()

    def test_player_applies_host_game_before_join_and_keeps_player_identity(self):
        client, _ = self.player_prejoin_fixture(test_mode=False)
        client.fetch.return_value["settings"] = odx_sync.validate_settings(dict(
            game_selection="arma3", record_audio=True, test_mode=False,
            task_id="host-task-01", target_duration=240))
        self.gui.config.update(game_selection="arma-reforger", player_id="p1", player_name="Player")
        self.gui.player_start_connection()
        fixture.DeferredThread.created[0].run()
        self.assertEqual(self.games.set_game_capture.call_args.args, ("arma3",))
        self.assertEqual(self.gui.config["game_selection"], "arma3")
        self.assertEqual(self.gui.config["player_id"], "p1")
        self.assertEqual(self.gui.config["player_name"], "Player")
        self.assertTrue(self.gui.player_connection_active)
        self.backend.cleanup_all_odx.assert_not_called()

    def test_shared_snapshot_contains_canonical_game_and_audio_policy(self):
        self.gui.config.update(game_selection="arma3", record_audio=False)
        snapshot = self.gui.host_settings_snapshot()
        self.assertEqual(snapshot["game_selection"], "arma3")
        self.assertIs(snapshot["record_audio"], True)
        self.assertEqual(snapshot, odx_sync.validate_settings(snapshot))
        self.assertNotIn("window_handle", snapshot)
        self.assertNotIn("source_label", snapshot)

    def test_member_ready_flag_cannot_override_current_native_error_before_start(self):
        self.gui.tracker.latest_data["members"][1].update(
            name="qhao", ready=True, problem="capture callback stalled")
        self.gui.host_trigger_start()
        self.assertIn("qhao", self.gui.lbl_host_status.options["text"])
        self.assertIn("callback stalled", self.gui.lbl_host_status.options["text"])
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertFalse(fixture.DeferredThread.created)

    def test_member_error_arriving_during_injection_cancels_prepared_start(self):
        self.gui.host_trigger_start()
        self.backend.set_task_box.return_value.join.side_effect = lambda **_: (
            self.gui.tracker.latest_data["members"][1].update(problem="encoder failed"))
        fixture.DeferredThread.created[0].run()
        self.backend.send_toggle_hotkey.assert_not_called()
        self.assertFalse(self.gui.is_counting_down)
        self.backend.cleanup_all_odx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
