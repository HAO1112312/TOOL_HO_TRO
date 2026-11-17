import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import odx_autopilot
import odx_check_pass
import odx_validation as validation


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="odx_validation_test_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "bin").mkdir()
        (self.root / "bin" / "odx.exe").write_bytes(b"test fixture; never executed")
        self.capture = self.root / "data" / "captures" / "task01"
        (self.capture / "raw").mkdir(parents=True)
        for filename in validation.REQUIRED_FILES + ("events.jsonl",):
            (self.capture / filename).write_text("fixture", encoding="utf-8")
        (self.capture / "key_bindings.json").write_text('{"bindings":{"W":"Forward","Left":"Fire"}}', encoding="utf-8")
        (self.capture / "raw" / "input_events.csv").write_text(
            "t_qpc_ms,kind,value,dx,dy\n0,key_down,W,0,0\n1,button_down,Left,0,0\n", encoding="utf-8")
        (self.capture / "frames.csv").write_text("frame_id,input_mouse_buttons\n0,Left\n", encoding="utf-8")

    def audit(self, issues=None, returncode=0, stdout=None, stderr="", on_message=None):
        result = subprocess.CompletedProcess(
            [], returncode, stdout if stdout is not None else json.dumps({str(self.capture): issues or []}), stderr)
        output = io.StringIO()
        with mock.patch.object(validation.subprocess, "run", return_value=result) as run:
            with contextlib.redirect_stdout(output):
                passed = validation.validate_and_inspect_capture(
                    self.capture, odx_root=self.root, on_message=on_message)
        self.assertIn("--json", run.call_args.args[0])
        return passed, output.getvalue()

    def test_success_requires_native_success_and_all_artifacts(self):
        self.assertTrue(self.audit()[0])
        (self.capture / "video.mp4").unlink()
        passed, output = self.audit()
        self.assertFalse(passed)
        self.assertIn("video.mp4", output)

    def test_nonzero_exit_cannot_pass_even_without_reported_errors(self):
        self.assertFalse(self.audit(returncode=1)[0])

    def test_explicit_native_error_cannot_pass_even_with_zero_exit(self):
        self.assertFalse(self.audit([{"severity": "error", "rule": "video.bad", "message": "bad data"}])[0])

    def test_invalid_or_unrelated_report_fails_closed(self):
        for stdout in ("PASS (0 errors, 0 warnings)", "{}", "[]", '{"other": []}'):
            with self.subTest(stdout=stdout):
                self.assertFalse(self.audit(stdout=stdout)[0])

    def test_unknown_issue_severity_and_stderr_fail_closed(self):
        self.assertFalse(self.audit([{"severity": "unexpected", "message": "data"}])[0])
        self.assertFalse(self.audit(stderr="FAIL: native error")[0])

    def test_warning_count_is_reported_accurately(self):
        issues = [{"severity": "warning", "rule": "camera.sparse_nulls", "message": "sparse"}] * 7
        passed, output = self.audit(issues)
        self.assertTrue(passed)
        self.assertIn("0 errors, 7 warnings", output)
        self.assertNotIn("0 errors, 0 warnings", output)

    def test_callback_receives_native_error_details_and_keeps_cli_output(self):
        messages = []
        passed, output = self.audit(
            [{"severity": "error", "rule": "camera.missing", "message": "No camera telemetry for frame 7"}],
            returncode=1, on_message=messages.append)
        self.assertFalse(passed)
        detail = "  [ERROR] camera.missing: No camera telemetry for frame 7"
        self.assertIn(detail, messages)
        self.assertIn(detail, output)
        self.assertTrue(any("1 errors, 0 warnings" in line for line in messages))

    def test_callback_failure_cannot_change_validation_result(self):
        def unavailable_display(line):
            raise RuntimeError("window already closed")
        self.assertTrue(self.audit(on_message=unavailable_display)[0])

    def test_audit_never_repairs_camera_or_creates_backup(self):
        path = self.capture / "frames.csv"
        original = b"frame_id,input_mouse_buttons,camera_c2w_matrix_0\n0,Left,null\n1,null,1\n"
        path.write_bytes(original)
        self.assertTrue(self.audit()[0])
        self.assertEqual(original, path.read_bytes())
        self.assertFalse(path.with_name(path.name + ".before-heal.bak").exists())

    def test_malformed_input_schema_fails_instead_of_passing_mouse_role(self):
        (self.capture / "raw" / "input_events.csv").write_text("other,column\n1,2\n", encoding="utf-8")
        self.assertFalse(self.audit()[0])

    def test_forbidden_buttons_detected_in_frames_and_raw_input(self):
        (self.capture / "frames.csv").write_text("frame_id,input_mouse_buttons\n0,XButton1\n", encoding="utf-8")
        self.assertFalse(self.audit()[0])
        (self.capture / "frames.csv").write_text("frame_id,input_mouse_buttons\n0,null\n", encoding="utf-8")
        (self.capture / "raw" / "input_events.csv").write_text("kind,value\nbutton_down,XButton2\n", encoding="utf-8")
        self.assertFalse(self.audit()[0])

    def test_wheel_delta_is_not_mistaken_for_a_button_name(self):
        (self.capture / "raw" / "input_events.csv").write_text("kind,value\nmouse_wheel,-120\n", encoding="utf-8")
        self.assertTrue(self.audit()[0])

    def test_button_lists_support_json_and_delimiters(self):
        self.assertEqual(["Left", "Right"], validation._button_values('["Left","Right"]'))
        self.assertEqual(["Left", "Right", "Middle"], validation._button_values("Left;Right|Middle"))
        for name in ("MouseLeft", "mouse_right", "MiddleButton"):
            self.assertTrue(validation._button_allowed(name))

    def test_explicit_heal_rejects_entirely_missing_column_and_nonfinite_values(self):
        path = self.capture / "frames.csv"
        for values in ("camera_c2w_matrix_0,camera_c2w_matrix_1\n1,null\nnull,null\n",
                       "camera_c2w_matrix_0\nNaN\nnull\n",
                       "camera_c2w_matrix_0\nInfinity\nnull\n"):
            with self.subTest(values=values):
                path.write_text(values, encoding="utf-8")
                original = path.read_bytes()
                self.assertFalse(validation.heal_camera_nulls_if_needed(self.capture))
                self.assertEqual(original, path.read_bytes())
                self.assertFalse(path.with_name(path.name + ".before-heal.bak").exists())

    def test_explicit_heal_keeps_original_backup_and_untouched_binary(self):
        path = self.capture / "frames.csv"
        original = b"camera_c2w_matrix_0,other\nnull,001\n1.25,002\nnull,003\n"
        path.write_bytes(original)
        binary = (self.capture / "frames_bin.bin").read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(validation.heal_camera_nulls_if_needed(self.capture))
        self.assertEqual(original, path.with_name(path.name + ".before-heal.bak").read_bytes())
        self.assertIn("1.25,001", path.read_text(encoding="utf-8"))
        self.assertEqual(binary, (self.capture / "frames_bin.bin").read_bytes())

    def test_heal_refuses_to_overwrite_existing_backup(self):
        path = self.capture / "frames.csv"
        original = b"camera_c2w_matrix_0\n1\nnull\n"
        path.write_bytes(original)
        backup = path.with_name(path.name + ".before-heal.bak")
        backup.write_bytes(b"older original")
        self.assertFalse(validation.heal_camera_nulls_if_needed(self.capture))
        self.assertEqual(original, path.read_bytes())
        self.assertEqual(b"older original", backup.read_bytes())

    def test_failed_atomic_replace_preserves_original(self):
        path = self.capture / "frames.csv"
        original = b"camera_c2w_matrix_0\n1\nnull\n"
        path.write_bytes(original)
        with mock.patch.object(validation.os, "replace", side_effect=OSError("locked capture")):
            self.assertFalse(validation.heal_camera_nulls_if_needed(self.capture))
        self.assertEqual(original, path.read_bytes())
        self.assertEqual([], list(self.capture.glob(".frames-heal-*.tmp")))

    def test_checker_default_scan_and_exit_codes_use_shared_root(self):
        with mock.patch.object(odx_check_pass, "resolve_odx_root", return_value=str(self.root)):
            with mock.patch.object(odx_check_pass, "validate_folder", return_value=True) as validate:
                self.assertEqual(0, odx_check_pass.main([]))
                validate.assert_called_once_with(self.capture, self.root)
            with mock.patch.object(odx_check_pass, "validate_folder", return_value=False):
                self.assertEqual(1, odx_check_pass.main(["task01"]))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2, odx_check_pass.main(["missing-task"]))


class CompatibilityLauncherTests(unittest.TestCase):
    def test_legacy_aliases_route_to_the_maintained_gui(self):
        for argument, expected in (("--host", "host"), ("master", "host"), ("--server", "host"),
                                   ("--player", "player"), ("client", "player"), ("--p", "player")):
            with self.subTest(argument=argument):
                with mock.patch.object(odx_autopilot, "_launch") as launch:
                    odx_autopilot.main([argument])
                    launch.assert_called_once_with(expected)

    def test_no_role_routes_to_unified_gui(self):
        with mock.patch.object(odx_autopilot, "_launch") as launch:
            odx_autopilot.main([])
            launch.assert_called_once_with(None)

    def test_gui_arguments_are_restored_without_starting_real_gui(self):
        import sys
        before = sys.argv
        seen = []
        fake_gui = types.SimpleNamespace(main=lambda: seen.append(list(sys.argv)))
        with mock.patch.dict(sys.modules, {"odx_gui": fake_gui}):
            odx_autopilot.run_player()
        self.assertEqual("--player", seen[0][1])
        self.assertIs(before, sys.argv)


if __name__ == "__main__":
    unittest.main()
