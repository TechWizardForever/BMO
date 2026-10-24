#!/usr/bin/env python3
"""Verify or restore a full Bemo backup while Bemo is stopped."""
from __future__ import annotations

import argparse
from pathlib import Path

from systems.backup import FullBackupManager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backup_folder")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--destination", help="Empty destination directory for restored Bemo data")
    parser.add_argument("--app-destination", help="Optional empty destination directory for restored Bemo application files")
    args = parser.parse_args()

    folder = Path(args.backup_folder).expanduser().resolve()
    result = FullBackupManager.verify_folder(folder)
    print("verify:", result)
    if args.verify_only:
        return
    if not result.get("ok"):
        raise SystemExit(2)
    if not args.destination:
        raise SystemExit("Provide --destination. Restore only while Bemo is stopped; the destination must be empty.")
    restored = FullBackupManager.restore_data_to(folder, Path(args.destination).expanduser())
    print("restore data:", restored)
    if not restored.get("ok"):
        raise SystemExit(3)
    if args.app_destination:
        app_restored = FullBackupManager.restore_app_to(
            folder, Path(args.app_destination).expanduser()
        )
        print("restore app:", app_restored)
        if not app_restored.get("ok"):
            raise SystemExit(4)


if __name__ == "__main__":
    main()
