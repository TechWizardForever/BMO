"""Display adapter: xrandr on X11 (portability plan P2, step 3).

Reading modes/refresh rates/monitor id, setting a mode, and the whole-desktop
fit transform, moved unchanged from DisplayManager, which keeps the preview,
auto-revert and saved-settings logic.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess


class XrandrBackend:
    name = "xrandr"

    def __init__(self, runner=None, binary: str = "xrandr"):
        self._run = runner or subprocess.run
        self.binary = binary

    @property
    def real(self) -> bool:
        """False under tests (a fake runner stands in for xrandr)."""
        return self._run is subprocess.run

    def query(self, *, current: bool = False):
        """The connected screen. current=True reads X's own record instead of asking the screen again: a full
        query re-reads every monitor over its cable and adds ~4 KB to the Xorg log (audit 2026-10-24: the
        5-second screen watcher grew it 3 MB an hour). X updates its record itself when a screen is swapped."""
        if self.real and (not os.environ.get("DISPLAY") or not shutil.which(self.binary)):
            return None
        try:
            result = self._run(
                [self.binary, "--current", "--props"] if current else [self.binary, "--props"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return None
        if int(getattr(result, "returncode", 1)) != 0:
            return None
        candidates: list[dict] = []
        active: dict | None = None
        for raw in str(getattr(result, "stdout", "")).splitlines():
            if raw and not raw[0].isspace():
                m = re.match(
                    r"^(\S+)\s+connected(?:(\s+primary))?(?:\s+(\d+)x(\d+)\+[-\d]+\+[-\d]+)?",
                    raw,
                )
                if m:
                    active = {
                        "output": m.group(1),
                        "primary": bool(m.group(2)),
                        "current": (int(m.group(3)), int(m.group(4))) if m.group(3) and m.group(4) else None,
                        "modes": [],
                        "edid": [],
                        "reading_edid": False,
                    }
                    candidates.append(active)
                else:
                    active = None
                continue
            if active is not None and raw[:1].isspace():
                stripped = raw.strip()
                if stripped == "EDID:":
                    active["reading_edid"] = True
                    continue
                if active.get("reading_edid"):
                    if re.fullmatch(r"[0-9A-Fa-f]{16,}", stripped):
                        active["edid"].append(stripped.lower())
                        continue
                    active["reading_edid"] = False
                m = re.match(r"^\s+(\d+)x(\d+)i?\s+(.+)$", raw)
                if m:
                    mode = (int(m.group(1)), int(m.group(2)))
                    if mode not in active["modes"]:
                        active["modes"].append(mode)
                    rates = active.setdefault("rates", {}).setdefault(mode, [])
                    for token in m.group(3).split():
                        value = re.match(r"^(\d+(?:\.\d+)?)", token)
                        if value and float(value.group(1)) not in rates:
                            rates.append(float(value.group(1)))
                        if value and "*" in token:
                            active["current_rate"] = float(value.group(1))
                    if "*" in m.group(3):
                        active["current"] = mode
        usable = [item for item in candidates if item.get("current")]
        if not usable:
            return None
        chosen = next((item for item in usable if item.get("primary")), usable[0])
        current = chosen["current"]
        rates = {mode: tuple(r) for mode, r in (chosen.get("rates") or {}).items()}
        current_rate = float(chosen.get("current_rate") or 0.0)
        edid_hex = "".join(chosen.get("edid") or [])
        if edid_hex:
            monitor_id = "edid:" + hashlib.sha256(edid_hex.encode("ascii")).hexdigest()[:24]
        else:
            monitor_id = "output:" + str(chosen["output"])
        return {"output": chosen["output"], "width": current[0], "height": current[1], "modes": chosen["modes"],
                "monitor_id": monitor_id, "rates": rates, "current_rate": current_rate}

    def set_mode(self, output: str, width: int, height: int, rate: float = 0.0) -> tuple[bool, str | None]:
        argv = [self.binary, "--output", output, "--mode", f"{width}x{height}"]
        if rate:
            argv += ["--rate", f"{float(rate):g}"]
        try:
            result = self._run(argv, capture_output=True, text=True, timeout=8, check=False)
        except Exception:
            return False, "xrandr-failed"
        if int(getattr(result, "returncode", 1)) != 0:
            return False, "xrandr-failed"
        return True, None

    def set_transform(self, output: str, width: int, height: int, matrix: str) -> bool:
        try:
            result = self._run([self.binary, "--output", output, "--fb", f"{int(width)}x{int(height)}",
                                "--transform", matrix], capture_output=True, text=True, timeout=8, check=False)
            return int(getattr(result, "returncode", 1)) == 0
        except Exception:
            return False
