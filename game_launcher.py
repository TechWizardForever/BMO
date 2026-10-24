#!/usr/bin/env python3
"""CLI and ES-DE launch bridge for Bemo's GameCube-only subsystem."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import config
from systems.games import GameEntry, GameError, GameLibrary, discover_removable_drives


DEFAULT_ESDE_METADATA = "https://gitlab.com/es-de/emulationstation-de/-/raw/master/latest_release.json"


def library() -> GameLibrary:
    return GameLibrary(config.GAME_ROOT, config.APP_ROOT)


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description="Bemo GameCube-only game subsystem")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup")
    setup.add_argument("--fetch-esde", action="store_true")
    setup.add_argument("--metadata-url", default=DEFAULT_ESDE_METADATA)

    scan = sub.add_parser("scan")
    scan.add_argument("--system", choices=["gamecube"])

    resolve = sub.add_parser("resolve")
    resolve.add_argument("query")
    resolve.add_argument("--system", choices=["gamecube"])

    launch = sub.add_parser("launch")
    launch.add_argument("query")
    launch.add_argument("--system", choices=["gamecube"])

    launch_file = sub.add_parser("launch-file")
    launch_file.add_argument("--system", required=True, choices=["gamecube"])
    launch_file.add_argument("--rom", required=True)

    front = sub.add_parser("frontend")
    front.add_argument("--width", type=int, default=config.GAME_FRONTEND_WIDTH)
    front.add_argument("--height", type=int, default=config.GAME_FRONTEND_HEIGHT)

    imp = sub.add_parser("import-usb", help="copy + verify supported GameCube ROMs from removable media")
    imp.add_argument("--source", action="append", default=[], help="explicit source root; may be repeated")

    sub.add_parser("doctor")

    args = parser.parse_args()
    lib = library()
    try:
        if args.command == "setup":
            result = {"ok": True, "paths": lib.ensure_layout()}
            if args.fetch_esde:
                result["esde"] = lib.fetch_esde(args.metadata_url)
            _print(result)
            return 0
        if args.command == "scan":
            games = lib.scan(args.system)
            _print({"ok": True, "count": len(games), "games": [g.public() for g in games]})
            return 0
        if args.command == "resolve":
            game, candidates = lib.find(args.query, args.system)
            _print({"ok": bool(game), "game": game.public() if game else None, "candidates": [g.public() for g in candidates]})
            return 0 if game else 2
        if args.command == "launch":
            game, candidates = lib.find(args.query, args.system)
            if not game:
                _print({"ok": False, "error": "Game not uniquely resolved", "candidates": [g.public() for g in candidates]})
                return 2
            return subprocess.call(lib.command_for(game))
        if args.command == "launch-file":
            rom = Path(args.rom).expanduser().resolve()
            try:
                rom.relative_to(lib.gamecube_dir)
            except ValueError:
                raise GameError("ES-DE requested a game outside Bemo's GameCube library")
            game = GameEntry(rom.stem, args.system, str(rom), rom.name)
            return subprocess.call(lib.command_for(game))
        if args.command == "frontend":
            command = lib.frontend_command(args.width, args.height)
            env = os.environ.copy()
            env.setdefault("APPIMAGE_EXTRACT_AND_RUN", "1")
            return subprocess.call(command, env=env)
        if args.command == "import-usb":
            roots = [Path(item).expanduser() for item in args.source] if args.source else discover_removable_drives()
            result = lib.import_from_roots(roots)
            _print(result)
            return 0 if result.get("ok") else 2
        if args.command == "doctor":
            result = lib.doctor()
            _print(result)
            return 0 if result.get("ok") else 2
    except (GameError, OSError, subprocess.SubprocessError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
