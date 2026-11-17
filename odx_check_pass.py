"""Read-only capture checker. Exit 0 means every requested capture passed."""

import argparse
from pathlib import Path
import sys

from odx_runtime import resolve_odx_root
from odx_validation import validate_and_inspect_capture

TOOL_DIR = Path(__file__).resolve().parent


def validate_folder(target_path, odx_root=None):
    return validate_and_inspect_capture(target_path, is_room_mode=True, odx_root=odx_root)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kiem tra capture ODX (chi doc, khong tu sua du lieu).")
    parser.add_argument("target", nargs="?", help="Duong dan capture hoac ten task trong data/captures")
    args = parser.parse_args(argv)
    try:
        odx_root = Path(resolve_odx_root(str(TOOL_DIR)))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[FAIL] Khong tim thay ODX Capture: {exc}")
        return 2
    captures = odx_root / "data" / "captures"
    if args.target:
        direct = Path(args.target).resolve()
        target = direct if direct.is_dir() else captures / args.target
        if not target.is_dir():
            print(f"[FAIL] Khong tim thay thu muc capture: {args.target}")
            return 2
        return 0 if validate_folder(target, odx_root) else 1
    if not captures.is_dir():
        print(f"[FAIL] Chua co thu muc captures: {captures}")
        return 2
    try:
        folders = sorted(path for path in captures.iterdir() if path.is_dir())
    except OSError as exc:
        print(f"[FAIL] Khong doc duoc thu muc captures: {exc}")
        return 2
    if not folders:
        print(f"[FAIL] Khong co capture nao de kiem tra: {captures}")
        return 2
    results = [validate_folder(folder, odx_root) for folder in folders]
    return 0 if all(results) else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
