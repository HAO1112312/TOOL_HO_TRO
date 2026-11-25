"""Check delivered keyboard bindings even when native validation succeeds.

All captures are disposable fixtures. No native recorder, room, keyboard hook,
configuration or real capture data is opened or changed by these tests.
"""
import contextlib
import csv
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import odx_validation as validation


class InputIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="odx-input-integrity-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "bin").mkdir()
        (self.root / "bin" / "odx.exe").write_bytes(b"fixture, never executed")
        self.capture = self.root / "data" / "captures" / "round-01"
        (self.capture / "raw").mkdir(parents=True)
        for name in validation.REQUIRED_FILES + ("events.jsonl",):
            (self.capture / name).write_text("complete fixture\n", encoding="utf-8")
        self.bindings(W="Forward", Shift="Sprint", Ctrl="Crouch", NumEnter="Third person", Left="Fire")
        self.raw((10, "key_down", "W", 0, 0), (11, "key_up", "W", 0, 0))
        self.frames()

    def bindings(self, **bindings):
        (self.capture / "key_bindings.json").write_text(
            json.dumps({"bindings": bindings}), encoding="utf-8")

    def raw(self, *rows):
        with (self.capture / "raw" / "input_events.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("t_qpc_ms", "kind", "value", "dx", "dy"))
            writer.writerows(rows)

    def frames(self, keys="W", actions="Forward", dx=0, dy=0):
        with (self.capture / "frames.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("frame_id", "input_keys", "input_actions", "input_mouse_buttons",
                             "input_mouse_dx", "input_mouse_dy"))
            writer.writerow((0, keys, actions, "null", dx, dy))

    def video_scope(self, grids=(100, 200), delivered_count=None):
        """Describe finalized delivered frames, retaining any raw encoder tail."""
        count = len(grids) if delivered_count is None else delivered_count
        (self.capture / "session.json").write_text(
            json.dumps({"frame_count": count, "fps": 30}), encoding="utf-8")
        with (self.capture / "raw" / "video_frames.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("frame_index", "t_grid_qpc_ms", "t_source_qpc_ms", "duplicate"))
            writer.writerows((index, grid, grid, 0) for index, grid in enumerate(grids))
        with (self.capture / "frames.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("frame_id", "input_keys", "input_actions", "input_mouse_buttons",
                             "input_mouse_dx", "input_mouse_dy"))
            writer.writerows((index, "W", "Forward", "null", 0, 0) for index in range(count))

    def inspect(self):
        passed, problems = validation.inspect_input_data(self.capture)
        return passed, "\n".join(problems)

    def reject(self, *details):
        passed, problems = self.inspect()
        self.assertFalse(passed, "Invalid input must not pass the local audit")
        for detail in details:
            self.assertIn(detail, problems)
        return problems

    def audit_with_native_success(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({str(self.capture): []}), "")
        output = io.StringIO()
        with patch.object(validation.subprocess, "run", return_value=result), contextlib.redirect_stdout(output):
            passed = validation.validate_and_inspect_capture(self.capture, odx_root=self.root)
        return passed, output.getvalue()

    def test_native_named_bound_keys_are_valid(self):
        self.raw((10, "key_down", "W", 0, 0), (10, "key_down", "Shift", 0, 0),
                 (11, "key_up", "Ctrl", 0, 0), (12, "key_down", "NumEnter", 0, 0))
        self.frames("W|Shift|Ctrl|NumEnter", "Forward|Sprint|Crouch|Third person")
        self.assertEqual(self.inspect(), (True, ""))
        self.assertTrue(self.audit_with_native_success()[0])

    def test_native_binding_lookup_is_case_sensitive(self):
        for bindings, raw_key in (({"w": "Forward"}, "W"), ({"W": "Forward"}, "w"),
                                  ({"shift": "Sprint"}, "Shift")):
            with self.subTest(bindings=bindings, raw_key=raw_key):
                self.bindings(**bindings)
                self.frames("null", "null")
                self.raw((10, "key_down", raw_key, 0, 0))
                self.reject("input_events.csv", raw_key)
        self.bindings(W="Forward")
        self.raw((10, "key_down", "W", 0, 0))
        self.frames("w", "Forward")
        self.reject("frames.csv", "w")

    def test_unbound_raw_keyboard_key_reports_file_key_and_row(self):
        self.raw((10, "key_down", "UNBOUND_KEY", 0, 0))
        problems = self.reject("input_events.csv", "UNBOUND_KEY")
        self.assertRegex(problems, r"(?:row|line|dòng)\s+2")

    def test_unbound_key_up_is_checked_even_without_an_in_file_key_down(self):
        self.raw((10, "key_up", "UNBOUND_KEY", 0, 0))
        self.reject("input_events.csv", "UNBOUND_KEY")

    def test_bound_key_action_does_not_mask_an_unbound_key_in_the_same_frame(self):
        self.frames("W|UNBOUND_KEY", "Forward")
        problems = self.reject("frames.csv", "UNBOUND_KEY")
        self.assertRegex(problems, r"(?:row|line|dòng)\s+2")

    def test_keyboard_arrow_is_not_satisfied_by_mouse_left_binding(self):
        self.raw((10, "key_down", "ArrowLeft", 0, 0))
        self.reject("ArrowLeft")
        self.bindings(W="Forward", ArrowLeft="Turn left", Left="Fire")
        self.assertEqual(self.inspect(), (True, ""))

    def test_control_binding_alias_does_not_authorize_native_ctrl_input(self):
        self.bindings(W="Forward", Control="Crouch")
        self.video_scope()
        self.raw((110, "key_down", "Ctrl", 0, 0))
        self.reject("input_events.csv", "Ctrl")

    def test_unknown_virtual_key_requires_an_explicit_binding(self):
        self.raw((10, "key_down", "VK_FE", 0, 0))
        self.reject("VK_FE")
        self.bindings(W="Forward", VK_FE="Custom action")
        self.assertEqual(self.inspect(), (True, ""))

    def test_missing_keyboard_name_never_counts_as_valid_input(self):
        self.raw((10, "key_down", "", 0, 0))
        self.reject("input_events.csv")

    def test_binding_actions_must_be_nonempty_strings(self):
        for action in ("", "   ", None, 123, [], {}):
            with self.subTest(action=action):
                self.bindings(W=action)
                self.reject("key_bindings.json")

    def test_native_success_does_not_override_unbound_keyboard_input(self):
        self.raw((10, "key_down", "UNBOUND_KEY", 0, 0))
        passed, output = self.audit_with_native_success()
        self.assertFalse(passed)
        self.assertIn("UNBOUND_KEY", output)
        self.assertIn("FAIL", output)

    def test_failed_and_successful_audits_leave_every_capture_byte_unchanged(self):
        for raw_key in ("W", "UNBOUND_KEY"):
            with self.subTest(raw_key=raw_key):
                self.raw((10, "key_down", raw_key, 0, 0))
                before = {path.relative_to(self.capture): path.read_bytes()
                          for path in self.capture.rglob("*") if path.is_file()}
                self.inspect()
                passed, _ = self.audit_with_native_success()
                self.assertEqual(passed, raw_key == "W")
                after = {path.relative_to(self.capture): path.read_bytes()
                         for path in self.capture.rglob("*") if path.is_file()}
                self.assertEqual(before, after)

    def test_unknown_key_tapped_before_video_is_not_delivered_input(self):
        self.video_scope()
        self.raw((10, "key_down", "UNBOUND_KEY", 0, 0), (20, "key_up", "UNBOUND_KEY", 0, 0),
                 (110, "key_down", "W", 0, 0), (120, "key_up", "W", 0, 0))
        self.assertEqual(self.inspect(), (True, ""))

    def test_unknown_key_held_across_video_start_still_fails(self):
        self.video_scope()
        self.raw((10, "key_down", "UNBOUND_KEY", 0, 0), (150, "key_up", "UNBOUND_KEY", 0, 0))
        self.reject("input_events.csv", "UNBOUND_KEY")

    def test_f10_after_delivered_video_end_is_not_a_recorded_game_key(self):
        self.video_scope()
        self.raw((110, "key_down", "W", 0, 0), (120, "key_up", "W", 0, 0),
                 (201, "key_down", "F10", 0, 0), (202, "key_up", "F10", 0, 0))
        self.assertEqual(self.inspect(), (True, ""))
        self.assertTrue(self.audit_with_native_success()[0])

    def test_f10_inside_video_and_on_final_grid_is_not_whitelisted(self):
        self.video_scope()
        for timestamp in (150, 200):
            with self.subTest(timestamp=timestamp):
                self.raw((timestamp, "key_down", "F10", 0, 0))
                self.reject("input_events.csv", "F10")

    def test_raw_video_tail_beyond_delivered_frame_count_does_not_extend_scope(self):
        self.video_scope(grids=(100, 200, 300), delivered_count=2)
        self.raw((110, "key_down", "W", 0, 0), (250, "key_down", "UNBOUND_KEY", 0, 0))
        self.assertEqual(self.inspect(), (True, ""))

    def test_session_count_mismatch_cannot_shrink_scope_and_hide_a_recorded_key(self):
        self.video_scope()
        (self.capture / "session.json").write_text(
            json.dumps({"frame_count": 1, "fps": 30}), encoding="utf-8")
        self.raw((150, "key_down", "UNBOUND_KEY", 0, 0))
        self.reject("input_events.csv", "UNBOUND_KEY")

    def test_missing_or_broken_scope_cannot_hide_unknown_raw_keys(self):
        for corruption in ("missing", "bad_header", "bad_time", "wrong_index", "insufficient_frames", "bad_session"):
            with self.subTest(corruption=corruption):
                self.video_scope()
                metadata = self.capture / "raw" / "video_frames.csv"
                if corruption == "missing":
                    metadata.unlink()
                elif corruption == "bad_header":
                    metadata.write_text("other,column\n1,2\n", encoding="utf-8")
                elif corruption == "bad_time":
                    metadata.write_text(
                        "frame_index,t_grid_qpc_ms,t_source_qpc_ms,duplicate\n0,NaN,100,0\n1,200,200,0\n",
                        encoding="utf-8")
                elif corruption == "wrong_index":
                    metadata.write_text(
                        "frame_index,t_grid_qpc_ms,t_source_qpc_ms,duplicate\n10,100,100,0\n11,200,200,0\n",
                        encoding="utf-8")
                elif corruption == "insufficient_frames":
                    metadata.write_text(
                        "frame_index,t_grid_qpc_ms,t_source_qpc_ms,duplicate\n0,100,100,0\n", encoding="utf-8")
                else:
                    (self.capture / "session.json").write_text('{"frame_count":', encoding="utf-8")
                self.raw((250, "key_down", "UNBOUND_KEY", 0, 0))
                problems = self.reject("input_events.csv", "UNBOUND_KEY")
                self.assertRegex(problems, r"(?:row|line|dòng)\s+2")


if __name__ == "__main__":
    unittest.main()
