"""OS adapters (portability plan P2): the only place BMO talks to OS-specific tools.

Each adapter says whether it works here (`available()` -> (ok, reason)) and
offers plain operations; the rest of BMO asks `current()` instead of calling
wmctrl, xdotool, xrandr and friends itself. Linux only (decided
2026-10-19); X11 is the implemented desktop, others report "not here".
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Window:
    window_id: str   # X11 id as hex text, e.g. "0x04c00003"
    pid: int
    wm_class: str
    title: str


class Unavailable(RuntimeError):
    """The OS can't do this here; the message says why (e.g. which package is missing)."""


_DESKTOP = None


def desktop():
    """Windows, input and screen capture for the running session (cached)."""
    global _DESKTOP
    if _DESKTOP is None:
        from systems.adapters.x11 import X11Desktop
        _DESKTOP = X11Desktop()
    return _DESKTOP


def set_desktop(adapter) -> None:
    """Tests and other sessions swap the desktop adapter here."""
    global _DESKTOP
    _DESKTOP = adapter


def accessibility():
    """Reading and operating app controls (AT-SPI on Linux); None where it isn't available."""
    from systems.adapters.atspi import host_accessibility_backend
    return host_accessibility_backend()


_SYSTEM = None


def system():
    """Trash, open-with-default-app, administrator authorization (cached)."""
    global _SYSTEM
    if _SYSTEM is None:
        from systems.adapters.linux_system import LinuxSystem
        _SYSTEM = LinuxSystem()
    return _SYSTEM


def services():
    """Start BMO with the desktop session (systemd user service + XDG autostart on Linux)."""
    from systems.autostart import AutostartManager
    import config
    return AutostartManager(config.APP_ROOT)


# Modules that ARE the Linux implementations (allowed to call OS tools directly).
# Everything else goes through the functions above. tests/test_adapter_boundary.py
# keeps it that way.
LINUX_IMPLEMENTATION_MODULES = (
    "systems/adapters/",          # windows, input, screen, idle, display, accessibility, system actions
    "systems/sound.py",           # pactl (PulseAudio / PipeWire)
    "systems/desktop_settings.py",  # xfconf-query (Xfce), gsettings (GNOME-style)
    "systems/apps.py",            # XDG app menu + gtk-launch
    "systems/autostart.py",       # systemd user service + XDG autostart
    "systems/host.py",            # detection: knows which tools exist
    "systems/games.py",           # Dolphin (native or Flatpak)
    "systems/diagnostics.py",     # lm-sensors
    "systems/windows_screensaver.py",  # Wine for Windows .scr savers
    "systems/wifi.py",            # NetworkManager (nmcli, nm-applet): first boot's internet step
    "systems/saver_guard.py",     # xprop, D-Bus power-management inhibitors, pgrep: movie/game check
    "systems/media.py",           # Celluloid and MPRIS over gdbus: movies and shows by voice
    "systems/sort_downloads.py",  # xdg-user-dir, gio trash, 7z: sorting the Downloads folder
)
