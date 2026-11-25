"""Configure the selected game's native Odx source/profile/audio without focus."""
import base64
import json
import math
import os
from pathlib import Path
import re
import threading
import time


GAMES = {
    "arma3": {"label": "Arma 3", "processes": ("arma3_x64.exe", "arma3.exe"),
              "profile": "arma3.json", "profiles": ("Arma 3", "Arma3", "arma3")},
    "arma-reforger": {"label": "Arma Reforger",
                      "processes": ("ArmaReforgerSteam.exe", "ArmaReforger.exe"),
                      "profile": "arma-reforger.json",
                      "profiles": ("Arma Reforger", "ArmaReforger", "arma-reforger")},
}


def normalize_game(game):
    if game not in GAMES:
        raise ValueError("Game phải là Arma 3 hoặc Arma Reforger.")
    return game


def game_process_names(game):
    return GAMES[normalize_game(game)]["processes"]


def game_process_running(game):
    import odx_backend as backend
    return any(backend.is_game_running(name) for name in game_process_names(game))


def _prepare_launch(game=None):
    import odx_backend as backend
    if game is not None:
        normalize_game(game)
    with backend._capture_test_mode_lock:
        try:
            if backend.find_odx_window() or backend.find_unmanaged_odx_window():
                return False, "Odx đang mở; hãy áp dụng nguồn quay trên giao diện qua tool."
            native_path = os.path.expandvars(r"%APPDATA%\Odx\odx-app.json")
            path = Path(native_path)
            config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
            if not isinstance(config, dict):
                raise ValueError("Invalid native configuration")
            if "RecordAudio" in config and type(config["RecordAudio"]) is not bool:
                raise ValueError("Invalid audio configuration")
            changed = config.get("RecordAudio") is not True
            config["RecordAudio"] = True
            if game is not None:
                spec = GAMES[game]
                profile = Path(backend.ODX_ROOT) / "bin" / "games" / spec["profile"]
                if not profile.is_file():
                    return False, f"Thiếu hồ sơ {spec['label']} trong bộ Odx được chọn."
                profile_data = json.loads(profile.read_text(encoding="utf-8-sig"))
                if not isinstance(profile_data, dict) or profile_data.get("game_title") not in spec["profiles"]:
                    return False, f"Hồ sơ {spec['label']} không đúng game đã chọn."
                process = spec["processes"][0][:-4]
                expected = {"GameProfile": str(profile), "WindowProcess": process}
                for key, value in expected.items():
                    if config.get(key) is not None and not isinstance(config[key], str):
                        raise ValueError("Invalid game configuration")
                    changed = changed or config.get(key) != value
                    config[key] = value
            if changed:
                backend._write_capture_test_json(native_path, config)
            return True, ""
        except (OSError, ValueError, TypeError, UnicodeError):
            return False, "Không đọc/ghi được cấu hình game/âm thanh Odx; chưa thay đổi phiên quay."


def prepare_game_capture_launch(game):
    """Persist only the requested game/source/audio before Odx is launched."""
    return _prepare_launch(normalize_game(game))


def prepare_audio_capture_launch():
    """Enable recording audio without modifying TEST desktop/profile choices."""
    return _prepare_launch()


_MESSAGES = {
    "GAME_UI_LOADING": "Đang chờ Odx tải giao diện nguồn quay/âm thanh.",
    "GAME_NOT_RUNNING": "Chưa thấy cửa sổ game đã chọn đang chạy; hãy mở game rồi thử lại.",
    "GAME_SOURCE_UNAVAILABLE": "Danh sách Odx chưa có cửa sổ game đang chạy; đang quét lại.",
    "GAME_PROFILE_UNAVAILABLE": "Odx chưa có hồ sơ đúng game đã chọn.",
    "GAME_AMBIGUOUS_SOURCE": "Có nhiều cửa sổ game trùng nguồn; chưa tự chọn một cửa sổ bất kỳ.",
    "GAME_CAPTURE_BUSY": "Odx đang quay hoặc lưu; chưa thay đổi nguồn quay hay âm thanh.",
    "GAME_CAPTURE_LOCKED": "Odx đang khóa cấu hình trong room; chưa đổi nguồn/hồ sơ game.",
    "GAME_AUDIO_DISABLED": "Odx đang khóa ghi âm thanh; chưa xác nhận ghi âm.",
    "GAME_SELECTION_CHANGED": "Lựa chọn Odx đã thay đổi trong lúc chuẩn bị; chưa ghi đè lựa chọn mới.",
    "GAME_PROCESS_EXITED": "Cửa sổ game vừa đóng hoặc thay đổi; chưa cho quay.",
    "GAME_READBACK_FAILED": "Odx chưa xác nhận đủ nguồn game, hồ sơ game và ghi âm.",
    "GAME_UI_ERROR": "Không đọc được cấu hình game/âm thanh trên Odx.",
    "GAME_RESPONSE_INVALID": "Xác nhận cấu hình game từ Odx không hợp lệ.",
    "ODX_WINDOW_CHANGED": "Cửa sổ Odx đã thay đổi; đã ngừng áp dụng game.",
    "ODX_WINDOW_NOT_OWNED": "Không truy cập được đúng cửa sổ Odx do tool mở.",
}
_RETRYABLE = {"GAME_UI_LOADING", "GAME_NOT_RUNNING", "GAME_SOURCE_UNAVAILABLE",
              "GAME_PROFILE_UNAVAILABLE", "CAPTURE_COMBO_MISSING", "CAPTURE_SELECTION_UNKNOWN",
              "CAPTURE_UI_LOADING", "CAPTURE_TEST_CHOICES_UNAVAILABLE"}


class GameCaptureError(RuntimeError):
    def __init__(self, code):
        self.code = code
        self.retryable = code in _RETRYABLE
        text = _MESSAGES.get(code)
        if text is None:
            import odx_backend as backend
            text = str(backend._CaptureConfigurationError(code))
        super().__init__(f"{text} [{code}]" if code in _MESSAGES else text)


def _capture_action_preamble():
    """Use native COM patterns; managed WPF actions can activate hidden Odx.

    Interface GUIDs and vtable slots follow the Windows SDK UIAutomationClient.h.
    Each action rechecks the exact owned window and control runtime identity.
    """
    from odx_uia import LEGACY_READY_SOURCE
    interfaces = r'''
[ComImport,Guid("c690fdb2-27a8-423c-812d-429773c9084e"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IGameItemContainer {
    [PreserveSig]int FindItemByProperty(ILegacyReadyElement start,int property,
        [MarshalAs(UnmanagedType.Struct)]object value,out ILegacyReadyElement found);
}
[ComImport,Guid("619be086-1f4e-4ee4-bafa-210128738730"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IGameExpand {
    [PreserveSig]int Expand();
    [PreserveSig]int Collapse();
    [PreserveSig]int GetCurrentState(out int state);
}
[ComImport,Guid("6ba3d7a6-04cf-4f11-8793-a8d1cde9969f"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IGameVirtualizedItem { [PreserveSig]int Realize(); }
[ComImport,Guid("94cf8058-9b8d-4ab9-8bfd-4cd0a33c8c70"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IGameToggle {
    [PreserveSig]int Toggle();
    [PreserveSig]int GetCurrentState(out int state);
}
'''
    methods = r'''
    static ILegacyReadyElement CaptureControl(ILegacyReadyAutomation client,long hwnd,int pid,
        string id,int type,int[] runtimeId,out ILegacyReadyElement window) {
        Check(client.ElementFromHandle(new IntPtr(hwnd),out window));
        if(Convert.ToInt32(Property(window,30002))!=pid ||
           !String.Equals(Convert.ToString(Property(window,30005)),"Odx Capture",StringComparison.Ordinal))
            throw new InvalidOperationException("ODX_WINDOW_CHANGED");
        IntPtr condition=IntPtr.Zero;ILegacyReadyElement control=null;
        try {
            Check(client.CreatePropertyCondition(30011,id,out condition));
            Check(window.FindFirst(4,condition,out control));
            if(control==null)throw new InvalidOperationException("GAME_UI_LOADING");
            int[] actual;Check(control.GetRuntimeId(out actual));
            if(runtimeId==null || actual==null || runtimeId.Length!=actual.Length)
                throw new InvalidOperationException("GAME_SELECTION_CHANGED");
            for(int i=0;i<actual.Length;i++)if(actual[i]!=runtimeId[i])
                throw new InvalidOperationException("GAME_SELECTION_CHANGED");
            if(Convert.ToInt32(Property(control,30002))!=pid || Convert.ToInt32(Property(control,30003))!=type ||
               !Convert.ToBoolean(Property(control,30010)))throw new InvalidOperationException("GAME_CAPTURE_LOCKED");
            return control;
        } catch {Release(control);throw;}
        finally {if(condition!=IntPtr.Zero)Marshal.Release(condition);}
    }
    public static void CaptureSelect(long hwnd,int pid,string id,int[] runtimeId,string name) {
        object clientObject=null,container=null,pattern=null,virtualized=null,expander=null,closePattern=null;
        bool wasCollapsed=false;
        ILegacyReadyElement window=null,box=null,item=null,duplicate=null;
        try {
            clientObject=Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("ff48dba4-60ef-4201-aa87-54103eef594e")));
            var client=(ILegacyReadyAutomation)clientObject;
            box=CaptureControl(client,hwnd,pid,id,50003,runtimeId,out window);
            Check(box.GetCurrentPattern(10005,out expander));
            int expandedState;Check(((IGameExpand)expander).GetCurrentState(out expandedState));
            wasCollapsed=expandedState==0;
            Check(box.GetCurrentPattern(10019,out container));
            if(container==null)throw new InvalidOperationException("GAME_UI_LOADING");
            Check(((IGameItemContainer)container).FindItemByProperty(null,30005,name,out item));
            if(item==null)throw new InvalidOperationException("GAME_SOURCE_UNAVAILABLE");
            Check(((IGameItemContainer)container).FindItemByProperty(item,30005,name,out duplicate));
            if(duplicate!=null)throw new InvalidOperationException("GAME_AMBIGUOUS_SOURCE");
            int status=item.GetCurrentPattern(10020,out virtualized);
            if(status>=0 && virtualized!=null)Check(((IGameVirtualizedItem)virtualized).Realize());
            Check(item.GetCurrentPattern(10018,out pattern));
            Check(((ILegacyReadyPattern)pattern).Select(2));
        } finally {
            if(wasCollapsed && expander!=null) {
                int state;
                if(((IGameExpand)expander).GetCurrentState(out state)>=0 && state!=0)
                    if(box.GetCurrentPattern(10018,out closePattern)>=0 && closePattern!=null)
                        ((ILegacyReadyPattern)closePattern).DoDefaultAction();
            }
            Release(closePattern);Release(expander);
            Release(virtualized);Release(pattern);Release(duplicate);Release(item);Release(container);
            Release(box);Release(window);Release(clientObject);
        }
    }
    public static void CaptureAudio(long hwnd,int pid,int[] runtimeId,bool wanted) {
        object clientObject=null,pattern=null,legacy=null;ILegacyReadyElement window=null,box=null;
        try {
            clientObject=Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("ff48dba4-60ef-4201-aa87-54103eef594e")));
            var client=(ILegacyReadyAutomation)clientObject;
            box=CaptureControl(client,hwnd,pid,"AudioBox",50002,runtimeId,out window);
            Check(box.GetCurrentPattern(10015,out pattern));
            var toggle=(IGameToggle)pattern;int state;Check(toggle.GetCurrentState(out state));
            if((state==1)!=wanted) {
                Check(box.GetCurrentPattern(10018,out legacy));
                Check(((ILegacyReadyPattern)legacy).DoDefaultAction());
            }
        } finally {Release(legacy);Release(pattern);Release(box);Release(window);Release(clientObject);}
    }
'''
    source = LEGACY_READY_SOURCE.replace("public static class OdxLegacyReady", interfaces + "\npublic static class OdxLegacyReady")
    index = source.rfind("}")
    source = source[:index] + methods + source[index:]
    return "if (-not ('OdxLegacyReady' -as [type])) {\nAdd-Type -TypeDefinition @'\n" + source + "\n'@\n}\n"


def _game_capture_script(context, game, action, expected=None, target=None):
    import odx_backend as backend
    # Keep the already-tested virtualized ComboBox lookup/selection/rollback
    # primitives used by desktop TEST mode. No native selectors use coordinates.
    common = backend._capture_test_selection_script(context, "inspect")
    common = common[:common.index("$sourceBox = Get-CaptureCombo 'SourceBox'")]
    common = common.replace("CAPTURE_TEST_RESULT:", "GAME_CAPTURE_RESULT:")
    common = common.replace("CAPTURE_[A-Z_]+|ODX_WINDOW_[A-Z_]+", "CAPTURE_[A-Z_]+|GAME_[A-Z_]+|ODX_WINDOW_[A-Z_]+")
    spec = GAMES[normalize_game(game)] if game is not None else None
    request = dict(action=action, processes=spec["processes"] if spec else (),
                   profiles=spec["profiles"] if spec else (), audio_only=game is None,
                   expected=expected, target=target)
    encoded = base64.b64encode(json.dumps(request, ensure_ascii=False).encode("utf-8")).decode("ascii")
    preamble = _capture_action_preamble() if action in ("apply", "refresh") else ""
    return preamble + common + r'''
$request=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__GAME_REQUEST__'))|ConvertFrom-Json
function Get-GameControl([string]$id) {
    $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty,$id)
    $item=$win.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$condition)
    if(-not $item) {throw 'GAME_UI_LOADING'}
    return $item
}
function Test-GameBusy {
    $button=Get-GameControl 'StartStopButton'
    $label=Normalize-GameLabel ([string]$button.Current.Name)
    if($label -match '^(end round|ket thuc round|cancel recording|huy quay|stop recording|dung quay|stopping|dang dung|processing|dang xu ly|saving|dang luu)') {
        throw 'GAME_CAPTURE_BUSY'
    }
}
function Normalize-GameLabel([string]$label) {
    $text=$label.Normalize([Text.NormalizationForm]::FormD).ToLowerInvariant()
    return ([regex]::Replace($text,'\p{Mn}','').Replace('đ','d')).Trim()
}
function Get-GameAudio {
    $box=Get-GameControl 'AudioBox'
    if($box.Current.ControlType -ne [System.Windows.Automation.ControlType]::CheckBox) {throw 'GAME_UI_LOADING'}
    return $box
}
function Test-GameAudio($box) {
    return $box.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Current.ToggleState -eq [System.Windows.Automation.ToggleState]::On
}
function Set-GameAudio($box,[bool]$wanted) {
    if((Test-GameAudio $box) -eq $wanted) {return}
    if(-not $box.Current.IsEnabled) {throw 'GAME_AUDIO_DISABLED'}
    for($i=0;$i -lt 3;$i++) {
        Test-GameBusy
        [OdxLegacyReady]::CaptureAudio($script:OdxTargetHwnd.ToInt64(),$script:OdxTargetPid,$box.GetRuntimeId(),$wanted)
        if((Test-GameAudio $box) -eq $wanted) {return}
    }
    throw 'GAME_READBACK_FAILED'
}
function Set-GameChoice($box,[string]$name) {
    if((Get-CaptureChoice $box) -ceq $name) {return}
    if(-not $box.Current.IsEnabled) {throw 'GAME_CAPTURE_LOCKED'}
    Test-GameBusy
    [OdxLegacyReady]::CaptureSelect($script:OdxTargetHwnd.ToInt64(),$script:OdxTargetPid,$box.Current.AutomationId,$box.GetRuntimeId(),$name)
    for($i=0;$i -lt 15;$i++) {
        if((Get-CaptureChoice $box) -ceq $name) {return}
        Start-Sleep -Milliseconds 40
    }
    throw 'GAME_READBACK_FAILED'
}
function Get-RunningGameTargets($sourceBox) {
    $live=@(Get-Process -ErrorAction SilentlyContinue|Where-Object {
        $request.processes -icontains ($_.ProcessName+'.exe') -and $_.MainWindowHandle -ne [IntPtr]::Zero -and $_.MainWindowTitle
    })
    if($live.Count -eq 0) {throw 'GAME_NOT_RUNNING'}
    $choices=@()
    foreach($process in $live) {
        $label=$process.MainWindowTitle+' ('+$process.ProcessName+'.exe)'
        try {
            $null=Find-CaptureChoice $sourceBox $label
            $choices+=@{source=$label;process=($process.ProcessName+'.exe');process_id=[int]$process.Id}
        } catch {if($_.Exception.Message -notmatch 'CAPTURE_TEST_CHOICES_UNAVAILABLE') {throw}}
    }
    if($choices.Count -eq 0) {throw 'GAME_SOURCE_UNAVAILABLE'}
    if($choices.Count -ne 1) {throw 'GAME_AMBIGUOUS_SOURCE'}
    return $choices[0]
}
function Test-GameProcess($target) {
    try {
        $process=Get-Process -Id ([int]$target.process_id) -ErrorAction Stop
        return $request.processes -icontains ($process.ProcessName+'.exe') -and
            $process.MainWindowHandle -ne [IntPtr]::Zero -and
            ($process.MainWindowTitle+' ('+$process.ProcessName+'.exe)') -ceq [string]$target.source
    } catch {return $false}
}
Test-GameBusy
$audioBox=Get-GameAudio
if($request.audio_only) {
    $beforeAudio=Test-GameAudio $audioBox
    if($request.action -ceq 'apply') {Set-GameAudio $audioBox $true}
    $actualAudio=Test-GameAudio $audioBox
    Write-CaptureResult @{success=$true;audio=$actualAudio;selection=@{audio=$actualAudio};target=@{audio=$true};changed=($actualAudio -ne $beforeAudio)}
    exit 0
}
$sourceBox=Get-CaptureCombo 'SourceBox';$gameBox=Get-CaptureCombo 'GameBox'
if($request.action -ceq 'refresh') {
    if(-not $sourceBox.Current.IsEnabled -or -not $gameBox.Current.IsEnabled) {throw 'GAME_CAPTURE_LOCKED'}
    $refresh=Get-GameControl 'RefreshButton'
    if(-not $refresh.Current.IsEnabled) {throw 'GAME_CAPTURE_LOCKED'}
    $label=[string]$refresh.Current.Name;$runtime=$refresh.GetRuntimeId()
    Test-GameBusy
    [OdxLegacyReady]::Invoke($script:OdxTargetHwnd.ToInt64(),$script:OdxTargetPid,$refresh.Current.AutomationId,$label,$runtime)
    Write-CaptureResult @{success=$true;refreshed=$true}
    exit 0
}
$before=@{source=(Get-CaptureChoice $sourceBox);game=(Get-CaptureChoice $gameBox);audio=(Test-GameAudio $audioBox)}
$chosen=Get-RunningGameTargets $sourceBox
$profile=$null
foreach($name in $request.profiles) {
    try {$null=Find-CaptureChoice $gameBox $name;$profile=[string]$name;break}
    catch {if($_.Exception.Message -notmatch 'CAPTURE_TEST_CHOICES_UNAVAILABLE') {throw}}
}
if(-not $profile) {throw 'GAME_PROFILE_UNAVAILABLE'}
$chosen.game=$profile;$chosen.audio=$true
if($request.action -ceq 'inspect') {
    Write-CaptureResult @{success=$true;selection=$before;target=$chosen;audio=$before.audio}
    exit 0
}
if($request.action -cne 'apply') {throw 'CAPTURE_ACTION_INVALID'}
if($before.source -cne $request.expected.source -or $before.game -cne $request.expected.game -or
   $before.audio -ne [bool]$request.expected.audio) {throw 'GAME_SELECTION_CHANGED'}
if($chosen.source -cne $request.target.source -or $chosen.game -cne $request.target.game -or
   $chosen.process_id -ne [int]$request.target.process_id -or -not (Test-GameProcess $chosen)) {throw 'GAME_PROCESS_EXITED'}
$needChoice=$before.source -cne $chosen.source -or $before.game -cne $chosen.game
if($needChoice -and (-not $sourceBox.Current.IsEnabled -or -not $gameBox.Current.IsEnabled)) {throw 'GAME_CAPTURE_LOCKED'}
# Resolve old/new virtualized items before changing either selector.
$sourceItem=Find-CaptureChoice $sourceBox $chosen.source;$gameItem=Find-CaptureChoice $gameBox $chosen.game
$oldSource=Find-CaptureChoice $sourceBox $before.source;$oldGame=Find-CaptureChoice $gameBox $before.game
try {
    Test-GameBusy
    Set-GameChoice $gameBox $chosen.game
    Set-GameChoice $sourceBox $chosen.source
    Set-GameAudio $audioBox $true
    $after=@{source=(Get-CaptureChoice $sourceBox);game=(Get-CaptureChoice $gameBox);audio=(Test-GameAudio $audioBox)}
    if($after.source -cne $chosen.source -or $after.game -cne $chosen.game -or -not $after.audio -or -not (Test-GameProcess $chosen)) {
        throw 'GAME_READBACK_FAILED'
    }
    Write-CaptureResult @{success=$true;selection=$after;target=$chosen;audio=$true;changed=($needChoice -or -not $before.audio)}
    exit 0
} catch {
    $failure=$_.Exception.Message;$rolledBack=$false
    try {
        Test-GameBusy
        Set-GameChoice $gameBox $before.game
        Set-GameChoice $sourceBox $before.source
        Set-GameAudio $audioBox ([bool]$before.audio)
        $rolledBack=(Get-CaptureChoice $sourceBox) -ceq $before.source -and
            (Get-CaptureChoice $gameBox) -ceq $before.game -and (Test-GameAudio $audioBox) -eq [bool]$before.audio
    } catch {}
    Write-CaptureResult @{success=$false;error=$failure;rolled_back=$rolledBack}
    exit 1
}
'''.replace("__GAME_REQUEST__", encoded)


def _request(context, game, action, timeout, **kwargs):
    import odx_backend as backend
    result = backend.run_ps_script(_game_capture_script(context, game, action, **kwargs), timeout=timeout)
    if result is not None:
        for line in (result.stdout or "").splitlines():
            if not line.startswith("GAME_CAPTURE_RESULT:"):
                continue
            try:
                data = json.loads(line.partition(":")[2])
            except (ValueError, TypeError):
                raise GameCaptureError("GAME_RESPONSE_INVALID") from None
            if not isinstance(data, dict):
                raise GameCaptureError("GAME_RESPONSE_INVALID")
            if (data.get("hwnd"), data.get("pid")) != tuple(context):
                raise GameCaptureError("ODX_WINDOW_CHANGED")
            if result.returncode != 0 or data.get("success") is not True:
                match = re.search(r"\b(CAPTURE_[A-Z_]+|GAME_[A-Z_]+|ODX_WINDOW_[A-Z_]+)\b", str(data.get("error") or ""))
                raise GameCaptureError(match[0] if match else "GAME_UI_ERROR")
            return data
        detail = (result.stdout or "") + "\n" + (result.stderr or "")
        match = re.search(r"\b(CAPTURE_[A-Z_]+|GAME_[A-Z_]+|ODX_WINDOW_[A-Z_]+)\b", detail)
        if match:
            raise GameCaptureError(match[0])
    raise GameCaptureError("GAME_UI_LOADING")


def _configure(game, timeout, cancel_event, on_status):
    import odx_backend as backend

    def notify(message, level):
        if on_status is not None:
            try:
                on_status(message, level)
            except Exception:
                pass  # A display callback cannot change a verified native state.

    def configure():
        with backend._capture_test_mode_lock:
            try:
                seconds = float(timeout)
                if not math.isfinite(seconds) or seconds <= 0:
                    raise ValueError("Thời gian chờ cấu hình không hợp lệ.")
                deadline = time.monotonic() + seconds

                def active():
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("Đã hủy chuẩn bị game/âm thanh Odx.")
                active()
                context = backend._owned_uia_context(min(1, seconds))
                if context is None:
                    raise GameCaptureError("ODX_WINDOW_NOT_OWNED")
                context = tuple(context)

                def request(action, **kwargs):
                    active()
                    if backend._owned_uia_context(0) != context:
                        raise GameCaptureError("ODX_WINDOW_CHANGED")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("Hết thời gian chờ nguồn game và ghi âm Odx.")
                    return _request(context, game, action, min(3, remaining), **kwargs)

                state, stable, refreshed, last_error, announced = None, None, False, None, False
                while time.monotonic() < deadline:
                    active()
                    try:
                        state = request("inspect")
                        signature = (state.get("selection"), state.get("target"))
                        if signature == stable:
                            break
                        stable = signature
                    except GameCaptureError as exc:
                        if not exc.retryable:
                            raise
                        state, stable = None, None
                        if last_error is None or exc.code != "GAME_UI_LOADING":
                            last_error = exc
                        if game is not None and not refreshed and exc.code == "GAME_SOURCE_UNAVAILABLE":
                            request("refresh")
                            refreshed = True
                    if not announced and on_status is not None:
                        label = GAMES[game]["label"] if game else "ghi âm"
                        notify(f"Đang chờ Odx quét cửa sổ {label} và cấu hình nguồn quay...", "WAIT")
                        announced = True
                    delay = min(.2, max(0, deadline - time.monotonic()))
                    cancel_event.wait(delay) if cancel_event is not None else time.sleep(delay)
                else:
                    raise RuntimeError("Hết thời gian chuẩn bị nguồn quay Odx. " + str(last_error or ""))
                active()
                if not isinstance(state.get("selection"), dict) or not isinstance(state.get("target"), dict):
                    raise GameCaptureError("GAME_RESPONSE_INVALID")
                actual = request("apply", expected=state["selection"], target=state["target"])
                active()
                if actual.get("audio") is not True:
                    raise GameCaptureError("GAME_READBACK_FAILED")
                selection, chosen = actual.get("selection", {}), actual.get("target", {})
                if game is not None and (selection.get("source") != chosen.get("source") or selection.get("game") != chosen.get("game")):
                    raise GameCaptureError("GAME_READBACK_FAILED")
                worker.success, worker.audio = True, True
                worker.changed = actual.get("changed") is True
                worker.process = chosen.get("process")
                worker.source_label, worker.game_label = selection.get("source"), selection.get("game")
                worker.status = "confirmed"
                if on_status is not None:
                    notify("Đã xác nhận nguồn quay, hồ sơ game và ghi âm thanh trên Odx.", "SUCCESS")
            except Exception as exc:
                worker.error = str(exc)
                worker.status = "failed"

    worker = threading.Thread(target=configure, daemon=True)
    worker.success, worker.audio, worker.changed = False, False, False
    worker.error, worker.status, worker.game = "", "pending", game
    worker.process = worker.source_label = worker.game_label = None
    worker.start()
    return worker


def set_game_capture(game, timeout=30, cancel_event=None, on_status=None):
    return _configure(normalize_game(game), timeout, cancel_event, on_status)


def ensure_capture_audio(timeout=30, cancel_event=None, on_status=None):
    return _configure(None, timeout, cancel_event, on_status)
