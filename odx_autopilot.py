"""Compatibility launcher: all entry points use the maintained desktop engine."""

import argparse
import sys

_ROLES = {
    "host": "host", "master": "host", "server": "host",
    "player": "player", "client": "player", "p": "player",
}


def _launch(role=None):
    # Import only when launched, without module-level cleanup or hotkey hooks.
    from odx_gui import main as gui_main
    original = sys.argv
    try:
        sys.argv = [original[0]] + (["--" + role] if role else [])
        return gui_main()
    finally:
        sys.argv = original


def run_host():
    return _launch("host")


def run_player():
    return _launch("player")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mo ODX Auto-Pilot Studio.")
    parser.add_argument("mode", nargs="?", choices=tuple(_ROLES))
    roles = parser.add_mutually_exclusive_group()
    roles.add_argument("--host", "--master", "--server", action="store_const", const="host", dest="role")
    roles.add_argument("--player", "--client", "--p", action="store_const", const="player", dest="role")
    args = parser.parse_args(argv)
    positional = _ROLES.get(args.mode)
    if positional and args.role and positional != args.role:
        parser.error("Chon mot vai tro duy nhat: host hoac player")
    return _launch(args.role or positional)


if __name__ == "__main__":
    raise SystemExit(main())
