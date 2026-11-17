import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import odx_updater as updater


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def archive(self, extra=None, prefix=""):
        target = self.base / "update.zip"
        entries = {name: "# valid source\n" for name in updater.REQUIRED_FILES}
        entries["version.json"] = json.dumps({"version": "99.0.0", "version_code": 9900})
        entries.update(extra or {})
        with zipfile.ZipFile(target, "w") as archive:
            for name, content in entries.items():
                archive.writestr(prefix + name, content)
        return target

    def staged(self, extra=None):
        stage = self.base / "_update_temp" / "pending-test"
        updater._stage_archive(self.archive(extra), stage / "payload")
        return stage

    def test_invalid_zip_is_rejected_before_installer_launch(self):
        def download(url, target, *args):
            Path(target).write_bytes(b"invalid ZIP" * 1000)
        with patch.object(updater, "TEMP_DIR", str(self.base)), patch.object(updater, "_download_archive", side_effect=download), patch.object(updater.subprocess, "Popen") as launch:
            ok, error = updater.download_and_apply_update("https://example.invalid/update.zip")
        self.assertFalse(ok)
        self.assertTrue(error)
        launch.assert_not_called()

    def test_outer_folder_removed_but_nested_paths_preserved(self):
        payload = self.base / "payload"
        updater._stage_archive(self.archive({"bin/games/config.json": "{}"}, "Tool_Tu_Dong/"), payload)
        self.assertTrue((payload / "bin/games/config.json").is_file())
        self.assertFalse((payload / "config.json").exists())

    def test_settings_and_capture_data_never_extracted(self):
        payload = self.base / "payload"
        updater._stage_archive(self.archive({"autopilot_config.json": "{}", "autopilot_ui.json": "{}", "odx_capture_test_restore.json": "{}", "data/captures/round/video.mp4": "bad", "odx-capture-x/data/logs/a.log": "bad", "bin/odx-capture.json": "{}", "odx-capture-x/bin/odx-capture.json": "{}"}), payload)
        self.assertFalse((payload / "autopilot_config.json").exists())
        self.assertFalse((payload / "autopilot_ui.json").exists())
        self.assertFalse((payload / "odx_capture_test_restore.json").exists())
        self.assertFalse((payload / "data").exists())
        self.assertFalse((payload / "odx-capture-x/data").exists())
        self.assertFalse((payload / "bin/odx-capture.json").exists())
        self.assertFalse((payload / "odx-capture-x/bin/odx-capture.json").exists())

    def test_traversal_absolute_and_windows_special_paths_rejected(self):
        for name in ("../outside.py", "C:/outside.py", "/absolute.py", "bin/../outside.py", "bin/NUL.txt", "bin/name. ", "bin\\..\\escape.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                updater._stage_archive(self.archive({name: "bad"}), self.base / "payload")

    def test_case_insensitive_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            updater._stage_archive(self.archive({"ODX_GUI.PY": "# duplicate"}), self.base / "payload")

    def test_invalid_python_and_wrong_version_rejected(self):
        with self.assertRaises(SyntaxError):
            updater._stage_archive(self.archive({"broken.py": "def broken(:"}), self.base / "bad-python")
        with self.assertRaises(ValueError):
            updater._stage_archive(self.archive(), self.base / "bad-version", "98.0.0")

    def test_missing_program_files_rejected(self):
        target = self.base / "empty.zip"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("readme.txt", "incomplete")
        with self.assertRaises(ValueError):
            updater._stage_archive(target, self.base / "payload")

    def test_successful_install_preserves_settings(self):
        (self.base / "odx_gui.py").write_text("# original", encoding="utf-8")
        config = self.base / "autopilot_config.json"
        config.write_text('{"player":"keep"}', encoding="utf-8")
        window = self.base / "autopilot_ui.json"
        window.write_text('{"normal":{"width":1200}}', encoding="utf-8")
        count = updater._install_payload(self.base, self.staged({"bin/nested/config.json": "{}"}))
        self.assertGreater(count, 0)
        self.assertEqual(config.read_text(), '{"player":"keep"}')
        self.assertEqual(window.read_text(), '{"normal":{"width":1200}}')
        self.assertEqual((self.base / "bin/nested/config.json").read_text(), "{}")
        self.assertEqual((self.base / "_update_temp/pending-test/rollback/odx_gui.py").read_text(), "# original")

    def test_failed_install_rolls_back_previous_and_new_files(self):
        original = self.base / "odx_backend.py"
        original.write_text("# original", encoding="utf-8")
        stage = self.staged({"aaa-new.txt": "new", "zzz-last.txt": "last"})
        real_replace = updater.os.replace
        def fail_last(source, target):
            if Path(target).name == "zzz-last.txt":
                raise PermissionError("locked file")
            return real_replace(source, target)
        with patch.object(updater.os, "replace", side_effect=fail_last), self.assertRaisesRegex(RuntimeError, "khôi phục"):
            updater._install_payload(self.base, stage)
        self.assertEqual(original.read_text(), "# original")
        self.assertFalse((self.base / "aaa-new.txt").exists())
        self.assertFalse((self.base / "odx_gui.py").exists())

    def test_stage_outside_update_directory_rejected(self):
        with self.assertRaises(ValueError):
            updater._install_payload(self.base, self.base / "outside")

    def test_hash_mismatch_and_truncated_download_rejected(self):
        class Response(io.BytesIO):
            headers = {"Content-Length": "3"}
        with patch.object(updater.urllib.request, "urlopen", return_value=Response(b"abc")), self.assertRaisesRegex(ValueError, "SHA-256"):
            updater._download_archive("https://example.invalid/a.zip", self.base / "hash.zip", expected_sha256="0" * 64)
        class Truncated(io.BytesIO):
            headers = {"Content-Length": "10"}
        with patch.object(updater.urllib.request, "urlopen", return_value=Truncated(b"abc")), self.assertRaisesRegex(ValueError, "chưa đầy đủ"):
            updater._download_archive("https://example.invalid/a.zip", self.base / "truncated.zip")

    def test_parent_still_alive_prevents_install_and_restart(self):
        with patch.object(updater, "_wait_parent_exit", return_value=False), patch.object(updater, "_install_payload") as install, patch.object(updater.subprocess, "Popen") as launch:
            self.assertEqual(updater._apply_worker(self.base, self.base / "stage", 1234), 1)
        install.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(json.loads((self.base / "update_status.json").read_text(encoding="utf-8"))["state"], "failed")

    def test_version_metadata_type_and_transport_checked(self):
        for invalid in ([], {}, {"version": "nonsense"}, {"version": "3.2.0", "version_code": "320"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                updater._validate_version_info(invalid)
        with patch.object(updater, "get_update_url", return_value="http://example.invalid/version.json"), patch.object(updater.urllib.request, "urlopen") as network:
            has_update, info, error = updater.check_for_updates()
        self.assertFalse(has_update)
        self.assertTrue(error)
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
