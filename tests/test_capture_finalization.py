"""Post-round audits must follow finalized local data, even on a slow VM.

Only temporary files and a deterministic clock are used. Native processes,
recorders, keyboard hooks, room requests and the real capture tree are untouched.
"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import odx_validation as validation


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.events = []

    def monotonic(self):
        return self.now

    def later(self, when, callback):
        self.events.append((when, callback))
        self.events.sort(key=lambda event: event[0])

    def sleep(self, seconds):
        destination = round(self.now + seconds, 9)
        while self.events and self.events[0][0] <= destination:
            when, callback = self.events.pop(0)
            self.now = when
            callback()
        self.now = destination


class CaptureFinalizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="odx_finalization_test_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = "round-02"
        self.target = self.root / "odx" / "data" / "captures" / self.task
        self.odx_root = self.root / "odx"
        self.clock = FakeClock()
        self.messages = []
        self.follower = types.SimpleNamespace(
            check_new_lines=mock.Mock(), current_stopped=(self.task, "round_end"),
            current_recording=None, last_armed=None,
            last_round_result=(self.task, "ok"), last_all_ok=None,
            last_kept_path=str(self.target),
        )
        self.audit_times = []
        self.audit_effect = lambda *args, **kwargs: True
        self.validator = mock.Mock(side_effect=self.perform_audit)

    def perform_audit(self, *args, **kwargs):
        self.audit_times.append(self.clock.monotonic())
        return self.audit_effect(*args, **kwargs)

    def populate(self, target=None):
        target = target or self.target
        (target / "raw").mkdir(parents=True, exist_ok=True)
        for name in validation.REQUIRED_FILES + ("events.jsonl",):
            (target / name).write_bytes(b"complete fixture\n")
        return target

    def wait(self, *, timeout=30, can_continue=lambda: True):
        with mock.patch.object(validation, "time", self.clock):
            with mock.patch.object(validation, "validate_and_inspect_capture", self.validator):
                return validation.validate_finalized_capture(
                    self.task, self.follower, odx_root=self.odx_root,
                    timeout=timeout, should_continue=can_continue,
                    on_message=self.messages.append,
                )

    def test_complete_files_wait_for_stability_before_auditing(self):
        self.populate()
        result = self.wait()
        self.assertTrue(result.passed)
        self.validator.assert_called_once()
        self.assertGreaterEqual(self.audit_times[0], 1.0)
        self.assertEqual(Path(result.target), self.target)

    def test_artifacts_arriving_after_fifteen_seconds_are_still_audited(self):
        self.clock.later(20, self.populate)
        result = self.wait(timeout=40)
        self.assertTrue(result.passed)
        self.assertGreaterEqual(self.audit_times[0], 21)
        self.assertGreater(self.follower.check_new_lines.call_count, 1)

    def test_events_file_alone_does_not_trigger_a_partial_audit(self):
        self.target.mkdir(parents=True)
        (self.target / "events.jsonl").write_bytes(b"{}\n")
        self.clock.later(18, self.populate)
        result = self.wait()
        self.assertTrue(result.passed)
        self.validator.assert_called_once()
        self.assertGreaterEqual(self.audit_times[0], 19)

    def test_zero_length_artifact_waits_until_data_is_written(self):
        self.populate()
        video = self.target / "video.mp4"
        video.write_bytes(b"")
        self.clock.later(5, lambda: video.write_bytes(b"finalized video"))
        self.assertTrue(self.wait().passed)
        self.assertGreaterEqual(self.audit_times[0], 6)

    def test_continuously_growing_file_must_settle(self):
        self.populate()
        video = self.target / "video.mp4"
        for number in range(1, 9):
            self.clock.later(number * 0.4, lambda number=number: video.write_bytes(b"video" * number))
        self.assertTrue(self.wait().passed)
        self.validator.assert_called_once()
        self.assertGreaterEqual(self.audit_times[0], 4.2)

    def test_stop_without_local_save_result_does_not_audit_existing_files(self):
        self.populate()
        self.follower.last_round_result = None
        result = self.wait(timeout=3)
        self.assertFalse(result.passed)
        self.validator.assert_not_called()
        self.assertTrue(result.error)

    def test_late_matching_saved_result_releases_wait(self):
        self.populate()
        self.follower.last_round_result = ("round-01", "all ok")
        self.clock.later(18, lambda: setattr(self.follower, "last_round_result", (self.task, "ok")))
        self.assertTrue(self.wait().passed)
        self.assertGreaterEqual(self.audit_times[0], 18)

    def test_unrelated_round_result_cannot_authorize_an_audit(self):
        self.populate()
        self.follower.last_round_result = ("another-task", "all ok")
        self.follower.last_all_ok = "another-task"
        result = self.wait(timeout=3)
        self.assertFalse(result.passed)
        self.validator.assert_not_called()

    def test_actual_saved_path_is_preferred_when_installation_was_moved(self):
        self.populate()
        actual = self.populate(self.root / "moved-installation" / self.task)
        self.follower.last_kept_path = str(actual)
        result = self.wait()
        self.assertTrue(result.passed)
        self.assertEqual(Path(result.target), actual)
        self.assertEqual(Path(self.validator.call_args.args[0]), actual)

    def test_unrelated_kept_path_is_not_audited(self):
        other = self.populate(self.root / "captures" / "unrelated-task")
        self.follower.last_kept_path = str(other)
        result = self.wait(timeout=3)
        self.assertFalse(result.passed)
        self.validator.assert_not_called()
        self.assertNotEqual(Path(result.target), other)

    def test_unrelated_kept_path_does_not_replace_valid_default_target(self):
        self.populate()
        other = self.populate(self.root / "captures" / "unrelated-task")
        self.follower.last_kept_path = str(other)
        result = self.wait()
        self.assertTrue(result.passed)
        self.assertEqual(Path(self.validator.call_args.args[0]), self.target)

    def test_matching_terminal_result_can_finalize_default_target(self):
        self.populate()
        self.follower.last_kept_path = None
        self.follower.last_round_result = (self.task, "all ok")
        self.assertTrue(self.wait().passed)

    def test_temporary_validator_lock_is_retried_without_file_changes(self):
        self.populate()
        self.audit_effect = mock.Mock(side_effect=[False, True])
        result = self.wait()
        self.assertTrue(result.passed)
        self.assertEqual(self.validator.call_count, 2)
        self.assertGreaterEqual(self.audit_times[1] - self.audit_times[0], 2)

    def test_saved_result_does_not_release_a_still_recording_native_task(self):
        self.populate()
        self.follower.current_recording = self.task
        self.clock.later(5, lambda: setattr(self.follower, "current_recording", None))
        self.assertTrue(self.wait().passed)
        self.assertGreaterEqual(self.audit_times[0], 6)

    def test_saved_result_does_not_release_a_still_armed_native_task(self):
        self.populate()
        self.follower.last_armed = self.task
        result = self.wait(timeout=3)
        self.assertFalse(result.passed)
        self.validator.assert_not_called()

    def test_cancellation_after_failed_attempt_does_not_start_another_audit(self):
        self.populate()
        allowed = [True]
        def failed_then_closed(*args, **kwargs):
            self.clock.later(self.clock.monotonic() + 0.5, lambda: allowed.__setitem__(0, False))
            return False
        self.audit_effect = failed_then_closed
        result = self.wait(can_continue=lambda: allowed[0])
        self.assertFalse(result.passed)
        self.validator.assert_called_once()

    def test_transient_validator_exception_remains_pending_then_recovers(self):
        self.populate()
        self.audit_effect = mock.Mock(side_effect=[OSError("sharing violation"), True])
        self.assertTrue(self.wait().passed)
        self.assertEqual(self.validator.call_count, 2)
        self.assertTrue(any("sharing violation" in line for line in self.messages))

    def test_success_during_file_mutation_is_discarded_and_rechecked(self):
        self.populate()
        def changing_first_audit(*args, **kwargs):
            if self.validator.call_count == 1:
                with open(self.target / "frames_bin.bin", "ab") as stream:
                    stream.write(b"last buffered frames")
            return True
        self.audit_effect = changing_first_audit
        result = self.wait()
        self.assertTrue(result.passed)
        self.assertEqual(self.validator.call_count, 2)
        self.assertGreaterEqual(self.audit_times[1] - self.audit_times[0], 1)

    def test_permanent_validation_failure_stays_failed_at_deadline(self):
        self.populate()
        self.audit_effect = lambda *args, **kwargs: False
        result = self.wait(timeout=8)
        self.assertFalse(result.passed)
        self.assertGreaterEqual(self.validator.call_count, 2)
        self.assertGreaterEqual(self.clock.monotonic(), 8)
        self.assertLess(self.clock.monotonic(), 9)
        self.assertTrue(result.error)
        self.assertIn(str(self.target), "\n".join(self.messages))

    def test_missing_artifact_timeout_reports_the_missing_name(self):
        self.populate()
        (self.target / "raw" / "input_events.csv").unlink()
        result = self.wait(timeout=3)
        self.assertFalse(result.passed)
        self.validator.assert_not_called()
        self.assertIn("input_events.csv", result.error + "\n".join(self.messages))

    def test_cancellation_before_wait_does_not_audit(self):
        self.populate()
        result = self.wait(can_continue=lambda: False)
        self.assertFalse(result.passed)
        self.validator.assert_not_called()
        self.assertLess(self.clock.monotonic(), 1)

    def test_cancellation_while_waiting_stops_without_failing_into_audit(self):
        allowed = [True]
        self.clock.later(2, lambda: allowed.__setitem__(0, False))
        result = self.wait(can_continue=lambda: allowed[0])
        self.assertFalse(result.passed)
        self.validator.assert_not_called()
        self.assertLess(self.clock.monotonic(), 3)

    def test_cancellation_during_successful_audit_never_returns_pass(self):
        self.populate()
        allowed = [True]
        def cancel_during_audit(*args, **kwargs):
            allowed[0] = False
            return True
        self.audit_effect = cancel_during_audit
        result = self.wait(can_continue=lambda: allowed[0])
        self.assertFalse(result.passed)
        self.validator.assert_called_once()

    def test_true_corruption_is_not_overridden_by_native_all_ok(self):
        self.populate()
        self.follower.last_all_ok = self.task
        self.follower.last_round_result = (self.task, "all ok")
        (self.target / "raw" / "input_events.csv").write_text("wrong,columns\n1,2\n", encoding="utf-8")
        (self.target / "frames.csv").write_text("frame_id,input_mouse_buttons\n0,null\n", encoding="utf-8")
        (self.target / "key_bindings.json").write_text('{"bindings":{"W":"Forward"}}', encoding="utf-8")
        (self.odx_root / "bin").mkdir()
        (self.odx_root / "bin" / "odx.exe").write_bytes(b"fixture, never executed")
        native_result = subprocess.CompletedProcess([], 0, json.dumps({str(self.target): []}), "")
        actual_validator = validation.validate_and_inspect_capture
        self.audit_effect = actual_validator
        with mock.patch.object(validation.subprocess, "run", return_value=native_result):
            with contextlib.redirect_stdout(io.StringIO()):
                result = self.wait(timeout=6)
        self.assertFalse(result.passed)
        self.assertGreaterEqual(self.validator.call_count, 2)
        self.assertIn("input_events.csv", "\n".join(self.messages))


if __name__ == "__main__":
    unittest.main()
