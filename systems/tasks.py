"""Persistent task/handoff ledger shared by every Bemo model role."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class TaskLedger:
    """Durable task state that survives model switches, crashes, and reboots.

    This stores concise work state, not hidden reasoning. Each specialist can add
    factual checkpoints before handing work to another role. The receiving role
    gets the same task record injected into its prompt.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_path = self.root / "active.json"

    def _task_path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.json"

    def _read_json(self, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    def _write_task(self, task: dict) -> dict:
        task["updated_at"] = _utc_now()
        _atomic_write_json(self._task_path(str(task["id"])), task)
        return task

    def _set_active_id(self, task_id: str | None) -> None:
        _atomic_write_json(
            self.active_path,
            {"active_task_id": task_id, "updated_at": _utc_now()},
        )

    def active_task(self) -> dict | None:
        active = self._read_json(self.active_path) or {}
        task_id = active.get("active_task_id")
        if not task_id:
            return None
        task = self._read_json(self._task_path(str(task_id)))
        if not task:
            return None
        if task.get("status") in {"completed", "cancelled"}:
            return None
        return task

    def task(self, task_id: str) -> dict | None:
        return self._read_json(self._task_path(str(task_id)))

    def begin_task(
        self,
        original_request: str,
        *,
        project_slug: str | None,
        current_role: str,
        resume_task_id: str | None = None,
    ) -> dict:
        if resume_task_id:
            existing = self.task(resume_task_id)
            if existing and existing.get("status") not in {"completed", "cancelled"}:
                existing["status"] = "in_progress"
                existing["current_role"] = current_role
                if project_slug:
                    existing["project_slug"] = project_slug
                existing.setdefault("events", []).append(
                    {"at": _utc_now(), "type": "resumed", "role": current_role}
                )
                self._write_task(existing)
                self._set_active_id(str(existing["id"]))
                return existing

        previous = self.active_task()
        if previous:
            previous["status"] = "interrupted"
            previous.setdefault("events", []).append(
                {"at": _utc_now(), "type": "interrupted_by_new_task"}
            )
            self._write_task(previous)

        task_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        task = {
            "id": task_id,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "status": "in_progress",
            "original_request": str(original_request),
            "project_slug": project_slug,
            "current_role": current_role,
            "previous_role": None,
            "selected_assets": [],
            "checkpoints": [],
            "events": [
                {"at": _utc_now(), "type": "started", "role": current_role}
            ],
            "final_result": "",
        }
        self._write_task(task)
        self._set_active_id(task_id)
        return task

    def set_project(self, project_slug: str | None) -> dict | None:
        task = self.active_task()
        if not task:
            return None
        task["project_slug"] = project_slug
        task.setdefault("events", []).append(
            {"at": _utc_now(), "type": "project_changed", "project_slug": project_slug}
        )
        return self._write_task(task)

    def add_asset(self, path: str, *, kind: str = "file") -> dict | None:
        task = self.active_task()
        if not task:
            return None
        path = str(path)
        assets = task.setdefault("selected_assets", [])
        if not any(str(item.get("path")) == path for item in assets if isinstance(item, dict)):
            assets.append({"path": path, "kind": kind, "added_at": _utc_now()})
        return self._write_task(task)

    def checkpoint(
        self,
        *,
        role: str,
        summary: str = "",
        completed_work: str = "",
        decisions: str = "",
        remaining_work: str = "",
        files: list[str] | None = None,
        artifacts: list[str] | None = None,
        next_role: str | None = None,
    ) -> dict | None:
        task = self.active_task()
        if not task:
            return None
        checkpoint = {
            "at": _utc_now(),
            "role": role,
            "summary": str(summary).strip(),
            "completed_work": str(completed_work).strip(),
            "decisions": str(decisions).strip(),
            "remaining_work": str(remaining_work).strip(),
            "files": [str(x) for x in (files or [])][:50],
            "artifacts": [str(x) for x in (artifacts or [])][:50],
            "next_role": next_role,
        }
        task.setdefault("checkpoints", []).append(checkpoint)
        task["current_role"] = role
        return self._write_task(task)

    def handoff(self, *, from_role: str, to_role: str) -> dict | None:
        task = self.active_task()
        if not task:
            return None
        task["previous_role"] = from_role
        task["current_role"] = to_role
        task.setdefault("events", []).append(
            {
                "at": _utc_now(),
                "type": "handoff",
                "from_role": from_role,
                "to_role": to_role,
            }
        )
        return self._write_task(task)

    def complete(self, *, role: str, final_result: str) -> dict | None:
        task = self.active_task()
        if not task:
            return None
        task["status"] = "completed"
        task["current_role"] = role
        task["final_result"] = str(final_result).strip()
        task.setdefault("events", []).append(
            {"at": _utc_now(), "type": "completed", "role": role}
        )
        self._write_task(task)
        self._set_active_id(None)
        return task

    def cancel(self, reason: str = "") -> dict | None:
        task = self.active_task()
        if not task:
            return None
        task["status"] = "cancelled"
        task.setdefault("events", []).append(
            {"at": _utc_now(), "type": "cancelled", "reason": str(reason)}
        )
        self._write_task(task)
        self._set_active_id(None)
        return task

    def latest_incomplete(self, *, project_slug: str | None = None) -> dict | None:
        candidates = []
        for path in self.root.glob("*.json"):
            if path.name == "active.json":
                continue
            task = self._read_json(path)
            if not task or task.get("status") in {"completed", "cancelled"}:
                continue
            if project_slug and task.get("project_slug") != project_slug:
                continue
            candidates.append(task)
        candidates.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        return candidates[0] if candidates else None

    def prompt_context(self, task: dict | None = None, *, max_checkpoints: int = 6) -> str:
        """Return task state only when a handoff/resume actually adds information.

        A freshly-created task mostly repeats the current user request. Injecting it
        every turn would change the prompt before the conversation and defeat
        prefix-cache reuse. Checkpoints, selected assets, handoffs, and resumes are
        material state and are still injected for the receiving role.
        """
        task = task or self.active_task()
        if not task:
            return ""
        events = list(task.get("events") or [])
        material_event = any(
            str(item.get("type") or "") in {"resumed", "handoff", "project_changed"}
            for item in events
            if isinstance(item, dict)
        )
        if not (
            task.get("checkpoints")
            or task.get("selected_assets")
            or task.get("previous_role")
            or material_event
        ):
            return ""
        return self.context(task, max_checkpoints=max_checkpoints)

    def context(self, task: dict | None = None, *, max_checkpoints: int = 6) -> str:
        task = task or self.active_task()
        if not task:
            return ""
        lines = [
            f"Task ID: {task.get('id')}",
            f"Status: {task.get('status')}",
            f"Original request: {task.get('original_request', '')}",
            f"Project: {task.get('project_slug') or 'none'}",
            f"Current role: {task.get('current_role') or 'unknown'}",
        ]
        assets = task.get("selected_assets") or []
        if assets:
            lines.append("Selected assets:")
            for item in assets[-12:]:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('kind', 'file')}: {item.get('path')}")
        checkpoints = list(task.get("checkpoints") or [])[-max(1, int(max_checkpoints)):]
        if checkpoints:
            lines.append("Handoff checkpoints (oldest to newest):")
            for cp in checkpoints:
                pieces = []
                for key in ("summary", "completed_work", "decisions", "remaining_work"):
                    value = str(cp.get(key) or "").strip()
                    if value:
                        pieces.append(f"{key.replace('_', ' ')}={value}")
                if cp.get("files"):
                    pieces.append("files=" + ", ".join(str(x) for x in cp["files"][:12]))
                if cp.get("artifacts"):
                    pieces.append("artifacts=" + ", ".join(str(x) for x in cp["artifacts"][:12]))
                role = cp.get("role") or "unknown"
                lines.append(f"- [{role}] " + ("; ".join(pieces) or "checkpoint recorded"))
        return "\n".join(lines)
