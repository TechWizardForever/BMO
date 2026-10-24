"""BMO's updates from the public Git repository.

Releases are Git tags ("v1.29.0") signed with the owner's SSH release key. BMO installs only a tag whose
signature matches a key in installer/allowed_signers *of the version he is running*, so a taken-over
repository account can't put code on people's computers. Nothing is automatic: "Hey BMO, check for
updates" says what is new, and installing needs "Hey BMO, yes".

Installing, in BMO's own folder (the previous version is simply the previous commit, so going back is one
checkout and the start-at-login service never moves):
  1. refuse if the folder has changes that aren't committed (a developer's copy, like the owner's);
  2. a small backup of BMO's data (~/.bemo/update-backups, the last three are kept);
  3. check out the tag; install the package list again only if it changed;
  4. run the self-test; if it fails, go back at once;
  5. restart BMO. If the new version crashes before it is ready, or isn't ready within 15 minutes, the
     Supervisor goes back to the previous version and says so.
State: ~/.bemo/install/update.json.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

VERIFY_SECONDS = 15 * 60
KEEP_BACKUPS = 3
BACKUP_SKIP = {"models", "wine-screensavers", "screensavers", "games", "install-cache", "runtime", "logs",
               "update-backups", "source-upgrade-backups"}
VERIFYING, OK, ROLLED_BACK, FAILED = "verifying", "ok", "rolled_back", "failed"


def version_of(text: str) -> tuple[int, ...] | None:
    found = re.search(r"v?(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return tuple(int(x) for x in found.groups()) if found else None


def spoken_version(version: tuple[int, ...]) -> str:
    return ".".join(str(x) for x in version)


@dataclass(frozen=True)
class Update:
    tag: str
    version: tuple[int, ...]
    subject: str


@dataclass(frozen=True)
class Check:
    status: str          # available | current | local_changes | no_remote | offline | no_release_key | unsigned
    update: Update | None = None
    detail: str = ""


class Updater:
    def __init__(self, app_root: Path, data_root: Path, *, remote: str = "origin", run=subprocess.run,
                 python: str = sys.executable, clock=time.time):
        self.app, self.data, self.remote = Path(app_root), Path(data_root), remote
        self._run, self.python, self.clock = run, python, clock
        self.state_path = self.data / "install" / "update.json"

    # --- git ---
    def git(self, *args: str, timeout: float = 120):
        return self._run(["git", "-C", str(self.app), *args], capture_output=True, text=True, timeout=timeout,
                         check=False, env=dict(os.environ, LC_ALL="C", GIT_TERMINAL_PROMPT="0"))

    def current_version(self) -> tuple[int, ...] | None:
        try:
            text = (self.app / "config.py").read_text(encoding="utf-8")
        except OSError:
            return None
        found = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
        return version_of(found.group(1)) if found else None

    def verified(self, tag: str) -> bool:
        signers = self.app / "installer" / "allowed_signers"
        done = self.git("-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={signers}", "verify-tag", tag)
        return done.returncode == 0

    # --- checking ---
    def check(self) -> Check:
        if self.git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            return Check("local_changes", detail="BMO's folder has changes that aren't committed")
        if self.git("remote", "get-url", self.remote).returncode != 0:
            return Check("no_remote")
        signers = self.app / "installer" / "allowed_signers"
        if not signers.is_file() or not any(l.strip() and not l.startswith("#") for l in signers.read_text().splitlines()):
            return Check("no_release_key")
        fetched = self.git("fetch", "--quiet", "--tags", "--force", self.remote, timeout=300)
        if fetched.returncode != 0:
            return Check("offline", detail=(fetched.stderr or "").strip()[:200])
        current = self.current_version() or (0, 0, 0)
        tags = self.git("tag", "--list", "v*", "--sort=-v:refname").stdout.split()
        newer = [t for t in tags if (version_of(t) or (0,)) > current]
        if not newer:
            return Check("current")
        for tag in newer:  # newest signed one
            if self.verified(tag):
                subject = self.git("tag", "--list", tag, "--format=%(contents:subject)").stdout.strip()
                return Check("available", Update(tag, version_of(tag), subject))
        return Check("unsigned", detail=newer[0])

    # --- saved state ---
    def state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    # --- installing ---
    def backup(self, label: str) -> Path:
        """BMO's data without the big downloadable parts; the conversation database copied safely."""
        root = self.data / "update-backups"
        target = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
        target.mkdir(parents=True, exist_ok=True)
        for item in sorted(self.data.iterdir()):
            if item.name in BACKUP_SKIP:
                continue
            destination = target / item.name
            if item.is_dir():
                shutil.copytree(item, destination, symlinks=True,
                                ignore=shutil.ignore_patterns("*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm"))
            elif item.is_file() and not item.name.endswith((".sqlite3-wal", ".sqlite3-shm")):
                shutil.copy2(item, destination)
        for database in self.data.rglob("*.sqlite3"):
            relative = database.relative_to(self.data)
            if relative.parts[0] in BACKUP_SKIP or "backups" in relative.parts:
                continue
            copy = target / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as source, sqlite3.connect(copy) as dest:
                source.backup(dest)
        for old in sorted(p for p in root.iterdir() if p.is_dir())[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)
        return target

    def _packages(self, before: str, after: str) -> bool:
        """Install the package list of `after` if it differs from `before`'s. True when fine."""
        if self.git("diff", "--quiet", before, after, "--", "installer/requirements.lock",
                    "installer/build-tools.lock").returncode == 0:
            return True
        for lock, extra in (("installer/build-tools.lock", []), ("installer/requirements.lock", ["--no-deps"])):
            done = self._run([self.python, "-m", "pip", "install", "--quiet", *extra, "-r", str(self.app / lock)],
                             capture_output=True, text=True, timeout=3600, check=False)
            if done.returncode != 0:
                return False
        return True

    def _self_test(self) -> bool:
        with tempfile.TemporaryDirectory(prefix="bemo-update-test-") as temp:
            done = self._run([self.python, "self_test.py"], cwd=self.app, capture_output=True, text=True,
                             timeout=600, check=False, env=dict(os.environ, BEMO_DATA_DIR=temp))
        return done.returncode == 0

    def install(self, update: Update) -> dict:
        """Steps 1-4. On success the caller restarts BMO; the Supervisor watches the first start."""
        if self.git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            return {"ok": False, "reason": "local_changes"}
        previous = self.git("rev-parse", "HEAD").stdout.strip()
        previous_version = self.current_version()
        backup = self.backup(f"before-{update.tag}")
        if self.git("checkout", "--quiet", "--detach", update.tag).returncode != 0:
            return {"ok": False, "reason": "checkout failed"}
        record = {"status": VERIFYING, "from": previous, "from_version": list(previous_version or ()),
                  "to": update.tag, "to_version": list(update.version), "started_at": self.clock(),
                  "backup": str(backup)}
        if not self._packages(previous, update.tag):
            self.roll_back(record, "the new package list didn't install")
            return {"ok": False, "reason": "packages"}
        if not self._self_test():
            self.roll_back(record, "the new version failed its self-test")
            return {"ok": False, "reason": "self-test"}
        self.save(record)
        return {"ok": True, "tag": update.tag}

    def roll_back(self, record: dict | None = None, reason: str = "") -> bool:
        record = record or self.state()
        previous = record.get("from")
        if not previous:
            return False
        current = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "--quiet", "--detach", previous)
        self._packages(current, previous)
        record.update(status=ROLLED_BACK, reason=reason, rolled_back_at=self.clock(), announced=False)
        self.save(record)
        return True

    def roll_back_if_unhealthy(self, *, crashed: bool) -> bool:
        """For the Supervisor: a new version that crashed, or isn't ready in time, is put back."""
        record = self.state()
        if record.get("status") != VERIFYING:
            return False
        too_slow = self.clock() - float(record.get("started_at", 0)) > VERIFY_SECONDS
        if crashed or too_slow:
            return self.roll_back(record, "the new version crashed" if crashed else "the new version didn't get ready")
        return False

    def missing_system_packages(self) -> list[str]:
        """Linux packages in this version's list (installer/apt-packages.txt) that the PC doesn't have. An update
        installs Python packages itself, but these need the owner's password (audit 2026-10-24: OpenSCAD came
        after the first public version, so an updated BMO would lose 3D parts without a word)."""
        try:
            text = (self.app / "installer" / "apt-packages.txt").read_text(encoding="utf-8")
        except OSError:
            return []
        wanted = [name for line in text.splitlines() for name in line.split("#")[0].split()]
        if not wanted:
            return []
        done = self._run(["dpkg-query", "-W", "-f=${Package} ${Status}\n", *wanted], capture_output=True,
                         text=True, timeout=60, check=False)
        have = {line.split()[0].split(":")[0] for line in str(done.stdout or "").splitlines()
                if line.strip().endswith("install ok installed")}
        return [name for name in wanted if name not in have]

    def mark_ready(self) -> str | None:
        """BMO reached "ready": confirm a verifying update, or report a switch-back once. The sentence to say."""
        record = self.state()
        if record.get("status") == VERIFYING:
            record.update(status=OK, ready_at=self.clock())
            missing = self.missing_system_packages()
            record["missing_packages"] = missing
            self.save(record)
            line = f"I'm updated to version {spoken_version(tuple(record.get('to_version') or ()))}."
            if missing:
                line += (f" This version also needs {', '.join(missing)}, which I can't install myself. "
                         f"Open the Terminal and type: sudo apt install {' '.join(missing)}.")
            return line
        if record.get("status") == ROLLED_BACK and not record.get("announced"):
            record["announced"] = True
            self.save(record)
            return (f"The update to version {spoken_version(tuple(record.get('to_version') or ()))} didn't work, "
                    f"because {record.get('reason') or 'something went wrong'}, so I went back to the version before.")
        return None


def default_updater() -> Updater:
    import config
    return Updater(Path(config.APP_ROOT), Path(config.DATA_ROOT),
                   remote=os.environ.get("BEMO_UPDATE_REMOTE", "origin"))


def spoken_check(check: Check) -> str:
    if check.status == "available":
        u = check.update
        what = f" {u.subject.rstrip('.')}." if u.subject else ""
        return f"Version {spoken_version(u.version)} is available.{what} Say, Hey BMO, yes, to install it."
    return {
        "current": "I'm up to date.",
        "local_changes": "My folder has changes that aren't saved in Git, so I won't update over them.",
        "no_remote": "I don't know where my updates come from yet.",
        "offline": "I couldn't reach the update server. Check the internet connection.",
        "no_release_key": "I don't have the release key yet, so I can't check that an update is genuine.",
        "unsigned": f"There's a newer version, {check.detail}, but it isn't signed with the release key, "
                    "so I won't install it.",
    }.get(check.status, "I couldn't check for updates.")


_CHECK = re.compile(r"^(?:check|look|search)\s+(?:for\s+)?(?:any\s+|new\s+)?(?:updates?|upgrades?)\b|"
                    r"^(?:are there|is there|any)\s+(?:any\s+)?(?:new\s+)?(?:updates?|upgrades?)\b|"
                    r"^update yourself\b|^(?:do you have|got) (?:an |any )?updates?\b", re.I)


def is_update_request(text: str) -> bool:
    words = re.sub(r"[^a-z ]+", " ", str(text or "").lower()).strip()
    words = re.sub(r"^(?:please|can you|could you)\s+", "", words)
    return bool(_CHECK.search(words))
