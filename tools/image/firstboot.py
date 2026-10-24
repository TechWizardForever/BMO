#!/usr/bin/env python3
"""BMO disk image, first start of the installed PC (installer plan I8): hand BMO to the new account.

Runs once as root from bemo-firstboot.service, before the login screen, never in the live session:
- the account made in Mint's installer becomes the owner of /opt/bemo, so BMO updates need no password
  (the brains still can't change it: it is a guarded path);
- the speech files built into the image (/opt/bemo-seed) move into that account's home;
- the account may use Ollama, logs in automatically and starts BMO's standby listener at login.
Then BMO's own spoken first boot (systems/first_boot.py) takes over. Standard library only.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

APP = Path("/opt/bemo")
SEED = Path("/opt/bemo-seed")
MARKER = Path("/var/lib/bemo/firstboot-done")
NO_LOGIN_SHELLS = ("nologin", "false", "sync")


def owner_account(passwd_text: str) -> tuple[str, int, int, Path] | None:
    """The person's account: the first ordinary user (uid 1000-59999 with a login shell and a home)."""
    for line in passwd_text.splitlines():
        parts = line.split(":")
        if len(parts) < 7:
            continue
        name, uid, gid, home, shell = parts[0], int(parts[2] or -1), int(parts[3] or -1), parts[5], parts[6]
        if 1000 <= uid < 60000 and home.startswith("/home/") and not shell.endswith(NO_LOGIN_SHELLS):
            return name, uid, gid, Path(home)
    return None


def chown_tree(path: Path, uid: int, gid: int) -> None:
    os.lchown(path, uid, gid)
    for folder, dirs, files in os.walk(path):
        for name in dirs + files:
            os.lchown(os.path.join(folder, name), uid, gid)


def move_seed(seed: Path, home: Path, uid: int, gid: int) -> list[str]:
    """Seed files the account doesn't have yet; never replaces one. Returns what was added (top level)."""
    added = []
    if not seed.is_dir():
        return added
    for item in sorted(seed.iterdir()):
        target = home / item.name
        if not target.exists():
            shutil.copytree(item, target, symlinks=True) if item.is_dir() else shutil.copy2(item, target)
            added.append(item.name)
        elif item.is_dir() and target.is_dir():
            for sub in sorted(item.rglob("*")):
                dest = target / sub.relative_to(item)
                if not dest.exists() and not dest.is_symlink():
                    if sub.is_dir() and not sub.is_symlink():
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(sub, dest, follow_symlinks=False)
        chown_tree(target, uid, gid)
    shutil.rmtree(seed)
    return added


def main(run=subprocess.run, passwd=Path("/etc/passwd"), app=APP, seed=SEED, marker=MARKER) -> int:
    account = owner_account(passwd.read_text(encoding="utf-8"))
    if account is None:
        print("BMO first boot: no ordinary user account yet; will try again next start.", file=sys.stderr)
        return 1
    name, uid, gid, home = account
    chown_tree(app, uid, gid)
    added = move_seed(seed, home, uid, gid)
    run(["usermod", "-a", "-G", "ollama", name], check=False)
    run(["python3", str(app / "mint_appliance.py"), "configure-autologin", "--user", name], check=False)
    as_owner = ["runuser", "-u", name, "--", "env", f"HOME={home}", str(app / ".venv" / "bin" / "python")]
    # Autostart files only; there is no user session yet, so its "daemon-reload" step fails harmlessly.
    run(as_owner + [str(app / "autostart_cli.py"), "install"], check=False, cwd=str(app))
    # BMO's spoken first boot (microphone, voice, screen, internet, then the brain downloads) runs while this
    # file is open; nothing else opens it on a PC installed from the image.
    if not (home / ".bemo" / "install" / "first-boot.json").exists():
        run(as_owner + [str(app / "first_boot_cli.py"), "start"], check=False, cwd=str(app))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{name}\n", encoding="utf-8")
    print(f"BMO first boot: BMO now belongs to {name}; added {', '.join(added) or 'nothing new'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
