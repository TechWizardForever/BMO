"""Gamepads for GameCube games (2026-10-21).

Every time BMO starts a game, the gamepads plugged in get players 1-4 with Dolphin's own "SDL Gamepad"
profile (Sys/Profiles/GCPad), which SDL lays out the same way for Xbox, PlayStation, Switch Pro, 8BitDo and
most other pads. With none plugged in, player 1 is the keyboard (X = A, Z = B, Enter = Start, arrows = stick).
A layout the owner changed in Dolphin is never overwritten: BMO only rewrites a file it wrote itself or
Dolphin's untouched default.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys

LOG = logging.getLogger("bemo-gamepads")

# Dolphin 2609's Sys/Profiles/GCPad/SDL Gamepad.ini. "Button A" etc. match by position (south, east, west,
# north), so a PlayStation Cross or a Switch B is the GameCube A.
PAD_PROFILE = """Buttons/A = `Button A`
Buttons/B = `Button B`
Buttons/X = `Button X`
Buttons/Y = `Button Y`
Buttons/Z = `Shoulder R`
Buttons/Start = `Start`
Main Stick/Up = `Left Y+`
Main Stick/Down = `Left Y-`
Main Stick/Left = `Left X-`
Main Stick/Right = `Left X+`
Main Stick/Calibration = 100.00
C-Stick/Up = `Right Y+`
C-Stick/Down = `Right Y-`
C-Stick/Left = `Right X-`
C-Stick/Right = `Right X+`
C-Stick/Calibration = 100.00
Triggers/L = `Trigger L`
Triggers/R = `Trigger R`
Triggers/L-Analog = `Trigger L`
Triggers/R-Analog = `Trigger R`
D-Pad/Up = `Pad N`
D-Pad/Down = `Pad S`
D-Pad/Left = `Pad W`
D-Pad/Right = `Pad E`
Rumble/Motor = `Motor L` | `Motor R`
"""

# Dolphin's default keyboard layout on Linux (what a fresh GCPadNew.ini holds for player 1).
KEYBOARD_DEVICE = "XInput2/0/Virtual core pointer"
KEYBOARD_PROFILE = """Buttons/A = `X`
Buttons/B = `Z`
Buttons/X = `C`
Buttons/Y = `S`
Buttons/Z = `D`
Buttons/Start = `Return`
Main Stick/Up = `Up`
Main Stick/Down = `Down`
Main Stick/Left = `Left`
Main Stick/Right = `Right`
Main Stick/Modifier = `Shift`
Main Stick/Calibration = 100.00 141.42 100.00 141.42 100.00 141.42 100.00 141.42
C-Stick/Up = `I`
C-Stick/Down = `K`
C-Stick/Left = `J`
C-Stick/Right = `L`
C-Stick/Modifier = `Ctrl`
C-Stick/Calibration = 100.00 141.42 100.00 141.42 100.00 141.42 100.00 141.42
Triggers/L = `Q`
Triggers/R = `W`
D-Pad/Up = `T`
D-Pad/Down = `G`
D-Pad/Left = `F`
D-Pad/Right = `H`
"""

GC_CONTROLLER, NO_DEVICE = 6, 0  # Dolphin's SIDevices values

# SDL's gamepad names, from a short separate process so SDL never starts inside the Supervisor.
_LIST_PADS = """
import os, json
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import pygame
from pygame._sdl2 import controller
pygame.init(); controller.init()
print(json.dumps([controller.name_forindex(i) for i in range(controller.get_count())
                  if controller.is_controller(i)]))
"""


def connected(run=subprocess.run, python=sys.executable) -> list[str]:
    """Names of the gamepads plugged in now (SDL's names, as Dolphin sees them)."""
    try:
        done = run([python, "-c", _LIST_PADS], capture_output=True, text=True, timeout=15)
        names = json.loads((done.stdout or "[]").strip().splitlines()[-1] or "[]")
        return [str(n) for n in names if n][:4]
    except Exception:
        LOG.exception("Could not list gamepads")
        return []


def dolphin_devices(names: list[str]) -> list[str]:
    """Dolphin numbers devices that share a name: two Xbox pads are SDL/0/... and SDL/1/..."""
    seen: dict[str, int] = {}
    devices = []
    for name in names:
        devices.append(f"SDL/{seen.get(name, 0)}/{name}")
        seen[name] = seen.get(name, 0) + 1
    return devices


def render(names: list[str]) -> str:
    if not names:
        sections = [("GCPad1", KEYBOARD_DEVICE, KEYBOARD_PROFILE)]
    else:
        sections = [(f"GCPad{i}", device, PAD_PROFILE) for i, device in enumerate(dolphin_devices(names), 1)]
    return "".join(f"[{port}]\nDevice = {device}\n{profile}" for port, device, profile in sections)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_dolphin_default(text: str) -> bool:
    """Dolphin's own first file: the keyboard layout on player 1 (other players blank or keyboard)."""
    first = text.split("[GCPad2]")[0]
    return f"Device = {KEYBOARD_DEVICE}" in first and all(line in first for line in KEYBOARD_PROFILE.splitlines())


def prepare(config_dir: Path, state_path: Path, names: list[str]) -> list[str]:
    """Write GCPadNew.ini for these pads (when BMO may) and return Dolphin's -C arguments for the ports."""
    path = Path(config_dir) / "GCPadNew.ini"
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    current = path.read_text(encoding="utf-8") if path.exists() else None
    ours = current is None or _sha(current) == state.get("written_sha256") or _is_dolphin_default(current)
    if not ours:
        LOG.info("Gamepads: the owner's own controller layout is kept (%d pad(s) plugged in)", len(names))
        return []
    wanted = render(names)
    if current != wanted:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wanted, encoding="utf-8")
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps({"written_sha256": _sha(wanted), "pads": names}, indent=2) + "\n",
                                encoding="utf-8")
    LOG.info("Gamepads: %d plugged in%s", len(names), f" ({', '.join(names)})" if names else "; keyboard on player 1")
    players = max(1, len(names))
    return [arg for i in range(4)
            for arg in ("-C", f"Dolphin.Core.SIDevice{i}={GC_CONTROLLER if i < players else NO_DEVICE}")]
