#!/usr/bin/env python3
"""Bemo supervisor: tiny parent process for model/app lifecycle and recovery.

It is intentionally *not* an autonomous agent. It accepts structured actions
over a user-only Unix socket. Model-requested computer actions are executed here
with the logged-in user's permissions, audited, and gated by explicit
confirmation when destructive/high-impact. Permanent silent root access is not
provided; approved administrator actions use the OS authorization prompt.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import config
from systems.computer import ComputerController, ComputerControlError
from systems.games import GameError, GameLibrary, discover_removable_drives
from systems.projects import ProjectManager
from voice_commands import COMMAND_CATALOG_VERSION, VOICE_COMMANDS

try:
    import ollama
except Exception:  # lets the non-model parts/self-test run without Ollama
    ollama = None


LOG = logging.getLogger("bemo-supervisor")


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _normalize_name(text: str) -> str:
    return " ".join(str(text).lower().replace("_", " ").replace("-", " ").split())


class SupervisorService:
    def __init__(
        self,
        *,
        socket_path: str | Path | None = None,
        state_path: str | Path | None = None,
        app_config: dict | None = None,
        known_models: list[str] | None = None,
        ollama_module: Any = ollama,
        computer_controller: ComputerController | None = None,
        computer_state_path: str | Path | None = None,
        pending_actions_path: str | Path | None = None,
        computer_action_log_path: str | Path | None = None,
        project_manager: ProjectManager | None = None,
    ):
        self.socket_path = Path(socket_path or config.SUPERVISOR_SOCKET_PATH)
        self.state_path = Path(state_path or config.SUPERVISOR_STATE_PATH)
        self.app_config = dict(app_config if app_config is not None else config.SUPERVISOR_APPS)
        self.known_models = list(
            dict.fromkeys(
                known_models
                if known_models is not None
                else [p.get("model", "") for p in config.MODEL_PROFILES.values() if p.get("model")]
            )
        )
        self.ollama = ollama_module
        self.lock = threading.RLock()
        # Slow Ollama model load/unload operations must never block the fast
        # supervisor status/control API used by the renderer and UI.
        self.model_lock = threading.RLock()
        self.server = None
        self.server_thread = None
        self.apps: dict[str, subprocess.Popen] = {}
        self.active_model: str | None = None
        self.bemo_pid: int | None = None
        self.started_at = time.time()
        self.last_error = ""
        self.restart_requested = threading.Event()
        self.stop_bemo_requested = threading.Event()
        self.start_bemo_requested = threading.Event()
        self.stop_requested = threading.Event()
        self._start_reason = ""
        self.standby_wake_ready = False
        self.standby_wake_error = ""
        self._app_log_handles: dict[str, Any] = {}
        self.computer_controller = computer_controller or ComputerController(
            screenshot_dir=config.COMPUTER_SCREENSHOT_DIR,
            action_timeout_seconds=config.COMPUTER_ACTION_TIMEOUT_SECONDS,
            output_max_chars=config.COMPUTER_ACTION_OUTPUT_MAX_CHARS,
            max_text_read_bytes=config.COMPUTER_TEXT_READ_MAX_BYTES,
            protected_paths=(
                config.COMPUTER_CONTROL_STATE_PATH,
                config.COMPUTER_PENDING_ACTIONS_PATH,
                config.COMPUTER_ACTION_LOG_PATH,
                config.SUPERVISOR_SOCKET_PATH,
                config.SUPERVISOR_STATE_PATH,
            ),
            guarded_paths=(config.APP_ROOT, config.DATA_ROOT),
            python_executable=sys.executable,
        )
        # Conversations, memories and logs are the owner's alone (safety review 2026-10-19: ~/.bemo was 775).
        try:
            if Path(config.DATA_ROOT).is_dir():
                os.chmod(config.DATA_ROOT, 0o700)
        except OSError:
            LOG.warning("Could not make %s private", config.DATA_ROOT)
        self.computer_enabled = bool(config.COMPUTER_CONTROL_DEFAULT_ENABLED)
        self.pending_actions_path = Path(pending_actions_path or config.COMPUTER_PENDING_ACTIONS_PATH)
        self.computer_state_path = Path(computer_state_path or config.COMPUTER_CONTROL_STATE_PATH)
        self.computer_action_log_path = Path(computer_action_log_path or config.COMPUTER_ACTION_LOG_PATH)
        self.pending_computer_actions: list[dict] = []
        self._load_computer_state()
        self._load_pending_computer_actions()

        self.game_library = GameLibrary(config.GAME_ROOT, config.APP_ROOT) if config.ENABLE_GAME_SUBSYSTEM else None
        self.game_import_lock = threading.Lock()
        self.project_manager = project_manager or ProjectManager(
            data_root=config.DATA_ROOT,
            projects_root=config.PROJECTS_ROOT,
            index_extensions=config.PROJECT_INDEX_EXTENSIONS,
            exclude_dirs=config.PROJECT_EXCLUDE_DIRS,
            max_file_bytes=config.PROJECT_FILE_MAX_BYTES,
            context_char_budget=config.PROJECT_CONTEXT_CHAR_BUDGET,
            retrieval_chunks=config.PROJECT_RETRIEVAL_CHUNKS,
            reindex_interval_seconds=config.PROJECT_REINDEX_INTERVAL_SECONDS,
            auto_git=config.AUTO_GIT_SNAPSHOTS,
            git_name=config.GIT_AUTHOR_NAME,
            git_email=config.GIT_AUTHOR_EMAIL,
            export_root_override=config.EXPORT_ROOT_OVERRIDE,
            export_include_git=config.EXPORT_INCLUDE_GIT_HISTORY,
            export_create_zip=config.EXPORT_CREATE_ZIP,
            memory_history_retain=config.PROJECT_MEMORY_HISTORY_RETAIN,
        )
        self.project_import_lock = threading.Lock()

        self.aliases: dict[str, str] = {}
        for key, spec in self.app_config.items():
            self.aliases[_normalize_name(key)] = key
            for alias in spec.get("aliases", ()):
                self.aliases[_normalize_name(alias)] = key

    # ------------------------------------------------------------------
    # Shared logged-in-user computer control
    # ------------------------------------------------------------------
    def _load_computer_state(self) -> None:
        try:
            data = json.loads(self.computer_state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "enabled" in data:
                self.computer_enabled = bool(data["enabled"])
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            pass

    def _save_computer_state(self) -> None:
        _atomic_json(self.computer_state_path, {"enabled": bool(self.computer_enabled)})

    def _load_pending_computer_actions(self) -> None:
        try:
            data = json.loads(self.pending_actions_path.read_text(encoding="utf-8"))
            rows = data.get("pending", []) if isinstance(data, dict) else []
            if isinstance(rows, list):
                self.pending_computer_actions = [x for x in rows if isinstance(x, dict)]
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            self.pending_computer_actions = []
        self._prune_pending_computer_actions(save=False)

    def _save_pending_computer_actions(self) -> None:
        _atomic_json(self.pending_actions_path, {"pending": self.pending_computer_actions})

    def _prune_pending_computer_actions(self, *, save: bool = True) -> None:
        cutoff = time.time()
        before = len(self.pending_computer_actions)
        self.pending_computer_actions = [
            item for item in self.pending_computer_actions
            if float(item.get("expires_at", 0) or 0) > cutoff
        ][-int(config.COMPUTER_PENDING_ACTION_LIMIT):]
        if save and len(self.pending_computer_actions) != before:
            self._save_pending_computer_actions()

    AUDIT_LOG_MAX_BYTES = 5_000_000  # ~850 KB in BMO's first two days; one older file is kept

    def _audit_computer_action(self, event: str, data: dict) -> None:
        try:
            self.computer_action_log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.computer_action_log_path.stat().st_size > self.AUDIT_LOG_MAX_BYTES:
                    os.replace(self.computer_action_log_path,
                               self.computer_action_log_path.with_name(self.computer_action_log_path.name + ".1"))
            except FileNotFoundError:
                pass
            row = {"time": time.time(), "event": event, **data}
            with self.computer_action_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            try:
                os.chmod(self.computer_action_log_path, 0o600)
            except OSError:
                pass
        except Exception:
            LOG.exception("Could not write computer action audit log")

    @staticmethod
    def _safe_payload_for_log(payload: dict) -> dict:
        clean = dict(payload or {})
        for key in ("content", "stdin", "text", "value"):  # value: text typed by gui_act
            if key in clean:
                value = str(clean[key])
                clean[key] = f"[redacted {len(value)} chars]"
        if "argv" in clean and isinstance(clean["argv"], list):
            clean["argv"] = [str(x)[:500] for x in clean["argv"][:64]]
        if "seen_signature" in clean:  # a 32x18 thumbnail of the screen: not for the log
            clean["seen_signature"] = "[screen thumbnail omitted]"
        return clean

    @staticmethod
    def _safe_result_for_log(result: dict) -> dict:
        clean = dict(result or {})
        for key in ("stdout", "stderr", "lines", "entries", "processes"):
            if key in clean:
                text = json.dumps(clean[key], default=str) if not isinstance(clean[key], str) else clean[key]
                clean[key] = text[:2000] + ("..." if len(text) > 2000 else "")
        return clean

    def computer_control_status(self) -> dict:
        with self.lock:
            self._prune_pending_computer_actions()
            return {
                "ok": True,
                "enabled": bool(self.computer_enabled),
                "pending_count": len(self.pending_computer_actions),
                "capabilities": self.computer_controller.status(),
            }

    def set_computer_control_enabled(self, enabled: bool) -> dict:
        with self.lock:
            self.computer_enabled = bool(enabled)
            self._save_computer_state()
            self._audit_computer_action("control-toggle", {"enabled": self.computer_enabled})
            return {"ok": True, "enabled": self.computer_enabled}

    def computer_action(self, *, computer_action: str, payload: dict | None = None, requester_role: str = "", task_id: str = "") -> dict:
        with self.lock:
            if not config.ENABLE_SHARED_COMPUTER_CONTROL:
                return {"ok": False, "error": "Shared computer control is disabled in config."}
            if not self.computer_enabled:
                return {"ok": False, "error": "Computer control is currently disabled. Enable it by voice or local command first."}
            payload = dict(payload or {})
            try:
                assessment = self.computer_controller.assess(computer_action, payload)
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if assessment.get("blocked"):
                result = {"ok": False, **assessment}
                self._audit_computer_action("blocked", {
                    "action": computer_action, "payload": self._safe_payload_for_log(payload), "requester_role": requester_role,
                    "task_id": task_id, "assessment": assessment,
                })
                return result
            if assessment.get("confirmation_required"):
                action_id = uuid.uuid4().hex[:12]
                row = {
                    "id": action_id,
                    "created_at": time.time(),
                    "expires_at": time.time() + float(config.COMPUTER_CONFIRMATION_TTL_SECONDS),
                    "action": str(computer_action),
                    "payload": payload,
                    "requester_role": str(requester_role or ""),
                    "task_id": str(task_id or ""),
                    "summary": str(assessment.get("summary") or computer_action),
                    "critical": bool(assessment.get("critical")),
                }
                self._prune_pending_computer_actions(save=False)
                self.pending_computer_actions.append(row)
                self.pending_computer_actions = self.pending_computer_actions[-int(config.COMPUTER_PENDING_ACTION_LIMIT):]
                self._save_pending_computer_actions()
                audit_row = dict(row)
                audit_row["payload"] = self._safe_payload_for_log(payload)
                self._audit_computer_action("confirmation-required", audit_row)
                phrase = "confirm critical computer action" if row["critical"] else "confirm computer action"
                return {
                    "ok": False,
                    "confirmation_required": True,
                    "critical": row["critical"],
                    "confirmation_id": action_id,
                    "summary": row["summary"],
                    "voice_confirmation": phrase,
                }
            try:
                result = self.computer_controller.execute(computer_action, payload)
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._audit_computer_action("executed", {
                "action": computer_action, "payload": self._safe_payload_for_log(payload), "requester_role": requester_role,
                "task_id": task_id, "result": self._safe_result_for_log(result),
            })
            return result

    def confirm_computer_action(self, confirmation_id: str | None = None, *, require_critical: bool | None = None) -> dict:
        with self.lock:
            if not self.computer_enabled:
                return {"ok": False, "error": "Computer control is currently disabled."}
            self._prune_pending_computer_actions()
            if not self.pending_computer_actions:
                return {"ok": False, "error": "There is no pending computer action to confirm."}
            item = None
            if confirmation_id:
                item = next((x for x in self.pending_computer_actions if x.get("id") == confirmation_id), None)
            else:
                item = self.pending_computer_actions[-1]
            if not item:
                return {"ok": False, "error": "That pending computer action was not found or expired."}
            if require_critical is True and not item.get("critical"):
                return {"ok": False, "error": "The newest pending action is not marked critical."}
            if item.get("critical") and require_critical is not True:
                return {
                    "ok": False,
                    "critical_confirmation_required": True,
                    "error": "This is a critical action. Say 'confirm critical computer action'.",
                    "summary": item.get("summary", ""),
                }
            try:
                result = self.computer_controller.execute(str(item.get("action", "")), dict(item.get("payload") or {}))
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self.pending_computer_actions = [x for x in self.pending_computer_actions if x.get("id") != item.get("id")]
            self._save_pending_computer_actions()
            audit_item = dict(item)
            audit_item["payload"] = self._safe_payload_for_log(dict(item.get("payload") or {}))
            audit_item["result"] = self._safe_result_for_log(result)
            self._audit_computer_action("confirmed-executed", audit_item)
            return {**result, "confirmed_action": item.get("summary", "")}

    def cancel_computer_action(self, confirmation_id: str | None = None) -> dict:
        with self.lock:
            self._prune_pending_computer_actions()
            if not self.pending_computer_actions:
                return {"ok": False, "error": "There is no pending computer action to cancel."}
            if confirmation_id:
                target = next((x for x in self.pending_computer_actions if x.get("id") == confirmation_id), None)
            else:
                target = self.pending_computer_actions[-1]
            if not target:
                return {"ok": False, "error": "That pending computer action was not found or expired."}
            self.pending_computer_actions = [x for x in self.pending_computer_actions if x.get("id") != target.get("id")]
            self._save_pending_computer_actions()
            audit_target = dict(target)
            audit_target["payload"] = self._safe_payload_for_log(dict(target.get("payload") or {}))
            self._audit_computer_action("cancelled", audit_target)
            return {"ok": True, "cancelled": target.get("summary", "")}

    def _cleanup_dead_apps(self) -> None:
        for key, proc in list(self.apps.items()):
            if proc.poll() is not None:
                self.apps.pop(key, None)
                handle = self._app_log_handles.pop(key, None)
                if handle:
                    try:
                        handle.close()
                    except Exception:
                        pass

    def _state(self) -> dict:
        self._cleanup_dead_apps()
        return {
            "active_model": self.active_model,
            "bemo_pid": self.bemo_pid,
            "bemo_running": self.bemo_pid is not None,
            "standby_wake_ready": bool(self.standby_wake_ready),
            "standby_wake_error": self.standby_wake_error,
            "apps": {
                key: {"pid": proc.pid, "running": proc.poll() is None}
                for key, proc in self.apps.items()
            },
            "available_apps": sorted(self.app_config),
            "computer_control_enabled": bool(self.computer_enabled),
            "pending_computer_actions": len(self.pending_computer_actions),
            # The permanent Supervisor imports the same canonical voice-command
            # registry that is injected into every model profile. Voice text is
            # still parsed by the chat worker; this version/count makes drift
            # visible in Supervisor status and self-tests.
            "voice_command_catalog_version": COMMAND_CATALOG_VERSION,
            "voice_command_count": len(VOICE_COMMANDS),
            "voice_commands": [item.syntax for item in VOICE_COMMANDS],
            "uptime_seconds": max(0.0, time.time() - self.started_at),
            "last_error": self.last_error,
        }

    def _save_state(self) -> None:
        try:
            _atomic_json(self.state_path, self._state())
        except Exception:
            LOG.exception("Could not write supervisor state")

    def set_bemo_pid(self, pid: int | None) -> None:
        with self.lock:
            self.bemo_pid = pid
            self._save_state()

    def bemo_is_running(self) -> bool:
        with self.lock:
            return self.bemo_pid is not None

    def request_start_bemo(self, reason: str = "api") -> None:
        with self.lock:
            self._start_reason = str(reason or "api")
            self.start_bemo_requested.set()
            self._save_state()

    def consume_start_reason(self) -> str:
        with self.lock:
            reason = self._start_reason or "api"
            self._start_reason = ""
            self.start_bemo_requested.clear()
            return reason

    def set_standby_wake_status(self, ready: bool, error: str = "") -> None:
        with self.lock:
            self.standby_wake_ready = bool(ready)
            self.standby_wake_error = str(error or "")
            self._save_state()

    def _resolve_app(self, name: str) -> str | None:
        return self.aliases.get(_normalize_name(name))

    @staticmethod
    def _model_name_matches(requested: str, installed: str) -> bool:
        requested = str(requested).strip().lower()
        installed = str(installed).strip().lower()
        if requested == installed:
            return True
        if ":" not in requested and installed == requested + ":latest":
            return True
        if ":" not in installed and requested == installed + ":latest":
            return True
        return False

    def _loaded_ollama_models(self) -> list[str]:
        if self.ollama is None or not hasattr(self.ollama, "ps"):
            return []
        try:
            response = self.ollama.ps()
            models = getattr(response, "models", None)
            if models is None and isinstance(response, dict):
                models = response.get("models", [])
            names = []
            for item in models or []:
                name = getattr(item, "model", None) or getattr(item, "name", None)
                if not name and isinstance(item, dict):
                    name = item.get("model") or item.get("name")
                if name:
                    names.append(str(name))
            return names
        except Exception:
            LOG.debug("Could not query Ollama loaded models", exc_info=True)
            return []

    def _unload_ollama_model(self, model: str) -> tuple[bool, str]:
        if not model:
            return True, ""
        if self.ollama is None:
            return False, "Ollama Python client is unavailable."
        try:
            self.ollama.chat(model=model, messages=[], stream=False, keep_alive=0)
            LOG.info("Unloaded model: %s", model)
            return True, ""
        except Exception as exc:
            LOG.warning("Could not unload model %s: %s", model, exc)
            return False, str(exc)

    def _preload_ollama_model(self, model: str, keep_alive: str | int) -> tuple[bool, str]:
        if self.ollama is None:
            return False, "Ollama Python client is unavailable."
        try:
            # Ollama supports an empty generate request specifically for loading a
            # model into memory without asking it to produce a user-visible answer.
            self.ollama.generate(
                model=model,
                prompt="",
                stream=False,
                keep_alive=keep_alive,
                options={
                    # Match the subsequent Bemo chat request exactly. Omitting
                    # num_ctx makes Ollama start the runner at its default
                    # context, then potentially unload/reinitialize it at 8192
                    # when the first user turn arrives (~36 s on 5700U).
                    "num_ctx": int(config.OLLAMA_NUM_CTX),
                    "num_predict": 1,
                    "num_thread": int(config.OLLAMA_NUM_THREAD),
                },
            )
            # Empty-generate maps the weights, but on some Ollama/model builds
            # the first real chat still pays a large one-time initialization
            # cost.  Warm the main brain with a tiny hidden inference so the
            # first user turn after wake is actually warm.
            main_model = str(config.MODEL_PROFILES[config.MAIN_MODEL_PROFILE]["model"])
            if (
                bool(getattr(config, "SUPERVISOR_WARM_MAIN_MODEL_ON_PRELOAD", True))
                and self._model_name_matches(main_model, model)
            ):
                self.ollama.chat(
                    model=model,
                    stream=False,
                    messages=[{"role": "user", "content": "Reply with exactly: ready"}],
                    options={
                        "num_ctx": int(config.OLLAMA_NUM_CTX),
                        "num_predict": 4,
                        "num_thread": int(config.OLLAMA_NUM_THREAD),
                        "temperature": 0.0,
                    },
                    keep_alive=keep_alive,
                )
                LOG.info("Preloaded and warmed main model: %s", model)
            else:
                LOG.info("Preloaded model: %s", model)
            return True, ""
        except Exception as exc:
            LOG.exception("Could not preload model %s", model)
            return False, str(exc)

    def switch_model(self, model: str, *, preload: bool, keep_alive: str | int) -> dict:
        model = str(model).strip()
        if not model:
            return {"ok": False, "error": "No model was supplied."}
        # Ollama load/unload can take tens of seconds.  Keep that serialization
        # separate from self.lock so 2-second renderer status polling, ping, and
        # other lightweight supervisor requests stay responsive during startup.
        with self.model_lock:
            # Enforce one configured Bemo brain resident at a time.  Unload the
            # tracked model first, then make a best-effort pass over other known
            # Bemo profiles in case a previous crash left one loaded.
            # Query Ollama for resident models when possible.  This avoids poking
            # at configured-but-not-installed tags and also cleans up a Bemo model
            # that survived a prior child-process crash.
            loaded = self._loaded_ollama_models()
            candidates = [self.active_model]
            for resident in loaded:
                if any(self._model_name_matches(known, resident) for known in self.known_models):
                    candidates.append(resident)

            seen = set()
            unload_errors = []
            for old in candidates:
                if not old or self._model_name_matches(model, old) or old in seen:
                    continue
                seen.add(old)
                ok, error = self._unload_ollama_model(old)
                if not ok and error:
                    unload_errors.append(f"{old}: {error}")

            requested_already_resident = any(
                self._model_name_matches(model, resident) for resident in loaded
            )
            if preload and not requested_already_resident:
                ok, error = self._preload_ollama_model(model, keep_alive)
                if not ok:
                    self.last_error = error
                    self._save_state()
                    return {"ok": False, "error": f"Could not load {model}: {error}"}
            elif preload:
                LOG.info("Requested model is already resident; skipping duplicate preload: %s", model)

            with self.lock:
                self.active_model = model
                self.last_error = "; ".join(unload_errors)
                self._save_state()
            return {
                "ok": True,
                "model": model,
                "preloaded": bool(preload),
                "unload_warnings": unload_errors,
            }

    def unload_model(self, model: str | None = None) -> dict:
        # Serialize with switch_model without freezing status()/ping().
        with self.model_lock:
            with self.lock:
                target = str(model or self.active_model or "").strip()
            if not target:
                return {"ok": True, "unloaded": None}
            ok, error = self._unload_ollama_model(target)
            with self.lock:
                if ok and (not model or target == self.active_model):
                    self.active_model = None
                self.last_error = "" if ok else error
                self._save_state()
            return {"ok": ok, "unloaded": target, "error": error if not ok else ""}

    def list_apps(self) -> dict:
        with self.lock:
            self._cleanup_dead_apps()
            return {
                "ok": True,
                "apps": [
                    {
                        "key": key,
                        "aliases": list(spec.get("aliases", ())),
                        "running": key in self.apps and self.apps[key].poll() is None,
                    }
                    for key, spec in sorted(self.app_config.items())
                ],
            }

    def _spawn_managed_process(self, key: str, command: list[str], *, cwd: str | None = None) -> dict:
        self._cleanup_dead_apps()
        running = self.apps.get(key)
        if running and running.poll() is None:
            return {"ok": True, "app": key, "pid": running.pid, "already_running": True}
        if not command:
            return {"ok": False, "error": f"No command is configured for {key}."}
        executable = command[0]
        if not os.path.isabs(executable) and shutil.which(executable) is None:
            return {"ok": False, "error": f"Executable not found: {executable}"}
        if os.path.isabs(executable) and not Path(executable).exists():
            return {"ok": False, "error": f"Executable not found: {executable}"}
        log_dir = Path(config.SUPERVISOR_LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        handle = open(log_dir / f"app-{key}.log", "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=handle,
                stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
            )
        except Exception as exc:
            handle.close()
            self.last_error = str(exc)
            self._save_state()
            return {"ok": False, "error": f"Could not launch {key}: {exc}"}
        self.apps[key] = proc
        self._app_log_handles[key] = handle
        self.last_error = ""
        self._save_state()
        LOG.info("Launched app %s pid=%s", key, proc.pid)
        return {"ok": True, "app": key, "pid": proc.pid, "already_running": False}

    def _close_managed_process(self, key: str) -> dict:
        self._cleanup_dead_apps()
        proc = self.apps.get(key)
        if not proc or proc.poll() is not None:
            return {"ok": True, "app": key, "already_stopped": True}
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=float(config.SUPERVISOR_APP_STOP_TIMEOUT_SECONDS))
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait(timeout=2)
        except ProcessLookupError:
            pass
        except Exception as exc:
            self.last_error = str(exc)
            self._save_state()
            return {"ok": False, "error": f"Could not close {key}: {exc}"}
        finally:
            self.apps.pop(key, None)
            handle = self._app_log_handles.pop(key, None)
            if handle:
                try:
                    handle.close()
                except Exception:
                    pass
        self.last_error = ""
        self._save_state()
        LOG.info("Closed app %s", key)
        return {"ok": True, "app": key, "already_stopped": False}

    def launch_app(self, name: str) -> dict:
        with self.lock:
            key = self._resolve_app(name)
            if not key:
                return {"ok": False, "error": "That app is not in the supervisor allowlist."}
            spec = self.app_config[key]
            command = list(spec.get("command") or [])
            return self._spawn_managed_process(key, command, cwd=spec.get("cwd") or None)

    def close_app(self, name: str) -> dict:
        with self.lock:
            key = self._resolve_app(name)
            if not key:
                return {"ok": False, "error": "That app is not in the supervisor allowlist."}
            return self._close_managed_process(key)

    def game_status(self) -> dict:
        with self.lock:
            if not self.game_library:
                return {"ok": False, "error": "The game subsystem is disabled."}
            self._cleanup_dead_apps()
            try:
                health = self.game_library.doctor()
            except Exception as exc:
                return {"ok": False, "error": f"Could not inspect game subsystem: {exc}"}
            health["frontend_running"] = bool(self.apps.get("games") and self.apps["games"].poll() is None)
            health["direct_game_running"] = bool(self.apps.get("game-session") and self.apps["game-session"].poll() is None)
            return health

    def list_games(self, system: str | None = None) -> dict:
        with self.lock:
            if not self.game_library:
                return {"ok": False, "error": "The game subsystem is disabled."}
            try:
                games = self.game_library.scan(system)
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "count": len(games), "games": [g.public() for g in games]}

    def launch_game(self, query: str, system: str | None = None) -> dict:
        with self.lock:
            if not self.game_library:
                return {"ok": False, "error": "The game subsystem is disabled."}
            try:
                game, candidates = self.game_library.find(query, system)
                if not game:
                    return {
                        "ok": False, "error": "Game name was not uniquely resolved.",
                        "candidates": [g.public() for g in candidates],
                    }
                # Only one direct voice-launched game at a time. ES-DE may remain
                # resident underneath so quitting Dolphin returns to the library.
                self._close_managed_process("game-session")
                command = self.game_library.command_for(game)
                result = self._spawn_managed_process("game-session", command)
                if result.get("ok"):
                    result["game"] = game.public()
                return result
            except GameError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:
                LOG.exception("Could not launch game")
                return {"ok": False, "error": f"Could not launch game: {exc}"}

    def close_game(self) -> dict:
        with self.lock:
            return self._close_managed_process("game-session")

    def _refresh_game_frontend_if_running(self) -> bool:
        with self.lock:
            self._cleanup_dead_apps()
            proc = self.apps.get("games")
            if not proc or proc.poll() is not None:
                return False
            self._close_managed_process("games")
            spec = self.app_config.get("games") or {}
            command = list(spec.get("command") or [])
            result = self._spawn_managed_process("games", command, cwd=spec.get("cwd") or None)
            return bool(result.get("ok"))

    def refresh_games(self) -> dict:
        if not self.game_library:
            return {"ok": False, "error": "The game subsystem is disabled."}
        try:
            games = self.game_library.scan("gamecube")
            refreshed = self._refresh_game_frontend_if_running()
            return {
                "ok": True,
                "count": len(games),
                "games": [g.public() for g in games],
                "frontend_refreshed": refreshed,
            }
        except Exception as exc:
            return {"ok": False, "error": f"Could not rescan games: {exc}"}

    def import_games_from_removable(self) -> dict:
        if not self.game_library:
            return {"ok": False, "error": "The game subsystem is disabled."}
        if not self.game_import_lock.acquire(blocking=False):
            return {"ok": False, "reason": "import-busy", "error": "A game import is already running."}
        try:
            roots = discover_removable_drives()
            result = self.game_library.import_from_roots(roots)
            if int(result.get("imported_count") or 0) > 0:
                result["frontend_refreshed"] = self._refresh_game_frontend_if_running()
            else:
                result["frontend_refreshed"] = False
            LOG.info(
                "Game import finished: discovered=%s imported=%s duplicates=%s errors=%s",
                result.get("discovered_count"), result.get("imported_count"),
                result.get("duplicate_count"), result.get("error_count"),
            )
            return result
        except Exception as exc:
            LOG.exception("Could not import games from removable media")
            return {"ok": False, "error": f"Could not import games: {exc}"}
        finally:
            self.game_import_lock.release()

    def close_all_apps(self) -> None:
        for key in list(self.apps):
            try:
                with self.lock:
                    self._close_managed_process(key)
            except Exception:
                LOG.exception("Could not close tracked app %s", key)

    def import_project_from_removable(self, selection: str | None = None) -> dict:
        if not self.project_import_lock.acquire(blocking=False):
            return {"ok": False, "reason": "import-busy", "error": "A project import is already running."}
        try:
            roots = discover_removable_drives()
            if not roots:
                return {"ok": False, "reason": "no-removable-drive", "candidates": []}
            result = self.project_manager.import_project_from_removable(roots, selection=selection or None)
            LOG.info(
                "Project import result: ok=%s reason=%s slug=%s candidates=%s",
                result.get("ok"), result.get("reason"), result.get("slug"), result.get("candidate_count"),
            )
            return result
        except Exception as exc:
            LOG.exception("Could not import project from removable media")
            return {"ok": False, "reason": "project-import-failed", "error": f"Could not import project: {exc}"}
        finally:
            self.project_import_lock.release()

    def import_files_into_project_from_removable(self, project_slug: str) -> dict:
        if not self.project_import_lock.acquire(blocking=False):
            return {"ok": False, "reason": "import-busy", "error": "A project import is already running."}
        try:
            roots = discover_removable_drives()
            if not roots:
                return {"ok": False, "reason": "no-removable-drive"}
            result = self.project_manager.import_files_from_removable(roots, slug=str(project_slug or "").strip() or None)
            LOG.info(
                "Project-file import result: ok=%s project=%s imported=%s duplicates=%s collisions=%s errors=%s",
                result.get("ok"), project_slug, result.get("imported_count"), result.get("duplicate_count"),
                result.get("collision_count"), result.get("error_count"),
            )
            return result
        except Exception as exc:
            LOG.exception("Could not import files into project from removable media")
            return {"ok": False, "reason": "project-file-import-failed", "error": f"Could not import project files: {exc}"}
        finally:
            self.project_import_lock.release()

    def export_project_items_to_removable(self, project_slug: str, selectors: list[str]) -> dict:
        if not self.project_import_lock.acquire(blocking=False):
            return {"ok": False, "reason": "export-busy", "error": "A project import/export is already running."}
        try:
            values = [str(item).strip() for item in (selectors or []) if str(item).strip()]
            roots = discover_removable_drives()
            if not roots:
                return {"ok": False, "reason": "no-removable-drive"}
            result = self.project_manager.export_project_items_to_removable(
                values,
                slug=str(project_slug or "").strip() or None,
                roots=roots,
            )
            LOG.info(
                "Project-item export result: ok=%s project=%s selected=%s new=%s duplicates=%s collisions=%s",
                result.get("ok"), project_slug, result.get("selection_count"), result.get("new_export_count"),
                result.get("duplicate_count"), result.get("collision_count"),
            )
            return result
        except Exception as exc:
            LOG.exception("Could not export project files to removable media")
            return {"ok": False, "reason": "project-file-export-failed", "error": f"Could not export project files: {exc}"}
        finally:
            self.project_import_lock.release()

    def handle(self, request: dict) -> dict:
        action = str(request.get("action", "")).strip()
        if action == "ping":
            return {"ok": True, "pong": True}
        if action == "status":
            with self.lock:
                return {"ok": True, **self._state()}
        if action == "list_apps":
            return self.list_apps()
        if action == "switch_model":
            return self.switch_model(
                request.get("model", ""),
                preload=bool(request.get("preload", True)),
                keep_alive=request.get("keep_alive", config.OLLAMA_KEEP_ALIVE),
            )
        if action == "unload_model":
            return self.unload_model(request.get("model"))
        if action == "launch_app":
            return self.launch_app(request.get("app", ""))
        if action == "close_app":
            return self.close_app(request.get("app", ""))
        if action == "game_status":
            return self.game_status()
        if action == "list_games":
            return self.list_games(request.get("system"))
        if action == "launch_game":
            return self.launch_game(request.get("query", ""), request.get("system"))
        if action == "close_game":
            return self.close_game()
        if action == "refresh_games":
            return self.refresh_games()
        if action == "import_games_from_removable":
            return self.import_games_from_removable()
        if action == "import_project_from_removable":
            return self.import_project_from_removable(request.get("selection"))
        if action == "reminders":
            keeper = getattr(self, "reminders", None)
            return keeper.handle(request) if keeper is not None else {"ok": False, "say": "My timers aren't running."}
        if action == "import_files_into_project_from_removable":
            return self.import_files_into_project_from_removable(request.get("project_slug", ""))
        if action == "export_project_items_to_removable":
            selectors = request.get("selectors") if isinstance(request.get("selectors"), list) else []
            return self.export_project_items_to_removable(request.get("project_slug", ""), selectors)
        if action == "computer_status":
            return self.computer_control_status()
        if action == "set_computer_control":
            return self.set_computer_control_enabled(bool(request.get("enabled")))
        if action == "computer_action":
            return self.computer_action(
                computer_action=request.get("computer_action", ""),
                payload=request.get("payload") if isinstance(request.get("payload"), dict) else {},
                requester_role=request.get("requester_role", ""),
                task_id=request.get("task_id", ""),
            )
        if action == "confirm_computer_action":
            return self.confirm_computer_action(
                request.get("confirmation_id"),
                require_critical=request.get("require_critical"),
            )
        if action == "cancel_computer_action":
            return self.cancel_computer_action(request.get("confirmation_id"))
        if action == "start_bemo":
            if self.bemo_is_running():
                return {"ok": True, "already_running": True}
            self.request_start_bemo(str(request.get("reason") or "api"))
            return {"ok": True, "start_requested": True}
        if action == "restart_bemo":
            self.restart_requested.set()
            return {"ok": True, "restart_requested": True}
        if action == "stop_bemo":
            self.stop_bemo_requested.set()
            return {"ok": True, "stop_requested": True}
        return {"ok": False, "error": f"Unknown supervisor action: {action}"}

    def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

        service = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                raw = self.rfile.readline(1024 * 1024)
                try:
                    request = json.loads(raw.decode("utf-8"))
                    if not isinstance(request, dict):
                        raise ValueError("request must be a JSON object")
                    response = service.handle(request)
                except Exception as exc:
                    LOG.exception("Supervisor request failed")
                    response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                try:
                    self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError):
                    # Clients can legitimately disappear (for example during a
                    # shutdown/reload or an old timeout).  Never turn that into
                    # a supervisor traceback storm.
                    LOG.debug("Supervisor client disconnected before reply")

        class ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = ThreadedUnixServer(str(self.socket_path), Handler)
        os.chmod(self.socket_path, 0o600)
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            name="bemo-supervisor-socket",
            daemon=True,
        )
        self.server_thread.start()
        self._save_state()
        LOG.info("Supervisor socket ready: %s", self.socket_path)

    def stop(self) -> None:
        self.stop_requested.set()
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                LOG.exception("Could not stop supervisor socket")
        if bool(config.SUPERVISOR_CLOSE_APPS_ON_EXIT):
            self.close_all_apps()
        if bool(config.SUPERVISOR_UNLOAD_MODEL_ON_EXIT) and self.active_model:
            try:
                self.unload_model(self.active_model)
            except Exception:
                LOG.exception("Could not unload model during supervisor shutdown")
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        self.set_bemo_pid(None)


def _configure_logging() -> None:
    from logging.handlers import RotatingFileHandler
    log_dir = Path(config.SUPERVISOR_LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            # Capped: 5 MB and two older files (audit 2026-10-24: it grew without limit, 3.4 MB in a week).
            RotatingFileHandler(log_dir / "supervisor.log", maxBytes=5_000_000, backupCount=2, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    # A line for every sound standby transcribes, and every brain request: 87% of the log, and no use.
    for chatty in ("faster_whisper", "httpx"):
        logging.getLogger(chatty).setLevel(logging.WARNING)


def _terminate_child(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    try:
        child.terminate()
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=2)


def _launch_bemo_child(command: list[str], *, woke_from_standby: bool = False) -> subprocess.Popen:
    env = os.environ.copy()
    if woke_from_standby:
        env["BEMO_STANDBY_WAKE"] = "1"
    else:
        env.pop("BEMO_STANDBY_WAKE", None)
    LOG.info("Starting Bemo child%s: %s", " from standby wake" if woke_from_standby else "", command)
    return subprocess.Popen(command, cwd=str(Path(__file__).resolve().parent), env=env)


def _unload_active_model_for_standby(service: SupervisorService) -> None:
    # No model name here: unload_model() picks the active model only after any switch still in
    # progress has finished. Passing service.active_model (read before) unloaded Coding while a
    # switch to Main completed, and Main stayed loaded in standby (live 2026-10-19).
    try:
        service.unload_model()
    except Exception:
        LOG.exception("Could not unload active model while entering standby")


def _hand_over_to_running_supervisor(*, standby: bool, socket_path=None, wait=time.sleep) -> bool:
    """Only one Supervisor at a time. A second one used to delete the first one's socket and take
    over, leaving two wake listeners on the microphone. Now it asks the running one to start BMO
    (unless this start was standby-only) and exits.
    """
    from systems.supervisor import SupervisorClient
    client = SupervisorClient(socket_path or config.SUPERVISOR_SOCKET_PATH, timeout=3.0)
    try:
        if not client.available():
            return False
    except Exception:
        return False
    if standby:
        # Waiting, not exiting: systemd restarts an exited service every 3 s, forever (review
        # 2026-10-19). When the other Supervisor goes away, this one takes over standby.
        LOG.info("A Supervisor is already running; standby waits until it stops.")
        print("BMO's Supervisor is already running; standby will take over when it stops.", flush=True)
        while True:
            wait(10.0)
            try:
                if not client.available():
                    break
            except Exception:
                break
        LOG.info("The other Supervisor stopped; starting standby.")
        return False
    try:
        if client.status().get("bemo_running"):
            # A start request would stay pending and relaunch BMO the next time he exits.
            print("BMO is already running.", flush=True)
            return True
        answer = client.start_bemo("second-start")
    except Exception as exc:  # it answered a moment ago; report rather than take over
        answer = {"ok": False, "error": str(exc)}
    LOG.info("A Supervisor is already running; asked it to start BMO: %s", answer)
    print("BMO's Supervisor is already running; asked it to start BMO"
          + ("." if answer.get("ok") else f", but it said: {answer.get('error') or answer}"), flush=True)
    return True


def _update_rolled_back(*, crashed: bool) -> bool:
    """Installer plan I5: a just-installed update that crashes, or isn't ready in time, is put back."""
    try:
        from systems.updates import default_updater
        if default_updater().roll_back_if_unhealthy(crashed=crashed):
            LOG.warning("The new version %s; went back to the previous version.",
                        "crashed" if crashed else "did not get ready in time")
            return True
    except Exception:
        LOG.exception("Could not check the update state")
    return False


def _first_boot_open() -> bool:
    try:
        from systems.first_boot import FirstBootState
        return FirstBootState.default().active()
    except Exception:
        LOG.exception("Could not read the first-boot state")
        return False


def run_supervised(command: list[str], *, standby: bool = False) -> int:
    """Run the permanent Supervisor and optionally wait in Hey-Bemo standby.

    In appliance mode (standby=True), the Supervisor and a tiny wake listener are
    the only Bemo components started at login.  A bare "Hey Bemo" launches the
    full UI/conversation process.  A deliberate full-Bemo shutdown returns here
    rather than terminating the Supervisor.
    """
    _configure_logging()
    if _hand_over_to_running_supervisor(standby=standby):
        return 0
    service = SupervisorService()
    service.start()
    shutdown = threading.Event()

    def request_shutdown(signum, frame):  # noqa: ARG001
        shutdown.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    wake_listener = None
    if standby and bool(getattr(config, "STANDBY_WAKE_ENABLED", True)):
        try:
            from standby_wake import StandbyWakeListener

            wake_listener = StandbyWakeListener(
                should_listen=lambda: (
                    not service.bemo_is_running()
                    and not service.start_bemo_requested.is_set()
                    and not service.restart_requested.is_set()
                    and not service.stop_requested.is_set()
                ),
                on_wake=lambda: service.request_start_bemo("standby_wake"),
                on_status=service.set_standby_wake_status,
            )
            wake_listener.start()
            LOG.info("Standby wake listener enabled; say 'Hey Bemo' to launch full Bemo.")
        except Exception as exc:
            LOG.exception("Could not start standby wake listener")
            service.set_standby_wake_status(False, f"{type(exc).__name__}: {exc}")

    try:
        # Installer plan I4: downloads that were started and not finished carry on after a reboot.
        from systems.downloads import resume_if_unfinished
        if resume_if_unfinished():
            LOG.info("Resumed BMO's unfinished brain downloads in the background.")
    except Exception:
        LOG.exception("Could not check the brain downloads")

    saver = None
    if standby:
        try:
            # 2026-10-20: screensavers in standby too, after 4 minutes, not over movies or games.
            from systems.standby_saver import from_config
            saver = from_config(lambda: service.bemo_is_running() or service.start_bemo_requested.is_set())
            if saver is not None:
                saver.start()
                LOG.info("Standby screensaver enabled (after %.0f s without input).",
                         float(config.CRT_SCREENSAVER_IDLE_SECONDS))
        except Exception:
            LOG.exception("Could not start the standby screensaver")
        try:
            # Each screen's own settings when the screen is swapped, even in standby (2026-10-22).
            from systems.display import ScreenWatcher
            ScreenWatcher(config.DISPLAY_SETTINGS_PATH).start()
        except Exception:
            LOG.exception("Could not start the screen watcher")

    try:
        # Timers and reminders go off awake or asleep (2026-10-24).
        from systems.reminders import ReminderKeeper
        service.reminders = ReminderKeeper(config.REMINDERS_PATH)
        service.reminders.start()
    except Exception:
        LOG.exception("Could not start timers and reminders")

    if not standby:
        service.request_start_bemo("startup")
    elif _first_boot_open():
        # Installer plan I2: the first boot is spoken, so BMO starts without waiting for "Hey BMO".
        LOG.info("First boot is not finished; starting BMO without the wake phrase.")
        service.request_start_bemo("first_boot")

    restarts = 0
    child_started = time.monotonic()
    last_update_check = 0.0
    child: subprocess.Popen | None = None
    exit_code = 0

    try:
        while not shutdown.is_set():
            if child is None:
                # A restart request while already in standby is equivalent to a
                # start request internally, but there is intentionally no user-
                # facing "start Bemo" voice command; Hey Bemo is the start gate.
                if service.restart_requested.is_set():
                    service.restart_requested.clear()
                    service.request_start_bemo("restart")

                if not service.start_bemo_requested.wait(0.2):
                    continue
                reason = service.consume_start_reason()
                if (
                    reason == "standby_wake"
                    and bool(getattr(config, "SUPERVISOR_PARALLEL_PRELOAD_ON_STANDBY_WAKE", True))
                ):
                    # Start loading/warming the main brain at the same instant
                    # the face/STT/TTS process launches.  The child later asks
                    # the Supervisor for the same model; switch_model() detects
                    # the resident copy and avoids doing the work twice.
                    main_profile = config.MODEL_PROFILES[config.MAIN_MODEL_PROFILE]
                    threading.Thread(
                        target=lambda: service.switch_model(
                            str(main_profile["model"]),
                            preload=True,
                            keep_alive=main_profile.get("keep_alive", -1),
                        ),
                        name="bemo-standby-main-preload",
                        daemon=True,
                    ).start()
                child = _launch_bemo_child(command, woke_from_standby=(reason == "standby_wake"))
                child_started = time.monotonic()
                service.set_bemo_pid(child.pid)
                service.stop_bemo_requested.clear()
                restarts = 0
                continue

            if service.restart_requested.is_set():
                service.restart_requested.clear()
                LOG.info("Deliberate Bemo reload requested.")
                _terminate_child(child)
                time.sleep(float(config.SUPERVISOR_RESTART_DELAY_SECONDS))
                child = _launch_bemo_child(command, woke_from_standby=False)
                child_started = time.monotonic()
                service.set_bemo_pid(child.pid)
                service.stop_bemo_requested.clear()
                restarts = 0
                continue

            if service.stop_bemo_requested.is_set():
                service.stop_bemo_requested.clear()
                LOG.info("Deliberate Bemo shutdown requested; returning to Hey-Bemo standby.")
                _terminate_child(child)
                child = None
                service.set_bemo_pid(None)
                _unload_active_model_for_standby(service)
                restarts = 0
                continue

            code = child.poll()
            if code is None:
                if time.monotonic() - last_update_check > 30:
                    last_update_check = time.monotonic()
                    if _update_rolled_back(crashed=False):
                        _terminate_child(child)
                        child = _launch_bemo_child(command, woke_from_standby=False)
                        child_started = time.monotonic()
                        service.set_bemo_pid(child.pid)
                        continue
                time.sleep(0.2)
                continue

            exit_code = int(code)
            child = None
            service.set_bemo_pid(None)

            if exit_code == 0:
                LOG.info("Bemo exited normally; Supervisor remains in standby.")
                _unload_active_model_for_standby(service)
                restarts = 0
                continue

            if _update_rolled_back(crashed=True):
                child = _launch_bemo_child(command, woke_from_standby=False)
                child_started = time.monotonic()
                service.set_bemo_pid(child.pid)
                restarts = 0
                continue

            restarts = _next_crash_count(restarts, time.monotonic() - child_started)
            if restarts > int(config.SUPERVISOR_MAX_CRASH_RESTARTS):
                LOG.error(
                    "Bemo exceeded crash restart limit (%s); returning to standby.",
                    config.SUPERVISOR_MAX_CRASH_RESTARTS,
                )
                _unload_active_model_for_standby(service)
                restarts = 0
                continue

            LOG.warning(
                "Bemo exited with code %s; restarting in %.1fs (%s/%s)",
                exit_code,
                config.SUPERVISOR_RESTART_DELAY_SECONDS,
                restarts,
                config.SUPERVISOR_MAX_CRASH_RESTARTS,
            )
            time.sleep(float(config.SUPERVISOR_RESTART_DELAY_SECONDS))
            child = _launch_bemo_child(command, woke_from_standby=False)
            child_started = time.monotonic()
            service.set_bemo_pid(child.pid)

    finally:
        if child is not None:
            _terminate_child(child)
        if saver is not None:
            saver.stop()
        if wake_listener is not None:
            wake_listener.stop()
        service.stop()
    return exit_code

STABLE_RUN_SECONDS = 600.0


def _next_crash_count(restarts: int, ran_seconds: float) -> int:
    """Only quick repeated crashes are a crash loop. A crash after a long healthy run starts a new
    count; before 2026-10-19 any 4 crashes without a deliberate reload, days apart, left BMO in standby."""
    return 1 if ran_seconds >= STABLE_RUN_SECONDS else restarts + 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Bemo lifecycle supervisor")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run Bemo lifecycle supervisor")
    run.add_argument(
        "--standby",
        action="store_true",
        help="start only Supervisor + Hey-Bemo wake listener until the wake phrase is heard",
    )
    run.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.cmd == "run":
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            command = [sys.executable, "__init__.py"]
        return run_supervised(command, standby=bool(args.standby))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
