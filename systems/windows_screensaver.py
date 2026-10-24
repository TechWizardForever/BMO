"""Legacy Windows .scr screensaver launcher for the CRT build.

Bemo keeps legacy Windows screensaver binaries outside the application source ZIP.
The installer can fetch the published compatibility pack into persistent Bemo
storage, and user-supplied ``.scr`` files or removable-drive copies are also
discovered. This manager runs them through Wine while Bemo keeps listening for
voice commands in the background.
"""
from __future__ import annotations

import os
import random
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from loguru import logger

SILENT_AUDIO_OVERRIDES = "winepulse.drv,winealsa.drv,wineoss.drv,winecoreaudio.drv=d"


class WindowsScreensaverManager:
    def __init__(
        self,
        saver_dir: Path,
        *,
        removable_provider: Callable[[], Iterable[Path]] | None = None,
        wine_binary: str | None = None,
        wine_prefix: Path | None = None,
        rotate_seconds: float = 180.0,
        startup_grace_seconds: float = 2.0,
        search_removable: bool = True,
        max_discovered: int = 200,
        enabled: bool = True,
        desktop_idle_provider: Callable[[], float | None] | None = None,
        idle_threshold_seconds: float = 180.0,
        display=None,
    ):
        self.saver_dir = Path(saver_dir).expanduser()
        self.saver_dir.mkdir(parents=True, exist_ok=True)
        self.removable_provider = removable_provider
        self.wine_binary_override = str(wine_binary).strip() if wine_binary else ""
        self.wine_prefix = Path(wine_prefix).expanduser() if wine_prefix else None
        self.rotate_seconds = max(10.0, float(rotate_seconds))
        self.startup_grace_seconds = max(0.25, float(startup_grace_seconds))
        self.search_removable = bool(search_removable)
        self.max_discovered = max(1, int(max_discovered))
        self.enabled = bool(enabled)
        self.desktop_idle_provider = desktop_idle_provider
        self.idle_threshold_seconds = max(1.0, float(idle_threshold_seconds))

        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._current_path: Path | None = None
        self._started_monotonic = 0.0
        self._last_path: Path | None = None
        self._unexpected_exit = False
        self._last_error = ""
        # Incompatible/crashing legacy savers are ignored for the rest of this
        # Bemo process. Restarting Bemo clears the quarantine and allows retry.
        self._quarantined: set[str] = set()
        # Some savers switch the screen to 640x480 and never switch back (live 2026-10-22: "Jungle" left the
        # desktop at 640x480 after it was woken). The mode before a saver is put back when it ends.
        self.display = display
        self._screen_before: dict | None = None

    # ------------------------------------------------------------------
    # Discovery / capability
    # ------------------------------------------------------------------
    def _wine_binary(self) -> str | None:
        if self.wine_binary_override:
            found = shutil.which(self.wine_binary_override)
            if found:
                return found
            candidate = Path(self.wine_binary_override).expanduser()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        for name in ("wine", "wine64"):
            found = shutil.which(name)
            if found:
                return found
        return None

    def wine_available(self) -> bool:
        return self._wine_binary() is not None

    def _iter_roots(self):
        yield self.saver_dir
        if self.search_removable and self.removable_provider:
            try:
                for root in self.removable_provider() or []:
                    path = Path(root).expanduser()
                    if path.is_dir():
                        yield path
            except Exception:
                logger.exception("Could not enumerate removable drives for Windows screensavers")

    def discover(self) -> list[Path]:
        """Return a bounded, stable list of .scr files without executing them."""
        found: list[Path] = []
        seen: set[str] = set()
        for root in self._iter_roots():
            try:
                for path in root.rglob("*"):
                    if len(found) >= self.max_discovered:
                        return found
                    try:
                        if not path.is_file() or path.suffix.lower() != ".scr":
                            continue
                    except OSError:
                        continue
                    key = str(path.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(path)
            except (OSError, PermissionError):
                continue
        found.sort(key=lambda p: (p.name.lower(), str(p).lower()))
        return found

    def status(self) -> dict:
        with self._lock:
            self._poll_locked()
            discovered = self.discover()
            return {
                "enabled": self.enabled,
                "wine_available": self.wine_available(),
                "directory": str(self.saver_dir),
                "discovered_count": len(discovered),
                "quarantined_count": len(self._quarantined),
                "running": self._proc is not None,
                "current": str(self._current_path) if self._current_path else None,
                "last_error": self._last_error or None,
            }

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------
    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("WINEDEBUG", "-all")
        # Savers stay silent (2026-10-20): with Wine's sound drivers disabled none of them can play audio.
        overrides = env.get("WINEDLLOVERRIDES", "").strip(";")
        env["WINEDLLOVERRIDES"] = ";".join(filter(None, [overrides, SILENT_AUDIO_OVERRIDES]))
        if self.wine_prefix:
            self.wine_prefix.mkdir(parents=True, exist_ok=True)
            env["WINEPREFIX"] = str(self.wine_prefix)
        return env

    @staticmethod
    def _key(path: Path) -> str:
        try:
            return str(path.resolve())
        except OSError:
            return str(path.absolute())

    def _choose(self, savers: list[Path]) -> Path | None:
        healthy = [p for p in savers if self._key(p) not in self._quarantined]
        if not healthy:
            return None
        if len(healthy) == 1:
            return healthy[0]
        choices = [
            p for p in healthy
            if self._last_path is None or self._key(p) != self._key(self._last_path)
        ]
        return random.choice(choices or healthy)

    def _start_locked(self, path: Path, now: float) -> bool:
        wine = self._wine_binary()
        if not wine:
            self._last_error = "Wine is not installed or not on PATH."
            return False
        self._remember_screen_locked()
        try:
            # Some classic savers load BMP/DLL/INI companions using relative
            # paths. Run from the SCR's own directory so those assets resolve.
            self._proc = subprocess.Popen(
                [wine, str(path), "/s"],
                cwd=str(path.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._environment(),
                start_new_session=True,
            )
        except OSError as exc:
            self._last_error = f"Could not start Wine screensaver: {exc}"
            logger.warning(self._last_error)
            self._proc = None
            return False
        self._current_path = path
        self._last_path = path
        self._started_monotonic = now
        self._unexpected_exit = False
        self._last_error = ""
        logger.info("Started Windows screensaver via Wine: {}", path)
        return True

    def _stop_locked(self, *, expected: bool = True) -> None:
        proc = self._proc
        if proc is None:
            self._current_path = None
            return
        try:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.terminate()
                try:
                    proc.wait(timeout=1.2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        proc.kill()
                    try:
                        proc.wait(timeout=0.8)
                    except subprocess.TimeoutExpired:
                        pass
        finally:
            self._proc = None
            self._current_path = None
            self._started_monotonic = 0.0
            if not expected:
                self._unexpected_exit = True
            self._restore_screen_locked()

    def _remember_screen_locked(self) -> None:
        if self.display is None or self._screen_before is not None:
            return
        try:
            self._screen_before = self.display.query()
        except Exception:
            self._screen_before = None

    def _restore_screen_locked(self) -> None:
        before, self._screen_before = self._screen_before, None
        if not before or self.display is None:
            return
        try:
            now = self.display.query()
            same = now and all(now.get(k) == before.get(k) for k in ("output", "width", "height"))
            if same:
                return
            ok, _ = self.display.set_mode(before["output"], before["width"], before["height"],
                                          float(before.get("current_rate") or 0.0))
            logger.info("Screen put back to {}x{} after a screensaver changed it to {} ({})",
                        before["width"], before["height"],
                        f"{now['width']}x{now['height']}" if now else "unknown", "ok" if ok else "failed")
        except Exception:
            logger.exception("Could not put the screen back after a screensaver")

    def run_path(self, path: Path | str) -> bool:
        """Start one specific discovered/user-supplied .scr file immediately."""
        candidate = Path(path).expanduser()
        if candidate.suffix.lower() != ".scr" or not candidate.is_file():
            self._last_error = "Selected screensaver is not a readable .scr file."
            return False
        with self._lock:
            self._stop_locked(expected=True)
            self._quarantined.discard(self._key(candidate))
            return self._start_locked(candidate, time.monotonic())

    def stop(self) -> None:
        with self._lock:
            self._stop_locked(expected=True)

    def _desktop_idle_seconds(self):
        if self.desktop_idle_provider is None:
            return None
        try:
            value = self.desktop_idle_provider()
            return None if value is None else max(0.0, float(value))
        except Exception:
            return None

    def _poll_locked(self, now: float | None = None) -> None:
        if self._proc is None:
            return
        code = self._proc.poll()
        if code is None:
            return
        now = time.monotonic() if now is None else float(now)
        path = self._current_path
        runtime = max(0.0, now - self._started_monotonic)
        logger.info("Windows screensaver exited after {:.2f}s: {}", runtime, path)
        self._proc = None
        self._current_path = None
        self._started_monotonic = 0.0
        self._restore_screen_locked()

        # A valid classic .scr can also exit inside the startup grace window if
        # the user moves the mouse or types immediately after it launches. Check
        # the X server's global idle timer before blaming the saver. This avoids
        # the old cascade where active desktop use quarantined every saver.
        desktop_idle = self._desktop_idle_seconds()
        early_user_wake = (
            path is not None
            and runtime < self.startup_grace_seconds
            and desktop_idle is not None
            and desktop_idle < self.idle_threshold_seconds
        )
        if early_user_wake:
            self._unexpected_exit = True
            self._last_error = ""
            logger.info(
                "Screensaver exited during startup after desktop input; treating as user wake: {}",
                path.name,
            )
        elif path is not None and runtime < self.startup_grace_seconds:
            self._quarantined.add(self._key(path))
            self._last_error = (
                f"Screensaver exited during startup after {runtime:.2f}s "
                f"(code {code}); quarantined for this session: {path.name}"
            )
            self._unexpected_exit = False
            logger.warning(self._last_error)
        else:
            # Classic .scr files normally terminate themselves on keyboard/mouse
            # activity. Once a saver has run stably, an unrequested exit is the
            # best available signal that the user woke it.
            self._unexpected_exit = True

    def tick(self, now: float | None = None) -> dict:
        """Keep one saver running while Bemo is idle.

        Returns ``user_exit=True`` when a saver that had been running stably
        terminates on its own. A saver that dies during startup is quarantined
        and does not reset Bemo's three-minute idle timer.
        """
        now = time.monotonic() if now is None else float(now)
        with self._lock:
            self._poll_locked(now)
            if self._unexpected_exit:
                self._unexpected_exit = False
                return {"running": False, "user_exit": True, "current": None}
            if not self.enabled:
                self._stop_locked(expected=True)
                return {"running": False, "user_exit": False, "current": None}
            if not self.wine_available():
                return {"running": False, "user_exit": False, "current": None, "reason": "wine-missing"}

            if self._proc is not None:
                if now - self._started_monotonic < self.rotate_seconds:
                    return {"running": True, "user_exit": False, "current": str(self._current_path)}
                # Normal timed rotation: this is not user activity.
                self._stop_locked(expected=True)

            savers = self.discover()
            selected = self._choose(savers)
            if not selected:
                reason = "no-healthy-scr-files" if savers else "no-scr-files"
                return {"running": False, "user_exit": False, "current": None, "reason": reason}
            if not self._start_locked(selected, now):
                return {"running": False, "user_exit": False, "current": None, "reason": "launch-failed"}
            return {"running": True, "user_exit": False, "current": str(selected)}
