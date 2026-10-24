"""Readability settings by voice (settings plan S3): text size, pointer size, panel auto-hide.

Deterministic, no model. Backends are picked from the running desktop:
Xfce (xfconf-query) and GNOME-style desktops (gsettings); anything else says
"not on this desktop". Every change is a preview the chat session keeps or
reverts, and a small journal records the previous value first, so a crash
mid-preview is undone at the next start.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

TEXT_LEVELS = (100, 112, 125, 150, 175, 200)   # percent of normal (96 DPI)
POINTER_LEVELS = (24, 32, 48, 64, 96)           # pixels
KINDS = ("text", "pointer", "panel")


def _run(runner, argv):
    done = runner(argv, capture_output=True, text=True, timeout=5, check=False)
    return done.returncode, (done.stdout or "").strip()


class XfceSettings:
    """xfconf-query: xsettings /Xft/DPI and /Gtk/CursorThemeSize, xfce4-panel autohide."""

    name = "Xfce"

    def __init__(self, runner=subprocess.run):
        self._run = runner

    def _get(self, channel, prop):
        code, out = _run(self._run, ["xfconf-query", "-c", channel, "-p", prop])
        return out if code == 0 else None

    def _set(self, channel, prop, kind, value):
        code, _ = _run(self._run, ["xfconf-query", "-c", channel, "-p", prop, "-n", "-t", kind, "-s", str(value)])
        if code != 0:
            raise RuntimeError(f"xfconf-query could not set {prop}")

    def supports(self, kind: str) -> bool:
        return kind in KINDS

    def text_percent(self) -> int:
        dpi = self._get("xsettings", "/Xft/DPI")
        value = int(dpi) if dpi and dpi.lstrip("-").isdigit() else -1
        return 100 if value <= 0 else round(value / 96 * 100)

    def set_text_percent(self, percent: int) -> None:
        if int(percent) == 100:  # back to the desktop's default, as a fresh install has it
            _run(self._run, ["xfconf-query", "-c", "xsettings", "-p", "/Xft/DPI", "-r"])
            return
        self._set("xsettings", "/Xft/DPI", "int", round(96 * int(percent) / 100))

    def pointer(self) -> int:
        size = self._get("xsettings", "/Gtk/CursorThemeSize")
        return int(size) if size and size.isdigit() and int(size) > 0 else 24

    def set_pointer(self, pixels: int) -> None:
        self._set("xsettings", "/Gtk/CursorThemeSize", "int", int(pixels))

    def _panels(self) -> list[str]:
        out = self._get("xfce4-panel", "/panels") or ""
        return [line.strip() for line in out.splitlines() if line.strip().isdigit()]

    def panel_hidden(self) -> bool:
        panels = self._panels()
        return bool(panels) and all((self._get("xfce4-panel", f"/panels/panel-{p}/autohide-behavior") or "0") == "2"
                                    for p in panels)

    def set_panel_hidden(self, hidden: bool) -> None:
        panels = self._panels()
        if not panels:
            raise RuntimeError("no panel found")
        for p in panels:  # 2 = always hide until the pointer touches the edge; 0 = never
            self._set("xfce4-panel", f"/panels/panel-{p}/autohide-behavior", "uint", 2 if hidden else 0)


class GnomeSettings:
    """gsettings org.gnome.desktop.interface (GNOME, Budgie, Unity...). No panel control."""

    name = "GNOME"

    def __init__(self, runner=subprocess.run):
        self._run = runner

    def supports(self, kind: str) -> bool:
        return kind in ("text", "pointer")

    def _get(self, key):
        code, out = _run(self._run, ["gsettings", "get", "org.gnome.desktop.interface", key])
        return out if code == 0 else None

    def _set(self, key, value):
        code, _ = _run(self._run, ["gsettings", "set", "org.gnome.desktop.interface", key, str(value)])
        if code != 0:
            raise RuntimeError(f"gsettings could not set {key}")

    def text_percent(self) -> int:
        try:
            return round(float(self._get("text-scaling-factor") or 1.0) * 100)
        except ValueError:
            return 100

    def set_text_percent(self, percent: int) -> None:
        self._set("text-scaling-factor", f"{int(percent) / 100:.2f}")

    def pointer(self) -> int:
        value = (self._get("cursor-size") or "24").split()[-1]
        return int(value) if value.isdigit() else 24

    def set_pointer(self, pixels: int) -> None:
        self._set("cursor-size", int(pixels))


def host_desktop_settings(env=os.environ):
    desktop = str(env.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "xfce" in desktop and shutil.which("xfconf-query"):
        return XfceSettings()
    if any(d in desktop for d in ("gnome", "budgie", "unity", "pantheon")) and shutil.which("gsettings"):
        return GnomeSettings()
    return None


def read(settings, kind: str):
    return {"text": settings.text_percent, "pointer": settings.pointer, "panel": settings.panel_hidden}[kind]()


def write(settings, kind: str, value) -> None:
    {"text": settings.set_text_percent, "pointer": settings.set_pointer, "panel": settings.set_panel_hidden}[kind](value)


def step(levels, current: int, direction: int) -> int | None:
    """Next level up/down from the current value (which may sit between levels)."""
    if direction > 0:
        return next((v for v in levels if v > current), None)
    return next((v for v in reversed(levels) if v < current), None)


def parse_setting(text: str) -> dict | None:
    """'make the text bigger' -> {'kind': 'text', 'dir': 1}; 'hide the panel' -> {'kind': 'panel', 'value': True}."""
    t = re.sub(r"[^a-z0-9% ]+", " ", str(text or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^(?:(?:can|could|would|will) you |please )+", "", t)
    text_word = r"(?:text|font|fonts|writing|letters|words)"
    pointer_word = r"(?:mouse pointer|mouse cursor|pointer|cursor|mouse arrow|arrow)"
    panel_word = r"(?:panel|taskbar|task bar|menu bar|bottom bar|top bar)"
    up = r"\b(?:bigger|larger|large|increase|grow|up)\b"
    down = r"\b(?:smaller|tinier|decrease|shrink|down)\b"
    normal = r"\b(?:normal|default|regular|reset|standard)\b"
    for kind, word, levels in (("text", text_word, TEXT_LEVELS), ("pointer", pointer_word, POINTER_LEVELS)):
        if not re.search(rf"\b{word}\b", t):
            continue
        if kind == "text" and re.search(r"\b(?:type|send|read|write|copy|translate|say)\b", t):
            return None  # "type this text", "read the text": not about text size
        if kind == "text" and re.search(rf"\b{pointer_word}\b", t):
            continue
        if re.search(normal, t):
            return {"kind": kind, "value": levels[0]}
        size = re.search(r"\b(\d{2,3})\s*(?:%|percent|pixels?|px)?\b", t)
        if size and re.search(r"\b(?:size|to|at|percent|pixels?|%)\b", t):
            return {"kind": kind, "value": int(size.group(1))}
        if re.search(up, t):
            return {"kind": kind, "dir": +1, "big": bool(re.search(r"\b(?:a lot|much|way)\b", t))}
        if re.search(down, t):
            return {"kind": kind, "dir": -1, "big": bool(re.search(r"\b(?:a lot|much|way)\b", t))}
    if re.search(rf"\b{panel_word}\b", t):
        if re.search(r"\b(?:hide|auto ?hide|autohide|get rid of|tuck away)\b", t):
            return {"kind": "panel", "value": True}
        if re.search(r"\b(?:show|unhide|bring back|keep)\b", t) or re.search(r"\b(?:stop|don t|dont) (?:auto ?)?hid", t):
            return {"kind": "panel", "value": False}
    return None


class RevertJournal:
    """The value before an unconfirmed change, so a crash can't leave it changed."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def record(self, kind: str, previous) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"kind": kind, "previous": previous}), encoding="utf-8")

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def pending(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if data.get("kind") in KINDS else None
        except (OSError, ValueError):
            return None
