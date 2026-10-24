"""Runtime presentation settings for Bemo.

Every process start begins in Face mode with TTS enabled. Runtime voice commands
may switch to Text mode and may disable TTS only while Text mode is active.
The JSON file is a diagnostic snapshot of the current session, not a source of
startup state.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class InterfaceSettings:
    VALID_DISPLAY_MODES = {"face", "text"}

    def __init__(self, path: str | Path, *, default_display_mode: str = "face", default_tts_enabled: bool = True):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._display_mode = self._normalize_mode(default_display_mode)
        self._tts_enabled = bool(default_tts_enabled)
        # Face mode is never allowed to be silent. Startup defaults are written
        # immediately so stale state from a previous session cannot be restored.
        if self._display_mode == "face":
            self._tts_enabled = True
        self._save()

    @classmethod
    def _normalize_mode(cls, value: str) -> str:
        mode = str(value or "").strip().lower()
        return mode if mode in cls.VALID_DISPLAY_MODES else "face"

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        with self._lock:
            data = {
                "version": 2,
                "display_mode": self._display_mode,
                "tts_enabled": bool(self._tts_enabled),
                "startup_policy": "face-tts-on",
            }
        temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.path)

    @property
    def display_mode(self) -> str:
        with self._lock:
            return self._display_mode

    @property
    def tts_enabled(self) -> bool:
        with self._lock:
            return bool(self._tts_enabled)

    def status(self) -> dict:
        with self._lock:
            return {
                "display_mode": self._display_mode,
                "tts_enabled": bool(self._tts_enabled),
            }

    def set_display_mode(self, mode: str) -> str:
        normalized = self._normalize_mode(mode)
        with self._lock:
            self._display_mode = normalized
            if normalized == "face":
                # Hard invariant: the animated face never runs silently.
                self._tts_enabled = True
        self._save()
        return normalized

    def toggle_display_mode(self) -> str:
        return self.set_display_mode("text" if self.display_mode == "face" else "face")

    def set_tts_enabled(self, enabled: bool) -> bool:
        with self._lock:
            if not enabled and self._display_mode == "face":
                # Reject silent Face mode. Caller can tell the user to enter
                # Text mode first.
                return True
            self._tts_enabled = bool(enabled)
        self._save()
        return self.tts_enabled

    def toggle_tts(self) -> bool:
        return self.set_tts_enabled(not self.tts_enabled)
