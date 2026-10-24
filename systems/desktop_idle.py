"""Global desktop-idle detection for the CRT saver.

Pygame only receives keyboard/mouse events while Bemo owns focus.  On the Linux
appliance that made Bemo think the machine was idle while the user was actively
working in another application.  ``xprintidle`` asks the X server for the real
system-wide input idle time.  If it is unavailable Bemo safely falls back to its
existing internal activity timer.
"""
from __future__ import annotations

import subprocess
import threading
import time

from loguru import logger


class DesktopIdleMonitor:
    def __init__(self, *, query_interval_seconds: float = 0.25):
        self.query_interval_seconds = max(0.05, float(query_interval_seconds))
        from systems.adapters import desktop
        self._desktop = desktop()  # xprintidle on X11 (portability plan P2)
        self.binary = "xprintidle" if self._desktop.idle_available() else None
        self._lock = threading.Lock()
        self._last_query = 0.0
        self._last_value = None
        self._warned = False

    @property
    def global_idle_available(self) -> bool:
        return bool(self.binary)

    def query_idle_seconds(self):
        """Return global X11 idle seconds, or None when unavailable."""
        if not self.binary:
            return None
        now = time.monotonic()
        with self._lock:
            if (
                self._last_value is not None
                and now - self._last_query < self.query_interval_seconds
            ):
                return self._last_value
            try:
                value = self._desktop.idle_seconds()
                self._last_value = value
                self._last_query = now
                return value
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self._last_query = now
                if not self._warned:
                    logger.warning(
                        "Global desktop idle query failed; using Bemo-local activity fallback: {}",
                        exc,
                    )
                    self._warned = True
                return None

    def idle_seconds(self, fallback_seconds: float) -> float:
        """Time since the last keyboard/mouse input anywhere, or since BMO was last talked to
        (fallback_seconds), whichever is shorter: talking to him wakes the saver too (2026-10-23)."""
        global_value = self.query_idle_seconds()
        if global_value is None:
            return max(0.0, float(fallback_seconds))
        return min(global_value, max(0.0, float(fallback_seconds)))
