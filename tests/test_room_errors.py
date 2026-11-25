"""Native room failures remain visible after Odx has returned to its Ready UI."""
import json
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

import odx_backend as backend


class RoomErrorsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch_root = patch.object(backend, "ODX_ROOT", str(self.root))
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.log = self.root / "fixture.log"
        self.log.write_text("", encoding="utf-8")
        self.follower = backend.LiveLogFollower(str(self.log))

    def round_record(self, task="round-01", **player_fields):
        player = dict(player_id="p2", name="qhao", done=True, kept=True,
                      verdict="failed", problems=["last 398 frames show the frame before (over 3 s)"],
                      error="stop: the capture callback did not let go within 2s (in 'readback')")
        player.update(player_fields)
        return dict(session_id=task, end_utc_ms=123456, players=[player])

    def write_record(self, record, task="round-01"):
        path = self.root / "data" / "captures" / task / "raw" / "round.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
        return path

    def test_native_record_reports_identity_task_and_both_exact_failure_details(self):
        record = self.round_record()
        path = self.write_record(record)
        before = path.read_bytes()
        self.follower.parse_line("round round-01: RECORD AGAIN · qhao: failed")
        state, detail = self.follower.get_round_outcome("round-01")
        self.assertEqual(state, "failed")
        for value in ("qhao", "p2", "round-01", record["players"][0]["error"], record["players"][0]["problems"][0]):
            self.assertIn(value, detail)
        self.assertEqual(path.read_bytes(), before, "Diagnostics must never repair native evidence")
        self.assertEqual(self.follower.get_round_errors()[0]["player_id"], "p2")

    def test_record_arriving_after_summary_replaces_generic_failure_with_exact_details(self):
        self.follower.parse_line("round round-01: RECORD AGAIN · qhao: failed")
        self.assertEqual(self.follower.get_round_errors()[0]["name"], "Odx")
        path = self.write_record(self.round_record())
        path.write_text('{"session_id":', encoding="utf-8")
        self.assertEqual(self.follower.get_round_errors()[0]["name"], "Odx")
        path.write_text(json.dumps(self.round_record()), encoding="utf-8")
        self.assertEqual(self.follower.get_round_errors()[0]["name"], "qhao")

    def test_local_done_is_not_full_round_all_ok_and_substrings_never_pass(self):
        self.follower.parse_line("round-01 done: kept in C:/captures/round-01; ok")
        self.assertEqual(self.follower.get_round_outcome("round-01")[0], "pending")
        for result in ("not all ok", "all ok but failed", "saved, check · qhao: degraded"):
            with self.subTest(result=result):
                self.follower.parse_line("round round-01: " + result)
                self.assertEqual(self.follower.get_round_outcome("round-01")[0], "failed")
        self.follower.parse_line("round round-01: all ok")
        self.assertEqual(self.follower.get_round_outcome("round-01"), ("all_ok", ""))
        self.assertEqual(self.follower.get_round_outcome("different-task")[0], "pending")

    def test_exact_final_native_all_ok_record_can_recover_a_missed_log(self):
        self.write_record(self.round_record(verdict="ok", problems=[], error=None))
        self.assertEqual(self.follower.get_round_outcome("round-01")[0], "all_ok")
        self.assertEqual(self.follower.get_round_errors("round-01"), [])

    def test_record_failure_overrides_earlier_all_ok_and_degraded_also_blocks(self):
        self.follower.parse_line("round round-01: all ok")
        self.write_record(self.round_record(verdict="degraded"))
        self.assertEqual(self.follower.get_round_outcome("round-01")[0], "failed")
        self.assertEqual(self.follower.get_round_errors("round-01")[0]["severity"], "warning")

    def test_wrong_session_and_path_traversal_are_not_used(self):
        self.write_record(self.round_record(task="another-round"))
        self.assertEqual(self.follower.get_round_outcome("round-01")[0], "pending")
        with patch.object(backend.os, "stat") as stat:
            self.follower.get_round_outcome("../round-01")
        stat.assert_not_called()

    def test_historical_error_stays_after_ready_but_does_not_poison_new_task(self):
        self.write_record(self.round_record())
        self.follower.parse_line("round round-01: RECORD AGAIN · qhao: failed")
        self.assertTrue(self.follower.get_round_errors())
        self.follower.parse_line("armed for round-01-fix1")
        self.assertEqual(self.follower.get_round_errors(), [])
        self.assertTrue(self.follower.get_round_errors("round-01"))

    def test_current_problem_blocks_green_ready_even_if_ready_flag_true(self):
        members = [dict(player_id="h", name="Host", ready=True),
                   dict(player_id="p2", name="qhao", ready=True, problem="readback failed")]
        tracker = backend.RoomTracker("ABC123")
        tracker.latest_data = dict(host="h", members=members)
        tracker.last_success_at = time.monotonic()
        self.assertFalse(tracker.is_all_ready(2))
        self.assertIn("LOI: readback failed", backend.format_member_table("ABC123", "h", members, 2))
        self.assertIn("qhao(!): readback failed", backend.format_hud_roster("ABC123", "h", members, 2))
        members[1].pop("problem")
        self.assertTrue(tracker.is_all_ready(2))
        members[1]["state"] = "failed"
        self.assertFalse(tracker.is_all_ready(2))

    def test_current_and_historical_errors_deduplicate_and_recover_separately(self):
        error = backend.round_record_errors(self.round_record(), "round-01")[0]
        data = dict(members=[dict(player_id="p2", name="qhao", problem="network failed")],
                    _round_errors=[error, dict(error)])
        self.assertEqual(len(backend.room_member_errors(data)), 2)
        self.assertEqual(len(backend.room_member_errors(data, "new-task")), 1)
        data["members"][0]["problem"] = None
        self.assertEqual(len(backend.room_member_errors(data)), 1)
        self.assertEqual(backend.room_member_errors(data, "new-task"), [])

    def test_error_record_changes_trigger_callback_without_roster_change(self):
        error = backend.round_record_errors(self.round_record(), "round-01")[0]
        follower = types.SimpleNamespace(check_new_lines=Mock(), get_round_errors=Mock(side_effect=[[], [error], []]))
        overlay = types.SimpleNamespace(show_roster_hud=Mock())
        callback = Mock()
        tracker = backend.RoomTracker("ABC123", follower=follower, overlay=overlay, on_change_callback=callback)
        data = dict(host="h", members=[dict(player_id="h", name="Host", ready=True)])
        ticks = 0
        def fetch(_):
            nonlocal ticks
            ticks += 1
            if ticks == 4:
                tracker.is_running = False
            return data
        tracker.is_running = True
        with patch.object(backend, "fetch_room_status", side_effect=fetch), patch.object(backend.time, "sleep"), patch.object(backend.sys, "stdout"):
            tracker._run_loop(1.2)
        self.assertEqual(callback.call_count, 3)
        calls = overlay.show_roster_hud.call_args_list
        self.assertIn(error["message"], calls[1].args[0])
        self.assertEqual(calls[1].args[1], "#EF4444")
        self.assertNotIn("ODX BÁO LỖI", calls[2].args[0])

    def test_summary_cache_is_bounded(self):
        for index in range(160):
            self.follower.parse_line(f"round task-{index}: all ok")
        self.assertEqual(len(self.follower.round_results), 128)

    def test_console_code_page_failure_does_not_invalidate_room_callback(self):
        data = dict(host="h", members=[dict(player_id="h", name="Host", ready=True)])
        callback = Mock()
        tracker = backend.RoomTracker("ABC123", on_change_callback=callback)
        def fetch(_):
            tracker.is_running = False
            return data
        # The writer is independent of room error state, including old Windows
        # consoles without a checkmark in their active code page.
        stream = types.SimpleNamespace(encoding="cp1252", write=Mock(side_effect=[UnicodeEncodeError("cp1252", "✓", 0, 1, "unmappable"), None]), flush=Mock())
        with patch.object(backend.sys, "stdout", stream):
            tracker._write_console("✓")
        self.assertEqual(stream.write.call_args.args[0], "?")
        self.assertIsNone(tracker.last_error)


if __name__ == "__main__":
    unittest.main()
