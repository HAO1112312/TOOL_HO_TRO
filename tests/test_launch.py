"""Probe Python-manager discovery with a disposable local console fixture.

Only the launcher's function declaration is evaluated. These tests never launch
the GUI, elevate, invoke a real Python manager, install software or open Store.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FAKE_MANAGER_SOURCE = r'''
using System;
using System.IO;
using System.Text;
using System.Threading;

public static class OdxFakePythonManager {
    public static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        string automatic = Environment.GetEnvironmentVariable("PYTHON_MANAGER_AUTOMATIC_INSTALL") ?? "";
        string log = Environment.GetEnvironmentVariable("ODX_TEST_MANAGER_LOG");
        File.WriteAllText(log, String.Join("|", args) + "|" + automatic, new UTF8Encoding(false));
        if (args.Length != 2 || args[0] != "list" || args[1] != "--format=exe" || automatic != "false")
            return 41;
        string mode = Environment.GetEnvironmentVariable("ODX_TEST_MANAGER_MODE");
        if (mode == "timeout") { Thread.Sleep(20000); return 0; }
        string runtime = Environment.GetEnvironmentVariable("ODX_TEST_RUNTIME");
        Console.WriteLine("Installed Python runtimes:");
        Console.WriteLine(runtime);
        // A rooted path without its drive still names the existing runtime
        // on this drive, but must not pass as a fully absolute executable.
        Console.WriteLine(runtime.Substring(2));
        Console.WriteLine("relative\\python.exe");
        Console.WriteLine(Path.Combine(Path.GetDirectoryName(runtime), "missing-python.exe"));
        Console.WriteLine("https://example.invalid/python.exe");
        if (mode == "failure") { Console.Error.WriteLine("fixture manager failure"); return 3; }
        return 0;
    }
}
'''

BUILD_SCRIPT = r'''
param([string]$SourcePath, [string]$ExePath)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition ([IO.File]::ReadAllText($SourcePath)) -Language CSharp -OutputAssembly $ExePath -OutputType ConsoleApplication
'''

PROBE_SCRIPT = r'''
param([string]$LauncherPath, [string]$ManagerPath, [string]$ResultPath, [int]$TimeoutMilliseconds=8000)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$tree = [Management.Automation.Language.Parser]::ParseFile($LauncherPath, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Launcher syntax errors' }
$helper = $tree.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-OdxPythonManagerList'
}, $true)
if (-not $helper) { throw 'Missing Invoke-OdxPythonManagerList' }
Invoke-Expression $helper.Extent.Text
$result = @(Invoke-OdxPythonManagerList -Command $ManagerPath -TimeoutMilliseconds $TimeoutMilliseconds)
$json = ConvertTo-Json -InputObject $result -Compress
[IO.File]::WriteAllText($ResultPath, $json, [Text.UTF8Encoding]::new($false))
'''

DISCOVERY_SCRIPT = r'''
param([string]$LauncherPath, [string]$LocalAppData, [string]$PathAlias, [string]$ResultPath)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$tree = [Management.Automation.Language.Parser]::ParseFile($LauncherPath, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Launcher syntax errors' }
$helper = $tree.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-OdxPythonManagerCommands'
}, $true)
if (-not $helper) { throw 'Missing Get-OdxPythonManagerCommands' }
Invoke-Expression $helper.Extent.Text
function Get-Command {
    param([string]$Name, [switch]$All, [string]$CommandType, [string]$ErrorAction)
    if ($Name -ne 'pymanager') { throw 'Only the unambiguous manager command may be queried' }
    if ($script:PathAlias) { [pscustomobject]@{Source=$script:PathAlias} }
}
$result = @(Get-OdxPythonManagerCommands -LocalAppData $LocalAppData)
$json = ConvertTo-Json -InputObject $result -Compress
[IO.File]::WriteAllText($ResultPath, $json, [Text.UTF8Encoding]::new($false))
'''


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher")
class PythonManagerLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="odx-launch-fixture-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        cls.manager = cls.folder / "pymanager.exe"
        source, build = cls.folder / "manager.cs", cls.folder / "build.ps1"
        source.write_text(FAKE_MANAGER_SOURCE, encoding="utf-8")
        build.write_text(BUILD_SCRIPT, encoding="utf-8-sig")
        completed = subprocess.run([
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(build),
            "-SourcePath", str(source), "-ExePath", str(cls.manager)],
            capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        if completed.returncode:
            raise RuntimeError("Could not build isolated manager fixture: " + completed.stderr)
        cls.probe = cls.folder / "probe.ps1"
        cls.probe.write_text(PROBE_SCRIPT, encoding="utf-8-sig")
        cls.discovery = cls.folder / "discovery.ps1"
        cls.discovery.write_text(DISCOVERY_SCRIPT, encoding="utf-8-sig")

    def probe_manager(self, mode, timeout_ms=8000):
        with tempfile.TemporaryDirectory(prefix="case-", dir=self.folder) as case:
            case = Path(case)
            runtime = case / "Python có khoảng trắng" / "python.exe"
            runtime.parent.mkdir()
            # Discovery must only verify the path, never execute this file.
            runtime.write_bytes(b"not an executable")
            result, log = case / "result.json", case / "manager.log"
            environment = dict(os.environ, ODX_TEST_MANAGER_MODE=mode, ODX_TEST_RUNTIME=str(runtime),
                               ODX_TEST_MANAGER_LOG=str(log))
            # A host preference to permit automatic installs must not reach the
            # read-only manager-list child launched by the tool.
            environment["PYTHON_MANAGER_AUTOMATIC_INSTALL"] = "true"
            started = time.monotonic()
            completed = subprocess.run([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.probe),
                "-LauncherPath", str(ROOT / "odx_launch.ps1"), "-ManagerPath", str(self.manager),
                "-ResultPath", str(result), "-TimeoutMilliseconds", str(timeout_ms)],
                env=environment, capture_output=True, text=True,
                timeout=15, creationflags=CREATE_NO_WINDOW)
            elapsed = time.monotonic() - started
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(result.exists(), "Probe must return an explicit result")
            if mode != "timeout" or log.exists():
                self.assertEqual(log.read_text(encoding="utf-8"), "list|--format=exe|false")
            return json.loads(result.read_text(encoding="utf-8")), str(runtime), elapsed

    def test_manager_lists_only_existing_absolute_executable_paths_without_install(self):
        result, runtime, _ = self.probe_manager("success")
        self.assertEqual(result, [runtime])

    def test_failed_manager_does_not_supply_a_runtime_even_if_stdout_contains_one(self):
        result, _, _ = self.probe_manager("failure")
        self.assertEqual(result, [])

    def test_manager_listing_timeout_returns_without_waiting_for_child_completion(self):
        result, _, elapsed = self.probe_manager("timeout", timeout_ms=150)
        self.assertEqual(result, [])
        self.assertGreaterEqual(elapsed, .1)
        self.assertLess(elapsed, 4, "Manager enumeration must finish within its bounded deadline")

    def test_manager_discovery_accepts_zero_byte_msix_aliases_without_running_them(self):
        with tempfile.TemporaryDirectory(prefix="discovery-", dir=self.folder) as case:
            case = Path(case)
            aliases = [case / "Microsoft" / "WindowsApps" / relative for relative in (
                Path("pymanager.exe"),
                Path("PythonSoftwareFoundation.PythonManager_3847v3x7pw1km") / "pymanager.exe",
                Path("PythonSoftwareFoundation.PythonManager_qbz5n2kfra8p0") / "pymanager.exe")]
            for alias in aliases:
                alias.parent.mkdir(parents=True, exist_ok=True)
                alias.touch()
                self.assertEqual(alias.stat().st_size, 0)
            result = case / "result.json"
            completed = subprocess.run([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.discovery),
                "-LauncherPath", str(ROOT / "odx_launch.ps1"), "-LocalAppData", str(case),
                "-PathAlias", str(aliases[0]), "-ResultPath", str(result)],
                capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(result.read_text(encoding="utf-8")), [str(path) for path in aliases])


if __name__ == "__main__":
    unittest.main()
