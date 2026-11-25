"""Team completion needs every Player's local audit, not only native ALL OK.

GUI state and time are simulated. No real network, Odx process, room, recording
or keyboard hook is used; the fixture keeps file writes inside a temporary root.
"""
import copy
import types
import unittest
from unittest.mock import Mock

if __package__:
    from . import test_gui as fixture
else:
    import test_gui as fixture


class GuiAuditSyncTests(unittest.TestCase):
    setUp = fixture.GuiTests.setUp
    task = "round-02"
    nonce = "a" * 32
    instance = "b" * 32

    def clock(self):
        self.now, self.on_sleep = 0.0, None

        def sleep(seconds):
            self.now += seconds
            if self.on_sleep is not None:
                self.on_sleep()

        self.ns["time"] = types.SimpleNamespace(monotonic=lambda: self.now, sleep=sleep)

    def host(self, reports=None):
        self.clock()
        self.gui.is_validating = True
        self.gui._sync_required = True
        self.gui.room_code = "ABC123"
        self.gui._round_audit_task = self.task
        self.gui._round_audit_nonce = self.nonce
        self.gui._round_audit_players = {"p1": "Alpha", "p2": "Bravo", "p3": "Charlie"}
        self.gui._sync_start_task = "next-task-03"
        self.gui._sync_start_nonce = "c" * 32
        self.server = Mock(room_code="ABC123", host_id="host")
        self.server.audit_results.return_value = {} if reports is None else reports
        self.gui._sync_server = self.server
        self.gui.follower.last_round_result = (self.task, "all ok")
        self.backend.validate_finalized_capture = Mock(return_value=types.SimpleNamespace(
            passed=True, target="actual-capture/" + self.task, error="", cancelled=False))
        self.gui.finish_validation = Mock(wraps=self.gui.finish_validation)
        self.gui.record_task_completion = Mock(return_value=("recorded", None))
        self.gui.batch_complete = Mock(return_value=False)
        self.gui.cooldown_tick = Mock()
        self.progress_before = copy.deepcopy(self.gui.progress)

    @staticmethod
    def passing_players():
        return {player: dict(passed=True, error="") for player in ("p1", "p2", "p3")}

    def finish_verdict(self):
        args = self.gui.finish_validation.call_args.args
        self.assertEqual(args[:2], (0, self.task))
        return args[2:5]

    def test_player_local_failure_blocks_host_completion_despite_native_all_ok(self):
        reports = self.passing_players()
        reports["p2"] = dict(passed=False, error="raw/input_events.csv row 19: UNBOUND_KEY")
        self.host(reports)
        self.gui.run_post_capture_validation(self.task, 0)
        passed, native_all_ok, error = self.finish_verdict()
        self.assertFalse(passed)
        self.assertTrue(native_all_ok)
        self.assertIn("Bravo", error)
        self.assertIn("UNBOUND_KEY", error)
        self.gui.record_task_completion.assert_not_called()
        self.assertEqual(self.gui.progress, self.progress_before)
        self.assertNotIn(self.task, self.gui.announced_all_ok)
        self.backend.cleanup_all_odx.assert_not_called()

    def test_host_waits_for_all_player_reports_for_the_saved_round_nonce(self):
        self.host()
        observed = []

        def reports_arrive():
            observed.append(self.gui.record_task_completion.call_count)
            self.server.audit_results.return_value = self.passing_players()

        self.on_sleep = reports_arrive
        self.gui.run_post_capture_validation(self.task, 0)
        self.assertTrue(observed, "Host must wait instead of using native ALL OK alone")
        self.assertTrue(all(count == 0 for count in observed))
        self.assertEqual(self.finish_verdict(), (True, True, None))
        self.gui.record_task_completion.assert_called_once_with(self.task)
        self.assertTrue(self.server.audit_results.call_args_list)
        for call in self.server.audit_results.call_args_list:
            self.assertEqual(call.args, (self.task, self.nonce))

    def test_missing_player_report_times_out_without_claiming_progress(self):
        self.host({"p1": dict(passed=True, error="")})
        self.gui.run_post_capture_validation(self.task, 0)
        passed, native_all_ok, error = self.finish_verdict()
        self.assertFalse(passed)
        self.assertTrue(native_all_ok)
        self.assertIn("Bravo", error)
        self.gui.record_task_completion.assert_not_called()
        self.assertEqual(self.gui.progress, self.progress_before)
        self.assertLessEqual(self.server.audit_results.call_count, 605)

    def test_disconnected_player_cannot_disappear_from_the_round_audit_requirement(self):
        self.host({"p1": dict(passed=True, error=""), "p3": dict(passed=True, error="")})
        self.gui.tracker.latest_data["members"] = [member for member in self.gui.tracker.latest_data["members"]
                                                    if member.get("player_id") != "p2"]
        self.gui.run_post_capture_validation(self.task, 0)
        passed, native_all_ok, error = self.finish_verdict()
        self.assertFalse(passed)
        self.assertTrue(native_all_ok)
        self.assertIn("Bravo", error)
        self.gui.record_task_completion.assert_not_called()

    def player(self, acknowledged=True):
        self.clock()
        self.gui.current_mode = self.gui.view_mode = "PLAYER"
        self.gui._sync_required = True
        self.gui.player_connection_active = True
        self.gui.room_code = "ABC123"
        self.gui.config["player_id"] = "p1"
        self.gui._player_round_token = 1
        self.gui._player_validation_token = (0, self.task, 1)
        self.gui.is_validating = True
        self.gui._player_ready_after_round = None
        self.gui._shared_audit_rounds = {}
        self.gui.follower.last_round_result = (self.task, "all ok")
        self.gui.auto_ready_player_next_round = Mock()
        self.client = Mock(instance_id=self.instance, last_error=None, last_audit_error="Host not reachable")
        self.client.report_audit.return_value = acknowledged
        self.gui._sync_client = self.client
        self.gui.remember_shared_audit_round(
            dict(task_id=self.task, start_request_id=self.nonce, phase="recording"), self.client, self.instance)
        # A newer settings snapshot must not retarget the completed round report.
        self.gui._shared_settings = dict(task_id="next-task-03", start_request_id="c" * 32)

    def run_report_worker(self):
        self.assertTrue(fixture.DeferredThread.created, "Audit reporting must run off the UI thread")
        fixture.DeferredThread.created[-1].run()

    def test_player_waits_for_report_ack_before_ready_and_uses_cached_task_nonce(self):
        self.player()
        self.gui.finish_player_validation(0, self.task, 1, True)
        self.assertTrue(self.gui.is_validating)
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.client.report_audit.assert_not_called()
        self.run_report_worker()
        self.client.report_audit.assert_called_once()
        call = self.client.report_audit.call_args
        self.assertEqual(call.args[:4], (self.task, self.nonce, "p1", True))
        self.assertFalse(self.gui.is_validating)
        self.gui.auto_ready_player_next_round.assert_called_once()

    def test_player_reports_local_key_failure_and_never_readies(self):
        self.player()
        reason = "raw/input_events.csv row 19: UNBOUND_KEY"
        self.gui.finish_player_validation(0, self.task, 1, False, error=reason)
        self.run_report_worker()
        self.client.report_audit.assert_called_once()
        call = self.client.report_audit.call_args
        self.assertEqual(call.args[:4], (self.task, self.nonce, "p1", False))
        self.assertIn(reason, str(call))
        self.assertTrue(self.gui._player_validation_failed)
        self.assertIsNone(self.gui._player_ready_after_round)
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()

    def test_unacknowledged_player_pass_cannot_enable_ready(self):
        self.player(acknowledged=False)
        self.gui.finish_player_validation(0, self.task, 1, True)
        self.run_report_worker()
        self.assertGreater(self.client.report_audit.call_count, 1)
        self.assertLessEqual(self.client.report_audit.call_count, 305)
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.assertIsNone(self.gui._player_ready_after_round)

    def test_replaced_sync_client_cannot_receive_an_old_round_report(self):
        self.player()
        self.gui.finish_player_validation(0, self.task, 1, True)
        replacement = Mock(instance_id="d" * 32)
        self.gui._sync_client = replacement
        self.run_report_worker()
        self.client.report_audit.assert_not_called()
        replacement.report_audit.assert_not_called()
        self.gui.auto_ready_player_next_round.assert_not_called()

    def test_stale_connection_generation_cannot_publish_or_ready_old_audit(self):
        self.player()
        self.gui.finish_player_validation(0, self.task, 1, True)
        self.gui._player_connection_generation += 1
        self.run_report_worker()
        self.client.report_audit.assert_not_called()
        self.gui.auto_ready_player_next_round.assert_not_called()
        self.assertIsNone(self.gui._player_ready_after_round)
        self.backend.cleanup_all_odx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
