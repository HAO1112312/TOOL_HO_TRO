$ErrorActionPreference = 'Stop'
$odxToolDir = $PSScriptRoot
$odxToolArgs = @($args)
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
Set-Location -LiteralPath $odxToolDir

function Get-OdxPythonManagerCommands {
    param([string]$LocalAppData = $env:LOCALAPPDATA)
    $odxManagers = [System.Collections.Generic.List[string]]::new()
    foreach ($odxFound in @(Get-Command pymanager -All -CommandType Application -ErrorAction SilentlyContinue)) {
        if ($odxFound -and (Test-Path -LiteralPath $odxFound.Source -PathType Leaf)) {
            # Python Install Manager's MSIX execution aliases can be zero bytes.
            $odxManagers.Add($odxFound.Source)
        }
    }
    if ($LocalAppData) {
        foreach ($odxAlias in @(
            'Microsoft\WindowsApps\pymanager.exe',
            'Microsoft\WindowsApps\PythonSoftwareFoundation.PythonManager_3847v3x7pw1km\pymanager.exe',
            'Microsoft\WindowsApps\PythonSoftwareFoundation.PythonManager_qbz5n2kfra8p0\pymanager.exe'
        )) {
            $odxAliasPath = Join-Path $LocalAppData $odxAlias
            if (Test-Path -LiteralPath $odxAliasPath -PathType Leaf) { $odxManagers.Add($odxAliasPath) }
        }
    }
    @($odxManagers | Select-Object -Unique)
}

function Invoke-OdxPythonManagerList {
    param([Parameter(Mandatory=$true)][string]$Command, [int]$TimeoutMilliseconds = 8000)
    $odxListProcess = $null
    try {
        $odxListStart = [System.Diagnostics.ProcessStartInfo]::new()
        $odxListStart.FileName = $Command
        # Listing installed runtimes never requests an install or an online index.
        $odxListStart.Arguments = 'list --format=exe'
        $odxListStart.UseShellExecute = $false
        $odxListStart.CreateNoWindow = $true
        $odxListStart.RedirectStandardOutput = $true
        $odxListStart.RedirectStandardError = $true
        $odxListStart.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $odxListStart.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
        $odxListStart.EnvironmentVariables['PYTHON_MANAGER_AUTOMATIC_INSTALL'] = 'false'
        $odxListStart.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
        $odxListProcess = [System.Diagnostics.Process]::Start($odxListStart)
        $odxListOutput = $odxListProcess.StandardOutput.ReadToEndAsync()
        $odxListErrors = $odxListProcess.StandardError.ReadToEndAsync()
        if (-not $odxListProcess.WaitForExit([math]::Max(100, [math]::Min(8000, $TimeoutMilliseconds)))) {
            $odxListProcess.Kill()
            $null = $odxListProcess.WaitForExit(1000)
            return
        }
        $odxListText = $odxListOutput.GetAwaiter().GetResult()
        $null = $odxListErrors.GetAwaiter().GetResult()
        if ($odxListProcess.ExitCode -ne 0) { return }
        foreach ($odxLine in ($odxListText -split '\r?\n')) {
            $odxRuntime = $odxLine.Trim()
            if ([System.IO.Path]::IsPathRooted($odxRuntime) -and
                [System.IO.Path]::GetFullPath($odxRuntime) -ieq $odxRuntime -and
                [System.IO.Path]::GetFileName($odxRuntime) -ieq 'python.exe' -and
                (Test-Path -LiteralPath $odxRuntime -PathType Leaf)) {
                $odxRuntime
            }
        }
    } catch {
        # A missing/broken manager must not hide a working standalone Python.
    } finally {
        if ($odxListProcess) {
            try { if (-not $odxListProcess.HasExited) { $odxListProcess.Kill() } } catch { }
            $odxListProcess.Dispose()
        }
    }
}

try {
    $odxIsCheck = $odxToolArgs -contains '--check'
    $odxIsDoctor = $odxToolArgs -contains '--doctor'
    $odxIsCleanup = $odxToolArgs -contains '--cleanup'
    $odxIsSelfTest = $odxToolArgs -contains '--self-test'
    $odxCandidates = [System.Collections.Generic.List[string]]::new()
    $odxManagers = @(Get-OdxPythonManagerCommands)
    foreach ($odxManager in $odxManagers) {
        foreach ($odxRuntime in @(Invoke-OdxPythonManagerList -Command $odxManager)) {
            $odxCandidates.Add($odxRuntime)
        }
    }
    foreach ($odxCommand in @('python', 'python3', 'py')) {
        $odxFound = Get-Command $odxCommand -CommandType Application -ErrorAction SilentlyContinue
        if ($odxFound) {
            foreach ($odxItem in @($odxFound)) {
                # Zero-byte Store aliases are not an installed interpreter.
                if ((Get-Item -LiteralPath $odxItem.Source).Length -gt 0) {
                    $odxCandidates.Add($odxItem.Source)
                }
            }
        }
    }
    $odxPythonHomes = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        (Join-Path $env:LOCALAPPDATA 'Python')
    )
    foreach ($odxPythonHome in $odxPythonHomes) {
        if (Test-Path -LiteralPath $odxPythonHome) {
            foreach ($odxExe in @(Get-ChildItem -LiteralPath $odxPythonHome -Filter python.exe -Recurse -File -ErrorAction SilentlyContinue)) {
                $odxCandidates.Add($odxExe.FullName)
            }
        }
    }
    $odxBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $odxBundledPython) { $odxCandidates.Add($odxBundledPython) }
    $odxPython = $null
    $odxTkFailed = $false
    $odxOldPython = $false
    $odxProbe = @'
import sys
if sys.version_info < (3, 10):
    print('ODX_PYTHON_TOO_OLD')
    sys.exit(2)
try:
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.destroy()
except Exception:
    print('ODX_PYTHON_TK_FAILED')
    sys.exit(3)
print('ODX_PYTHON_READY')
'@
    foreach ($odxCandidate in @($odxCandidates | Select-Object -Unique)) {
        try {
            $odxProbeOutput = @(& $odxCandidate -c $odxProbe 2>$null)
            if ($LASTEXITCODE -eq 0 -and $odxProbeOutput -contains 'ODX_PYTHON_READY') { $odxPython = $odxCandidate; break }
            if ($odxProbeOutput -contains 'ODX_PYTHON_TK_FAILED') { $odxTkFailed = $true }
            if ($odxProbeOutput -contains 'ODX_PYTHON_TOO_OLD') { $odxOldPython = $true }
        } catch { }
    }
    if (-not $odxPython) {
        if ($odxTkFailed) {
            throw 'Da tim thay Python nhung Tkinter/Tcl-Tk khong hoat dong. Hay Modify/Repair Python va bat Tcl/Tk. Kiem tra trong CMD: py -m tkinter.'
        }
        if ($odxOldPython) { throw 'Python hien tai cu hon 3.10. Hay cai Python 3.10+ kem Tcl/Tk.' }
        if ($odxManagers.Count -gt 0) {
            throw 'Da tim thay Python Install Manager nhung chua tim duoc Python runtime dung duoc. Mo CMD trong may nay, chay: pymanager install 3.13 . Sau do kiem tra: py -3.13 -m tkinter .'
        }
        throw 'Chua tim duoc Python 3.10+ co Tcl/Tk. Neu moi cai Python Install Manager, mo CMD chay: pymanager install 3.13 . Hoac cai Python Windows installer (64-bit), bat Tcl/Tk va Add Python to PATH.'
    }

    if (-not $odxIsCheck -and -not $odxIsDoctor -and -not $odxIsSelfTest -and -not $odxIsCleanup) {
        $odxIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $odxPrincipal = [Security.Principal.WindowsPrincipal]::new($odxIdentity)
        if (-not $odxPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            # Only fixed flags are passed when requesting elevation.
            $odxElevatedArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'))
            foreach ($odxFlag in @('--host', '--player', '--cleanup')) {
                if ($odxToolArgs -contains $odxFlag) { $odxElevatedArgs += $odxFlag }
            }
            $odxElevated = Start-Process -FilePath powershell.exe -ArgumentList $odxElevatedArgs -Verb RunAs -WindowStyle Hidden -PassThru -Wait
            exit $odxElevated.ExitCode
        }
    }

    if ($odxIsCheck) {
        $odxScriptArgs = @('-B', (Join-Path $odxToolDir 'odx_check_pass.py'))
        $odxScriptArgs += @($odxToolArgs | Where-Object { $_ -ne '--check' })
    } elseif ($odxIsDoctor) {
        $odxScriptArgs = @('-B', (Join-Path $odxToolDir 'odx_diagnostics.py'))
    } elseif ($odxIsSelfTest) {
        $odxScriptArgs = @('-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v')
    } elseif ($odxIsCleanup) {
        $odxScriptArgs = @('-B', (Join-Path $odxToolDir 'odx_diagnostics.py'), '--cleanup')
    } else {
        $odxScriptArgs = @('-B', (Join-Path $odxToolDir 'odx_gui.py'))
        $odxScriptArgs += @($odxToolArgs | Where-Object { $_ -in '--host', '--player' })
    }
    & $odxPython @odxScriptArgs
    exit $LASTEXITCODE
} catch {
    Write-Host ('[LOI] ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
