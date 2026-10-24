"""Linux Mint Xfce autostart management for Bemo.

Bemo uses an XDG desktop-autostart bridge to start its systemd user service only
after the graphical Xfce session exists.  This guarantees the Supervisor inherits
DISPLAY/XAUTHORITY/DBus/audio environment before a later "Hey Bemo" launches the
full UI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


class AutostartManager:
    SERVICE_NAME = "bemo.service"
    DESKTOP_NAME = "bemo-supervisor.desktop"

    def __init__(self, app_root: str | Path):
        self.app_root = Path(app_root).resolve()
        self.user_unit_dir = Path.home() / ".config" / "systemd" / "user"
        self.unit_path = self.user_unit_dir / self.SERVICE_NAME
        self.xdg_autostart_dir = Path.home() / ".config" / "autostart"
        self.desktop_path = self.xdg_autostart_dir / self.DESKTOP_NAME

    def supported(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("systemctl") is not None

    @staticmethod
    def _systemd_quote(value: str | Path) -> str:
        text = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{text}"'

    def render_unit(self) -> str:
        start = self.app_root / "start.sh"
        data_root = os.environ.get("BEMO_DATA_DIR", "")
        env_line = f'Environment="BEMO_DATA_DIR={data_root}"\n' if data_root else ""
        return (
            "[Unit]\n"
            "Description=Bemo standby Supervisor and Hey-Bemo wake listener\n\n"
            "[Service]\n"
            "Type=simple\n"
            # Not quoted: systemd rejects a quoted WorkingDirectory and with it the whole unit, so BMO
            # never started at boot (journal, 2026-10-17 to 2026-10-19: "path is not absolute").
            f"WorkingDirectory={str(self.app_root).replace('%', '%%')}\n"
            f"ExecStart=/bin/bash {self._systemd_quote(start)}\n"
            f"{env_line}"
            "Restart=always\n"
            "RestartSec=3\n"
        )

    @staticmethod
    def _desktop_quote(path: Path) -> str:
        # Desktop Exec fields accept shell-style double quoting for an argument.
        text = str(path).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
        return f'"{text}"'

    def render_desktop_entry(self) -> str:
        bridge = self.app_root / "bemo-session-start.sh"
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Version=1.0\n"
            "Name=Bemo Standby Supervisor\n"
            "Comment=Import the Xfce session environment and start Bemo standby\n"
            f"Exec=/bin/bash {self._desktop_quote(bridge)}\n"
            "Terminal=false\n"
            "StartupNotify=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            "OnlyShowIn=XFCE;\n"
            "NoDisplay=true\n"
        )

    def install(self) -> dict:
        if not self.supported():
            return {"ok": False, "reason": "systemd-user-unavailable"}
        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        self.xdg_autostart_dir.mkdir(parents=True, exist_ok=True)
        self.unit_path.write_text(self.render_unit(), encoding="utf-8")
        self.desktop_path.write_text(self.render_desktop_entry(), encoding="utf-8")
        result = subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        return {
            "ok": result.returncode == 0,
            "error": result.stderr.strip(),
            "unit": str(self.unit_path),
            "desktop": str(self.desktop_path),
        }

    def enable(self) -> dict:
        installed = self.install()
        if not installed.get("ok"):
            return installed
        # v1.15 enabled the unit against default.target, which can start before
        # Xfce has DISPLAY/audio session variables. Remove that old enablement;
        # the XDG desktop entry is now the canonical graphical-session trigger.
        subprocess.run(
            ["systemctl", "--user", "disable", self.SERVICE_NAME],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        started = False
        start_error = ""
        if os.environ.get("DISPLAY"):
            bridge = self.app_root / "bemo-session-start.sh"
            result = subprocess.run(
                ["/bin/bash", str(bridge)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            started = result.returncode == 0
            start_error = result.stderr.strip()
        return {
            "ok": True,
            "enabled": True,
            "trigger": "xfce-xdg-autostart",
            "desktop": str(self.desktop_path),
            "service_started_now": started,
            "start_error": start_error,
        }

    def disable(self) -> dict:
        if not self.supported():
            return {"ok": False, "reason": "systemd-user-unavailable"}
        try:
            self.desktop_path.unlink()
        except FileNotFoundError:
            pass
        result = subprocess.run(
            ["systemctl", "--user", "disable", self.SERVICE_NAME],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        # 'disable' may return nonzero when the unit was never enabled even though
        # the XDG trigger has been successfully removed; stopping is what matters.
        return {
            "ok": True,
            "enabled": False,
            "desktop": str(self.desktop_path),
            "systemctl": (result.stdout or result.stderr).strip(),
        }

    def status(self) -> dict:
        if not self.supported():
            return {"ok": False, "reason": "systemd-user-unavailable", "enabled": False}
        active = subprocess.run(
            ["systemctl", "--user", "is-active", self.SERVICE_NAME],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        return {
            "ok": True,
            "enabled": self.desktop_path.exists(),
            "trigger": "xfce-xdg-autostart" if self.desktop_path.exists() else "disabled",
            "service_active": active.returncode == 0,
            "service_state": (active.stdout or active.stderr).strip(),
            "desktop": str(self.desktop_path),
            "unit": str(self.unit_path),
        }
