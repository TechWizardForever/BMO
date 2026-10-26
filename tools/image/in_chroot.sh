#!/usr/bin/env bash
# BMO disk image (installer plan I8): runs as root INSIDE the unpacked Mint system (chroot), called by
# build_image.sh. The same steps as install.sh, minus what only makes sense on a running PC: the brains
# (downloaded at first boot), the hardware benchmark, autologin and autostart (tools/image/firstboot.py).
# Per-user files (voice, wake word, speech recognition) go to /opt/bemo-seed and move to the account's
# home at first boot.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
APP=/opt/bemo
SEED=/opt/bemo-seed
cd "$APP"
step() { echo; echo "=== in image: $*"; }

step "Linux packages"
apt-get update
# shellcheck disable=SC2046
apt-get install -y $(grep -v '^#' installer/apt-packages.txt)
apt-get install -y xprintidle || echo "WARNING: xprintidle unavailable; the screensaver uses BMO's own idle time."
apt-get install -y libfuse2t64 || apt-get install -y libfuse2 || true
# The legacy Windows 95/98 screensavers run through Wine; most are 32-bit.
dpkg --add-architecture i386
apt-get update
apt-get install -y wine wine64 wine32:i386 || apt-get install -y wine

step "Ollama (processor only)"
BEMO_SUDO="" BEMO_OLLAMA_SKIP_SERVICE=1 BEMO_INSTALL_CACHE=/var/cache/bemo bash installer/ollama.sh
id ollama >/dev/null 2>&1 || useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama
cat > /etc/systemd/system/ollama.service <<'EOF'
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

[Install]
WantedBy=default.target
EOF
systemctl enable ollama.service

step "Python environment (exact versions)"
python3 -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -r installer/build-tools.lock
.venv/bin/python -m pip install --no-cache-dir --no-deps -r installer/requirements.lock
.venv/bin/python installer/check_packages.py
.venv/bin/python - <<'PY'
import numpy, cadquery, openpyxl, scipy, trimesh, piper, faster_whisper, openwakeword
print(f"NumPy {numpy.__version__}; CadQuery {cadquery.__version__}")
PY

step "Per-user files for the first account (voice, wake word, speech recognition)"
mkdir -p "$SEED/.bemo/models/wakeword"
export HOME="$SEED" BEMO_DATA_DIR="$SEED/.bemo"
bash installer/voice.sh .venv/bin/python
for f in hey_bmo.onnx melspectrogram.onnx embedding_model.onnx; do
  cp -n "assets/wakeword/$f" "$SEED/.bemo/models/wakeword/$f"
done
.venv/bin/python prefetch_runtime_models.py prefetch
# Not the Windows screensavers (Microsoft's files) nor any game or video: the image carries none of those, and
# BMO fetches the screensavers and the game player on the installed PC (systems/downloads.py, 2026-10-24).
# Only the models stay: onnxruntime leaves a random device ID (every PC from the image would share it)
# and the Hugging Face download leaves logs.
rm -rf "$SEED/.bemo/install-cache" "$SEED/.cache/Microsoft" "$SEED/.cache/huggingface/xet" \
  "$SEED/.cache/huggingface/.agent_harnesses.json"
find "$SEED" -name deviceid -delete
unset HOME BEMO_DATA_DIR
export HOME=/root

step "First start of the installed PC"
install -m 644 tools/image/bemo-firstboot.service /etc/systemd/system/bemo-firstboot.service
systemctl enable bemo-firstboot.service
# The install screen's Linux updates step (2026-10-25): root's own copy of the helper, a service that runs
# only it, and a polkit rule that lets the person at the computer start that one service.
install -d -m 755 /usr/local/lib/bemo
install -m 755 -o root -g root tools/image/system_update.py /usr/local/lib/bemo/system_update.py
install -m 644 tools/image/bemo-system-update.service /etc/systemd/system/bemo-system-update.service
install -d -m 755 /etc/polkit-1/rules.d
install -m 644 tools/image/bemo-system-update.rules /etc/polkit-1/rules.d/50-bemo-system-update.rules

step "BMO self-test"
check_home="$(mktemp -d)"
if (HOME="$check_home" BEMO_DATA_DIR="$check_home/.bemo" .venv/bin/python self_test.py); then
  echo "SELF-TEST PASSED"
else
  echo "WARNING: SELF-TEST FAILED inside the image (see above)."
fi
rm -rf "$check_home"

step "Tidy up"
apt-get clean
rm -rf /var/cache/bemo /root/.cache /tmp/* /var/lib/apt/lists/*
find "$APP" -path "$APP/.venv" -prune -o -name __pycache__ -type d -prune -exec rm -rf {} +
echo "IN-IMAGE STEPS DONE"
