"""Native game setup keeps identity/configuration intact and fails closed."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import odx_backend as backend
import odx_games as games


class GameLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="odx-game-config-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.native = self.folder / "Odx" / "odx-app.json"
        self.native.parent.mkdir()
        profiles = self.folder / "bin" / "games"
        profiles.mkdir(parents=True)
        for game, data in games.GAMES.items():
            (profiles / data["profile"]).write_text(json.dumps({"game_title": data["label"]}), encoding="utf-8")
        self.original = {"RecordAudio": False, "WindowProcess": "old-game", "GameProfile": "old.json",
                         "PlayerId": "player-42", "Name": "Local player", "Secret": "kept-private",
                         "OutputRoot": "D:/own-captures", "OtherOption": {"nested": True}}
        self.native.write_text(json.dumps(self.original), encoding="utf-8")
        for patcher in (patch.object(backend, "ODX_ROOT", str(self.folder)),
                        patch.object(backend, "find_odx_window", return_value=None),
                        patch.object(backend, "find_unmanaged_odx_window", return_value=None),
                        patch.object(games.os.path, "expandvars", return_value=str(self.native))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_both_game_profiles_preserve_local_identity_paths_and_unknown_fields(self):
        for game in games.GAMES:
            with self.subTest(game=game):
                success, error = games.prepare_game_capture_launch(game)
                self.assertTrue(success, error)
                actual = json.loads(self.native.read_text(encoding="utf-8-sig"))
                self.assertTrue(actual.pop("RecordAudio"))
                self.assertEqual(actual.pop("WindowProcess"), games.GAMES[game]["processes"][0][:-4])
                self.assertEqual(Path(actual.pop("GameProfile")).name, games.GAMES[game]["profile"])
                expected = {key: value for key, value in self.original.items()
                            if key not in {"RecordAudio", "WindowProcess", "GameProfile"}}
                self.assertEqual(actual, expected)

    def test_audio_only_keeps_desktop_source_and_profile(self):
        self.assertTrue(games.prepare_audio_capture_launch()[0])
        expected = dict(self.original, RecordAudio=True)
        self.assertEqual(json.loads(self.native.read_text(encoding="utf-8-sig")), expected)
        with patch.object(backend, "_write_capture_test_json") as write:
            self.assertTrue(games.prepare_audio_capture_launch()[0])
            write.assert_not_called()

    def test_missing_profile_never_partially_enables_audio(self):
        (self.folder / "bin" / "games" / "arma3.json").unlink()
        self.assertFalse(games.prepare_game_capture_launch("arma3")[0])
        self.assertEqual(json.loads(self.native.read_text()), self.original)

    def test_live_native_window_prevents_disk_edits(self):
        with patch.object(backend, "find_odx_window", return_value=123):
            self.assertFalse(games.prepare_game_capture_launch("arma3")[0])
        self.assertEqual(json.loads(self.native.read_text()), self.original)

    def test_malformed_config_is_preserved(self):
        self.native.write_text('{"RecordAudio":"false"}', encoding="utf-8")
        self.assertFalse(games.prepare_audio_capture_launch()[0])
        self.assertEqual(self.native.read_text(), '{"RecordAudio":"false"}')

    def test_process_catalog_matches_native_arma_profiles(self):
        with patch.object(backend, "is_game_running", side_effect=lambda name: name == "ArmaReforger.exe"):
            self.assertTrue(games.game_process_running("arma-reforger"))
            self.assertFalse(games.game_process_running("arma3"))
        with self.assertRaises(ValueError):
            games.set_game_capture("unknown")


class GameWorkerTests(unittest.TestCase):
    def run_worker(self, responses, game="arma3", **kwargs):
        with patch.object(backend, "_owned_uia_context", return_value=(123, 456)), \
             patch.object(games, "_request", side_effect=responses) as request:
            worker = games.set_game_capture(game, **kwargs) if game else games.ensure_capture_audio(**kwargs)
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            return worker, request.call_args_list

    @staticmethod
    def state(audio=False):
        return {"success": True, "selection": {"source": "Desktop", "game": "None", "audio": audio},
                "target": {"source": "Arma (arma3_x64.exe)", "game": "Arma 3", "audio": True,
                           "process": "arma3_x64.exe", "process_id": 789}}

    @staticmethod
    def success():
        return {"success": True, "selection": {"source": "Arma (arma3_x64.exe)", "game": "Arma 3", "audio": True},
                "target": GameWorkerTests.state()["target"], "audio": True, "changed": True}

    def test_applies_only_after_two_identical_live_source_inspections(self):
        state = self.state()
        worker, calls = self.run_worker([state, state, self.success()])
        self.assertTrue(worker.success, worker.error)
        self.assertEqual(worker.process, "arma3_x64.exe")
        self.assertEqual([call.args[2] for call in calls], ["inspect", "inspect", "apply"])

    def test_missing_live_source_refreshes_once_then_waits_for_selection(self):
        state = self.state()
        worker, calls = self.run_worker([games.GameCaptureError("GAME_SOURCE_UNAVAILABLE"),
                                        {"success": True}, state, state, self.success()])
        self.assertTrue(worker.success, worker.error)
        self.assertEqual([call.args[2] for call in calls].count("refresh"), 1)

    def test_capture_busy_never_attempts_mutation(self):
        worker, calls = self.run_worker([games.GameCaptureError("GAME_CAPTURE_BUSY")])
        self.assertFalse(worker.success)
        self.assertIn("GAME_CAPTURE_BUSY", worker.error)
        self.assertEqual(len(calls), 1)

    def test_cancelled_before_owned_window_lookup(self):
        cancel = threading.Event()
        cancel.set()
        worker, calls = self.run_worker([], cancel_event=cancel)
        self.assertFalse(worker.success)
        self.assertEqual(calls, [])

    def test_ownership_change_prevents_apply(self):
        with patch.object(backend, "_owned_uia_context", side_effect=[(123, 456), (321, 654)]), \
             patch.object(games, "_request") as request:
            worker = games.set_game_capture("arma3")
            worker.join(timeout=3)
            self.assertFalse(worker.success)
            self.assertIn("ODX_WINDOW_CHANGED", worker.error)
            request.assert_not_called()

    def test_audio_readback_is_mandatory(self):
        state = self.state()
        result = dict(self.success(), audio=False)
        worker, _ = self.run_worker([state, state, result])
        self.assertFalse(worker.success)
        self.assertIn("GAME_READBACK_FAILED", worker.error)

    def test_display_callback_error_does_not_undo_confirmed_native_state(self):
        state = self.state()
        def broken_callback(*args):
            raise ValueError("display unavailable")
        worker, _ = self.run_worker([state, state, self.success()], on_status=broken_callback)
        self.assertTrue(worker.success, worker.error)

    def test_response_must_match_owned_window_and_never_echoes_arbitrary_stderr(self):
        for stdout, code in (("GAME_CAPTURE_RESULT:" + json.dumps({"hwnd": 999, "pid": 456, "success": True}), "ODX_WINDOW_CHANGED"),
                             ('GAME_CAPTURE_RESULT:{"hwnd":123,"pid":456,"success":false,"error":"private-token"}', "GAME_UI_ERROR")):
            with self.subTest(code=code), patch.object(backend, "run_ps_script", return_value=
                    subprocess.CompletedProcess([], 1, stdout, "private-token")):
                with self.assertRaises(games.GameCaptureError) as raised:
                    games._request((123, 456), "arma3", "inspect", 1)
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn("private-token", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
