#!/usr/bin/env python3
"""Linux Mint 22.3 Xfce appliance setup/status helpers for Bemo.

The Bemo installer calls this as a normal user for target validation and through
sudo only for the small LightDM autologin config write.  It intentionally does
not touch firmware/UEFI settings; autologin begins only after Linux/LightDM.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

TARGET_ID = "linuxmint"
TARGET_VERSION = "22.3"
TARGET_CODENAME = "zena"
TARGET_DESKTOP = "xfce"
LIGHTDM_CONFIG_DIR = Path("/etc/lightdm")
LIGHTDM_BEMO_CONFIG = LIGHTDM_CONFIG_DIR / "lightdm.conf"
LIGHTDM_BEMO_BACKUP = LIGHTDM_CONFIG_DIR / "lightdm.conf.pre-bemo"


def parse_key_value_file(path: str | Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def os_info() -> dict[str, str]:
    info = parse_key_value_file("/etc/os-release")
    mint_info = parse_key_value_file("/etc/linuxmint/info")
    if mint_info:
        # /etc/os-release remains authoritative; Mint-specific fields fill gaps.
        info.setdefault("RELEASE", mint_info.get("RELEASE", ""))
        info.setdefault("CODENAME", mint_info.get("CODENAME", ""))
        info.setdefault("EDITION", mint_info.get("EDITION", ""))
    return info


def target_os_matches(info: dict[str, str] | None = None) -> bool:
    info = info or os_info()
    return info.get("ID", "").lower() == TARGET_ID and info.get("VERSION_ID", "") == TARGET_VERSION


def detect_xfce_session() -> str | None:
    preferred = Path("/usr/share/xsessions/xfce.desktop")
    if preferred.exists():
        return preferred.stem
    sessions = Path("/usr/share/xsessions")
    if sessions.is_dir():
        matches = sorted(sessions.glob("*xfce*.desktop"))
        if matches:
            return matches[0].stem
    return None


def lightdm_available() -> bool:
    return shutil.which("lightdm") is not None or Path("/usr/sbin/lightdm").exists()


def render_lightdm_autologin(username: str, *, session: str | None = None) -> str:
    username = str(username).strip()
    if not username or any(ch.isspace() for ch in username) or "\n" in username or "\r" in username:
        raise ValueError("invalid Linux username")
    lines = [
        "# Managed by Bemo for Linux Mint 22.3 Xfce appliance mode.",
        "[Seat:*]",
        "autologin-guest=false",
        f"autologin-user={username}",
        "autologin-user-timeout=0",
    ]
    if session:
        lines.append(f"autologin-session={session}")
    return "\n".join(lines) + "\n"


def update_lightdm_config_text(existing: str, username: str, *, session: str | None = None) -> str:
    """Set Bemo's autologin keys in [Seat:*] while preserving unrelated config."""
    desired = {
        "autologin-guest": "false",
        "autologin-user": str(username).strip(),
        "autologin-user-timeout": "0",
    }
    if session:
        desired["autologin-session"] = session
    # Validate the username using the same strict renderer.
    render_lightdm_autologin(desired["autologin-user"], session=session)

    lines = str(existing or "").splitlines()
    out: list[str] = []
    in_seat = False
    found_seat = False
    written: set[str] = set()

    def flush_missing() -> None:
        for key, value in desired.items():
            if key not in written:
                out.append(f"{key}={value}")
                written.add(key)

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_seat:
                flush_missing()
            in_seat = stripped.lower() == "[seat:*]"
            if in_seat:
                found_seat = True
            out.append(line)
            continue
        if in_seat and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip().lower()
            if key in desired:
                if key not in written:
                    out.append(f"{key}={desired[key]}")
                    written.add(key)
                continue
        out.append(line)

    if in_seat:
        flush_missing()
    if not found_seat:
        if out and out[-1].strip():
            out.append("")
        out.extend(render_lightdm_autologin(username, session=session).rstrip("\n").splitlines())
    return "\n".join(out).rstrip() + "\n"


def check_target(*, allow_unsupported: bool = False) -> dict:
    info = os_info()
    session = detect_xfce_session()
    result = {
        "ok": True,
        "target": "Linux Mint 22.3 Xfce 64-bit",
        "os_id": info.get("ID", ""),
        "version_id": info.get("VERSION_ID", ""),
        "codename": (info.get("VERSION_CODENAME") or info.get("CODENAME") or "").lower(),
        "edition": info.get("EDITION", ""),
        "xfce_session": session or "",
        "lightdm": lightdm_available(),
    }
    problems: list[str] = []
    if not target_os_matches(info):
        problems.append(f"expected Linux Mint {TARGET_VERSION}, found {info.get('PRETTY_NAME') or info.get('NAME') or 'unknown OS'}")
    if not session:
        problems.append("Xfce X11 session was not found under /usr/share/xsessions")
    if not lightdm_available():
        problems.append("LightDM was not found")
    if sys.maxsize <= 2**32:
        problems.append("64-bit Python/OS is required")
    result["problems"] = problems
    if problems and not allow_unsupported:
        result["ok"] = False
    return result


def _require_known_user(username: str) -> None:
    try:
        entry = pwd.getpwnam(username)
    except KeyError as exc:
        raise SystemExit(f"ERROR: Linux user {username!r} does not exist") from exc
    if entry.pw_uid == 0:
        raise SystemExit("ERROR: Bemo appliance autologin must target a normal desktop user, not root")


def configure_autologin(username: str) -> dict:
    if os.geteuid() != 0:
        raise SystemExit("ERROR: configure-autologin must be run with sudo/root (install.sh does this automatically)")
    _require_known_user(username)
    if not lightdm_available():
        raise SystemExit("ERROR: LightDM is not installed; use Linux Mint 22.3 Xfce")
    session = detect_xfce_session()
    if not session:
        raise SystemExit("ERROR: Xfce X11 session is not installed")
    LIGHTDM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        existing = LIGHTDM_BEMO_CONFIG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        existing = ""
    if existing and not LIGHTDM_BEMO_BACKUP.exists():
        LIGHTDM_BEMO_BACKUP.write_text(existing, encoding="utf-8")
        os.chmod(LIGHTDM_BEMO_BACKUP, 0o644)
    text = update_lightdm_config_text(existing, username, session=session)
    tmp = LIGHTDM_CONFIG_DIR / ".lightdm.conf.bemo.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, LIGHTDM_BEMO_CONFIG)
    return {
        "ok": True,
        "user": username,
        "session": session,
        "path": str(LIGHTDM_BEMO_CONFIG),
    }


def status() -> dict:
    target = check_target(allow_unsupported=True)
    configured = False
    configured_user = ""
    configured_session = ""
    try:
        text = LIGHTDM_BEMO_CONFIG.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("autologin-user="):
                configured_user = line.split("=", 1)[1].strip()
            elif line.startswith("autologin-session="):
                configured_session = line.split("=", 1)[1].strip()
        configured = bool(configured_user)
    except OSError:
        pass
    active = "unknown"
    if shutil.which("systemctl"):
        proc = subprocess.run(
            ["systemctl", "is-enabled", "lightdm.service"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        active = (proc.stdout or proc.stderr).strip() or "unknown"
    return {
        **target,
        "autologin_configured": configured,
        "autologin_user": configured_user,
        "autologin_session": configured_session,
        "autologin_config": str(LIGHTDM_BEMO_CONFIG),
        "lightdm_service_enabled": active,
        "current_desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
        "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bemo Linux Mint 22.3 Xfce appliance setup")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-target")
    check.add_argument("--allow-unsupported", action="store_true")
    cfg = sub.add_parser("configure-autologin")
    cfg.add_argument("--user", required=True)
    sub.add_parser("status")
    args = parser.parse_args()

    if args.command == "check-target":
        result = check_target(allow_unsupported=bool(args.allow_unsupported))
    elif args.command == "configure-autologin":
        result = configure_autologin(args.user)
    else:
        result = status()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", False) else 2


if __name__ == "__main__":
    raise SystemExit(main())
