#!/usr/bin/env bash
# BMO's voice for Piper (epoch 23), from the "voice-1" release of the public repository, checked against its
# SHA-256 (decided 2026-10-20). Skipped when the voice is already installed. Usage: voice.sh PYTHON
# Tests override BEMO_VOICE_BASE_URL, BEMO_VOICE_SHA256_ONNX, BEMO_VOICE_SHA256_JSON and BEMO_DATA_DIR.
set -euo pipefail

PYTHON="${1:-python}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="en_US-bemo-best-v3.onnx"
BASE_URL="${BEMO_VOICE_BASE_URL:-https://github.com/TechWizardForever/BMO/releases/download/voice-1}"
SHA_ONNX="${BEMO_VOICE_SHA256_ONNX:-897c58bff4fdd726cd1d465bbc22caa3acca9e82b7b666bedb9d38bd2da5921f}"
SHA_JSON="${BEMO_VOICE_SHA256_JSON:-7f148fbd2f9ec7af2bb464af3229fed50acb51038d2769b0b802e98826a5b4ae}"
DATA="${BEMO_DATA_DIR:-$HOME/.bemo}"
INSTALLED="$DATA/voice/piper/$NAME"
CACHE="$DATA/install-cache/voice"

if [ -s "$INSTALLED" ] && [ -s "$INSTALLED.json" ]; then
  echo "BMO's voice is already installed."
  exit 0
fi

matches() { [ -f "$1" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ]; }

fetch() {  # file, expected sha256
  local target="$CACHE/$1"
  if matches "$target" "$2"; then return 0; fi
  curl -fL --retry 5 --retry-all-errors --connect-timeout 20 -C - -o "$target.part" "$BASE_URL/$1"
  if ! matches "$target.part" "$2"; then
    rm -f "$target.part"
    echo "ERROR: $1 does not match its published checksum, so it was not installed." >&2
    exit 1
  fi
  mv "$target.part" "$target"
}

mkdir -p "$CACHE"
echo "Downloading BMO's voice (63.5 MB; resumes if interrupted)..."
fetch "$NAME.json" "$SHA_JSON"
fetch "$NAME" "$SHA_ONNX"
echo "BMO's voice checked (SHA-256 matches)."
(cd "$SCRIPT_DIR" && BEMO_DATA_DIR="$DATA" "$PYTHON" piper_deploy.py install "$CACHE/$NAME")
rm -f "$CACHE/$NAME" "$CACHE/$NAME.json"
