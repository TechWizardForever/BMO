#!/usr/bin/env python3
"""List or test user-supplied legacy Windows .scr screensavers through Wine."""
from __future__ import annotations

import argparse
import time

import config
from systems.projects import ProjectManager
from systems.windows_screensaver import WindowsScreensaverManager


def manager():
    # We only need removable-drive discovery here; avoid opening project state.
    def removable_roots():
        roots = []
        from pathlib import Path
        import os
        user = os.environ.get("USER", "")
        for base in (Path("/media") / user, Path("/run/media") / user, Path("/mnt")):
            if base.is_dir():
                try:
                    roots.extend([p for p in base.iterdir() if p.is_dir() and os.access(p, os.R_OK)])
                except OSError:
                    pass
        return roots

    return WindowsScreensaverManager(
        config.WINDOWS_SCREENSAVER_DIR,
        removable_provider=removable_roots,
        wine_binary=config.WINDOWS_SCREENSAVER_WINE_BINARY,
        wine_prefix=config.WINDOWS_SCREENSAVER_WINEPREFIX,
        rotate_seconds=config.WINDOWS_SCREENSAVER_ROTATE_SECONDS,
        startup_grace_seconds=config.WINDOWS_SCREENSAVER_STARTUP_GRACE_SECONDS,
        search_removable=config.WINDOWS_SCREENSAVER_SEARCH_REMOVABLE,
        max_discovered=config.WINDOWS_SCREENSAVER_MAX_DISCOVERED,
        enabled=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "list", "run"))
    parser.add_argument("name", nargs="?", help="Substring of the SCR filename for run")
    args = parser.parse_args()
    mgr = manager()
    savers = mgr.discover()
    if args.command == "status":
        status = mgr.status()
        print(f"Enabled: {status.get('enabled')}")
        print(f"Idle trigger: {config.CRT_SCREENSAVER_IDLE_SECONDS:.0f} seconds")
        print(f"Rotation: {config.WINDOWS_SCREENSAVER_ROTATE_SECONDS:.0f} seconds")
        print(f"Wine available: {status.get('wine_available')}")
        print(f"Local folder: {status.get('directory')}")
        print(f"Discovered SCR files: {status.get('discovered_count')}")
        print(f"Quarantined this session: {status.get('quarantined_count', 0)}")
        if status.get('current'):
            print(f"Current: {status.get('current')}")
        if status.get('last_error'):
            print(f"Last error: {status.get('last_error')}")
        return
    if args.command == "list":
        print(f"Wine available: {mgr.wine_available()}")
        print(f"Local folder: {mgr.saver_dir}")
        if not savers:
            print("No .scr files found.")
        for path in savers:
            print(path)
        return

    if not savers:
        raise SystemExit("No .scr files found.")
    selected = None
    if args.name:
        needle = args.name.lower()
        selected = next((p for p in savers if needle in p.name.lower()), None)
        if selected is None:
            raise SystemExit(f"No .scr filename contains {args.name!r}.")
    else:
        selected = savers[0]

    # For a manual test, temporarily expose only the selected file by using the
    # manager's internal lifecycle. Ctrl+C returns to the shell.
    print(f"Testing {selected}. Press Ctrl+C to stop.")
    if not mgr.run_path(selected):
        raise SystemExit(mgr.status().get("last_error") or "Could not start screensaver")
    try:
        while mgr.status().get("running"):
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        mgr.stop()


if __name__ == "__main__":
    main()
