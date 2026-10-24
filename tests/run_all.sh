#!/usr/bin/env bash
# Run every BMO regression suite against this source tree.
# Uses a throwaway data dir so tests can never touch ~/.bemo.
set -uo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
TMP_DATA="$(mktemp -d)"
trap 'rm -rf "$TMP_DATA"' EXIT
export BEMO_TEST_CODE_ROOT="$ROOT" BEMO_DATA_DIR="$TMP_DATA" PYTHONDONTWRITEBYTECODE=1
FAILED=0
for TEST in "$ROOT"/tests/legacy/test_*.py "$ROOT"/tests/test_*.py; do
  [[ -f "$TEST" ]] || continue
  echo "== $(basename "$TEST")"
  OUT="$("$PY" "$TEST" -q 2>&1)"; CODE=$?
  printf '%s\n' "$OUT" | grep -E '^(Ran |OK|FAILED|ERROR|FAIL:|Traceback)' || true
  [[ $CODE -eq 0 ]] || { printf '%s\n' "$OUT" | tail -40; echo "FAILED: $TEST"; FAILED=1; }
done
# The broad non-audio self-test (projects, Git, export, backup, games, USB import, Supervisor).
# It had been failing at its first check since before the transfer; brought up to date 2026-10-19.
echo "== self_test.py"
OUT="$(cd "$ROOT" && "$PY" self_test.py 2>&1)"; CODE=$?
printf '%s\n' "$OUT" | grep -E 'self-test: PASS|Traceback|AssertionError' || true
[[ $CODE -eq 0 ]] || { printf '%s\n' "$OUT" | grep -v DEBUG | tail -40; echo "FAILED: self_test.py"; FAILED=1; }
# Offline soak, short: persistence, projects, Git, USB export, backup and restore, three times over.
echo "== soak_test.py --cycles 3"
OUT="$(cd "$ROOT" && "$PY" soak_test.py --cycles 3 2>&1)"; CODE=$?
printf '%s\n' "$OUT" | grep -E 'soak test: PASS|Traceback|AssertionError' || true
[[ $CODE -eq 0 ]] || { printf '%s\n' "$OUT" | grep -v DEBUG | tail -40; echo "FAILED: soak_test.py"; FAILED=1; }
exit $FAILED
