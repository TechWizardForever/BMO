#!/usr/bin/env bash
# Starts the Bemo systemd user service *from inside the Xfce desktop session*.
# This is intentionally an XDG-autostart bridge: it imports the real X11/audio
# session environment before systemd launches the long-lived Supervisor.
set -u
cd "$(dirname "$0")"

if ! command -v systemctl >/dev/null 2>&1; then
  exec ./start.sh
fi

VARS=()
for var in DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR XDG_SESSION_TYPE XDG_CURRENT_DESKTOP PULSE_SERVER WAYLAND_DISPLAY; do
  if [ -n "${!var:-}" ]; then
    VARS+=("$var")
  fi
done
if [ "${#VARS[@]}" -gt 0 ]; then
  systemctl --user import-environment "${VARS[@]}" >/dev/null 2>&1 || true
  if command -v dbus-update-activation-environment >/dev/null 2>&1; then
    dbus-update-activation-environment --systemd "${VARS[@]}" >/dev/null 2>&1 || true
  fi
fi

# Bemo owns CRT idle protection. Do not let the X server blank/power down the
# display underneath the Windows-95 saver/fullscreen UI.
if [ -n "${DISPLAY:-}" ] && command -v xset >/dev/null 2>&1; then
  xset s off >/dev/null 2>&1 || true
  xset s noblank >/dev/null 2>&1 || true
  xset -dpms >/dev/null 2>&1 || true
fi

systemctl --user daemon-reload >/dev/null 2>&1 || true
systemctl --user reset-failed bemo.service >/dev/null 2>&1 || true
exec systemctl --user start bemo.service
