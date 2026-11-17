import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import odx_diagnostics as diagnostics


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="odx_diagnostics_test_")
        self.addCleanup(self.temporary.cleanup)
        self.tool = Path(self.temporary.name)
        self.root = self.tool / "Odx nguoi dung"
        (self.root / "bin").mkdir(parents=True)
        for name in ("Odx Capture.exe", "bin/odx.exe", "bin/odx-app.exe"):
            (self.root / name).write_bytes(b"test fixture; never executed")
        for name in ("autopilot_config.json", "version.json"):
            (self.tool / name).write_text("{}", encoding="utf-8")
        (self.tool / "valid_module.py").write_text("raise RuntimeError('must never import')\n", encoding="utf-8")

    def collect(self, tkinter_module=None):
        fake_root = mock.Mock()
        fake_root.tk.call.return_value = "8.6.12"
        tkinter_module = tkinter_module or types.SimpleNamespace(Tk=mock.Mock(return_value=fake_root))
        with mock.patch.object(diagnostics, "__file__", str(self.tool / "doctor_entry.py")):
            with mock.patch.object(diagnostics, "resolve_odx_root", return_value=str(self.root)) as resolve:
                with mock.patch.dict("sys.modules", {"tkinter": tkinter_module}):
                    report = diagnostics.collect_diagnostics()
                resolve.assert_called_once_with(str(self.tool))
                return report

    def test_collection_uses_shared_root_and_compiles_without_importing(self):
        report = self.collect()
        self.assertTrue(report["ok"])
        self.assertEqual(str(self.root), report["odx_root"])
        checks = {item["name"]: item for item in report["checks"]}
        self.assertTrue(checks["valid_module.py"]["ok"])
        self.assertTrue(checks["bin/odx.exe"]["ok"])

    def test_json_syntax_and_missing_binary_fail_diagnostics(self):
        (self.tool / "autopilot_config.json").write_text("[]", encoding="utf-8")
        (self.tool / "broken.py").write_text("def broken(:\n", encoding="utf-8")
        (self.root / "bin" / "odx-app.exe").unlink()
        report = self.collect()
        self.assertFalse(report["ok"])
        checks = {item["name"]: item for item in report["checks"]}
        for name in ("autopilot_config.json", "broken.py", "bin/odx-app.exe"):
            self.assertFalse(checks[name]["ok"])

    def test_importable_but_broken_tcl_runtime_fails_preflight(self):
        fake_tk = types.SimpleNamespace(Tk=mock.Mock(side_effect=RuntimeError("cannot find init.tcl")))
        report = self.collect(fake_tk)
        self.assertFalse(report["ok"])
        check = next(item for item in report["checks"] if item["name"] == "Tkinter")
        self.assertIn("init.tcl", check["detail"])

    def test_hidden_tk_window_is_destroyed_after_preflight(self):
        fake_root = mock.Mock()
        fake_root.tk.call.return_value = "8.6.12"
        report = self.collect(types.SimpleNamespace(Tk=mock.Mock(return_value=fake_root)))
        self.assertTrue(report["ok"])
        fake_root.withdraw.assert_called_once_with()
        fake_root.destroy.assert_called_once_with()

    def test_standalone_cleanup_helper_never_inspects_or_terminates_processes(self):
        with mock.patch("subprocess.run") as enumerate_processes, \
             mock.patch("subprocess.Popen") as launch, \
             mock.patch("ctypes.WinDLL", create=True) as windows_api, \
             mock.patch.object(diagnostics, "resolve_odx_root") as resolve:
            for selected in (None, self.root, self.tool / "missing-install"):
                with self.subTest(selected=selected):
                    with self.assertRaisesRegex(RuntimeError, "Odx chỉ đóng khi đóng tool"):
                        diagnostics.cleanup_selected_install(selected)
            enumerate_processes.assert_not_called()
            launch.assert_not_called()
            windows_api.assert_not_called()
            resolve.assert_not_called()

    def test_legacy_cleanup_cli_explains_policy_without_loading_ui_or_process_apis(self):
        with mock.patch("subprocess.run") as enumerate_processes, \
             mock.patch("subprocess.Popen") as launch, \
             mock.patch("ctypes.WinDLL", create=True) as windows_api, \
             mock.patch.object(diagnostics, "collect_diagnostics") as collect, \
             mock.patch.object(diagnostics, "cleanup_selected_install") as cleanup:
            for flags in (("--cleanup",), ("--cleanup", "--json")):
                with self.subTest(flags=flags), mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(diagnostics.main(flags), 0)
                    self.assertIn("Odx chỉ đóng khi đóng tool", output.getvalue())
                    self.assertIn("không có tiến trình nào bị đóng", output.getvalue())
                    self.assertNotIn("[STOP]", output.getvalue())
            enumerate_processes.assert_not_called()
            launch.assert_not_called()
            windows_api.assert_not_called()
            collect.assert_not_called()
            cleanup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
