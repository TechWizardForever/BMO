#!/usr/bin/env bash
# Convenience full-interface start; strict Hey Bemo remains required on each turn.
set -euo pipefail
cd "$(dirname "$0")"
export BEMO_START_FULL_ON_LOGIN=1
export BEMO_SKIP_HARDWARE_AUTOTUNE=1
exec bash ./start.sh
