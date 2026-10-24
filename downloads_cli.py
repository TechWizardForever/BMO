#!/usr/bin/env python3
"""BMO's brain downloads (installer plan, milestone I4): Main first, the game player and screensavers, the rest, the benchmark.

  status    how far it got, in BMO's words
  start     start the background downloader (does nothing if it is running or everything is done)
  run       the downloader itself (started by BMO; one at a time)
"""
from __future__ import annotations

import argparse
import fcntl
import sys

from systems import downloads


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("status", "start", "run"), nargs="?", default="status")
    args = parser.parse_args(argv)
    if args.command == "status":
        print(downloads.report(downloads.DownloadState()))
        return 0
    if args.command == "start":
        print("Downloader started." if downloads.start_in_background() else "Nothing to start.")
        return 0
    path = downloads.lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("Another downloader is already running.")
            return 0
        ok = downloads.run_all(downloads.DownloadState(), benchmark=downloads.simple_benchmark,
                               extras=downloads.EXTRAS)
        print("All done." if ok else "A download kept failing; it will be tried again at the next start.")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
