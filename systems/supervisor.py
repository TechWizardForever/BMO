"""Client for Bemo's tiny always-running supervisor process.

The supervisor deliberately exposes a very small JSON-over-Unix-socket API.  It
owns model/app lifecycle, while the conversational process owns STT/TTS, memory,
and the UI.  Keeping this client dependency-free makes it safe to import from the
chat worker.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


class SupervisorError(RuntimeError):
    pass


class SupervisorClient:
    def __init__(self, socket_path: str | Path, timeout: float = 3.0):
        self.socket_path = Path(socket_path)
        self.timeout = float(timeout)

    def request(self, action: str, *, timeout: float | None = None, **payload: Any) -> dict:
        request = {"action": str(action), **payload}
        data = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
        reply = bytearray()

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(float(timeout if timeout is not None else self.timeout))
                sock.connect(str(self.socket_path))
                sock.sendall(data)
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    reply.extend(chunk)
                    if b"\n" in chunk:
                        break
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError) as exc:
            raise SupervisorError(f"Supervisor is unavailable: {exc}") from exc

        if not reply:
            raise SupervisorError("Supervisor returned an empty response")
        try:
            response = json.loads(reply.split(b"\n", 1)[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SupervisorError("Supervisor returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise SupervisorError("Supervisor returned an invalid response")
        return response

    def available(self) -> bool:
        try:
            return bool(self.request("ping").get("ok"))
        except SupervisorError:
            return False

    def status(self) -> dict:
        return self.request("status")

    def list_apps(self) -> dict:
        return self.request("list_apps")

    def switch_model(
        self,
        model: str,
        *,
        preload: bool = True,
        keep_alive: str | int = "10m",
        timeout: float | None = None,
    ) -> dict:
        return self.request(
            "switch_model",
            model=model,
            preload=bool(preload),
            keep_alive=keep_alive,
            timeout=timeout,
        )

    def unload_model(self, model: str | None = None, *, timeout: float | None = None) -> dict:
        return self.request("unload_model", model=model, timeout=timeout)

    def launch_app(self, app: str) -> dict:
        return self.request("launch_app", app=app)

    def close_app(self, app: str) -> dict:
        return self.request("close_app", app=app)

    def game_status(self) -> dict:
        return self.request("game_status")

    def list_games(self, system: str | None = None) -> dict:
        return self.request("list_games", system=system)

    def launch_game(self, query: str, system: str | None = None) -> dict:
        return self.request("launch_game", query=query, system=system)

    def close_game(self) -> dict:
        return self.request("close_game")

    def refresh_games(self) -> dict:
        return self.request("refresh_games")

    def import_games_from_removable(self, *, timeout: float | None = None) -> dict:
        return self.request(
            "import_games_from_removable",
            timeout=float(timeout if timeout is not None else 24 * 60 * 60),
        )

    def import_project_from_removable(self, selection: str | None = None, *, timeout: float | None = None) -> dict:
        return self.request(
            "import_project_from_removable",
            selection=selection or "",
            timeout=float(timeout if timeout is not None else 24 * 60 * 60),
        )

    def import_files_into_project_from_removable(self, project_slug: str, *, timeout: float | None = None) -> dict:
        return self.request(
            "import_files_into_project_from_removable",
            project_slug=str(project_slug or ""),
            timeout=float(timeout if timeout is not None else 24 * 60 * 60),
        )

    def export_project_items_to_removable(self, project_slug: str, selectors: list[str], *, timeout: float | None = None) -> dict:
        return self.request(
            "export_project_items_to_removable",
            project_slug=str(project_slug or ""),
            selectors=[str(item) for item in (selectors or [])],
            timeout=float(timeout if timeout is not None else 24 * 60 * 60),
        )

    def computer_status(self) -> dict:
        return self.request("computer_status")

    def set_computer_control(self, enabled: bool) -> dict:
        return self.request("set_computer_control", enabled=bool(enabled))

    def computer_action(
        self,
        computer_action: str,
        payload: dict | None = None,
        *,
        requester_role: str = "",
        task_id: str = "",
        timeout: float | None = None,
    ) -> dict:
        return self.request(
            "computer_action",
            computer_action=computer_action,
            payload=dict(payload or {}),
            requester_role=requester_role,
            task_id=task_id,
            timeout=timeout,
        )

    def confirm_computer_action(
        self,
        confirmation_id: str | None = None,
        *,
        require_critical: bool | None = None,
        timeout: float | None = None,
    ) -> dict:
        return self.request(
            "confirm_computer_action",
            confirmation_id=confirmation_id,
            require_critical=require_critical,
            timeout=timeout,
        )

    def cancel_computer_action(self, confirmation_id: str | None = None) -> dict:
        return self.request("cancel_computer_action", confirmation_id=confirmation_id)

    def start_bemo(self, reason: str = "api") -> dict:
        return self.request("start_bemo", reason=reason)

    def restart_bemo(self) -> dict:
        return self.request("restart_bemo")

    def stop_bemo(self) -> dict:
        return self.request("stop_bemo")
