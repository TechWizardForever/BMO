#!/usr/bin/env python3
"""After installing: are BMO's locked packages consistent? (installer plan, milestone I1)

Runs `pip check` and fails only on problems among the packages in requirements.lock. Leftovers
from older BMO versions (Chatterbox and its helpers on PCs installed before v1.28.71) are listed
as a note: BMO doesn't use them, and removing them is the owner's call.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LOCK = Path(__file__).resolve().parent / "requirements.lock"
# Declared but deliberately not installed: no Python 3.12 build, and BMO runs the ONNX models.
KNOWN = ("requires tflite-runtime, which is not installed",)


def _key(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def locked_names(lock_text: str) -> set[str]:
    return {_key(line.split("==")[0]) for line in lock_text.splitlines() if "==" in line and not line.startswith("#")}


def judge(pip_check_output: str, locked: set[str]) -> tuple[list[str], list[str]]:
    """(problems among BMO's packages, lines about leftovers)."""
    problems, leftovers = [], []
    for line in pip_check_output.splitlines():
        if not line.strip() or line.startswith("No broken requirements"):
            continue
        if _key(line.split(" ")[0]) in locked:
            if not any(known in line for known in KNOWN):
                problems.append(line)
        else:
            leftovers.append(line)
    return problems, leftovers


def main() -> int:
    output = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True).stdout
    problems, leftovers = judge(output, locked_names(LOCK.read_text()))
    for line in problems:
        print(line, file=sys.stderr)
    if leftovers:
        print("Note: packages from an older BMO that BMO no longer uses (left in place):")
        for line in leftovers:
            print("  " + line)
    if problems:
        print("ERROR: BMO's Python packages are not consistent (listed above).", file=sys.stderr)
        return 1
    print("BMO's Python packages are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
