"""System actions adapter for Linux desktops (portability plan P2, step 4).

Which commands move files to the trash, open a file with its default app, and
ask the OS to authorize an administrator command. Moved unchanged from
computer.py; the Supervisor's controller still decides what is allowed.
"""
from __future__ import annotations

import shutil


class LinuxSystem:
    name = "Linux (gio/trash-cli, xdg-open, pkexec)"

    def __init__(self, which=shutil.which):
        self._which = which

    def trash_command(self) -> list[str] | None:
        """argv prefix that moves a path to the desktop trash (recoverable)."""
        gio = self._which("gio")
        if gio:
            return [gio, "trash"]
        trash_put = self._which("trash-put")
        return [trash_put] if trash_put else None

    def open_command(self) -> list[str] | None:
        """argv prefix that opens a path with its default application."""
        opener = self._which("xdg-open")
        return [opener] if opener else None

    def admin_wrapper(self) -> list[str] | None:
        """argv prefix that asks the OS to authorize one administrator command (never stored root)."""
        pkexec = self._which("pkexec")
        return [pkexec] if pkexec else None

    @property
    def admin_name(self) -> str | None:
        return "pkexec" if self._which("pkexec") else None
