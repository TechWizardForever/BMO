#!/usr/bin/env bash
# Install Ollama at the version BMO is tested with, checked against its published SHA-256
# (installer plan, milestone I1). Mirrors the Linux steps of Ollama's own install script for the same
# version, without its GPU driver and ROCm downloads: BMO runs its brains on the processor.
#
# An Ollama that is already installed is never replaced silently: a different version is kept unless
# BEMO_OLLAMA_REPLACE=1. Tests override BEMO_OLLAMA_URL, BEMO_OLLAMA_SHA256, BEMO_OLLAMA_PREFIX,
# BEMO_SUDO and BEMO_OLLAMA_SKIP_SERVICE.
set -euo pipefail

OLLAMA_VERSION="0.40.5"
# ollama-linux-amd64.tar.zst, 1.34 GB, from https://github.com/ollama/ollama/releases/tag/v0.40.5
OLLAMA_SHA256="${BEMO_OLLAMA_SHA256:-dc5eabc292f9949546c90a97c820f924b035e05243b0de13c7f366182562169c}"
OLLAMA_URL="${BEMO_OLLAMA_URL:-https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst}"
PREFIX="${BEMO_OLLAMA_PREFIX:-/usr/local}"
SUDO="${BEMO_SUDO-sudo}"
CACHE="${BEMO_INSTALL_CACHE:-${BEMO_DATA_DIR:-$HOME/.bemo}/install-cache}"
ARCHIVE="$CACHE/ollama-${OLLAMA_VERSION}-linux-amd64.tar.zst"

installed_version() {
  local binary="$PREFIX/bin/ollama"
  if [ ! -x "$binary" ] && [ "$PREFIX" = /usr/local ]; then
    binary="$(command -v ollama 2>/dev/null || true)"
  fi
  [ -n "$binary" ] && [ -x "$binary" ] || return 0
  "$binary" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | tail -n1 || true
}

current="$(installed_version)"
if [ "$current" = "$OLLAMA_VERSION" ]; then
  echo "Ollama $OLLAMA_VERSION is already installed."
  exit 0
fi
if [ -n "$current" ] && [ "${BEMO_OLLAMA_REPLACE:-0}" != "1" ]; then
  echo "Ollama $current is installed; BMO is tested with $OLLAMA_VERSION. Keeping $current."
  echo "To install $OLLAMA_VERSION instead, rerun with BEMO_OLLAMA_REPLACE=1."
  exit 0
fi
case "$(uname -m)" in
  x86_64|amd64) ;;
  *) echo "ERROR: BMO's Ollama install is for 64-bit Intel/AMD processors only." >&2; exit 1 ;;
esac
command -v zstd >/dev/null 2>&1 || { echo "ERROR: zstd is needed to unpack Ollama (sudo apt-get install zstd)." >&2; exit 1; }

matches() { [ -f "$1" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$OLLAMA_SHA256" ]; }

mkdir -p "$CACHE"
if ! matches "$ARCHIVE"; then
  echo "Downloading Ollama $OLLAMA_VERSION (1.34 GB; resumes if interrupted)..."
  curl -fL --retry 5 --retry-all-errors --connect-timeout 20 -C - -o "$ARCHIVE.part" "$OLLAMA_URL"
  if ! matches "$ARCHIVE.part"; then
    rm -f "$ARCHIVE.part"
    echo "ERROR: the Ollama download does not match its published checksum, so it was not installed." >&2
    echo "Nothing was changed. Run the installer again to download it afresh." >&2
    exit 1
  fi
  mv "$ARCHIVE.part" "$ARCHIVE"
fi
echo "Ollama download checked (SHA-256 matches)."

if [ -z "${BEMO_OLLAMA_SKIP_SERVICE:-}" ] && systemctl is-active --quiet ollama 2>/dev/null; then
  $SUDO systemctl stop ollama
fi
if [ -d "$PREFIX/lib/ollama" ]; then
  $SUDO rm -rf "$PREFIX/lib/ollama"
fi
$SUDO install -o0 -g0 -m755 -d "$PREFIX/bin" "$PREFIX/lib/ollama" 2>/dev/null || \
  $SUDO install -m755 -d "$PREFIX/bin" "$PREFIX/lib/ollama"
zstd -dc "$ARCHIVE" | $SUDO tar -xf - -C "$PREFIX"
[ -x "$PREFIX/bin/ollama" ] || { echo "ERROR: $PREFIX/bin/ollama is missing after unpacking." >&2; exit 1; }
# BMO runs its brains on the processor: the NVIDIA and AMD graphics libraries (~4.7 GB) are never used.
if [ "${BEMO_OLLAMA_KEEP_GPU:-0}" != "1" ]; then
  $SUDO rm -rf "$PREFIX"/lib/ollama/cuda_* "$PREFIX"/lib/ollama/rocm*
fi
echo "Ollama $OLLAMA_VERSION installed in $PREFIX."

if [ -z "${BEMO_OLLAMA_SKIP_SERVICE:-}" ] && command -v systemctl >/dev/null 2>&1; then
  if ! id ollama >/dev/null 2>&1; then
    $SUDO useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama
  fi
  for group in render video; do
    if getent group "$group" >/dev/null 2>&1; then $SUDO usermod -a -G "$group" ollama; fi
  done
  $SUDO usermod -a -G ollama "$(whoami)"
  $SUDO tee /etc/systemd/system/ollama.service >/dev/null <<EOF
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=$PREFIX/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="PATH=$PATH"

[Install]
WantedBy=default.target
EOF
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable ollama
  $SUDO systemctl restart ollama
fi
rm -f "$ARCHIVE"
