#!/usr/bin/env python3
"""Install Bemo's locked Ollama model roster.

The installer uses exact Q4_K_M model tags/refs and stable local aliases that the
runtime already expects. It is designed to be safe to rerun after interruption:
completed aliases are skipped when their recorded source matches, incomplete
pulls are retried, and state is written atomically only after a pull+alias
finishes successfully.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

DATA_DIR = Path(os.environ.get("BEMO_DATA_DIR", str(Path.home() / ".bemo"))).expanduser()
STATE_DIR = DATA_DIR / "install"
STATE_PATH = STATE_DIR / "model-roster.json"


@dataclass(frozen=True)
class ModelSpec:
    role: str
    source: str
    alias: str
    approx_gb: float


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        "Main Brain",
        "qwen3:30b-a3b-instruct-2507-q4_K_M",
        "qwen3-main:30b",
        19.0,
    ),
    ModelSpec(
        "Engineering / Deep Reasoning",
        "qwen3:30b-a3b-thinking-2507-q4_K_M",
        "qwen3-engineering:30b",
        19.0,
    ),
    ModelSpec(
        "Heavy Coding",
        "qwen3-coder:30b-a3b-q4_K_M",
        "qwen3-coder:30b",
        19.0,
    ),
    ModelSpec(
        "Deep Technical Vision",
        "hf.co/bartowski/OpenGVLab_InternVL3_5-30B-A3B-GGUF:Q4_K_M",
        "internvl3.5-deep:30b",
        18.6,
    ),
    ModelSpec(
        "Emergency / Recovery",
        "qwen3:4b-instruct-2507-q4_K_M",
        "qwen3-emergency:4b",
        2.5,
    ),
)


def _run(argv: list[str], *, capture: bool = False, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=check,
    )


def _load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _model_exists(name: str) -> bool:
    result = _run(["ollama", "show", name], capture=True)
    return result.returncode == 0


def _require_ollama() -> None:
    if not shutil.which("ollama"):
        raise SystemExit("Ollama is not installed. Run ./install.sh or install Ollama first.")
    result = _run(["ollama", "list"], capture=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Ollama server is not reachable").strip()
        raise SystemExit(message)


def _select(names: Iterable[str] | None) -> list[ModelSpec]:
    if not names:
        return list(MODELS)
    wanted = {n.strip().lower() for n in names if n.strip()}
    selected: list[ModelSpec] = []
    for spec in MODELS:
        hay = {spec.role.lower(), spec.alias.lower(), spec.source.lower()}
        if any(w == h or w in h for w in wanted for h in hay):
            selected.append(spec)
    if not selected:
        raise SystemExit(f"No roster model matched: {', '.join(sorted(wanted))}")
    return selected


def _pull(spec: ModelSpec, retries: int) -> None:
    retries = max(1, retries)
    for attempt in range(1, retries + 1):
        print(f"\n[{spec.role}] Pulling exact source: {spec.source}", flush=True)
        result = _run(["ollama", "pull", spec.source])
        if result.returncode == 0:
            return
        if attempt >= retries:
            raise RuntimeError(f"ollama pull failed for {spec.source} after {retries} attempt(s)")
        delay = min(60, 3 * (2 ** (attempt - 1)))
        print(
            f"Pull interrupted/failed. Retrying in {delay}s. Existing Ollama blobs/partial "
            "GGUF download state are left in place for the next attempt.",
            flush=True,
        )
        time.sleep(delay)


def install(selected: list[ModelSpec], *, retries: int, force: bool = False) -> int:
    _require_ollama()
    state = _load_state()
    records = state.setdefault("models", {})
    failed = 0

    for spec in selected:
        record = records.get(spec.alias, {})
        if (
            not force
            and record.get("complete") is True
            and record.get("source") == spec.source
            and _model_exists(spec.alias)
        ):
            print(f"[skip] {spec.role}: {spec.alias} already installed from the locked source.")
            continue

        try:
            _pull(spec, retries)
            print(f"[{spec.role}] Creating stable Bemo alias: {spec.alias}", flush=True)
            result = _run(["ollama", "cp", spec.source, spec.alias])
            if result.returncode != 0:
                raise RuntimeError(f"ollama cp failed: {spec.source} -> {spec.alias}")
            if not _model_exists(spec.alias):
                raise RuntimeError(f"alias verification failed for {spec.alias}")
            records[spec.alias] = {
                **asdict(spec),
                "complete": True,
                "completed_at_unix": int(time.time()),
            }
            _save_state(state)
            print(f"[ok] {spec.role}: {spec.alias}")
        except Exception as exc:
            failed += 1
            records[spec.alias] = {
                **asdict(spec),
                "complete": False,
                "error": str(exc),
                "updated_at_unix": int(time.time()),
            }
            _save_state(state)
            print(f"[FAILED] {spec.role}: {exc}", file=sys.stderr)
            break

    if failed:
        print(
            "\nModel installation is incomplete. Re-run this command or ./install.sh; completed "
            "roles are retained and the failed pull will be retried.",
            file=sys.stderr,
        )
        return 1
    print("\nAll requested Bemo models are installed and aliased.")
    return 0


def status(selected: list[ModelSpec]) -> int:
    _require_ollama()
    state = _load_state().get("models", {})
    missing = 0
    for spec in selected:
        exists = _model_exists(spec.alias)
        locked = state.get(spec.alias, {}).get("source") == spec.source and state.get(spec.alias, {}).get("complete") is True
        flag = "OK" if exists and locked else ("PRESENT/UNVERIFIED" if exists else "MISSING")
        print(f"{flag:18} {spec.role:28} {spec.alias}")
        if not (exists and locked):
            missing += 1
    print(f"\nLocked roster download estimate: ~{sum(s.approx_gb for s in selected):.1f} GB")
    return 1 if missing else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Install/status Bemo's locked Ollama model roster")
    sub = parser.add_subparsers(dest="command")

    p_install = sub.add_parser("install", help="pull exact models and create Bemo aliases")
    p_install.add_argument("--only", action="append", default=[], help="role/name substring; may be repeated")
    p_install.add_argument("--retries", type=int, default=int(os.environ.get("BEMO_MODEL_PULL_RETRIES", "8")))
    p_install.add_argument("--force", action="store_true", help="refresh even if state says the exact alias is complete")

    p_status = sub.add_parser("status", help="show exact-roster installation state")
    p_status.add_argument("--only", action="append", default=[], help="role/name substring; may be repeated")

    sub.add_parser("list", help="print the locked source->alias roster")
    args = parser.parse_args()

    command = args.command or "status"
    if command == "list":
        for spec in MODELS:
            print(f"{spec.role}\n  source: {spec.source}\n  alias:  {spec.alias}\n  approx: {spec.approx_gb:.1f} GB")
        print(f"\nTotal download estimate: ~{sum(s.approx_gb for s in MODELS):.1f} GB")
        return 0

    selected = _select(getattr(args, "only", None))
    if command == "install":
        return install(selected, retries=args.retries, force=args.force)
    return status(selected)


if __name__ == "__main__":
    raise SystemExit(main())
