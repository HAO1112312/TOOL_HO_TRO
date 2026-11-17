"""Player readiness follows the completed audit, including delayed VM files.

The shared GUI fixture creates no Tk windows, Odx processes or network requests.
"""
import types
import unittest
from unittest.mock import Mock

if __package__:
    from . import test_gui as gui_fixture
else:
    import test_gui as gui_fixture


class PlayerAuditWaitTests(unittest.TestCase):
    setUp = gui_fixture.GuiTests.setUp

    def player(self, *, passed=True, error="", cancelled=False):
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui.player_connection_active = True
        self.gui._player_round_token = 1
        self.gui._player_validation_token = (0, "round-02", 1)
        self.gui._player_validation_failed = False
        self.gui._player_failed_audit = None
        self.gui._player_ready_after_round = None
        self.gui.is_validating = True
        self.gui.auto_ready_player_next_round = Mock()
        result = types.SimpleNamespace(
            passed=passed, target="actual-capture/round-02", error=error,
            cancelled=cancelled,
        )
        self.backend.validate_finalized_capture = Mock(return_value=result)
        return result

    def test_finalized_audit_success_automatically_requests_ready(self):
        self.player()
        self.gui.post_player_round(0, "round-02", 1)
        self.backend.validate_finalized_capture.assert_called_once()
        self.assertFalse(self.gui.is_validating)
        self.assertFalse(self.gui._player_validation_failed)
        self.assertEqual(self.gui._player_ready_after_round, (0, 1, "round-02"))
        self.gui.auto_ready_player_next_round.assert_called_once()
        self.assertIn("PASS", self.gui.lbl_p_status.options["text"])

    def test_true_failure_keeps_ready_blocked_and_exposes_error(self):
        detail = "raw/input_events.csv: malformed data"
        self.player(passed=False, error=detail)
        self.gui.post_player_round(0, "round-02", 1)
        self.assertTrue(self.gui._player_validation_failed)
        self.assertFalse(self.gui.is_validating)
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.assertEqual(self.gui._player_failed_audit, (0, "round-02", 1))
        self.assertIn(detail, self.gui.lbl_p_status.options["text"])
        self.assertIn("KIỂM ĐỊNH LẠI", self.gui.btn_p_connect.options["text"])
        self.backend.cleanup_all_odx.assert_not_called()

    def test_cancelled_wait_cannot_latch_failure_or_advertise_pass(self):
        self.player(passed=False, cancelled=True)
        self.gui.finish_player_validation = Mock()
        self.gui.post_player_round(0, "round-02", 1)
        self.gui.finish_player_validation.assert_not_called()
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.assertFalse(self.gui._player_validation_failed)

    def test_helper_predicate_requires_current_connection_round_and_validation(self):
        self.player()
        observations = []
        def check_predicate(*args, **kwargs):
            active = kwargs["should_continue"]
            observations.append(active())
            for field, stale, original in (
                ("_player_connection_generation", 1, 0),
                ("_player_round_token", 2, 1),
                ("_player_validation_token", (0, "different-task", 1), (0, "round-02", 1)),
                ("is_running", False, True),
            ):
                setattr(self.gui, field, stale)
                observations.append(active())
                setattr(self.gui, field, original)
            return types.SimpleNamespace(passed=True, target="round-02", error="", cancelled=False)
        self.backend.validate_finalized_capture.side_effect = check_predicate
        self.gui.post_player_round(0, "round-02", 1)
        self.assertEqual(observations, [True, False, False, False, False])

    def test_new_round_during_audit_discards_old_success(self):
        self.player()
        def next_round(*args, **kwargs):
            self.gui._player_round_token = 2
            self.gui._player_validation_token = (0, "round-03", 2)
            return types.SimpleNamespace(passed=True, target="round-02", error="", cancelled=False)
        self.backend.validate_finalized_capture.side_effect = next_round
        self.gui.post_player_round(0, "round-02", 1)
        self.assertTrue(self.gui.is_validating)
        self.assertEqual(self.gui._player_validation_token, (0, "round-03", 2))
        self.assertIsNone(self.gui._player_ready_after_round)
        self.gui.auto_ready_player_next_round.assert_not_called()

    def test_tool_close_during_audit_does_not_request_ready(self):
        self.player()
        def closed(*args, **kwargs):
            self.gui.is_running = False
            return types.SimpleNamespace(passed=True, target="round-02", error="", cancelled=False)
        self.backend.validate_finalized_capture.side_effect = closed
        self.gui.post_player_round(0, "round-02", 1)
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.assertIsNone(self.gui._player_ready_after_round)

    def test_ui_installs_validation_token_before_starting_worker(self):
        self.player()
        self.gui.is_validating = False
        self.gui._player_round_token = 0
        self.gui._player_validation_token = None
        self.gui.begin_player_round_validation(0, "round-02", 1)
        self.assertTrue(self.gui.is_validating)
        self.assertEqual(self.gui._player_validation_token, (0, "round-02", 1))
        self.backend.validate_finalized_capture.assert_not_called()
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        gui_fixture.DeferredThread.created[0].run()
        self.assertFalse(self.gui.is_validating)
        self.gui.auto_ready_player_next_round.assert_called_once()

    def test_worker_waits_for_queued_ui_state_before_auditing(self):
        self.player()
        self.gui._ui_thread_id = -1
        self.gui.is_validating = False
        self.gui._player_round_token = 0
        self.gui._player_validation_token = None
        self.gui.post_ui(self.gui.begin_player_round_validation, 0, "round-02", 1)
        self.assertFalse(gui_fixture.DeferredThread.created)
        self.backend.validate_finalized_capture.assert_not_called()
        callback, args, kwargs = self.gui.ui_queue.get_nowait()
        callback(*args, **kwargs)
        self.assertTrue(self.gui.is_validating)
        self.assertEqual(self.gui._player_validation_token, (0, "round-02", 1))
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)

    def test_stale_queued_begin_cannot_replace_a_newer_round(self):
        self.player()
        self.gui._player_round_token = 2
        self.gui._player_validation_token = (0, "round-03", 2)
        self.gui.begin_player_round_validation(0, "round-02", 1)
        self.assertEqual(self.gui._player_validation_token, (0, "round-03", 2))
        self.assertFalse(gui_fixture.DeferredThread.created)

    def test_recheck_button_revalidates_without_reconnecting_or_closing_odx(self):
        self.player(passed=False, error="temporary disk lock")
        self.gui.post_player_round(0, "round-02", 1)
        self.assertTrue(self.gui._player_validation_failed)
        self.gui.player_start_connection()
        self.assertEqual(self.gui._player_connection_generation, 0)
        self.assertTrue(self.gui.player_connection_active)
        self.assertTrue(self.gui.is_validating)
        self.assertEqual(self.gui._player_validation_token, (0, "round-02", 1))
        self.backend.fetch_room_status.assert_not_called()
        self.gui.follower.reset_to_end.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()
        self.assertEqual(len(gui_fixture.DeferredThread.created), 1)
        self.backend.validate_finalized_capture.return_value = types.SimpleNamespace(
            passed=True, target="actual-capture/round-02", error="", cancelled=False)
        gui_fixture.DeferredThread.created[0].run()
        self.assertFalse(self.gui._player_validation_failed)
        self.assertIsNone(self.gui._player_failed_audit)
        self.gui.auto_ready_player_next_round.assert_called_once()

    def test_unsuccessful_recheck_still_blocks_ready(self):
        self.player(passed=False, error="invalid capture")
        self.gui.post_player_round(0, "round-02", 1)
        self.gui.player_start_connection()
        gui_fixture.DeferredThread.created[0].run()
        self.assertTrue(self.gui._player_validation_failed)
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.assertEqual(self.gui._player_connection_generation, 0)
        self.backend.cleanup_all_odx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
