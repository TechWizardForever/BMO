#!/usr/bin/env python3
import argparse
from pathlib import Path
from systems.autostart import AutostartManager


def main():
    parser = argparse.ArgumentParser(description="Manage Bemo's Xfce-login standby autostart bridge")
    parser.add_argument("action", choices=("install", "enable", "disable", "status"))
    args = parser.parse_args()
    manager = AutostartManager(Path(__file__).resolve().parent)
    print(getattr(manager, args.action)())


if __name__ == "__main__":
    main()
