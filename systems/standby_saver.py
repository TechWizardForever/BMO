"""The screensaver while BMO is in standby (2026-10-20).

With "go to sleep" now meaning standby, BMO's own window (which drives the saver while he is awake) is
closed, so the Supervisor takes over: after CRT_SCREENSAVER_IDLE_SECONDS (4 minutes) with no keyboard or
mouse anywhere on the PC, the same legacy Windows savers run full screen, unless systems/saver_guard.py
says a movie or a game is on. Any input (the saver closes itself) or BMO starting ends it.
"""
from __future__ import annotations

import logging
import threading
import time

LOG = logging.getLogger("bemo-standby-saver")


class StandbySaver:
    def __init__(self, *, bemo_running, manager, idle, guard, idle_seconds: float, poll_seconds: float = 2.0):
        self.bemo_running, self.manager, self.idle, self.guard = bemo_running, manager, idle, guard
        self.idle_seconds, self.poll_seconds = float(idle_seconds), float(poll_seconds)
        self.running = False
        self._thread = None
        self._stop = threading.Event()

    def _end(self, why: str) -> str:
        if self.running:
            self.manager.stop()
            self.running = False
            LOG.info("Standby screensaver stopped (%s)", why)
        return why

    def step(self) -> str:
        """One check; the word says what happened (for logs and tests)."""
        if self.bemo_running():
            return self._end("bemo")
        idle = self.idle()
        if idle is None:
            return self._end("no idle time")
        if self.running:
            status = self.manager.tick()
            if status.get("user_exit") or idle < self.poll_seconds + 1:
                return self._end("woken")
            if not status.get("running"):
                self.running = False
                return "ended"
            return "running"
        if idle < self.idle_seconds:
            return "waiting"
        reason = self.guard.hold_off()
        if reason:
            return f"held off ({reason})"
        status = self.manager.tick()
        if status.get("running"):
            self.running = True
            LOG.info("Standby screensaver started after %.0f s idle: %s", idle, status.get("current"))
            return "started"
        return f"not started ({status.get('reason', 'unknown')})"

    def start(self) -> None:
        def loop():
            while not self._stop.wait(self.poll_seconds):
                try:
                    self.step()
                except Exception:
                    LOG.exception("Standby screensaver check failed")
            self._end("supervisor stopping")
        self._thread = threading.Thread(target=loop, name="bemo-standby-saver", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def from_config(bemo_running) -> StandbySaver | None:
    import config
    if not (getattr(config, "ENABLE_CRT_SCREENSAVER", True) and getattr(config, "ENABLE_WINDOWS_SCR_SCREENSAVERS", True)):
        return None
    from systems.desktop_idle import DesktopIdleMonitor
    from systems.saver_guard import SaverGuard
    from systems.windows_screensaver import WindowsScreensaverManager
    from systems.adapters.xrandr import XrandrBackend
    idle = DesktopIdleMonitor(query_interval_seconds=1.0)
    manager = WindowsScreensaverManager(
        config.WINDOWS_SCREENSAVER_DIR, wine_binary=config.WINDOWS_SCREENSAVER_WINE_BINARY,
        wine_prefix=config.WINDOWS_SCREENSAVER_WINEPREFIX, rotate_seconds=config.WINDOWS_SCREENSAVER_ROTATE_SECONDS,
        startup_grace_seconds=config.WINDOWS_SCREENSAVER_STARTUP_GRACE_SECONDS, search_removable=False,
        max_discovered=config.WINDOWS_SCREENSAVER_MAX_DISCOVERED, enabled=True,
        desktop_idle_provider=idle.query_idle_seconds, idle_threshold_seconds=config.CRT_SCREENSAVER_IDLE_SECONDS,
        display=XrandrBackend())
    return StandbySaver(bemo_running=bemo_running, manager=manager, idle=idle.query_idle_seconds,
                        guard=SaverGuard(own_pid=-1), idle_seconds=config.CRT_SCREENSAVER_IDLE_SECONDS)
