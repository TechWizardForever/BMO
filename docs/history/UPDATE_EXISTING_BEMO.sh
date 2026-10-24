#!/usr/bin/env bash
# Install Bemo v1.27 Piper update + bundled epoch-23 ONNX without reinstalling
# Ollama, downloading models, or replacing the existing virtualenv/data tree.
set -euo pipefail
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODEL="$SOURCE/bundled_voice/en_US-bemo-best-v3.onnx"

if [[ "$(id -u)" -eq 0 ]]; then
  echo 'Do not run as root or with sudo. Use your regular Bemo desktop account.' >&2
  exit 2
fi

if [[ ! -s "$MODEL" || ! -s "$MODEL.json" ]]; then
  echo "Bundled voice model/config missing from $SOURCE/bundled_voice" >&2
  exit 2
fi

if [[ $# -gt 1 ]]; then
  echo "Usage: bash UPDATE_EXISTING_BEMO.sh [path-to-existing-bemo-master]" >&2
  exit 2
fi

if [[ $# -eq 1 ]]; then
  TARGET="$(realpath -m -- "$1")"
else
  CANDIDATES=(
    "$HOME/bemo-master"
    "$HOME/Bemo/bemo-master"
    "$HOME/Bemo"
    "$HOME/bemo"
    "$HOME/bemo-supervisor/bemo-master"
  )
  TARGET=''
  for candidate in "${CANDIDATES[@]}"; do
    if [[ -f "$candidate/start.sh" && -f "$candidate/systems/chat.py" && -d "$candidate/.venv" ]]; then
      if [[ -n "$TARGET" ]]; then
        echo 'Multiple existing Bemo installations detected. Pass your desired installation path.' >&2
        printf 'Examples: bash UPDATE_EXISTING_BEMO.sh "$HOME/bemo-master"\n' >&2
        exit 2
      fi
      TARGET="$(realpath -m -- "$candidate")"
    fi
  done
fi

if [[ -z "$TARGET" || ! -f "$TARGET/start.sh" || ! -f "$TARGET/systems/chat.py" || ! -f "$TARGET/config.py" || ! -x "$TARGET/.venv/bin/python" ]]; then
  echo 'Could not safely find your existing Bemo installation with its .venv.' >&2
  echo 'Usage: bash UPDATE_EXISTING_BEMO.sh /full/path/to/your/existing/bemo-master' >&2
  exit 2
fi
if [[ "$TARGET" == "$SOURCE" ]]; then
  echo 'This is the extracted update, not your existing Bemo installation.' >&2
  echo 'Pass the path to the currently installed Bemo directory.' >&2
  exit 2
fi

printf '\nBemo Piper epoch-23 all-in-one update\n'
printf 'Update source: %s\n' "$SOURCE"
printf 'Existing Bemo: %s\n' "$TARGET"
printf 'Data directory: %s\n' "${BEMO_DATA_DIR:-$HOME/.bemo}"
printf '\nClose Bemo before proceeding. Existing Ollama models, .venv, ~/.bemo and systemd settings are preserved.\n'
read -r -p 'Type YES to continue: ' CONFIRM
if [[ "$CONFIRM" != YES ]]; then
  echo 'Update cancelled. No changes made.'
  exit 1
fi

# Dependency install happens before any source or data files are changed.
echo 'Installing Piper into the existing Bemo virtualenv (requires internet once)...'
"$TARGET/.venv/bin/python" -m pip install -c "$SOURCE/constraints.txt" 'piper-tts==1.8.0'

# Match the shipped voice to its recorded digest before deployment.
echo 'Checking bundled epoch-23 model integrity...'
python3 - "$MODEL" "$SOURCE/bundled_voice/VOICE_SHA256.txt" <<'PY'
import hashlib, pathlib, sys
model = pathlib.Path(sys.argv[1])
expected = pathlib.Path(sys.argv[2]).read_text().strip().split()[0]
actual = hashlib.sha256(model.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f'Bundled model checksum mismatch: {actual} != {expected}')
print('Model SHA256 OK:', actual)
PY

BACKUP="${BEMO_DATA_DIR:-$HOME/.bemo}/source-upgrade-backups/v1.27.0-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
echo "Backing up replaced source files under $BACKUP ..."
python3 - "$SOURCE" "$TARGET" "$BACKUP" <<'PY'
from pathlib import Path
import shutil, sys
src, dst, bak = map(Path, sys.argv[1:])
exclude = {'bundled_voice', '__pycache__', '.venv', '.git', '.pytest_cache'}
count = 0
for entry in src.rglob('*'):
    rel = entry.relative_to(src)
    if any(part in exclude for part in rel.parts) or not entry.is_file():
        continue
    target = dst / rel
    if target.exists():
        old = bak / rel
        old.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, old)
    count += 1
print(f'{count} source files to update; existing versions backed up where present.')
PY

echo 'Updating Bemo source (preserving its .venv and separate ~/.bemo data)...'
python3 - "$SOURCE" "$TARGET" <<'PY'
from pathlib import Path
import shutil, sys
src, dst = map(Path, sys.argv[1:])
exclude = {'bundled_voice', '__pycache__', '.venv', '.git', '.pytest_cache'}
for entry in src.rglob('*'):
    rel = entry.relative_to(src)
    if any(part in exclude for part in rel.parts) or not entry.is_file():
        continue
    target = dst / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(entry, target)
PY

echo 'Installing the bundled epoch-23 voice into Bemo data...'
(cd "$TARGET" && .venv/bin/python piper_deploy.py install "$MODEL")

echo 'Checking Python syntax of new runtime components...'
"$TARGET/.venv/bin/python" - "$TARGET" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for rel in ('config.py','piper_deploy.py','systems/piper_streaming.py','systems/chat.py'):
    compile((root/rel).read_bytes(),str(root/rel),'exec')
print('Python syntax OK')
PY

echo 'Running Piper synthesis benchmark on this machine...'
(cd "$TARGET" && .venv/bin/python piper_deploy.py benchmark)

printf '\nUPDATE FINISHED. The voice and Bemo v1.27 source are installed.\n'
printf 'Source backups: %s\n' "$BACKUP"
printf 'Bemo data: %s\n' "${BEMO_DATA_DIR:-$HOME/.bemo}"
printf 'Restart Bemo the same way you normally do.\n'
