"""Deterministic display resolution + CRT safe-area preview manager.

Display changes are previewed first and are only persisted after explicit user
approval.  Resolution and refresh changes use xrandr (any X11 driver).  The
crop ("fit") has two forms:

- fit="desktop" (settings plan S2, approved): the whole desktop is scaled
  and shifted into the safe area with an xrandr transform, so the panel, other
  apps and BMO all fit an overscanning CRT/converter;
- fit="app" (older saved settings, or a driver without transforms): only BMO's
  own window is inset.

A preview automatically rolls back if it is not approved in time, and startup
always re-applies the last approved state, so a crash mid-preview can't leave
the desktop shrunk.

Each screen keeps its own approved settings, by its EDID (2026-10-22: a CRT
needs 1280x720 shrunk 30 px a side, the TV 1920x1080 as it is). A screen with none
yet starts from its own live mode with no shrink.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class DisplayState:
    width: int
    height: int
    crop_left: int = 0
    crop_right: int = 0
    crop_top: int = 0
    crop_bottom: int = 0
    monitor_id: str = ""
    fit: str = "app"      # "desktop": xrandr transform; "app": only BMO's window is inset
    rate: float = 0.0     # refresh in Hz; 0 = whatever the mode defaults to

    @property
    def viewport_width(self) -> int:
        return max(1, self.width - self.crop_left - self.crop_right)

    @property
    def viewport_height(self) -> int:
        return max(1, self.height - self.crop_top - self.crop_bottom)

    def crop_summary(self) -> str:
        return (
            f"left {self.crop_left}, right {self.crop_right}, "
            f"top {self.crop_top}, bottom {self.crop_bottom} pixels"
        )

    @property
    def has_crop(self) -> bool:
        return bool(self.crop_left or self.crop_right or self.crop_top or self.crop_bottom)


def fit_transform(state: DisplayState) -> str:
    """xrandr --transform matrix that shows the whole desktop inside the cropped area.

    Output pixel x maps to desktop x' = a*(x - left); outside the safe area it
    samples outside the desktop and shows black.
    """
    w, h = int(state.width), int(state.height)
    a = w / max(1, state.viewport_width)
    e = h / max(1, state.viewport_height)
    c, f = -state.crop_left * a, -state.crop_top * e
    return ",".join(f"{v:.6f}".rstrip("0").rstrip(".") if "." in f"{v:.6f}" else str(v)
                    for v in (a, 0, c, 0, e, f, 0, 0, 1))


IDENTITY_TRANSFORM = "1,0,0,0,1,0,0,0,1"


class DisplayManager:
    def __init__(
        self,
        state_path: str | Path,
        *,
        fallback_width: int = 1280,
        fallback_height: int = 800,
        preview_timeout_seconds: float = 45.0,
        runner: Callable | None = None,
        xrandr_binary: str = "xrandr",
    ):
        self.state_path = Path(state_path).expanduser()
        self.preview_timeout_seconds = max(10.0, float(preview_timeout_seconds))
        self._runner = runner or subprocess.run
        self.xrandr_binary = xrandr_binary
        from systems.adapters.xrandr import XrandrBackend
        self._backend = XrandrBackend(self._runner, xrandr_binary)
        self._lock = threading.RLock()
        self._generation = 0
        self._pending_baseline: DisplayState | None = None
        self._pending_deadline = 0.0
        self._last_timeout_revert = False
        self._output_name: str | None = None
        self._modes: tuple[tuple[int, int], ...] = ()
        self._rates: dict[tuple[int, int], tuple[float, ...]] = {}
        self._current_rate = 0.0
        self._transforms_work: bool | None = None  # learned on first use

        self._saved_screens: dict[str, DisplayState] = {}
        self._last_screen = ""
        self._load_saved()
        detected = self._query_xrandr()
        saved = self._saved_for(detected[4] if detected else None)
        if detected:
            output, width, height, modes, monitor_id = detected
            self._output_name = output
            self._modes = tuple(modes)
            detected_state = DisplayState(width=width, height=height, monitor_id=monitor_id)
        else:
            detected_state = DisplayState(width=max(1, int(fallback_width)), height=max(1, int(fallback_height)))

        self._saved_state = saved
        current = self._validated_state(saved or detected_state, detected_state)
        self._current_state = DisplayState(*[getattr(current, k) for k in (
            "width", "height", "crop_left", "crop_right", "crop_top", "crop_bottom")],
            monitor_id=detected_state.monitor_id or current.monitor_id, fit=current.fit, rate=current.rate)

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def pending(self) -> bool:
        with self._lock:
            return self._pending_baseline is not None

    def current_state(self) -> DisplayState:
        with self._lock:
            return self._current_state

    def saved_state(self) -> DisplayState | None:
        with self._lock:
            return self._saved_state

    @staticmethod
    def _state_from(data: dict) -> DisplayState:
        return DisplayState(
            width=int(data["width"]),
            height=int(data["height"]),
            crop_left=int(data.get("crop_left", 0)),
            crop_right=int(data.get("crop_right", 0)),
            crop_top=int(data.get("crop_top", 0)),
            crop_bottom=int(data.get("crop_bottom", 0)),
            monitor_id=str(data.get("monitor_id", "")),
            fit=str(data.get("fit", "app")) if data.get("fit") in ("app", "desktop") else "app",
            rate=float(data.get("rate", 0.0) or 0.0),
        )

    def _load_saved(self) -> None:
        """Every screen's approved settings: {"screens": {monitor_id: state}, "last": monitor_id}. A file from
        before 2026-10-22 holds one state; it becomes that screen's entry."""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        try:
            if isinstance(data.get("screens"), dict):
                for key, value in data["screens"].items():
                    try:
                        self._saved_screens[str(key)] = self._state_from(dict(value, monitor_id=key))
                    except Exception:
                        continue
                self._last_screen = str(data.get("last") or "")
            else:
                old = self._state_from(data)
                self._saved_screens[old.monitor_id] = old
                self._last_screen = old.monitor_id
        except Exception:
            self._saved_screens = {}

    def _saved_for(self, monitor_id: str | None) -> DisplayState | None:
        """This screen's approved settings; with no screen id (no xrandr), the last screen's."""
        if monitor_id:
            return self._saved_screens.get(monitor_id)
        return self._saved_screens.get(self._last_screen)

    def _save(self, state: DisplayState) -> None:
        self._saved_screens[state.monitor_id] = state
        self._last_screen = state.monitor_id
        data = {"screens": {k: asdict(v) for k, v in sorted(self._saved_screens.items())}, "last": state.monitor_id}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    @staticmethod
    def _validated_state(state: DisplayState, fallback: DisplayState | None = None) -> DisplayState:
        width = max(320, int(state.width))
        height = max(240, int(state.height))
        left = max(0, int(state.crop_left))
        right = max(0, int(state.crop_right))
        top = max(0, int(state.crop_top))
        bottom = max(0, int(state.crop_bottom))
        if left + right > width - 160 or top + bottom > height - 120:
            if fallback is not None:
                return DisplayState(
                    width=width,
                    height=height,
                    crop_left=max(0, int(fallback.crop_left)),
                    crop_right=max(0, int(fallback.crop_right)),
                    crop_top=max(0, int(fallback.crop_top)),
                    crop_bottom=max(0, int(fallback.crop_bottom)),
                    monitor_id=str(state.monitor_id or fallback.monitor_id or ""),
                    fit=fallback.fit, rate=float(state.rate or 0.0),
                )
            raise ValueError("Crop would leave too little visible screen area")
        return DisplayState(width, height, left, right, top, bottom, str(state.monitor_id or ""),
                            state.fit if state.fit in ("app", "desktop") else "app", float(state.rate or 0.0))

    def _query_xrandr(self):
        """(output, width, height, modes, monitor_id) from the display adapter, or None."""
        found = self._backend.query()
        if not found:
            return None
        self._rates, self._current_rate = found["rates"], found["current_rate"]
        return found["output"], found["width"], found["height"], found["modes"], found["monitor_id"]

    def refresh_modes(self) -> tuple[tuple[int, int], ...]:
        with self._lock:
            detected = self._query_xrandr()
            if detected:
                self._output_name, _, _, modes, _ = detected
                self._modes = tuple(modes)
            return self._modes

    def _apply_resolution(self, width: int, height: int, rate: float = 0.0) -> tuple[bool, str | None]:
        detected = self._query_xrandr()
        if not detected:
            # Headless/non-X test environments still exercise state/crop logic.
            if self._backend.real:
                return False, "xrandr-unavailable"
            output = self._output_name or "TEST-1"
            modes = self._modes or ((width, height),)
        else:
            output, _, _, modes, _ = detected
            self._output_name = output
            self._modes = tuple(modes)
        if modes and (width, height) not in modes:
            return False, "unsupported-mode"
        return self._backend.set_mode(output, width, height, rate)

    def _apply_fit(self, state: DisplayState) -> bool:
        """Desktop-wide crop via an xrandr transform (identity for 'app' fit or no crop).

        Returns False when the driver refuses transforms; the caller then falls
        back to insetting BMO's own window.
        """
        output = self._output_name
        if not output:
            return state.fit != "desktop" or not state.has_crop
        matrix = fit_transform(state) if state.fit == "desktop" and state.has_crop else IDENTITY_TRANSFORM
        if matrix == IDENTITY_TRANSFORM and self._transforms_work is None:
            pass  # nothing to undo yet; still send it so a leftover preview is cleared
        ok = self._backend.set_transform(output, state.width, state.height, matrix)
        if matrix != IDENTITY_TRANSFORM:
            self._transforms_work = ok
        return ok

    def rates_for(self, width: int, height: int) -> tuple[float, ...]:
        with self._lock:
            return self._rates.get((int(width), int(height)), ())

    @property
    def current_rate(self) -> float:
        with self._lock:
            return float(self._current_state.rate or self._current_rate or 0.0)

    def apply_startup(self) -> dict:
        """Apply an approved saved mode when available; otherwise use live mode."""
        with self._lock:
            detected = self._query_xrandr()
            if detected:
                self._output_name, live_w, live_h, modes, monitor_id = detected
                self._modes = tuple(modes)
            else:
                live_w, live_h = self._current_state.width, self._current_state.height
                monitor_id = self._current_state.monitor_id

            self._saved_state = self._saved_for(monitor_id)
            if self._saved_state is None:
                # A screen with no approved settings of its own (another screen's are kept for it) starts from
                # its live mode with no shrink; this also clears a transform left by an unfinished preview.
                self._current_state = DisplayState(live_w, live_h, monitor_id=monitor_id)
                self._apply_fit(self._current_state)
                self._generation += 1
                used = "different-monitor" if self._saved_screens else "detected"
                return {"ok": True, "used": used, "state": asdict(self._current_state)}

            saved = self._saved_state
            if (saved.width, saved.height) != (live_w, live_h) or (saved.rate and abs(saved.rate - self._current_rate) > 0.05):
                ok, reason = self._apply_resolution(saved.width, saved.height, saved.rate)
                if not ok:
                    # A different monitor may not offer the old mode. Start safely
                    # on the live mode with zero crop instead of forcing stale CRT settings.
                    self._current_state = DisplayState(live_w, live_h, monitor_id=monitor_id)
                    self._apply_fit(self._current_state)
                    self._generation += 1
                    return {"ok": True, "used": "detected-fallback", "reason": reason, "state": asdict(self._current_state)}
            saved_for_monitor = DisplayState(
                saved.width, saved.height, saved.crop_left, saved.crop_right, saved.crop_top, saved.crop_bottom,
                monitor_id or saved.monitor_id, saved.fit, saved.rate,
            )
            self._current_state = self._validated_state(saved_for_monitor, DisplayState(saved.width, saved.height, monitor_id=monitor_id))
            if not self._apply_fit(self._current_state) and self._current_state.fit == "desktop":
                # The saved desktop fit can't be applied here: inset BMO's window instead.
                self._current_state = DisplayState(*[getattr(self._current_state, k) for k in (
                    "width", "height", "crop_left", "crop_right", "crop_top", "crop_bottom", "monitor_id")],
                    fit="app", rate=self._current_state.rate)
            self._generation += 1
            return {"ok": True, "used": "saved", "state": asdict(self._current_state)}

    def _begin_or_update_preview(self, candidate: DisplayState) -> None:
        if self._pending_baseline is None:
            self._pending_baseline = self._current_state
        self._current_state = self._validated_state(candidate)
        self._pending_deadline = time.monotonic() + self.preview_timeout_seconds
        self._last_timeout_revert = False
        self._generation += 1

    def test_resolution(self, width: int, height: int, rate: float = 0.0) -> dict:
        width, height, rate = int(width), int(height), float(rate or 0.0)
        if width < 320 or height < 240 or width > 8192 or height > 8192:
            return {"ok": False, "reason": "invalid-mode"}
        with self._lock:
            modes = self.refresh_modes()
            if modes and (width, height) not in modes:
                return {"ok": False, "reason": "unsupported-mode", "modes": modes}
            rates = self._rates.get((width, height), ())
            if rate and rates:
                rate = min(rates, key=lambda r: abs(r - rate))  # 60 -> 60.00 or 59.94, whichever exists
            ok, reason = self._apply_resolution(width, height, rate)
            if not ok:
                return {"ok": False, "reason": reason, "modes": modes}
            cur = self._current_state
            if cur.fit == "desktop" and (cur.width, cur.height) != (width, height):
                # Borders stay the same share of the picture at the new mode.
                sx, sy = width / max(1, cur.width), height / max(1, cur.height)
                crops = (round(cur.crop_left * sx), round(cur.crop_right * sx), round(cur.crop_top * sy),
                         round(cur.crop_bottom * sy))
            else:
                crops = (cur.crop_left, cur.crop_right, cur.crop_top, cur.crop_bottom)
            candidate = DisplayState(width, height, *crops, cur.monitor_id, cur.fit, rate)
            # If the old crop is impossible at a much smaller mode, temporarily
            # reset it rather than making the preview unusable.
            try:
                candidate = self._validated_state(candidate)
            except ValueError:
                candidate = DisplayState(width, height, monitor_id=cur.monitor_id, fit=cur.fit, rate=rate)
            self._begin_or_update_preview(candidate)
            self._apply_fit(self._current_state)
            return {"ok": True, "state": asdict(self._current_state), "timeout": self.preview_timeout_seconds}

    def test_crop(self, target: str, pixels: int, *, relative: bool = False, fit: str | None = None) -> dict:
        target = str(target).strip().lower()
        pixels = int(pixels)
        with self._lock:
            cur = self._current_state
            values = {
                "left": cur.crop_left,
                "right": cur.crop_right,
                "top": cur.crop_top,
                "bottom": cur.crop_bottom,
            }
            targets = {
                "all": ("left", "right", "top", "bottom"),
                "left": ("left",),
                "right": ("right",),
                "top": ("top",),
                "bottom": ("bottom",),
                "horizontal": ("left", "right"),
                "vertical": ("top", "bottom"),
            }.get(target)
            if not targets:
                return {"ok": False, "reason": "invalid-crop-target"}
            for key in targets:
                values[key] = max(0, values[key] + pixels) if relative else max(0, pixels)
            return self._preview_crop(values, fit or cur.fit)

    def test_fit(self, left: int, right: int, top: int, bottom: int) -> dict:
        """Settings plan S2: preview a whole-desktop fit with explicit per-edge borders."""
        with self._lock:
            return self._preview_crop({"left": left, "right": right, "top": top, "bottom": bottom}, "desktop")

    def _preview_crop(self, values: dict, fit: str) -> dict:
        cur = self._current_state
        candidate = DisplayState(cur.width, cur.height, max(0, int(values["left"])), max(0, int(values["right"])),
                                 max(0, int(values["top"])), max(0, int(values["bottom"])), cur.monitor_id, fit,
                                 cur.rate)
        try:
            candidate = self._validated_state(candidate)
        except ValueError:
            return {"ok": False, "reason": "crop-too-large"}
        if fit == "desktop" and not self._apply_fit(candidate):
            # This driver can't transform the desktop: inset BMO's own window instead.
            candidate = DisplayState(*[getattr(candidate, k) for k in (
                "width", "height", "crop_left", "crop_right", "crop_top", "crop_bottom", "monitor_id")],
                fit="app", rate=candidate.rate)
            self._begin_or_update_preview(candidate)
            return {"ok": True, "state": asdict(candidate), "timeout": self.preview_timeout_seconds,
                    "desktop_fit": False}
        if fit != "desktop":
            self._apply_fit(DisplayState(cur.width, cur.height))  # make sure no desktop transform is left
        self._begin_or_update_preview(candidate)
        return {"ok": True, "state": asdict(candidate), "timeout": self.preview_timeout_seconds,
                "desktop_fit": fit == "desktop"}

    def extend_preview(self) -> bool:
        """The owner is still talking during calibration: restart the auto-revert clock."""
        with self._lock:
            if self._pending_baseline is None:
                return False
            self._pending_deadline = time.monotonic() + self.preview_timeout_seconds
            return True

    def modes_and_rates(self):
        with self._lock:
            return tuple(self._modes), dict(self._rates)

    def approve(self) -> dict:
        with self._lock:
            if self._pending_baseline is None:
                return {"ok": False, "reason": "no-pending-preview"}
            # Filed under the screen connected now (2026-10-22: every approval kept the first screen's id, so a
            # CRT's settings landed on the TV).
            detected = self._query_xrandr()
            live_id = detected[4] if detected else self._current_state.monitor_id
            cur = self._current_state
            self._current_state = DisplayState(cur.width, cur.height, cur.crop_left, cur.crop_right, cur.crop_top,
                                               cur.crop_bottom, live_id or cur.monitor_id, cur.fit, cur.rate)
            self._saved_state = self._current_state
            self._save(self._saved_state)
            self._pending_baseline = None
            self._pending_deadline = 0.0
            self._generation += 1
            return {"ok": True, "state": asdict(self._saved_state)}

    def reject(self, *, timeout: bool = False) -> dict:
        with self._lock:
            if self._pending_baseline is None:
                return {"ok": False, "reason": "no-pending-preview"}
            baseline = self._pending_baseline
            if ((baseline.width, baseline.height) != (self._current_state.width, self._current_state.height)
                    or (baseline.rate and baseline.rate != self._current_state.rate)):
                self._apply_resolution(baseline.width, baseline.height, baseline.rate)
            self._apply_fit(baseline)
            self._current_state = baseline
            self._pending_baseline = None
            self._pending_deadline = 0.0
            self._last_timeout_revert = bool(timeout)
            self._generation += 1
            return {"ok": True, "state": asdict(self._current_state), "timeout": bool(timeout)}

    def tick(self) -> dict | None:
        with self._lock:
            if self._pending_baseline is not None and time.monotonic() >= self._pending_deadline:
                return self.reject(timeout=True)
            return None

    def status(self) -> dict:
        with self._lock:
            remaining = max(0.0, self._pending_deadline - time.monotonic()) if self._pending_baseline else 0.0
            return {
                "ok": True,
                "output": self._output_name,
                "current": asdict(self._current_state),
                "saved": asdict(self._saved_state) if self._saved_state else None,
                "pending": self._pending_baseline is not None,
                "preview_seconds_remaining": remaining,
                "modes": list(self._modes),
                "rates": {f"{w}x{h}": list(r) for (w, h), r in self._rates.items()},
                "current_rate": self._current_rate,
                "generation": self._generation,
                "last_timeout_revert": self._last_timeout_revert,
            }


def saved_for_connected(state_path: str | Path, backend=None) -> DisplayState | None:
    """The approved settings of the screen connected now, or None (a screen not set up yet, or no X11)."""
    from systems.adapters.xrandr import XrandrBackend
    found = (backend or XrandrBackend()).query()
    if not found:
        return None
    try:
        data = json.loads(Path(state_path).expanduser().read_text(encoding="utf-8"))
        screens = data.get("screens") if isinstance(data.get("screens"), dict) else {data.get("monitor_id", ""): data}
        entry = screens.get(found["monitor_id"])
        return DisplayManager._state_from(dict(entry, monitor_id=found["monitor_id"])) if entry else None
    except Exception:
        return None


def games_should_stretch(state: DisplayState | None) -> bool:
    """A trimmed widescreen signal is a CRT behind an HDMI converter that squeezes 16:9 into its 4:3 tube
    (2026-10-22: games stayed narrow on the CRT). Stretching the 4:3 game
    across the 16:9 picture undoes the squeeze, so it fills the tube with the right proportions."""
    return bool(state and state.has_crop and state.width / max(1, state.height) >= 1.6)


class ScreenWatcher:
    """Applies each screen's own settings when the connected screen changes, even while BMO sleeps (2026-10-22:
    after swapping the CRT for the TV in standby, the TV kept the CRT's shrink until BMO woke)."""

    def __init__(self, state_path: str | Path, *, backend=None, make_manager=None, poll_seconds: float = 5.0):
        from systems.adapters.xrandr import XrandrBackend
        self.state_path, self.poll_seconds = Path(state_path), float(poll_seconds)
        self._backend = backend or XrandrBackend()
        self._make_manager = make_manager or (lambda: DisplayManager(self.state_path))
        self._last: str | None = None
        self._stop = threading.Event()

    def check(self) -> str | None:
        """One look; returns what was applied ("saved", "different-monitor"...) when the screen changed."""
        found = self._backend.query(current=True)  # X's own record: no re-read of the screen every 5 s
        current = found["monitor_id"] if found else None
        if current is None or current == self._last:
            return None
        first = self._last is None
        self._last = current
        if first:
            return None  # BMO's own start applies the first screen's settings
        return self._make_manager().apply_startup().get("used")

    def start(self) -> threading.Thread:
        import logging
        log = logging.getLogger("bemo-screen-watcher")
        self.check()

        def loop():
            while not self._stop.wait(self.poll_seconds):
                try:
                    used = self.check()
                    if used:
                        log.info("Screen changed (%s): its own display settings applied (%s)", self._last, used)
                except Exception:
                    log.exception("Screen watcher check failed")
        thread = threading.Thread(target=loop, name="bemo-screen-watcher", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
