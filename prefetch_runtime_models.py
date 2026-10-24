#!/usr/bin/env python3
"""Pre-download Bemo's local Whisper models and check the Piper voice during install."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import config

DATA_DIR = Path(os.environ.get("BEMO_DATA_DIR", str(Path.home() / ".bemo"))).expanduser()
STATE_DIR = DATA_DIR / "install"
STATE_PATH = STATE_DIR / "runtime-models.json"


def _load() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _prefetch_whisper(state: dict, *, state_key: str, model_name: str, compute_type: str, force: bool) -> None:
    key = f"faster-whisper:{model_name}:{compute_type}"
    legacy_ready = state_key == "whisper_main" and state.get("whisper") == key
    if not force and (state.get(state_key) == key or legacy_ready):
        print(f"[skip] Whisper cache already prepared: {model_name}")
        state[state_key] = key
        _save(state)
        return

    print(f"Downloading/caching Whisper model: {model_name}", flush=True)
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_name,
        device="cpu",
        compute_type=compute_type,
        cpu_threads=1,
        num_workers=1,
    )
    del model
    state[state_key] = key
    state[f"{state_key}_completed_at_unix"] = int(time.time())
    _save(state)


def prefetch(force: bool = False) -> int:
    state = _load()

    _prefetch_whisper(
        state,
        state_key="whisper_main",
        model_name=config.STT_MODEL,
        compute_type=config.STT_COMPUTE_TYPE,
        force=force,
    )
    _prefetch_whisper(
        state,
        state_key="whisper_standby",
        model_name=config.STANDBY_WAKE_STT_MODEL,
        compute_type=config.STANDBY_WAKE_STT_COMPUTE_TYPE,
        force=force,
    )

    tts_key = f"{config.TTS_BACKEND}:{config.TTS_MODEL}"
    if force or state.get("tts") != tts_key:
        print(f"Checking local Piper voice: {config.PIPER_MODEL_PATH}", flush=True)
        from systems.piper_streaming import PiperSpeechEngine
        model = PiperSpeechEngine(config.PIPER_MODEL_PATH)
        del model
        state["tts"] = tts_key
        state["tts_completed_at_unix"] = int(time.time())
        _save(state)
    else:
        print(f"[skip] TTS cache already prepared: {config.TTS_MODEL}")

    print("Main Whisper + standby wake Whisper + Piper voice are ready.")
    return 0


def status() -> int:
    state = _load()
    main_key = f"faster-whisper:{config.STT_MODEL}:{config.STT_COMPUTE_TYPE}"
    standby_key = f"faster-whisper:{config.STANDBY_WAKE_STT_MODEL}:{config.STANDBY_WAKE_STT_COMPUTE_TYPE}"
    tts_key = f"{config.TTS_BACKEND}:{config.TTS_MODEL}"
    main_ready = state.get("whisper_main") == main_key or state.get("whisper") == main_key
    standby_ready = state.get("whisper_standby") == standby_key
    tts_ready = state.get("tts") == tts_key
    print(f"Main Whisper {config.STT_MODEL}: {'READY' if main_ready else 'NOT PREFETCHED'}")
    print(f"Standby wake Whisper {config.STANDBY_WAKE_STT_MODEL}: {'READY' if standby_ready else 'NOT PREFETCHED'}")
    print(f"TTS {config.TTS_MODEL}: {'READY' if tts_ready else 'NOT PREFETCHED'}")
    return 0 if main_ready and standby_ready and tts_ready else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-download/status Bemo speech model assets")
    parser.add_argument("command", choices=("prefetch", "status"), nargs="?", default="status")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    return prefetch(args.force) if args.command == "prefetch" else status()


if __name__ == "__main__":
    raise SystemExit(main())
