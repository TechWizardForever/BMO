#!/usr/bin/python3 -I
"""BMO disk image: Linux's own updates for the install screen (2026-10-25). Standard library only.

Runs as root through bemo-system-update.service. The disk image copies this file to /usr/local/lib/bemo, owned
by root, so nothing the person's account (or BMO's own updates of /opt/bemo) can change runs as root. A polkit
rule lets the person at the computer start that one service, and only start it, without a password
(tools/image/bemo-system-update.rules). It does nothing but `apt-get update` and `apt-get upgrade` (new
packages a newer kernel needs come in; nothing is ever removed), and writes its progress for BMO's install
screen to STATUS. The full apt output goes to LOG.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

STATUS = Path("/var/lib/bemo/system-update.json")
LOG = Path("/var/log/bemo-system-update.log")
APT_OPTIONS = ["-y", "-q", "-o", "DPkg::Lock::Timeout=900",  # Mint's Update Manager may hold the lock a while
               "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"]
# (apt arguments, start and share of the whole bar)
STAGES = ((["update"], 0.0, 0.10), (["--with-new-pkgs", "upgrade"], 0.10, 0.90))


def progress_from(line: str):
    """One APT::Status-Fd line -> (phase, percent of that stage, what it's on), or None.

    'dlstatus:3:42.5:Retrieving file 3 of 7' while downloading, 'pmstatus:libc6:61.2:Installing libc6'
    while installing."""
    kind, _, rest = str(line).strip().partition(":")
    parts = rest.split(":", 2)
    if kind not in ("dlstatus", "pmstatus") or len(parts) < 2:
        return None
    try:
        percent = max(0.0, min(100.0, float(parts[1])))
    except ValueError:
        return None
    return ("downloading" if kind == "dlstatus" else "installing"), percent, (parts[2] if len(parts) > 2 else "")


def reboot_needed(boot: Path = Path("/boot"), release: str | None = None,
                  flag: Path = Path("/run/reboot-required")) -> bool:
    """A newer kernel is installed than the one running (or Ubuntu's own flag says so)."""
    if flag.exists():
        return True
    release = release or os.uname().release
    kernels = sorted(boot.glob("vmlinuz-*"), key=lambda p: p.stat().st_mtime)
    return bool(kernels) and kernels[-1].name != f"vmlinuz-{release}"


def write_status(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)  # BMO reads it as the person's account
    os.replace(tmp, path)


def run_stage(args, start, share, status, write, log, popen=subprocess.Popen) -> tuple[int, str]:
    read_fd, write_fd = os.pipe()
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive", LC_ALL="C")
    with tempfile.TemporaryFile("w+", encoding="utf-8") as errors:  # a file, so a chatty apt can't block
        proc = popen(["apt-get", *APT_OPTIONS, "-o", f"APT::Status-Fd={write_fd}", *args], pass_fds=(write_fd,),
                     stdout=log, stderr=errors, text=True, env=env)
        os.close(write_fd)
        with os.fdopen(read_fd, encoding="utf-8", errors="replace") as lines:
            for line in lines:
                found = progress_from(line)
                if found:
                    phase, percent, detail = found
                    status.update(phase=phase, percent=round(100 * (start + share * percent / 100), 1),
                                  detail=detail[:80])
                    write(status)
        code = proc.wait()
        errors.seek(0)
        text = errors.read()
    log.write(text)
    last = [line for line in text.splitlines() if line.strip()]
    return code, (last[-1] if last else "")


def main(status_path: Path = STATUS, log_path: Path = LOG, popen=subprocess.Popen, reboot=reboot_needed) -> int:
    try:
        previous = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    status = {"run": int(previous.get("run", 0)) + 1, "state": "running", "phase": "starting", "percent": 0.0,
              "detail": ""}
    write = lambda data: write_status(status_path, data)
    write(status)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log:
        for args, start, share in STAGES:
            code, error = run_stage(args, start, share, status, write, log, popen)
            if code != 0:
                status.update(state="failed", ok=False, error=error or f"apt-get {' '.join(args)} failed ({code})")
                write(status)
                return 1
    status.update(state="done", ok=True, phase="done", percent=100.0, detail="", reboot_needed=bool(reboot()))
    write(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
