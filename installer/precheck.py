#!/usr/bin/env python3
"""Check this computer before BMO's installer changes anything (installer plan, milestone I1).

Standard library only: it runs before BMO's Python environment exists. Prints one screen of
"OK / NOT OK, because …" and exits 1 when something blocks the install. Every input can be
replaced for tests. Overrides, each named in the output when used:
  BEMO_ALLOW_UNSUPPORTED_OS=1  an OS other than Linux Mint 22.3 Xfce is a note, not a stop
  BEMO_PRECHECK_TEST=1         too little memory or disk is a note (virtual-machine tests only)
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_RAM_GIB = 27.5   # same as systems/host.py: "28 GB" machines report ~28.2 GiB
EXTRA_DISK_GB = 25   # Python environment, Ollama, speech models, click-by-sight and room to work
PYTHON = (3, 12)     # installer/requirements.lock is for Python 3.12 (Linux Mint 22.x)
HOSTS = (("github.com", 443), ("pypi.org", 443), ("registry.ollama.ai", 443), ("huggingface.co", 443))
OK, NOTE, FAIL = "OK", "NOTE", "NOT OK"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _gib(meminfo: str) -> float:
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) / (1024 * 1024)
    return 0.0


def check_os(target: dict, machine: str, has_apt: bool, allow_unsupported: bool) -> Check:
    problems = list(target.get("problems") or [])
    if machine not in ("x86_64", "amd64"):
        problems.insert(0, f"a 64-bit Intel/AMD processor is needed (this is {machine})")
    if not has_apt:
        problems.append("apt-get was not found")
    if not problems:
        return Check("System", OK, f"Linux Mint {target.get('version_id', '')} Xfce, 64-bit")
    detail = "; ".join(problems)
    if allow_unsupported and machine in ("x86_64", "amd64") and has_apt:
        return Check("System", NOTE, detail + " (allowed by BEMO_ALLOW_UNSUPPORTED_OS=1; not tested)")
    return Check("System", FAIL, detail)


def check_python(version=sys.version_info) -> Check:
    found = f"{version[0]}.{version[1]}"
    if tuple(version[:2]) == PYTHON:
        return Check("Python", OK, found)
    return Check("Python", FAIL, f"BMO's package list is for Python {PYTHON[0]}.{PYTHON[1]}; this system has {found}")


def check_memory(meminfo: str, test_override: bool) -> Check:
    gib = _gib(meminfo)
    if gib >= MIN_RAM_GIB:
        return Check("Memory", OK, f"{gib:.1f} GiB")
    detail = f"{gib:.1f} GiB; BMO's brains need about 28 GB"
    return Check("Memory", NOTE, detail + " (test override)") if test_override else Check("Memory", FAIL, detail)


def models_still_to_download(state_path: Path) -> float:
    """GB of brains not yet downloaded, from the roster and model_installer's state."""
    from model_installer import MODELS
    try:
        done = json.loads(state_path.read_text(encoding="utf-8")).get("models", {})
    except (OSError, ValueError):
        done = {}
    return sum(spec.approx_gb for spec in MODELS if not (done.get(spec.alias) or {}).get("complete"))


def _existing(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _mount(path: Path) -> Path:
    while not os.path.ismount(path) and path != path.parent:
        path = path.parent
    return path


def check_disk(model_dir: Path, home: Path, models_gb: float, test_override: bool, *, statvfs=os.statvfs,
               stat=os.stat, mount=_mount) -> Check:
    places = {}
    for path, need in ((_existing(model_dir), models_gb), (_existing(home), EXTRA_DISK_GB)):
        device = stat(path).st_dev
        free = statvfs(path)
        entry = places.setdefault(device, {"path": mount(path), "need": 0.0,
                                           "free": free.f_bavail * free.f_frsize / 1e9})
        entry["need"] += need
    short = [p for p in places.values() if p["free"] < p["need"]]
    what = "the brains still to download" if models_gb else "the brains are already downloaded"
    detail = "; ".join(f"{p['free']:.0f} GB free on {p['path']}, needs {p['need']:.0f} GB" for p in places.values())
    if not short:
        return Check("Disk", OK, f"{detail} ({what})")
    if test_override:
        return Check("Disk", NOTE, f"{detail} (test override)")
    return Check("Disk", FAIL, detail)


def check_session(env) -> Check:
    session = str(env.get("XDG_SESSION_TYPE") or "").lower()
    if not session:
        session = "wayland" if env.get("WAYLAND_DISPLAY") else "x11" if env.get("DISPLAY") else ""
    if session == "x11":
        return Check("Desktop", OK, "X11 session")
    if session == "wayland":
        return Check("Desktop", NOTE, "Wayland session: BMO can't use the keyboard and mouse for you there yet")
    return Check("Desktop", NOTE, "no desktop session found; run the installer from the desktop to check this")


def check_microphone(which=shutil.which, run=subprocess.run) -> Check:
    if not which("arecord"):
        return Check("Microphone", NOTE, "can't check yet (alsa-utils is installed in the next step)")
    try:
        listing = run(["arecord", "-l"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        listing = ""
    cards = sum(1 for line in listing.splitlines() if line.startswith("card "))
    if cards:
        return Check("Microphone", OK, f"{cards} recording device{'s' if cards != 1 else ''} found")
    return Check("Microphone", NOTE, "none found; BMO needs one to hear you (it can be plugged in later)")


def check_cpu(cpuinfo: str, cores: int) -> Check:
    flags = next((line.split(":", 1)[1].split() for line in cpuinfo.splitlines() if line.startswith("flags")), [])
    if "avx2" not in flags:
        return Check("Processor", NOTE, f"{cores} threads, no AVX2: the brains will answer slowly")
    if cores < 8:
        return Check("Processor", NOTE, f"{cores} threads: the brains will answer slowly (8 or more recommended)")
    return Check("Processor", OK, f"{cores} threads, AVX2")


def check_internet(connect=socket.create_connection, hosts=HOSTS) -> Check:
    failed = []
    for host, port in hosts:
        try:
            connect((host, port), timeout=6).close()
        except OSError:
            failed.append(host)
    if not failed:
        return Check("Internet", OK, "reached " + ", ".join(h for h, _ in hosts))
    return Check("Internet", FAIL, "could not reach " + ", ".join(failed) + "; the install downloads from there")


def check_tools(which=shutil.which, systemd_dir="/run/systemd/system") -> Check:
    missing = [name for name in ("sudo", "curl") if not which(name)]
    if not os.path.isdir(systemd_dir):
        missing.append("systemd (for starting at login)")
    if missing:
        return Check("Tools", FAIL, "missing: " + ", ".join(missing))
    return Check("Tools", OK, "sudo, curl, systemd")


def run_checks(*, offline: bool = False, env=None) -> list[Check]:
    env = os.environ if env is None else env
    from mint_appliance import check_target
    allow = env.get("BEMO_ALLOW_UNSUPPORTED_OS") == "1"
    test = env.get("BEMO_PRECHECK_TEST") == "1"
    data = Path(env.get("BEMO_DATA_DIR") or Path.home() / ".bemo").expanduser()
    model_dir = Path(env.get("OLLAMA_MODELS") or "/usr/share/ollama/.ollama/models")
    checks = [
        check_os(check_target(allow_unsupported=True), platform.machine(), bool(shutil.which("apt-get")), allow),
        check_python(),
        check_memory(Path("/proc/meminfo").read_text(), test),
        check_disk(model_dir, Path.home(), models_still_to_download(data / "install" / "model-roster.json"), test),
        check_cpu(Path("/proc/cpuinfo").read_text(), os.cpu_count() or 1),
        check_session(env),
        check_microphone(),
        check_tools(),
    ]
    if not offline:
        checks.append(check_internet())
    return checks


def report(checks: list[Check]) -> str:
    lines = ["BMO pre-install check (nothing has been changed)"]
    lines += [f"  {c.status:<7} {c.name}: {c.detail}" for c in checks]
    blocking = [c for c in checks if c.status == FAIL]
    if blocking:
        lines.append("This PC: NOT OK, because " + "; ".join(f"{c.name.lower()}: {c.detail}" for c in blocking) + ".")
    else:
        notes = sum(1 for c in checks if c.status == NOTE)
        lines.append("This PC: OK" + (f" ({notes} note{'s' if notes != 1 else ''} above)." if notes else "."))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    parser.add_argument("--offline", action="store_true", help="skip the internet check")
    args = parser.parse_args(argv)
    checks = run_checks(offline=args.offline)
    print(json.dumps([asdict(c) for c in checks], indent=2) if args.json else report(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
