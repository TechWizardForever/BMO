#!/usr/bin/env python3
"""BMO's spoken first boot (installer plan, milestones I2-I3): start, check or close it.

  status            the steps and how far they got
  start             open the first boot (the installer does this; on an installed BMO it is a rehearsal:
                    the next start runs the steps without waiting for "Hey BMO"). --practice-wifi
                    rehearses the Wi-Fi part on a PC that is already online (it really connects).
  finish            close it; steps not done are marked skipped
"""
from __future__ import annotations

import argparse

from systems.first_boot import DONE, PENDING, SKIPPED, STEP_ORDER, FirstBootState


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("status", "start", "finish"), nargs="?", default="status")
    parser.add_argument("--steps", default=",".join(STEP_ORDER), help="comma-separated steps for start")
    parser.add_argument("--practice-wifi", action="store_true",
                        help="rehearse the Wi-Fi part even though this PC is online (it really connects)")
    args = parser.parse_args(argv)
    state = FirstBootState.default()
    if args.command == "start":
        names = [n.strip() for n in args.steps.split(",") if n.strip()]
        unknown = [n for n in names if n not in STEP_ORDER]
        if unknown:
            parser.error(f"unknown step(s): {', '.join(unknown)}; known: {', '.join(STEP_ORDER)}")
        state.begin(names, {"internet": {"practice_wifi": True}} if args.practice_wifi else None)
        print(f"First boot opened ({', '.join(names)}). BMO runs it at his next start.")
        return 0
    if args.command == "finish":
        for step in state.steps():
            if step["status"] not in (DONE, SKIPPED):
                state.mark(step["name"], SKIPPED, {"reason": "closed by first_boot_cli.py finish"})
        print("First boot closed.")
        return 0
    if not state.data:
        print("No first boot on this computer (BMO was installed before first boot existed, or it was removed).")
        return 0
    print(f"First boot: {'open' if state.active() else 'finished ' + str(state.data.get('completed_at'))}")
    for step in state.steps():
        print(f"  {step['name']:<12} {step['status']:<8} tries {step.get('attempts', 0)}  {step.get('result') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
