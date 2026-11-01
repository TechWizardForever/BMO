"""'Hey BMO, eject the USB' (owner 2026-11-01): unmounts the stick and powers it off, then checks it really let go, so
it's safe to pull out. Live, the brain said "USB ejected." with nothing behind it."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_STICK = r"(?:usb(?: (?:stick|drive|key))?|thumb ?drive|flash ?drive|memory stick|stick|external drive)"
_ASK = re.compile(r"^(?:eject|unmount|dismount|safely (?:eject|remove|unmount)|remove|unplug|disconnect|take out)"
                  r"(?: (?:the|my|this|that|your))? " + _STICK + r"(?: safely)?$"
                  r"|^eject$"
                  r"|^(?:make|get) (?:the |my )?" + _STICK + r" (?:safe|ready) to (?:remove|unplug|pull out)$")


def is_request(text: str) -> bool:
    """'eject the USB' / 'unmount my USB stick' / 'safely remove the USB drive' / 'eject'."""
    from systems.media_phrases import clean
    return bool(_ASK.match(clean(text)))


def _mounted() -> dict[str, str]:
    """Mount point -> block device."""
    import psutil
    return {str(part.mountpoint): str(part.device) for part in psutil.disk_partitions(all=False)}


def _whole_disk(device: str, run) -> str:
    """/dev/sdb1 -> /dev/sdb: powering off works on the whole stick."""
    done = run(["lsblk", "-no", "PKNAME", device], capture_output=True, text=True, timeout=10)
    parent = (done.stdout or "").strip().splitlines()
    return f"/dev/{parent[0].strip()}" if done.returncode == 0 and parent and parent[0].strip() else device


def eject(root: str | Path, *, run=subprocess.run, mounted=_mounted) -> dict:
    """{'ok': True} once `root` is really unmounted, else {'ok': False, 'why': 'busy' | 'failed'}. Never asks for a
    password: udisks either allows it for the person at the computer or it fails."""
    root = str(root)
    device = mounted().get(root)
    if not device:
        return {"ok": False, "why": "failed"}
    try:
        done = run(["udisksctl", "unmount", "--no-user-interaction", "-b", device],
                   capture_output=True, text=True, timeout=120)  # writes still in memory go to the stick first
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "why": "failed"}
    if done.returncode != 0:
        busy = "busy" in f"{done.stderr} {done.stdout}".lower()
        return {"ok": False, "why": "busy" if busy else "failed"}
    if root in mounted():
        return {"ok": False, "why": "failed"}
    try:  # the light goes out on most sticks; unmounted is already safe, so a failure here changes nothing
        run(["udisksctl", "power-off", "--no-user-interaction", "-b", _whole_disk(device, run)],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
    return {"ok": True}
