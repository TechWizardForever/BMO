#!/usr/bin/env python3
"""Fetch the published legacy Windows screensaver pack used by Bemo.

This intentionally keeps the binaries outside the Bemo source ZIP. On the target
machine it clones the public screensaver collection and installs the complete
``windows95`` subtree under Bemo's persistent data directory so companion files
stay next to their .scr executables.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

DEFAULT_SOURCE = "https://github.com/NinjasCL/windows-screensavers-for-linux.git"
_DATA_DIR = Path(os.environ.get("BEMO_DATA_DIR", str(Path.home() / ".bemo"))).expanduser()
DEFAULT_DEST = _DATA_DIR / "screensavers" / "windows" / "legacy-pack"
DEFAULT_CACHE = _DATA_DIR / "install-cache" / "windows-screensavers-for-linux"


def _run(argv: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=False)


def install_from_directory(source_windows95: Path, destination: Path, *, force: bool = False, source_note: str = "local source") -> dict:
    source_windows95 = Path(source_windows95).expanduser().resolve()
    destination = Path(destination).expanduser()
    if not source_windows95.is_dir():
        raise RuntimeError(f"Screensaver source directory does not exist: {source_windows95}")
    scr_files = sorted(p for p in source_windows95.rglob("*") if p.is_file() and p.suffix.lower() == ".scr")
    if not scr_files:
        raise RuntimeError(f"No .scr files found under {source_windows95}")
    if destination.exists():
        if not force:
            existing = sorted(destination.rglob("*.scr"))
            if existing:
                return {"ok": True, "skipped": True, "count": len(existing), "destination": str(destination)}
            shutil.rmtree(destination)
        else:
            shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_windows95, destination)
    (destination / "BEMO_SOURCE.txt").write_text(
        f"Installed by Bemo legacy screensaver fetcher.\nSource: {source_note}\n",
        encoding="utf-8",
    )
    installed = sorted(destination.rglob("*.scr"))
    return {"ok": True, "skipped": False, "count": len(installed), "destination": str(destination)}


def fetch(source: str = DEFAULT_SOURCE, destination: Path = DEFAULT_DEST, *, force: bool = False, cache: Path = DEFAULT_CACHE) -> dict:
    if not shutil.which("git"):
        raise RuntimeError("git is required to fetch the legacy screensaver pack")

    # Fast restart path: once the persistent destination is complete, later
    # installer runs do not hit the network unless --force was requested.
    existing = sorted(destination.rglob("*.scr")) if destination.is_dir() else []
    if existing and not force:
        return {"ok": True, "skipped": True, "count": len(existing), "destination": str(destination)}

    cache = Path(cache).expanduser()
    cache.parent.mkdir(parents=True, exist_ok=True)
    if (cache / ".git").is_dir():
        # Keep the checkout between installer runs. A fetch interrupted by a
        # freeze can simply be retried against the same repository metadata.
        result = _run(["git", "fetch", "--depth", "1", "origin"], cwd=cache)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "git fetch failed").strip()
            raise RuntimeError(message)
        result = _run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=cache)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "git reset failed").strip()
            raise RuntimeError(message)
    else:
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
        result = _run(["git", "clone", "--depth", "1", source, str(cache)])
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "git clone failed").strip()
            raise RuntimeError(message)

    source_dir = cache / "windows95"
    if not source_dir.is_dir():
        raise RuntimeError("Published screensaver repository did not contain the expected windows95 directory")
    commit = _run(["git", "rev-parse", "HEAD"], cwd=cache)
    commit_id = commit.stdout.strip() if commit.returncode == 0 else "unknown"
    return install_from_directory(source_dir, destination, force=True, source_note=f"{source} @ {commit_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download/install Bemo's published legacy Windows screensaver pack")
    parser.add_argument("command", nargs="?", choices=("fetch", "status"), default="fetch")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    destination = args.destination.expanduser()
    if args.command == "status":
        files = sorted(destination.rglob("*.scr")) if destination.is_dir() else []
        print(f"Destination: {destination}")
        print(f"Installed .scr files: {len(files)}")
        for path in files:
            print(path)
        return
    result = fetch(args.source, destination, force=args.force, cache=args.cache)
    action = "already present" if result.get("skipped") else "installed"
    print(f"Legacy Windows screensavers {action}: {result['count']} .scr files")
    print(f"Location: {result['destination']}")


if __name__ == "__main__":
    main()
