"""Accessibility adapter (AT-SPI): read and operate app controls by name (portability plan P2).

Moved unchanged from gui_control.py. The helper script runs under the system
Python, which has pyatspi; BMO's venv does not.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


class AtspiBackend:
    """Linux: talk to a11y_helper.py (next to this file) running under the system Python."""

    def __init__(self, python: str = "/usr/bin/python3", timeout: float = 20.0):
        self.python, self.timeout = python, timeout
        self.helper = str(Path(__file__).with_name("a11y_helper.py"))

    def _call(self, request: dict) -> dict:
        try:
            done = subprocess.run([self.python, self.helper], input=json.dumps(request), capture_output=True,
                                  text=True, timeout=self.timeout, check=False)
            return json.loads(done.stdout or "{}") or {"ok": False, "error": done.stderr.strip()[-300:] or "no reply"}
        except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
            return {"ok": False, "error": f"accessibility helper failed: {exc}"}

    def windows(self) -> dict:
        return self._call({"cmd": "windows"})

    def snapshot(self, pid: int, limit: int = 400) -> dict:
        return self._call({"cmd": "snapshot", "pid": int(pid), "limit": int(limit)})

    def act(self, pid: int, path: str, action: str, expect: dict | None = None, value=None) -> dict:
        return self._call({"cmd": "act", "pid": int(pid), "path": path, "action": action,
                           "expect": expect or {}, "value": value})


_BACKEND_CHECKED: dict = {}


def host_accessibility_backend(python: str = "/usr/bin/python3"):
    """AT-SPI on Linux when the system Python has pyatspi; None elsewhere (not built yet)."""
    if python in _BACKEND_CHECKED:
        return _BACKEND_CHECKED[python]
    backend = None
    if platform.system() == "Linux" and (shutil.which(python) or os.path.exists(python)):
        probe = subprocess.run([python, "-c", "import pyatspi"], capture_output=True, timeout=15, check=False)
        if probe.returncode == 0:
            backend = AtspiBackend(python)
    _BACKEND_CHECKED[python] = backend
    return backend
