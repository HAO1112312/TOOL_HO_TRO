import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import odx_runtime as runtime


class RootDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.tool = self.base / "tool"
        self.tool.mkdir()
        self.env = patch.dict(os.environ, {"ODX_ROOT": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def root(self, path):
        (path / "bin").mkdir(parents=True, exist_ok=True)
        (path / "bin" / "odx.exe").write_bytes(b"test fixture")
        return str(path.resolve())

    def test_bundled_child_is_discovered(self):
        bundled = self.root(self.tool / "odx-capture-0.3.5-win-x64")
        self.assertEqual(runtime.resolve_odx_root(self.tool), bundled)

    def test_release_zip_with_same_named_nested_folder_is_discovered(self):
        name = "odx-capture-0.3.8-win-x64"
        bundled = self.root(self.tool / name / name)
        self.assertEqual(runtime.resolve_odx_root(self.tool), bundled)

    def test_nested_new_release_beats_direct_older_local_package(self):
        self.root(self.tool / "odx-capture-0.3.5-win-x64")
        name = "odx-capture-0.3.8-win-x64"
        bundled = self.root(self.tool / name / name)
        self.assertEqual(runtime.resolve_odx_root(self.tool), bundled)

    def test_nested_local_package_still_beats_parent_sibling(self):
        name = "odx-capture-0.3.8-win-x64"
        bundled = self.root(self.tool / name / name)
        self.root(self.base / "odx-capture-9.9.9")
        self.assertEqual(runtime.resolve_odx_root(self.tool), bundled)

    def test_explicit_environment_accepts_extraction_wrapper(self):
        wrapper = self.base / "odx-capture-0.3.8-win-x64"
        native = self.root(wrapper / wrapper.name)
        self.root(self.tool / "odx-capture-9.9.9")
        with patch.dict(os.environ, {"ODX_ROOT": str(wrapper)}):
            self.assertEqual(runtime.resolve_odx_root(self.tool), native)

    def test_relative_config_accepts_extraction_wrapper(self):
        wrapper = self.tool / "odx-capture-0.3.8-win-x64"
        native = self.root(wrapper / wrapper.name)
        (self.tool / "autopilot_config.json").write_text(json.dumps({"odx_root": wrapper.name}))
        self.assertEqual(runtime.resolve_odx_root(self.tool), native)

    def test_package_inside_backup_is_not_auto_selected(self):
        self.root(self.tool / "_backups" / "odx-capture-9.9.9")
        self.assertEqual(runtime.resolve_odx_root(self.tool), str(self.tool.resolve()))

    def test_package_lookup_does_not_recurse_beyond_one_wrapper(self):
        self.root(self.tool / "odx-capture-0.3.8" / "outer" / "inner")
        self.assertEqual(runtime.resolve_odx_root(self.tool), str(self.tool.resolve()))

    def test_invalid_nested_package_falls_back_to_valid_old_install(self):
        (self.tool / "odx-capture-0.3.8" / "odx-capture-0.3.8").mkdir(parents=True)
        old = self.root(self.tool / "odx-capture-0.3.5")
        self.assertEqual(runtime.resolve_odx_root(self.tool), old)

    def test_local_direct_root_wins_over_newer_sibling(self):
        direct = self.root(self.tool)
        self.root(self.base / "odx-capture-9.9.9")
        self.assertEqual(runtime.resolve_odx_root(self.tool), direct)

    def test_bundled_child_wins_over_parent_sibling(self):
        bundled = self.root(self.tool / "odx-capture-0.3.5")
        self.root(self.base / "odx-capture-9.9.9")
        self.assertEqual(runtime.resolve_odx_root(self.tool), bundled)

    def test_newest_local_child_is_selected(self):
        self.root(self.tool / "odx-capture-0.3.4")
        newer = self.root(self.tool / "odx-capture-0.3.5")
        self.assertEqual(runtime.resolve_odx_root(self.tool), newer)

    def test_explicit_environment_root_wins(self):
        self.root(self.tool)
        explicit = self.root(self.base / "explicit")
        with patch.dict(os.environ, {"ODX_ROOT": explicit}):
            self.assertEqual(runtime.resolve_odx_root(self.tool), explicit)

    def test_relative_config_override(self):
        configured = self.root(self.tool / "custom")
        (self.tool / "autopilot_config.json").write_text(json.dumps({"odx_root": "custom"}))
        self.assertEqual(runtime.resolve_odx_root(self.tool), configured)

    def test_invalid_environment_override_falls_back_to_bundle(self):
        bundled = self.root(self.tool / "odx-capture-0.3.5")
        with patch.dict(os.environ, {"ODX_ROOT": str(self.base / "missing")}):
            self.assertEqual(runtime.resolve_odx_root(self.tool), bundled)

    def test_parent_root_for_nested_tool(self):
        parent = self.root(self.base)
        self.assertEqual(runtime.resolve_odx_root(self.tool), parent)

    def test_no_recursive_search(self):
        self.root(self.tool / "unrelated" / "nested" / "odx")
        with patch("os.walk", side_effect=AssertionError("Recursive scanning is prohibited")):
            self.assertEqual(runtime.resolve_odx_root(self.tool), str(self.tool.resolve()))

    def test_version_fallback_supports_multiple_digits(self):
        self.assertEqual(runtime.get_odx_file_version("odx-capture-0.12.35/bin/odx.exe"), (0, 12, 35, 0))


class ManagedProcessTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(runtime._managed_processes)
        runtime._managed_processes.clear()
        self.addCleanup(self.restore)

    def restore(self):
        runtime._managed_processes.clear()
        runtime._managed_processes.update(self.old)

    def process(self, pid, name="odx-app.exe", exited=False):
        return Mock(pid=pid, args=[name], poll=Mock(return_value=0 if exited else None))

    def test_cleanup_only_registered_pid_tree(self):
        proc = self.process(112233)
        with patch.object(runtime, "_assign_to_job"), patch.object(runtime.sys, "platform", "win32"), patch.object(runtime.subprocess, "run", return_value=Mock(returncode=0)) as run:
            self.assertIs(runtime.register_managed_process(proc), proc)
            runtime.cleanup_managed_processes(tool_closing=True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/F", "/T", "/PID", "112233"])
        self.assertNotIn(112233, runtime._managed_processes)

    def test_no_cleanup_of_unregistered_or_exited_processes(self):
        with patch.object(runtime, "_assign_to_job"), patch.object(runtime.subprocess, "run") as run:
            runtime.cleanup_managed_processes(tool_closing=True)
            runtime.register_managed_process(self.process(112233, exited=True))
            runtime.cleanup_managed_processes(tool_closing=True)
        run.assert_not_called()

    def test_full_cleanup_also_terminates_owned_job_descendants(self):
        kernel = Mock()
        with patch.object(runtime, "_job_handle", 123), patch.object(runtime, "_kernel32", return_value=kernel), patch.object(runtime.sys, "platform", "win32"), patch.object(runtime.subprocess, "run") as run:
            runtime.cleanup_managed_processes(tool_closing=True)
        kernel.TerminateJobObject.assert_called_once_with(123, 1)
        run.assert_not_called()

    def test_kill_gui_false_keeps_owned_gui(self):
        proc = self.process(112233)
        with patch.object(runtime, "_assign_to_job"), patch.object(runtime.subprocess, "run") as run:
            runtime.register_managed_process(proc)
            runtime.cleanup_managed_processes(kill_gui=False, tool_closing=True)
        run.assert_not_called()
        self.assertIn(proc.pid, runtime._managed_processes)

    def test_nonclosing_cleanup_preserves_gui_launchers_helpers_and_owned_job(self):
        gui = self.process(112233)
        ancestor = self.process(112244, name="powershell.exe")
        with patch.object(runtime, "_assign_to_job"), \
             patch.object(runtime, "_job_handle", 123), \
             patch.object(runtime.sys, "platform", "win32"), \
             patch.object(runtime, "_kernel32") as kernel, \
             patch.object(runtime.subprocess, "run") as run:
            runtime.register_managed_process(gui)
            runtime.register_managed_process(ancestor)
            for keep_gui in (True, False):
                with self.subTest(kill_gui=keep_gui):
                    self.assertIs(runtime.cleanup_managed_processes(kill_gui=keep_gui), False)
                    self.assertEqual(set(runtime._managed_processes), {112233, 112244})
                    self.assertEqual(runtime._job_handle, 123)
            kernel.assert_not_called()
            run.assert_not_called()
        for proc in (gui, ancestor):
            proc.terminate.assert_not_called()
            proc.kill.assert_not_called()
            proc.wait.assert_not_called()

    def test_tool_shutdown_authorizes_cleanup_then_releases_owned_job(self):
        kernel = Mock()
        with patch.object(runtime, "_job_handle", 123), \
             patch.object(runtime, "_kernel32", return_value=kernel), \
             patch.object(runtime, "cleanup_managed_processes") as cleanup:
            runtime._shutdown()
            cleanup.assert_called_once_with(tool_closing=True)
            kernel.CloseHandle.assert_called_once_with(123)
            self.assertIsNone(runtime._job_handle)

    def test_console_interrupt_preserves_odx_until_tool_exit(self):
        kernel = Mock()
        kernel.CreateJobObjectW.return_value = 123
        kernel.SetInformationJobObject.return_value = True
        with patch.object(runtime, "_protection_ready", False), \
             patch.object(runtime, "_job_handle", None), \
             patch.object(runtime, "_ctrl_handler", None), \
             patch.object(runtime.sys, "platform", "win32"), \
             patch.object(runtime, "_kernel32", return_value=kernel), \
             patch.object(runtime.atexit, "register"), \
             patch.object(runtime, "cleanup_managed_processes") as cleanup:
            runtime.setup_process_protection()
            callback = kernel.SetConsoleCtrlHandler.call_args.args[0]
            for event in (0, 1):
                self.assertFalse(callback(event))
            cleanup.assert_not_called()
            for event in (2, 5, 6):
                with self.subTest(console_exit_event=event):
                    cleanup.reset_mock()
                    self.assertFalse(callback(event))
                    cleanup.assert_called_once_with(tool_closing=True)

    def test_only_registered_live_pid_is_owned_without_job(self):
        with patch.object(runtime, "_assign_to_job"), patch.object(runtime, "_job_handle", None):
            runtime.register_managed_process(self.process(112233))
            self.assertTrue(runtime.owns_pid(112233))
            self.assertFalse(runtime.owns_pid(556677))

    def test_owned_job_descendant_is_recognised_and_query_handle_closed(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 987

        def belongs(process, job, pointer):
            pointer._obj.value = True
            return True

        kernel.IsProcessInJob.side_effect = belongs
        with patch.object(runtime, "_job_handle", 123), patch.object(runtime, "_kernel32", return_value=kernel), patch.object(runtime.sys, "platform", "win32"):
            self.assertTrue(runtime.owns_pid(556677))
        kernel.CloseHandle.assert_called_once_with(987)

    def test_explicit_setup_does_not_assign_parent_to_job(self):
        kernel = Mock()
        kernel.CreateJobObjectW.return_value = 123
        kernel.SetInformationJobObject.return_value = True
        with patch.object(runtime, "_protection_ready", False), patch.object(runtime, "_job_handle", None), patch.object(runtime, "_ctrl_handler", None), patch.object(runtime, "_kernel32", return_value=kernel), patch.object(runtime.atexit, "register") as register:
            runtime.setup_process_protection()
            runtime.setup_process_protection()
            self.assertEqual(register.call_count, 1)
            kernel.AssignProcessToJobObject.assert_not_called()
            kernel.GetCurrentProcess.assert_not_called()

    def test_import_installs_no_handlers_or_process_calls(self):
        with patch("atexit.register") as register, patch("subprocess.run") as run:
            importlib.reload(runtime)
        register.assert_not_called()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
