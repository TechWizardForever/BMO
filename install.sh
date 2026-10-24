#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BEMO_DATA_DIR="${BEMO_DATA_DIR:-$HOME/.bemo}"

if [ "$(id -u)" -eq 0 ]; then
  echo "ERROR: Run ./install.sh as the normal Bemo desktop user, not with sudo." >&2
  echo "The installer invokes sudo only for the system changes that need it." >&2
  exit 2
fi

BEMO_LOGIN_USER="${BEMO_AUTOLOGIN_USER:-$(id -un)}"
# One screen of "this PC: OK / not OK, because ..." before anything is changed (installer plan I1).
# ./install.sh --check runs only this. BEMO_ALLOW_UNSUPPORTED_OS=1 lets another apt-based Linux through.
if [ "${1:-}" = "--check" ]; then
  exec python3 "$SCRIPT_DIR/installer/precheck.py"
fi
if ! python3 "$SCRIPT_DIR/installer/precheck.py"; then
  echo "Nothing was changed. Fix what is listed above, then run ./install.sh again." >&2
  exit 1
fi
INSTALL_LOG_DIR="$BEMO_DATA_DIR/install"
mkdir -p "$INSTALL_LOG_DIR"

retry_cmd() {
  local attempts="$1"; shift
  local delay=3
  local n=1
  until "$@"; do
    if [ "$n" -ge "$attempts" ]; then
      return 1
    fi
    echo "Command failed; retrying in ${delay}s (${n}/${attempts})..."
    sleep "$delay"
    n=$((n + 1))
    if [ "$delay" -lt 30 ]; then delay=$((delay * 2)); fi
  done
}

if command -v apt-get >/dev/null 2>&1; then
  echo "Installing Linux/system dependencies..."
  sudo apt-get update
  # The list is shared with the disk image build (tools/image/in_chroot.sh).
  # shellcheck disable=SC2046
  sudo apt-get install -y $(grep -v '^#' "$SCRIPT_DIR/installer/apt-packages.txt")

  # xprintidle reports keyboard/mouse activity across the whole X11 desktop,
  # not just events delivered to Bemo's own pygame window.
  sudo apt-get install -y xprintidle || \
    echo "WARNING: xprintidle unavailable; CRT saver will use Bemo-local idle fallback."

  # Bemo GameCube appliance: standalone Dolphin with Flatpak fallback.
  if [ "${BEMO_SKIP_DOLPHIN_SETUP:-0}" != "1" ]; then
    if ! command -v dolphin-emu >/dev/null 2>&1 && ! command -v dolphin >/dev/null 2>&1; then
      if ! sudo apt-get install -y dolphin-emu; then
        echo "Native dolphin-emu package was not available; trying Flatpak."
        sudo apt-get install -y flatpak || true
        if command -v flatpak >/dev/null 2>&1; then
          flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo || true
          flatpak install --user -y flathub org.DolphinEmu.dolphin-emu || \
            echo "WARNING: Dolphin Flatpak install failed. Install Dolphin manually and rerun game_launcher.py doctor."
        fi
      fi
    fi
    # The Flatpak sees the home folder read-only; saves and settings go in BMO's own Dolphin folder.
    if command -v flatpak >/dev/null 2>&1 && flatpak info org.DolphinEmu.dolphin-emu >/dev/null 2>&1; then
      mkdir -p "$BEMO_DATA_DIR/games/dolphin-user"
      flatpak override --user --filesystem="$BEMO_DATA_DIR/games/dolphin-user" org.DolphinEmu.dolphin-emu || \
        echo "WARNING: Dolphin could not be given its save folder; game saves may not be kept."
    fi
  fi
  sudo apt-get install -y libfuse2t64 2>/dev/null || sudo apt-get install -y libfuse2 2>/dev/null || true

  # Linux Mint 22.3 Xfce appliance mode: configure LightDM to log the Bemo
  # desktop account in automatically. Firmware/BIOS still runs before LightDM.
  if [ "${BEMO_SKIP_AUTOLOGIN_SETUP:-0}" != "1" ]; then
    echo "Configuring LightDM automatic login for ${BEMO_LOGIN_USER}..."
    sudo python3 mint_appliance.py configure-autologin --user "$BEMO_LOGIN_USER"
    sudo systemctl enable lightdm.service >/dev/null 2>&1 || true
  fi

  # The legacy Windows 95/98 .scr files are run through Wine. Most are 32-bit.
  if [ "${BEMO_SKIP_WINE:-0}" != "1" ]; then
    sudo dpkg --add-architecture i386 || true
    sudo apt-get update
    if ! sudo apt-get install -y wine wine64 wine32:i386; then
      echo "WARNING: Full 32-bit Wine install failed. Trying the distro Wine package only."
      sudo apt-get install -y wine || true
    fi
  fi
fi

# Ollama at the version BMO is tested with, checked against its published SHA-256
# (installer/ollama.sh). An installed Ollama of another version is kept unless BEMO_OLLAMA_REPLACE=1.
if [ "${BEMO_SKIP_OLLAMA_SETUP:-0}" != "1" ]; then
  retry_cmd 3 bash "$SCRIPT_DIR/installer/ollama.sh"

  # Prefer the official systemd service when present. If the distro/install
  # environment does not expose it, start a local server for this install run.
  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl enable --now ollama >/dev/null 2>&1 || true
  fi
  if ! curl -fsS --connect-timeout 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "Starting Ollama server..."
    nohup ollama serve >"$INSTALL_LOG_DIR/ollama-serve.log" 2>&1 &
  fi
  for _ in $(seq 1 60); do
    if curl -fsS --connect-timeout 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if ! curl -fsS --connect-timeout 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "ERROR: Ollama was installed but its local server did not become ready." >&2
    echo "Reboot or start 'ollama serve', then rerun ./install.sh. Completed install work is retained." >&2
    exit 1
  fi
fi

# Python environment/package installs are restartable. pip/apt retain their own
# package caches, so rerunning does not restart the whole install.
python3 -m venv .venv
. .venv/bin/activate
# Exact versions, as tested (installer/requirements.lock, made by tools/make_lock.py). The list is
# complete, so --no-deps: pip resolves nothing and cannot drift to newer releases.
echo "Installing Python build tools..."
retry_cmd 4 python -m pip install -r "$SCRIPT_DIR/installer/build-tools.lock"
echo "Installing BMO's Python packages (exact versions)..."
retry_cmd 4 python -m pip install --no-deps -r "$SCRIPT_DIR/installer/requirements.lock"

# BMO's epoch-23 Piper voice: from a bundled copy when there is one (all-in-one archives), otherwise from
# the repository's "voice-1" release, checked against its SHA-256 (installer/voice.sh).
if [ -s "$SCRIPT_DIR/bundled_voice/en_US-bemo-best-v3.onnx" ] && \
   [ -s "$SCRIPT_DIR/bundled_voice/en_US-bemo-best-v3.onnx.json" ]; then
  echo "Installing bundled Bemo Piper epoch-23 voice..."
  python piper_deploy.py install "$SCRIPT_DIR/bundled_voice/en_US-bemo-best-v3.onnx"
else
  retry_cmd 3 bash "$SCRIPT_DIR/installer/voice.sh" python
fi

# "Hey BMO" detector (installer plan I7): the public one shipped in assets/wakeword, only where none is
# installed yet (a detector trained for this PC is never replaced). Each person's own voice is learnt at
# first boot.
WAKEWORD_DIR="$BEMO_DATA_DIR/models/wakeword"
mkdir -p "$WAKEWORD_DIR"
for f in hey_bmo.onnx melspectrogram.onnx embedding_model.onnx; do
  cp -n "$SCRIPT_DIR/assets/wakeword/$f" "$WAKEWORD_DIR/$f"
done

echo "Verifying core imports..."
python - <<'PYDEPS'
import numpy, cadquery, openpyxl, scipy, trimesh, piper, faster_whisper, openwakeword
print(f"NumPy {numpy.__version__}; CadQuery {cadquery.__version__}; OpenPyXL {openpyxl.__version__}")
PYDEPS
echo "Checking installed Python dependency consistency..."
python "$SCRIPT_DIR/installer/check_packages.py"

# Fetch the Windows 95/98 screensaver collection. The destination is persistent;
# once complete, later installer runs skip it. A failed/interrupted fetch can be
# rerun independently without touching the rest of Bemo.
if [ "${BEMO_SKIP_LEGACY_SCREENSAVER_FETCH:-0}" != "1" ]; then
  echo "Fetching legacy Windows 95/98 screensaver pack..."
  retry_cmd 4 python fetch_legacy_screensavers.py fetch || {
    echo "WARNING: Screensaver pack did not finish. Re-run ./install.sh or:" >&2
    echo "  .venv/bin/python fetch_legacy_screensavers.py fetch" >&2
  }
fi

# Prepare the GameCube library and fetch ES-DE. systems/games.py preserves the
# .download partial and resumes with HTTP Range when the server supports it.
echo "Preparing Bemo GameCube frontend..."
if [ "${BEMO_SKIP_GAME_SETUP:-0}" != "1" ]; then
  if ! retry_cmd 4 python game_launcher.py setup --fetch-esde; then
    echo "WARNING: ES-DE download/setup did not complete. Creating folders/config only."
    python game_launcher.py setup || true
  fi
fi

# Pre-download the speech assets so the first real Bemo launch does not need to
# fetch Whisper or the voice-activity model. Their upstream caches survive/reuse partial work.
if [ "${BEMO_SKIP_SPEECH_MODEL_PREFETCH:-0}" != "1" ]; then
  echo "Prefetching local Whisper speech models..."
  retry_cmd 4 python prefetch_runtime_models.py prefetch
fi

# Pull the exact qualified model roster and create the stable local aliases used
# by config.py. This is intentionally last because it is by far the largest
# download (~98 GB total). The exact Q4_K_M GGUF pulls are restartable: rerunning
# install.sh skips completed aliases and retries the interrupted model pull.
if [ "${BEMO_SKIP_MODEL_DOWNLOADS:-0}" != "1" ]; then
  echo "Installing Bemo's exact Ollama model roster (~98 GB total)..."
  python model_installer.py install
fi

# Empirically choose the efficient Ollama thread count after both the speech stack
# and main 30B model are available. The result is hardware-fingerprinted and
# reused on future installs/boots; if the CPU changes, ensure runs it again.
if [ "${BEMO_SKIP_HARDWARE_AUTOTUNE:-0}" != "1" ]; then
  echo "Calibrating efficient CPU thread count for this hardware..."
  python hardware_autotune.py ensure || \
    echo "WARNING: Hardware efficiency benchmark did not complete. Bemo will use the safe topology fallback." >&2
fi

# Basic offline integrity checks after installation. Live model/vision validation
# can be run separately on the target machine after all pulls are complete.
echo "Running Bemo self-test..."
python self_test.py

# Enable the tiny Supervisor + Hey-Bemo standby listener for future Xfce logins.
# XDG autostart first imports the live X11/audio session environment, then starts
# the restartable systemd user service. Full Bemo still waits for the wake phrase.
if [ "${BEMO_SKIP_AUTOSTART_ENABLE:-0}" != "1" ]; then
  echo "Enabling Bemo standby Supervisor for Xfce desktop login..."
  python autostart_cli.py enable || \
    echo "WARNING: Could not enable the user-systemd Bemo service automatically." >&2
fi

# BMO's spoken first boot (microphone, voice, screen, internet, downloads) on a brand-new install only:
# no conversations yet. Rerunning the installer on a BMO that is already in use never reopens it.
if [ ! -e "$BEMO_DATA_DIR/install/first-boot.json" ] && [ ! -e "$BEMO_DATA_DIR/memory/conversation.sqlite3" ]; then
  python first_boot_cli.py start
fi

echo
echo "Bemo installation complete."
echo "Exact model status:        .venv/bin/python model_installer.py status"
echo "Exact model roster:        .venv/bin/python model_installer.py list"
echo "Speech model status:       .venv/bin/python prefetch_runtime_models.py status"
echo "Screensaver status:        .venv/bin/python fetch_legacy_screensavers.py status"
echo "Windows saver runtime:     .venv/bin/python windows_screensaver_cli.py status"
echo "Hardware auto-tuning:      .venv/bin/python print_hardware.py"
echo "Hardware benchmark:        .venv/bin/python hardware_autotune.py status"
echo "Rerun hardware benchmark:  .venv/bin/python hardware_autotune.py run --force"
echo "Engineering tools:         .venv/bin/python engineering_tools_cli.py smoke"
echo "Grounded tools:            .venv/bin/python grounded_tools_cli.py smoke"
echo "Game subsystem health:     .venv/bin/python game_launcher.py doctor"
echo "GameCube folder:           ~/.bemo/games/roms/gamecube"
echo "Dolphin user data:         ~/.bemo/games/dolphin-user"
echo "Voice wake/start gate:      'Hey Bemo'"
echo "Program reload:             'Hey Bemo, reboot yourself' or 'Hey Bemo, reload yourself'"
echo "Program standby shutdown:   'Hey Bemo, shutdown yourself'"
echo "Conversation sleep:         'go to sleep'"
echo "Offline soak test:         .venv/bin/python soak_test.py --cycles 20"
echo "Live model/vision check:   .venv/bin/python live_validation.py --image /path/to/test.png"
echo "Standby Supervisor:        ./start.sh   (full Bemo starts on 'Hey Bemo')"
echo "Mint appliance status:     .venv/bin/python mint_appliance.py status"
echo "Power-on appliance mode:   LightDM autologin configured; BIOS/UEFI access is unaffected"
echo "What BMO can do here:      .venv/bin/python print_capabilities.py   (or ask: 'Hey Bemo, what can you do on this computer?')"
echo "GUI control check:         bash tools/gui_vision/setup_gui_vision.sh   (guide: docs/GUI_CONTROL.md)"
echo
# GUI control: accessibility works everywhere; the optional vision fallback
# (MAI-UI-2B, ~2.6 GB) is built by hand on PCs with >= 28 GB. Report only.
bash tools/gui_vision/setup_gui_vision.sh >/dev/null 2>&1 \
  && echo "GUI control: ready, including the vision fallback." \
  || echo "GUI control: run 'bash tools/gui_vision/setup_gui_vision.sh' to see what is missing (optional)."
echo
echo "If installation is interrupted or the PC freezes, run ./install.sh again."
echo "Completed steps are reused; exact-model pulls and the ES-DE partial are resumed/rechecked rather than reinstalling Bemo from zero."
