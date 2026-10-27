#!/usr/bin/env bash
# Build BMO's disk image (installer plan I8): the official Linux Mint 22.3 Xfce installer with BMO added.
#
#   sudo bash tools/image/build_image.sh            build (resumes after a stop: finished steps are skipped)
#   bash tools/image/build_image.sh --check         check the inputs only; no password, changes nothing
#
# Inputs: the Mint ISO (checked against Mint's SHA-256), BMO's code from this repository's committed `main`
# (never the working folder or ~/.bemo), and, when present and identical to the pinned build, the
# click-by-sight files. Everything else is downloaded inside the image with the same checks as install.sh.
# Work folder and log: ~/BMO-image-build (owned by you). Needs about 40 GB free.
set -euo pipefail

MINT_SHA256="45a835b5dddaf40e84d776549e0b19b3fbd49673b6cc6434ebddbfcd217df776"  # linuxmint-22.3-xfce-64bit.iso
PUBLIC_REPO="https://github.com/TechWizardForever/BMO.git"
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
OWNER="${SUDO_USER:-$(id -un)}"
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
BASE="${BEMO_IMAGE_DIR:-$OWNER_HOME/BMO-image-build}"
ISO="${BEMO_MINT_ISO:-$BASE/iso/linuxmint-22.3-xfce-64bit.iso}"
GUI_VISION="${BEMO_GUI_VISION_DIR:-$OWNER_HOME/.bemo/models/gui}"
WORK="$BASE/work"
ROOTFS="$WORK/rootfs"
TREE="$WORK/iso-tree"
OUT_DIR="$BASE/out"
LOG="$BASE/build.log"

say() { echo "[$(date +%H:%M:%S)] $*"; }
fail() { say "STOPPED: $*"; exit 1; }
done_marker() { echo "$WORK/.done-$1"; }
stage_done() { [ -f "$(done_marker "$1")" ]; }
mark_done() { touch "$(done_marker "$1")"; }

check_inputs() {
  local ok=1
  for tool in xorriso unsquashfs mksquashfs git sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || { say "MISSING tool: $tool"; ok=0; }
  done
  if [ ! -f "$ISO" ]; then
    say "MISSING the Mint ISO: $ISO"; ok=0
  elif [ ! -f "$WORK/.iso-checked" ] || [ "$ISO" -nt "$WORK/.iso-checked" ]; then
    say "Checking the Mint ISO (SHA-256)..."
    if [ "$(sha256sum "$ISO" | cut -d' ' -f1)" = "$MINT_SHA256" ]; then
      mkdir -p "$WORK" && touch "$WORK/.iso-checked"
    else
      say "The Mint ISO does not match Mint's published SHA-256."; ok=0
    fi
  fi
  git -C "$REPO" rev-parse --verify -q refs/heads/main >/dev/null || { say "No main branch in $REPO"; ok=0; }
  local free_gb
  free_gb=$(( $(df --output=avail -k "$BASE" | tail -1) / 1024 / 1024 ))
  [ "$free_gb" -ge 40 ] || { say "Only $free_gb GB free in $BASE; the build needs about 40."; ok=0; }
  gui_vision_ok && say "Click-by-sight: will be built in (identical to the pinned build)." \
    || say "Click-by-sight: not built in (files missing or different); BMO can add it later."
  [ "$ok" = 1 ] && say "Inputs OK. BMO code: main at $(git -C "$REPO" rev-parse --short refs/heads/main)."
  [ "$ok" = 1 ]
}

gui_vision_ok() {  # the three pinned files from tools/gui_vision/setup_gui_vision.sh, byte for byte
  local name sha rest
  [ -d "$GUI_VISION" ] || return 1
  while read -r _ name sha rest; do
    [ -f "$GUI_VISION/$name" ] || return 1
    [ "$(sha256sum "$GUI_VISION/$name" | cut -d' ' -f1)" = "$sha" ] || return 1
  done < <(grep -E '^check_file ' "$REPO/tools/gui_vision/setup_gui_vision.sh")
}

unmount_all() {
  for m in dev/pts dev proc sys run; do
    mountpoint -q "$ROOTFS/$m" 2>/dev/null && umount -lf "$ROOTFS/$m" || true
  done
}

# The build borrows the host's internet settings and must leave the image exactly as Mint ships it:
# its resolv.conf link, no services started, and no machine ID (each installed PC makes its own).
enter_image() {
  mount --bind /dev "$ROOTFS/dev"
  mount --bind /dev/pts "$ROOTFS/dev/pts"
  mount -t proc proc "$ROOTFS/proc"
  mount -t sysfs sys "$ROOTFS/sys"
  mount -t tmpfs tmpfs "$ROOTFS/run"
  if [ ! -e "$WORK/saved/state" ]; then
    rm -rf "$WORK/saved" && mkdir -p "$WORK/saved"
    cp -a "$ROOTFS/etc/resolv.conf" "$WORK/saved/resolv.conf"
    for f in etc/machine-id var/lib/dbus/machine-id; do
      if [ -e "$ROOTFS/$f" ] || [ -L "$ROOTFS/$f" ]; then mkdir -p "$WORK/saved/$(dirname "$f")"; cp -a "$ROOTFS/$f" "$WORK/saved/$f"; fi
    done
    touch "$WORK/saved/state"
  fi
  rm -f "$ROOTFS/etc/resolv.conf"
  cat /etc/resolv.conf > "$ROOTFS/etc/resolv.conf"
  printf '#!/bin/sh\nexit 101\n' > "$ROOTFS/usr/sbin/policy-rc.d"
  chmod 755 "$ROOTFS/usr/sbin/policy-rc.d"
}

leave_image() {
  unmount_all
  [ -e "$WORK/saved/state" ] || return 0
  rm -f "$ROOTFS/usr/sbin/policy-rc.d" "$ROOTFS/etc/resolv.conf"
  cp -a "$WORK/saved/resolv.conf" "$ROOTFS/etc/resolv.conf"
  for f in etc/machine-id var/lib/dbus/machine-id; do
    rm -f "$ROOTFS/$f"
    if [ -e "$WORK/saved/$f" ] || [ -L "$WORK/saved/$f" ]; then cp -a "$WORK/saved/$f" "$ROOTFS/$f"; fi
  done
  rm -rf "$WORK/saved"
}

if [ "${1:-}" = "--check" ]; then
  mkdir -p "$BASE"
  check_inputs
  exit $?
fi
[ "$(id -u)" -eq 0 ] || fail "run it as: sudo bash tools/image/build_image.sh"
[ "$OWNER" != root ] || fail "run it with sudo from your own account, not as root"

mkdir -p "$BASE" "$WORK" "$OUT_DIR"
chown "$OWNER": "$BASE" "$OUT_DIR"
exec > >(tee -a "$LOG") 2>&1
chown "$OWNER": "$LOG"
trap 'unmount_all; chown -R "$OWNER": "$OUT_DIR" "$LOG" 2>/dev/null || true' EXIT
say "BMO disk image build started."
check_inputs || fail "fix what is listed above, then run it again."

if ! stage_done unpack; then
  say "1/6 Unpacking the Mint ISO..."
  rm -rf "$TREE" "$ROOTFS"
  xorriso -osirrox on -indev "$ISO" -extract / "$TREE" >/dev/null
  chmod -R u+w "$TREE"
  [ -f "$TREE/casper/filesystem.squashfs" ] || fail "no casper/filesystem.squashfs in the ISO"
  unsquashfs -no-progress -f -d "$ROOTFS" "$TREE/casper/filesystem.squashfs"
  mark_done unpack
fi

# BMO's code follows committed main: when main has moved on since the last build, the code is refreshed in
# place (the Python environment built inside the image stays) and only the packing steps run again.
MAIN_COMMIT="$(git -C "$REPO" rev-parse refs/heads/main)"
if ! stage_done code || [ "$MAIN_COMMIT" != "$(cat "$WORK/bemo-commit" 2>/dev/null)" ]; then
  say "2/6 Adding BMO's code (committed main only)..."
  rm -rf "$WORK/bemo-src"
  # Cloned as you, so git's ownership check is happy; then copied in as root.
  runuser -u "$OWNER" -- git clone -q --branch main --single-branch "file://$REPO" "$WORK/bemo-src"
  git -C "$WORK/bemo-src" remote set-url origin "$PUBLIC_REPO"
  mkdir -p "$ROOTFS/opt/bemo"
  rsync -a --delete --exclude /.venv "$WORK/bemo-src/" "$ROOTFS/opt/bemo/"
  chown -R root:root "$ROOTFS/opt/bemo"
  git -C "$WORK/bemo-src" log -1 --format='BMO code: %h %s' | tee "$WORK/bemo-commit.txt"
  echo "$MAIN_COMMIT" > "$WORK/bemo-commit"
  rm -f "$(done_marker squash)" "$(done_marker iso)"
  mark_done code
fi

if ! stage_done inside; then
  say "3/6 Installing BMO inside the image (Linux packages, Ollama, Python, voice; the longest step)..."
  trap 'leave_image; chown -R "$OWNER": "$OUT_DIR" "$LOG" 2>/dev/null || true' EXIT
  enter_image
  status=0
  chroot "$ROOTFS" /bin/bash /opt/bemo/tools/image/in_chroot.sh || status=$?
  leave_image
  [ "$status" = 0 ] || fail "a step inside the image failed (see above). Running the build again retries it."
  if gui_vision_ok; then
    say "Adding click-by-sight (checked against the pinned build)..."
    mkdir -p "$ROOTFS/opt/bemo-seed/.bemo/models"
    rm -rf "$ROOTFS/opt/bemo-seed/.bemo/models/gui"
    cp -a "$GUI_VISION" "$ROOTFS/opt/bemo-seed/.bemo/models/gui"
    chown -R root:root "$ROOTFS/opt/bemo-seed/.bemo/models/gui"
  fi
  mark_done inside
fi

# The install screen's Linux updates step (2026-10-25): root's own copy of the helper, a service that runs only
# it, and a polkit rule that lets the person at the computer start that one service. Copied on every build, not
# in the long in-image step above, so a code refresh picks up a change to them too (found 2026-10-27).
system_file() {  # mode, file in BMO's code, where it goes in the image
  if ! cmp -s "$ROOTFS/opt/bemo/$2" "$ROOTFS$3"; then
    install -D -m "$1" -o root -g root "$ROOTFS/opt/bemo/$2" "$ROOTFS$3"
    rm -f "$(done_marker squash)" "$(done_marker iso)"
  fi
}
system_file 755 tools/image/system_update.py /usr/local/lib/bemo/system_update.py
system_file 644 tools/image/bemo-system-update.service /etc/systemd/system/bemo-system-update.service
system_file 644 tools/image/bemo-system-update.rules /etc/polkit-1/rules.d/50-bemo-system-update.rules

# Nothing that isn't ours to share goes out: no Windows screensavers (BMO fetches them on the installed PC), no
# games, no videos (2026-10-24). Images built before then had the screensaver pack in the seed; it goes here.
for leftover in "$ROOTFS/opt/bemo-seed/.bemo/screensavers" "$ROOTFS/opt/bemo-seed/.bemo/install-cache"; do
  if [ -e "$leftover" ]; then
    rm -rf "$leftover"
    rm -f "$(done_marker squash)" "$(done_marker iso)"
  fi
done
shipped="$(find "$ROOTFS/opt" "$ROOTFS/etc/skel" "$ROOTFS/root" "$ROOTFS/home" -type f \( -iname '*.scr' \
  -o -iname '*.rvz' -o -iname '*.gcm' -o -iname '*.gcz' -o -iname '*.ciso' -o -iname '*.wia' -o -iname '*.wbfs' \
  -o -iname '*.iso' -o -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' -o -iname '*.m4v' -o -iname '*.webm' \) \
  2>/dev/null | head -5)"
[ -z "$shipped" ] || fail "the image would carry files it mustn't share (screensavers, games or videos): $shipped"

if ! stage_done squash; then
  say "4/6 Packing the system (squashfs; takes a while)..."
  chroot "$ROOTFS" dpkg-query -W --showformat='${Package} ${Version}\n' > "$TREE/casper/filesystem.manifest"
  du -sx --block-size=1 "$ROOTFS" | cut -f1 > "$TREE/casper/filesystem.size"
  # Packed the way Mint packed it (xz, 128 KB blocks in 22.3).
  info="$(unsquashfs -s "$TREE/casper/filesystem.squashfs")"
  comp="$(sed -n 's/^Compression //p' <<<"$info" | head -1)"
  block="$(sed -n 's/^Block size //p' <<<"$info" | head -1)"
  rm -f "$WORK/filesystem.squashfs"
  mksquashfs "$ROOTFS" "$WORK/filesystem.squashfs" -noappend -no-progress -comp "${comp:-xz}" -b "${block:-131072}"
  mv -f "$WORK/filesystem.squashfs" "$TREE/casper/filesystem.squashfs"
  mark_done squash
fi

VERSION="$(sed -n 's/^APP_VERSION = "supervisor-\(v[0-9.]*\).*/\1/p' "$ROOTFS/opt/bemo/config.py")"
IMAGE="$OUT_DIR/bmo-${VERSION}-mint-22.3-xfce.iso"
if ! stage_done iso; then
  say "5/6 Writing the new ISO (Mint's own boot setup is kept)..."
  (cd "$TREE" && find . -type f ! -name md5sum.txt ! -path './isolinux/*' -print0 | sort -z \
    | xargs -0 md5sum > md5sum.txt)
  rm -f "$IMAGE"
  # Level 3 lets one file pass 4 GB (the packed system can).
  xorriso -indev "$ISO" -outdev "$IMAGE" -compliance iso_9660_level=3 -boot_image any replay \
    -map "$TREE/casper/filesystem.squashfs" /casper/filesystem.squashfs \
    -map "$TREE/casper/filesystem.manifest" /casper/filesystem.manifest \
    -map "$TREE/casper/filesystem.size" /casper/filesystem.size \
    -map "$TREE/md5sum.txt" /md5sum.txt
  mark_done iso
fi

say "6/6 Checksum..."
(cd "$OUT_DIR" && sha256sum "$(basename "$IMAGE")" > "$(basename "$IMAGE").sha256")
chown -R "$OWNER": "$OUT_DIR"
say "DONE: $IMAGE ($(du -h "$IMAGE" | cut -f1)). $(cat "$WORK/bemo-commit.txt" 2>/dev/null)"
