"""X11 desktop adapter: windows (wmctrl), input (xdotool), screen capture (Pillow, scrot & co).

Moved here unchanged from apps.py, gui_vision.py and computer.py (portability
plan P2, step 1). Works in X11 sessions and for XWayland windows.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from systems.adapters import Unavailable, Window


class X11Desktop:
    name = "X11 (wmctrl, xdotool)"

    def __init__(self, runner=subprocess.run, which=shutil.which, env=None):
        self._run, self._which = runner, which
        self._env = os.environ if env is None else env

    # ------------------------------------------------------------- availability
    def available(self) -> tuple[bool, str]:
        if not self._env.get("DISPLAY"):
            return False, "no X11 display"
        if not self._which("wmctrl"):
            return False, "install wmctrl"
        return True, ""

    def input_available(self) -> bool:
        return bool(self._which("xdotool") and self._env.get("DISPLAY"))

    # ------------------------------------------------------------------ windows
    def windows(self) -> dict[str, Window]:
        """Normal windows (panels and the desktop, on workspace -1, are left out)."""
        out = self._run(["wmctrl", "-lpx"], capture_output=True, text=True, timeout=3, check=False).stdout
        found = {}
        for line in out.splitlines():
            parts = line.split(None, 5)
            if len(parts) < 5 or parts[1] == "-1":
                continue
            try:
                pid = int(parts[2])
            except ValueError:
                pid = 0
            found[parts[0]] = Window(parts[0], pid, parts[3], parts[5] if len(parts) > 5 else "")
        return found

    def window_regions(self, pid: int) -> list[tuple[str, tuple[int, int, int, int], str]]:
        """(window id, (x, y, w, h), title) for each window of a process."""
        if not self._which("wmctrl"):
            return []
        out = self._run(["wmctrl", "-lpG"], capture_output=True, text=True, timeout=5, check=False).stdout
        found = []
        for line in out.splitlines():
            parts = line.split(None, 7)
            if len(parts) >= 7 and parts[2] == str(pid):
                x, y, w, h = (int(v) for v in parts[3:7])
                if w > 50 and h > 50:
                    found.append((parts[0], (x, y, w, h), parts[7] if len(parts) > 7 else ""))
        return found

    def window_ids(self, pid: int) -> list[str]:
        out = self._run(["wmctrl", "-lp"], capture_output=True, text=True, timeout=5, check=False).stdout
        return [line.split()[0] for line in out.splitlines() if len(line.split()) > 2 and line.split()[2] == str(pid)]

    def all_window_ids(self) -> set[str]:
        out = self._run(["wmctrl", "-lp"], capture_output=True, text=True, timeout=5, check=False).stdout
        return {line.split()[0] for line in out.splitlines() if line.split()}

    def activate(self, window_id: str) -> None:
        if self._which("wmctrl"):
            self._run(["wmctrl", "-ia", window_id], capture_output=True, timeout=5, check=False)

    def close(self, window_id: str) -> None:
        """Ask the window to close, like its title-bar X (the app may ask about saving)."""
        self._run(["wmctrl", "-ic", window_id], capture_output=True, timeout=5, check=False)

    def active_window(self) -> int | None:
        if not self._which("xdotool"):
            return None
        out = self._run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=5, check=False)
        try:
            return int(out.stdout.strip())
        except ValueError:
            return None

    def active_window_name(self) -> str:
        exe = self._which("xdotool")
        if not exe or not self._env.get("DISPLAY"):
            return ""
        try:
            done = self._run([exe, "getactivewindow", "getwindowname"], capture_output=True, text=True, timeout=3,
                             check=False)
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    # -------------------------------------------------------------------- input
    def _xdotool(self, *args: str) -> None:
        exe = self._which("xdotool")
        if not exe or not self._env.get("DISPLAY"):
            raise Unavailable("Desktop input backend unavailable; xdotool requires an X11/XWayland DISPLAY.")
        done = self._run([exe, *args], capture_output=True, text=True, timeout=15, check=False)
        if done.returncode != 0:
            raise Unavailable((done.stderr or done.stdout or "xdotool failed").strip())

    def move(self, x: int, y: int) -> None:
        self._xdotool("mousemove", str(int(x)), str(int(y)))

    def click(self, button: int = 1, repeat: int = 1) -> None:
        if repeat > 1:
            self._xdotool("click", "--repeat", str(int(repeat)), str(int(button)))
        else:
            self._xdotool("click", str(int(button)))

    def type_text(self, text: str, delay_ms: int = 10) -> None:
        self._xdotool("type", "--delay", str(int(delay_ms)), "--", str(text))

    def key(self, combo: str) -> None:
        self._xdotool("key", combo)

    # ------------------------------------------------------------------- screen
    def capture(self, region: tuple[int, int, int, int]):
        """Screenshot of a screen region. Returns a Pillow image."""
        from PIL import ImageGrab
        x, y, w, h = region
        return ImageGrab.grab(bbox=(x, y, x + w, y + h))

    def screenshot_command(self) -> tuple[str | None, list[str] | None]:
        if self._env.get("WAYLAND_DISPLAY") and self._which("grim"):
            return "grim", [self._which("grim")]
        if self._which("gnome-screenshot"):
            return "gnome-screenshot", [self._which("gnome-screenshot"), "-f"]
        if self._which("scrot"):
            return "scrot", [self._which("scrot")]
        if self._which("import") and self._env.get("DISPLAY"):
            return "imagemagick-import", [self._which("import"), "-window", "root"]
        return None, None

    # --------------------------------------------------------------------- idle
    def idle_available(self) -> bool:
        return bool(self._which("xprintidle"))

    def idle_seconds(self) -> float:
        """Desktop-wide time since the last keyboard/mouse input. Raises when it can't tell."""
        done = self._run([self._which("xprintidle") or "xprintidle"], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, timeout=0.20, check=True)
        return max(0.0, float(done.stdout.strip()) / 1000.0)
