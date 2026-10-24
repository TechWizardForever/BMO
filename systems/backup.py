"""Verified full-system Bemo backups to removable storage."""
from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class FullBackupManager:
    def __init__(self, *, data_root: str | Path, app_root: str | Path, removable_provider):
        self.data_root = Path(data_root).expanduser()
        self.app_root = Path(app_root).resolve()
        self.removable_provider = removable_provider

    # Left out of a backup because BMO downloads or rebuilds them (2026-10-23: a backup to a USB stick was
    # heading for 4 GB and an hour, mostly the screensavers' Wine layer: 1.3 GB in 1,700 small files).
    REBUILT_OR_DOWNLOADED = {
        "install-cache",           # installer downloads
        "wine-screensavers",       # the screensavers' Windows layer: Wine makes it again on the first saver
        "cache",                   # shapes-menu pictures and the like, drawn again
        "source-upgrade-backups",  # copies of older BMO code kept by updates
    }

    @staticmethod
    def _copy_ignore(directory: str, names: list[str]):
        ignored = set()
        # Runtime sockets/PIDs are not restorable state. Search indexes are
        # rebuildable and may be WAL-backed while Bemo is running.
        for name in names:
            if name in {"runtime", "__pycache__", ".pytest_cache"}:
                ignored.add(name)
            if name in {"conversation.sqlite3", "conversation.sqlite3-wal", "conversation.sqlite3-shm"}:
                ignored.add(name)
            if name.startswith("index.sqlite3"):
                ignored.add(name)
        # Game libraries can be hundreds of gigabytes and are replaceable local
        # content. Preserve Dolphin's user directory (memory cards, save states,
        # controller mappings) and config, but don't silently turn a normal
        # Bemo backup into a ROM/artwork/AppImage archive.
        if Path(directory).name == "games":
            ignored.update({"roms", "artwork", "bin"})
        return ignored

    @staticmethod
    def _app_copy_ignore(directory: str, names: list[str]):
        # Keep the complete Bemo source/config/policies/sprites/audio, but skip
        # machine-local caches, virtual environments, Git internals, and build
        # products that can be recreated. Model weights live outside this tree.
        ignored = set()
        for name in names:
            if name in {
                "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                ".git", ".venv", "venv", "env", "build", "dist",
            }:
                ignored.add(name)
            if name.endswith(".pyc") or name.endswith(".pyo"):
                ignored.add(name)
        return ignored

    def _write_manifest(self, root: Path) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS.txt":
                rel = path.relative_to(root).as_posix()
                hashes[rel] = _sha256(path)
        _atomic_text(
            root / "SHA256SUMS.txt",
            "".join(f"{digest}  {rel}\n" for rel, digest in hashes.items()),
        )
        return hashes

    @staticmethod
    def _verify(root: Path, hashes: dict[str, str]) -> bool:
        return all((root / rel).is_file() and _sha256(root / rel) == digest for rel, digest in hashes.items())

    @staticmethod
    def verify_folder(folder: str | Path) -> dict:
        folder = Path(folder)
        manifest = folder / "SHA256SUMS.txt"
        if not manifest.is_file():
            return {"ok": False, "reason": "missing-manifest"}
        hashes = {}
        try:
            for line in manifest.read_text(encoding="utf-8").splitlines():
                digest, rel = line.split("  ", 1)
                hashes[rel] = digest
        except (OSError, ValueError):
            return {"ok": False, "reason": "invalid-manifest"}
        return {"ok": FullBackupManager._verify(folder, hashes), "files": len(hashes)}

    @staticmethod
    def restore_data_to(folder: str | Path, destination: str | Path) -> dict:
        """Restore backup data into an empty destination for recovery/testing.

        The live Bemo data directory should only be replaced while Bemo is
        stopped. Requiring an empty destination avoids accidental in-place merge
        corruption; the CLI/user can rename the old data root first.
        """
        folder = Path(folder)
        verified = FullBackupManager.verify_folder(folder)
        if not verified.get("ok"):
            return {"ok": False, "reason": "verification-failed"}
        source = folder / "data"
        if not source.is_dir():
            return {"ok": False, "reason": "missing-data"}
        destination = Path(destination)
        if destination.exists() and any(destination.iterdir()):
            return {"ok": False, "reason": "destination-not-empty"}
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=False)
        return {"ok": True, "destination": str(destination)}

    @staticmethod
    def restore_app_to(folder: str | Path, destination: str | Path) -> dict:
        """Restore the backed-up Bemo application tree into an empty directory."""
        folder = Path(folder)
        verified = FullBackupManager.verify_folder(folder)
        if not verified.get("ok"):
            return {"ok": False, "reason": "verification-failed"}
        source = folder / "app"
        if not source.is_dir():
            return {"ok": False, "reason": "missing-app"}
        destination = Path(destination)
        if destination.exists() and any(destination.iterdir()):
            return {"ok": False, "reason": "destination-not-empty"}
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=False)
        return {"ok": True, "destination": str(destination)}

    def create(self, *, memory_store, create_zip: bool = True) -> dict:
        drives = list(self.removable_provider() or [])
        if not drives:
            return {"ok": False, "reason": "no-removable-drive"}
        drive = Path(drives[0])
        if not drive.exists() or not os.access(drive, os.W_OK):
            return {"ok": False, "reason": "drive-not-writable"}

        root = drive / "Bemo Backups"
        try:
            root.resolve().relative_to(self.data_root.resolve())
            return {"ok": False, "reason": "backup-destination-inside-data-root"}
        except ValueError:
            pass
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        final = root / f"bemo_{stamp}"
        partial = root / f"bemo_{stamp}.partial"
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True, exist_ok=False)

        data_target = partial / "data"
        top = self.data_root.resolve()

        def ignore(directory, names):
            ignored = self._copy_ignore(directory, names)
            here = Path(directory).resolve()
            if here == top:
                ignored.update(name for name in names if name in self.REBUILT_OR_DOWNLOADED)
            elif here == top / "models":
                # The click-by-sight helper (2.6 GB, models/ since v1.28.27) is downloaded again; the trained
                # "Hey BMO" wake word beside it (4 MB) is the owner's and is kept.
                ignored.update(name for name in names if name == "gui")
            return ignored

        if self.data_root.exists():
            shutil.copytree(
                self.data_root,
                data_target,
                symlinks=False,
                ignore=ignore,
                dirs_exist_ok=True,
            )
        else:
            data_target.mkdir(parents=True, exist_ok=True)

        # Always create a transactionally consistent live conversation DB rather
        # than copying SQLite + WAL files while Bemo is using them.
        memory_store.backup_to(data_target / "memory" / "conversation.sqlite3")

        app_target = partial / "app"
        if self.app_root.is_dir():
            shutil.copytree(
                self.app_root,
                app_target,
                symlinks=False,
                ignore=self._app_copy_ignore,
                dirs_exist_ok=True,
            )
        else:
            app_target.mkdir(parents=True, exist_ok=True)

        _atomic_text(
            partial / "RESTORE_README.txt",
            "This is a verified full Bemo backup. data/ contains BEMO_DATA_DIR and app/ contains the Bemo application tree.\n"
            "Restore only while Bemo is stopped. Restore data/ to the desired BEMO_DATA_DIR and app/ to a new/empty\n"
            "application directory, then reinstall/update the Python environment if needed. Model weights are intentionally\n"
            "not duplicated (Ollama's store and data/models/): reinstall them with model_installer.py and\n"
            "tools/gui_vision/setup_gui_vision.sh.\n",
        )
        hashes = self._write_manifest(partial)
        if not self._verify(partial, hashes):
            shutil.rmtree(partial, ignore_errors=True)
            return {"ok": False, "reason": "verification-failed"}
        os.replace(partial, final)

        zip_path = None
        if create_zip:
            zip_path = Path(shutil.make_archive(str(final), "zip", root_dir=final))
            _atomic_text(
                zip_path.with_suffix(zip_path.suffix + ".sha256"),
                f"{_sha256(zip_path)}  {zip_path.name}\n",
            )
        return {
            "ok": True,
            "drive": str(drive),
            "folder": str(final),
            "zip": str(zip_path) if zip_path else None,
            "files": len(hashes),
        }
