"""Shared, read-only capture audits. Camera repair requires an explicit call."""

import csv
from dataclasses import dataclass
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

REQUIRED_FILES = (
    "video.mp4", "frames.csv", "frames_bin.bin", "player.json",
    "session.json", "key_bindings.json", "raw/input_events.csv",
)
_BUTTONS = {
    "left", "mouseleft", "leftbutton", "lbutton", "right", "mouseright",
    "rightbutton", "rbutton", "middle", "mousemiddle", "middlebutton", "mbutton",
    "wheel", "scroll", "mousewheel",
}
_RAW_KINDS = {
    "key_down", "key_up", "button_down", "button_up", "mouse_move",
    "action_start", "action_end", "mouse_wheel", "mouse_scroll", "wheel", "scroll",
}


def _button_allowed(value):
    return re.sub(r"[\s_\-]", "", value.lower()) in _BUTTONS


def _button_values(value):
    text = value.strip()
    if text.lower() in {"", "null", "none", "[]"}:
        return []
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list) or not all(isinstance(v, str) for v in parsed):
            raise ValueError("button array must contain names")
        return parsed
    return [v.strip().strip("\"'") for v in re.split(r"[,;|]", text) if v.strip()]


def _csv_rows(path, required_columns):
    with open(path, "r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if not reader.fieldnames or not required_columns.issubset(reader.fieldnames):
            raise ValueError("missing CSV columns: " + ", ".join(sorted(required_columns)))
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("duplicate CSV columns")
        for row_number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"malformed CSV row {row_number}")
            yield row_number, row


def inspect_input_data(target_path):
    """Return (valid, diagnostics); malformed input never counts as a mouse PASS."""
    target = Path(target_path)
    problems = []
    try:
        for number, row in _csv_rows(target / "raw" / "input_events.csv", {"kind", "value"}):
            kind = row["kind"].strip().lower()
            value = row["value"].strip()
            if kind not in _RAW_KINDS:
                raise ValueError(f"unknown input kind {kind!r} at row {number}")
            if kind in {"button_down", "button_up"}:
                if not _button_allowed(value):
                    problems.append(f"raw input row {number}: disallowed mouse button {value!r}")
            elif kind in {"mouse_wheel", "mouse_scroll", "wheel", "scroll"}:
                if not math.isfinite(float(value)):
                    raise ValueError(f"invalid wheel delta at row {number}")
    except (OSError, UnicodeError, csv.Error, ValueError, TypeError) as exc:
        problems.append(f"raw/input_events.csv: {exc}")

    try:
        rows = 0
        for number, row in _csv_rows(target / "frames.csv", {"input_mouse_buttons"}):
            rows += 1
            for button in _button_values(row["input_mouse_buttons"]):
                if not _button_allowed(button):
                    problems.append(f"frames.csv row {number}: disallowed mouse button {button!r}")
        if not rows:
            problems.append("frames.csv: no frame data")
    except (OSError, UnicodeError, csv.Error, ValueError, TypeError) as exc:
        problems.append(f"frames.csv: {exc}")

    try:
        with open(target / "key_bindings.json", "r", encoding="utf-8-sig") as stream:
            data = json.load(stream)
        bindings = data.get("bindings") if isinstance(data, dict) else None
        if not isinstance(bindings, dict) or not bindings:
            raise ValueError("bindings must be a nonempty object")
        for name in bindings:
            if any(marker in name.lower() for marker in ("mouse", "button", "click", "xbutton", "side", "thumb")):
                if not _button_allowed(name):
                    problems.append(f"key_bindings.json: disallowed mouse binding {name!r}")
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        problems.append(f"key_bindings.json: {exc}")
    return not problems, problems


def _native_report(stdout, target_path):
    """Parse odx 0.3.5 `validate --json`: an object mapping capture paths to issues."""
    report = json.loads(stdout)
    if not isinstance(report, dict) or len(report) != 1:
        raise ValueError("ODX report must be an object keyed by capture path")
    expected = os.path.normcase(os.path.abspath(target_path))
    matching = [issues for path, issues in report.items()
                if os.path.normcase(os.path.abspath(path)) == expected]
    if len(matching) != 1 or not isinstance(matching[0], list):
        raise ValueError("ODX report does not contain the requested capture")
    issues = matching[0]
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("severity") not in {"error", "warning", "info"}:
            raise ValueError("ODX report contains an unrecognized issue")
    return issues


def validate_and_inspect_capture(target_path, is_room_mode=True, odx_root=None, on_message=None,
                                 native_timeout=60):
    """Audit without repair; return True only for a verified successful audit."""
    def emit(message):
        for line in str(message).splitlines() or [""]:
            try:
                print(line)
            except (OSError, ValueError, UnicodeError):
                pass
            if on_message is not None:
                try:
                    on_message(line)
                except Exception:
                    # A display callback must never alter the audit's outcome.
                    pass

    target = Path(target_path).resolve()
    emit("\n" + "=" * 72)
    emit(f"  [KIEM DINH] {target}")
    if not target.is_dir():
        emit("  [FAIL] Khong tim thay thu muc capture.")
        return False
    required = list(REQUIRED_FILES) + (["events.jsonl"] if is_room_mode else [])
    missing = []
    for name in required:
        path = target / name
        try:
            present = path.is_file() and path.stat().st_size > 0
        except OSError:
            present = False
        if not present:
            missing.append(name)
            emit(f"  [FAIL] File thieu hoac rong: {name}")
    input_ok, input_problems = inspect_input_data(target)
    for problem in input_problems:
        emit(f"  [FAIL] {problem}")
    native_ok = False
    errors = warnings = 0
    native_summary = "chua xac nhan duoc"
    try:
        if odx_root is None:
            from odx_runtime import resolve_odx_root
            odx_root = resolve_odx_root()
        binary = Path(odx_root) / "bin" / "odx.exe"
        if not binary.is_file():
            raise FileNotFoundError(f"Khong tim thay validator: {binary}")
        result = subprocess.run(
            [str(binary), "validate", str(target), "--json"], capture_output=True,
            text=True, encoding="utf-8-sig", errors="strict", timeout=native_timeout,
        )
        issues = _native_report(result.stdout, str(target))
        errors = sum(issue["severity"] == "error" for issue in issues)
        warnings = sum(issue["severity"] == "warning" for issue in issues)
        native_summary = f"{errors} errors, {warnings} warnings"
        for issue in issues:
            emit(f"  [{issue['severity'].upper()}] {issue.get('rule', '')}: {issue.get('message', '')}")
        native_ok = result.returncode == 0 and errors == 0 and not result.stderr.strip()
        if result.returncode != 0:
            emit(f"  [FAIL] ODX validate exit code: {result.returncode}")
        if result.stderr.strip():
            emit(f"  [FAIL] ODX validator stderr: {result.stderr.strip()}")
    except (OSError, ValueError, TypeError, UnicodeError, subprocess.SubprocessError) as exc:
        emit(f"  [FAIL] Khong the xac nhan ket qua ODX validate: {exc}")
    passed = not missing and input_ok and native_ok
    status = "PASS" if passed else "FAIL / CHUA DAT"
    emit(f"  ==> {status} (ODX: {native_summary})")
    emit("=" * 72)
    return passed


@dataclass(frozen=True)
class CaptureAuditResult:
    passed: bool
    target: str
    error: str = ""
    cancelled: bool = False
    duration_seconds: float | None = None


def capture_duration_seconds(target):
    """Read the actual native session duration without changing capture data."""
    try:
        data = json.loads((Path(target) / "session.json").read_text(encoding="utf-8-sig"))
        value = data.get("duration_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
            return float(value)
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError, OverflowError):
        pass
    return None


def _capture_snapshot(target):
    """All room artifacts must exist and stop changing before/through an audit."""
    signature, problems = [], []
    for name in REQUIRED_FILES + ("events.jsonl",):
        path = Path(target) / name
        try:
            stat = path.stat()
            if not path.is_file() or stat.st_size <= 0:
                raise ValueError("thiếu hoặc rỗng")
            signature.append((name, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
        except (OSError, ValueError) as exc:
            problems.append(f"{name}: {exc}")
    return tuple(signature), problems


def validate_finalized_capture(task_id, follower, *, odx_root=None, should_continue=None,
                               on_message=None, timeout=90, settle_seconds=1.0,
                               retry_seconds=2.0):
    """Wait for Odx's local save and stable artifacts; retry read-only audits.

    A stop event or the existence of events.jsonl alone is not a saved capture.
    Slow VM disks and temporarily locked files remain pending within this budget;
    neither a native ALL OK nor a timeout can substitute for a successful audit.
    """
    if odx_root is None:
        from odx_runtime import resolve_odx_root
        odx_root = resolve_odx_root()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(task_id)):
        return CaptureAuditResult(False, "", "Mã task không hợp lệ.")
    target = str(Path(odx_root) / "data" / "captures" / task_id)
    deadline = time.monotonic() + max(0, timeout)
    previous, stable_since = None, None
    attempts, retry_at, last_notice = 0, 0, float("-inf")
    reason = "chưa nhận xác nhận Odx lưu xong đúng task"

    def active():
        return should_continue is None or should_continue()

    def note(message):
        try:
            if on_message is not None:
                on_message(message)
            else:
                print(message)
        except Exception:
            pass

    while time.monotonic() < deadline:
        if not active():
            return CaptureAuditResult(False, target, cancelled=True)
        follower.check_new_lines()
        kept = getattr(follower, "last_kept_path", None)
        if kept and Path(kept).name == task_id and Path(kept).is_absolute():
            target = str(Path(kept))
        result = getattr(follower, "last_round_result", None)
        saved = bool(result and result[0] == task_id and result[1])
        busy = (getattr(follower, "current_recording", None) == task_id or
                getattr(follower, "last_armed", None) == task_id)
        snapshot, problems = _capture_snapshot(target)
        identity = (os.path.normcase(os.path.abspath(target)), snapshot)
        now = time.monotonic()
        if not saved or busy or problems:
            previous, stable_since = None, None
            reason = ("chưa nhận xác nhận Odx lưu xong đúng task" if not saved or busy
                      else "file chưa sẵn sàng: " + "; ".join(problems))
        elif identity != previous:
            previous, stable_since = identity, now
            reason = "đang chờ toàn bộ file ngừng thay đổi"
        elif now - stable_since >= settle_seconds and now >= retry_at:
            if not active():
                return CaptureAuditResult(False, target, cancelled=True)
            attempts += 1
            details = []

            def audit_message(line):
                if any(tag in line for tag in ("[FAIL]", "[ERROR]")) and len(details) < 3:
                    details.append(line.strip())
                note(line)

            note(f"[AUDIT] Kiểm định [{task_id}], lần {attempts}: {target}")
            try:
                passed = validate_and_inspect_capture(
                    target, is_room_mode=True, odx_root=odx_root, on_message=audit_message,
                    native_timeout=max(0.01, min(60, deadline - time.monotonic())))
            except Exception as exc:
                passed = False
                details.append(str(exc))
            if not active():
                return CaptureAuditResult(False, target, cancelled=True)
            after, after_problems = _capture_snapshot(target)
            if after != snapshot or after_problems:
                previous, stable_since = None, None
                reason = "file tiếp tục thay đổi trong lúc kiểm định; chờ ghi xong rồi kiểm tra lại"
            elif passed:
                return CaptureAuditResult(True, target, duration_seconds=capture_duration_seconds(target))
            else:
                reason = "; ".join(details) or "kiểm định chưa đạt; xem chi tiết AUDIT phía trên"
            retry_at = time.monotonic() + min(10, retry_seconds * attempts)
            note(f"[WAIT] [{task_id}] {reason}. Sẽ tự kiểm tra lại trong thời gian chờ.")
        if now - last_notice >= 5:
            note(f"[WAIT] Chờ dữ liệu [{task_id}]: {reason}. Thư mục: {target}")
            last_notice = now
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    if not active():
        return CaptureAuditResult(False, target, cancelled=True)
    error = f"Hết {timeout:g}s chờ lưu/kiểm định: {reason}. Thư mục: {target}"
    note(f"[FAIL] [{task_id}] {error}")
    return CaptureAuditResult(False, target, error)


def heal_camera_nulls_if_needed(target_path):
    """Explicit CSV-only repair with a backup and atomic write; never invent zeros.

    An entirely missing column, nonfinite value, malformed CSV, or existing backup
    aborts the repair. Audits never call this function. Binary data is untouched.
    """
    path = Path(target_path) / "frames.csv"
    temporary = None
    try:
        original = path.read_bytes()
        reader = csv.DictReader(io.StringIO(original.decode("utf-8-sig"), newline=""), strict=True)
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)):
            return False
        columns = [name for name in fields if name.startswith(("camera_c2w_matrix_", "C2W_"))]
        if not columns:
            return False
        rows = list(reader)
        if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
            return False
        changed = False
        for column in columns:
            known = []
            for index, row in enumerate(rows):
                value = row[column].strip()
                if value.lower() in {"", "null"}:
                    changed = True
                elif not math.isfinite(float(value)):
                    return False
                else:
                    known.append(index)
            if not known:
                return False
            previous = rows[known[0]][column]
            for row in rows:
                if row[column].strip().lower() in {"", "null"}:
                    row[column] = previous
                else:
                    previous = row[column]
        if not changed or path.read_bytes() != original:
            return False
        backup = path.with_name(path.name + ".before-heal.bak")
        with open(backup, "xb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix=".frames-heal-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != original:
            return False
        os.replace(temporary, path)
        temporary = None
        print(f"[REPAIR] frames.csv repaired; original: {backup}. frames_bin.bin was not changed.")
        return True
    except (OSError, UnicodeError, csv.Error, ValueError, TypeError):
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
