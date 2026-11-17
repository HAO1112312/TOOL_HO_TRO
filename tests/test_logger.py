import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import odx_logger as logger


class LoggerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.logs = self.base / "logs"
        self.logs.mkdir()
        for name, value in (("BASE_DIR", str(self.base)), ("LOGS_DIR", str(self.logs))):
            setting = patch.object(logger, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def entries(self):
        return [json.loads(line) for file in self.logs.glob("*.jsonl")
                for line in file.read_text(encoding="utf-8").splitlines()]

    def test_non_dictionary_data_is_logged_without_exception(self):
        self.assertTrue(logger.log_event("LIST", data=[1, "two"], extra="value"))
        self.assertEqual(self.entries()[0]["data"], {"value": [1, "two"], "extra": "value"})

    def test_secrets_are_redacted_before_writing_log(self):
        data = {"access_key": "private-key", "nested": {"password": "private-pass"},
                "message": "Authorization: Bearer abc123secret", "args": ["--api-key", "arg-secret"],
                "safe": "keep me"}
        self.assertTrue(logger.log_event("AUTH", data=data))
        entry = self.entries()[0]
        raw = json.dumps(entry)
        for secret in ("private-key", "private-pass", "abc123secret", "arg-secret"):
            self.assertNotIn(secret, raw)
        self.assertEqual(entry["data"]["safe"], "keep me")
        self.assertEqual(data["access_key"], "private-key")  # caller's object is unchanged

    def test_disk_failure_does_not_write_to_redirected_stderr(self):
        redirected = Mock()
        original = io.StringIO()
        with patch("builtins.open", side_effect=OSError("disk full")), patch.object(logger.sys, "stderr", redirected), patch.object(logger.sys, "__stderr__", original):
            self.assertFalse(logger.log_event("FAIL", data="value"))
        redirected.write.assert_not_called()
        self.assertIn("disk full", original.getvalue())

    def test_reentrant_error_stream_cannot_recurse_into_logger(self):
        class RecursiveStream:
            def write(self, text):
                logger.log_event("STREAM", data=text)

        with patch("builtins.open", side_effect=OSError("disk full")) as opened, patch.object(logger.sys, "__stderr__", RecursiveStream()):
            self.assertFalse(logger.log_event("FAIL"))
        self.assertEqual(opened.call_count, 1)

    def test_export_filename_stays_inside_requested_directory(self):
        output = self.base / "exports"
        with patch.object(logger, "collect_system_telemetry", return_value={}):
            result = Path(logger.export_debug_logs(str(output), player_id='../../bad\\name:C?<>|"'))
        self.assertEqual(result.parent.resolve(), output.resolve())
        self.assertTrue(result.is_file())
        self.assertNotIn(":", result.name)
        self.assertNotIn("\\", result.name)
        with zipfile.ZipFile(result) as archive:
            self.assertIn("telemetry.json", archive.namelist())

    def test_export_redacts_existing_json_and_plaintext_logs_and_config(self):
        config = {"access_key": "config-key-123", "nested": {"refresh_token": "refresh-private"}, "role": "host"}
        (self.base / "autopilot_config.json").write_text(json.dumps(config), encoding="utf-8")
        (self.logs / "old.jsonl").write_text(json.dumps({"data": {"api_key": "older-key", "message": "bare config-key-123"}}) + "\n", encoding="utf-8")
        (self.logs / "plain.log").write_text("Authorization: Bearer bearer-secret\npassword=plaintext-secret\nconfig-key-123\n", encoding="utf-8")
        with patch.object(logger, "collect_system_telemetry", return_value={"os": "test"}):
            result = logger.export_debug_logs(player_id="player1")
        with zipfile.ZipFile(result) as archive:
            text = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
            exported = json.loads(archive.read("autopilot_config.json"))
        for secret in ("config-key-123", "refresh-private", "older-key", "bearer-secret", "plaintext-secret"):
            self.assertNotIn(secret, text)
        self.assertEqual(exported["role"], "host")
        self.assertEqual(exported["access_key"], "[REDACTED]")

    def test_telemetry_uses_shared_odx_root(self):
        bundled = self.base / "odx-capture-0.3.5"
        (bundled / "bin").mkdir(parents=True)
        (bundled / "bin" / "odx-capture.json").write_text(json.dumps({"time_server": "time.windows.com", "orchestrator": "https://example.invalid", "vendor_name": "test-vendor"}), encoding="utf-8")
        with patch.object(logger, "resolve_odx_root", return_value=str(bundled)) as resolve:
            result = logger.collect_system_telemetry()
        resolve.assert_called_once_with(str(self.base))
        self.assertEqual(result["odx_vendor_name"], "test-vendor")
        self.assertEqual(result["odx_root"], str(bundled))


if __name__ == "__main__":
    unittest.main()
