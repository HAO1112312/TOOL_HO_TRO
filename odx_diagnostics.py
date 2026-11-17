"""Read-only startup diagnostics; Odx processes close only with the owning tool."""
import argparse
import json
from pathlib import Path
import sys

from odx_runtime import resolve_odx_root


CLEANUP_DISABLED_MESSAGE = (
    "Odx chỉ đóng khi đóng tool. Chức năng dọn/dừng Odx riêng đã được tắt; "
    "không có tiến trình nào bị đóng."
)


def collect_diagnostics():
    tool = Path(__file__).resolve().parent
    root = Path(resolve_odx_root(str(tool)))
    checks = []
    checks.append({"name": "Python 3.10+", "ok": sys.version_info >= (3, 10), "detail": sys.version.split()[0]})
    try:
        import tkinter
        tk_root = tkinter.Tk()
        try:
            tk_root.withdraw()
            patchlevel = str(tk_root.tk.call("info", "patchlevel"))
        finally:
            tk_root.destroy()
        checks.append({"name": "Tkinter", "ok": True, "detail": patchlevel})
    except Exception as exc:
        checks.append({"name": "Tkinter", "ok": False, "detail": str(exc)})
    for relative in ("Odx Capture.exe", "bin/odx-app.exe", "bin/odx.exe"):
        target = root / relative
        checks.append({"name": relative, "ok": target.is_file(), "detail": str(target)})
    for target in sorted(tool.glob("*.py")):
        try:
            compile(target.read_text(encoding="utf-8-sig"), str(target), "exec")
            checks.append({"name": target.name, "ok": True, "detail": "syntax OK"})
        except (OSError, SyntaxError) as exc:
            checks.append({"name": target.name, "ok": False, "detail": str(exc)})
    for name in ("autopilot_config.json", "version.json"):
        try:
            value = json.loads((tool / name).read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("JSON root must be an object")
            checks.append({"name": name, "ok": True, "detail": "JSON OK"})
        except (OSError, ValueError) as exc:
            checks.append({"name": name, "ok": False, "detail": str(exc)})
    return {"ok": all(check["ok"] for check in checks), "odx_root": str(root), "checks": checks}


def cleanup_selected_install(root=None):
    """Compatibility guard: never enumerate or terminate a selected install."""
    raise RuntimeError(CLEANUP_DISABLED_MESSAGE)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.cleanup:
        print(CLEANUP_DISABLED_MESSAGE)
        return 0
    report = collect_diagnostics()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Odx: {report['odx_root']}")
        for check in report["checks"]:
            print(f"[{'OK' if check['ok'] else 'LOI'}] {check['name']}: {check['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
