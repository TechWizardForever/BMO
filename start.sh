#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
else
  PYTHON=python3
fi

# Never block everyday boot on multi-minute Ollama benchmark runs.
# Opt in explicitly with BEMO_AUTOTUNE_ON_STARTUP=1 when tuning hardware;
# CPU-topology fallback or previously stored hardware tuning remains active.
if [ "${BEMO_SKIP_HARDWARE_AUTOTUNE:-0}" != "1" ] && [ "${BEMO_AUTOTUNE_ON_STARTUP:-0}" = "1" ]; then
  "$PYTHON" hardware_autotune.py ensure || \
    echo "WARNING: Bemo hardware benchmark was unavailable; using topology fallback." >&2
fi

# Developer escape hatch for debugging the UI without the parent process.
if [ "${BEMO_NO_SUPERVISOR:-0}" = "1" ]; then
  exec "$PYTHON" __init__.py
fi

# Appliance mode: the permanent Supervisor and lightweight Hey-Bemo wake listener
# start at login. The full UI/LLM/STT/TTS stack starts only after "Hey Bemo".
# Set BEMO_START_FULL_ON_LOGIN=1 only for debugging/legacy behavior.
if [ "${BEMO_START_FULL_ON_LOGIN:-0}" = "1" ]; then
  exec "$PYTHON" supervisor.py run -- "$PYTHON" __init__.py
fi
exec "$PYTHON" supervisor.py run --standby -- "$PYTHON" __init__.py
