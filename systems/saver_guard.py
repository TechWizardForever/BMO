"""When the screensaver must wait (2026-10-20): a movie or a game.

The saver starts after 4 minutes with no keyboard or mouse anywhere on the PC (CRT_SCREENSAVER_IDLE_SECONDS),
in standby too, except while:
  - the active window is full screen (a game, a full-screen movie), unless it is BMO's own face;
  - a media or game program has asked the desktop to keep the screen on (browsers and video players do this
    while a video plays: org.freedesktop.PowerManagement.Inhibit, served by xfce4-power-manager on Mint).
    Other programs that ask (seen 2026-10-20: a desktop chat app) don't count;
  - one of BMO's games is running (Dolphin, ES-DE, RetroArch).
Linux/X11 (xprop, D-Bus, pgrep). Each answer is kept for a few seconds; the renderer asks every frame.
"""
from __future__ import annotations

import os
import re
import subprocess
import time

MEDIA_AND_GAMES = ("chrome", "chromium", "firefox", "brave", "vivaldi", "opera", "msedge", "vlc", "mpv",
                   "totem", "celluloid", "smplayer", "mplayer", "kodi", "haruna", "parole", "xplayer",
                   "hypnotix", "dragon", "steam", "lutris", "dolphin", "retroarch", "es-de", "emulationstation")
GAME_PROCESSES = ("dolphin-emu", "es-de", "retroarch", "emulationstation")
CACHE_SECONDS = 5.0


def _out(run, argv, timeout=3) -> str:
    try:
        done = run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return done.stdout if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def media_inhibitors(run=subprocess.run) -> list[str]:
    """Media or game programs currently asking the desktop to keep the screen on."""
    reply = _out(run, ["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.PowerManagement",
                       "/org/freedesktop/PowerManagement/Inhibit",
                       "org.freedesktop.PowerManagement.Inhibit.GetInhibitors"])
    names = re.findall(r'string "([^"]*)"', reply)
    return [n for n in names if any(word in os.path.basename(n).lower() for word in MEDIA_AND_GAMES)]


def fullscreen_window(run=subprocess.run, own_pid: int | None = None) -> str | None:
    """The full-screen active window's name, unless it's BMO's own."""
    active = re.search(r"window id # (0x[0-9a-f]+)", _out(run, ["xprop", "-root", "_NET_ACTIVE_WINDOW"]))
    if not active or int(active.group(1), 16) == 0:
        return None
    props = _out(run, ["xprop", "-id", active.group(1), "_NET_WM_STATE", "_NET_WM_PID", "WM_CLASS"])
    if "_NET_WM_STATE_FULLSCREEN" not in props:
        return None
    pid = re.search(r"_NET_WM_PID\(CARDINAL\) = (\d+)", props)
    if pid and int(pid.group(1)) == (own_pid if own_pid is not None else os.getpid()):
        return None
    name = re.search(r'WM_CLASS\(STRING\) = "[^"]*", "([^"]*)"', props)
    return name.group(1) if name else "a full-screen window"


def game_running(run=subprocess.run) -> str | None:
    for name in GAME_PROCESSES:
        if _out(run, ["pgrep", "-x", name]).strip():
            return name
    return None


class SaverGuard:
    def __init__(self, run=subprocess.run, clock=time.monotonic, own_pid: int | None = None):
        self._run, self._clock, self._own_pid = run, clock, own_pid
        self._checked_at, self._reason = -1e9, None

    def hold_off(self) -> str | None:
        """Why the saver must wait, or None."""
        now = self._clock()
        if now - self._checked_at < CACHE_SECONDS:
            return self._reason
        self._checked_at = now
        window = fullscreen_window(self._run, self._own_pid)
        media = media_inhibitors(self._run)
        game = game_running(self._run)
        self._reason = (f"full screen: {window}" if window else f"playing: {os.path.basename(media[0])}" if media
                        else f"game: {game}" if game else None)
        return self._reason
