"""Bounded read-only filename/path discovery across Bemo-accessible storage."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable, Iterable


IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_.-]{2,}", str(text).lower()) if t]


class FileLocator:
    """Bounded, safe, read-only filesystem search.

    Bemo may inspect filenames/paths under the user's home directory, mounted
    removable drives, selected system roots, and optionally a bounded whole-PC
    scan. This discovery component itself exposes no write/delete operations;
    full computer actions are Supervisor-mediated in systems.computer.
    """

    def __init__(
        self,
        *,
        home_root: str | Path | None = None,
        removable_provider: Callable[[], Iterable[Path]] | None = None,
        exclude_dirs: Iterable[str] = (),
        system_roots: Iterable[str | Path] = (),
        enable_full_pc_search: bool = False,
        max_scan_files: int = 80_000,
        search_timeout_seconds: float = 4.0,
        max_image_bytes: int = 100 * 1024 * 1024,
    ):
        self.home_root = Path(home_root or Path.home()).expanduser()
        self.removable_provider = removable_provider
        self.exclude_dirs = {str(x) for x in exclude_dirs}
        self.system_roots = [Path(x).expanduser() for x in system_roots]
        self.enable_full_pc_search = bool(enable_full_pc_search)
        self.max_scan_files = max(1000, int(max_scan_files))
        self.search_timeout_seconds = max(0.25, float(search_timeout_seconds))
        self.max_image_bytes = max(1, int(max_image_bytes))

    def roots(self, scope: str = "all") -> list[Path]:
        scope = str(scope or "all").strip().lower()
        result: list[Path] = []
        if scope in {"all", "home"} and self.home_root.exists():
            result.append(self.home_root)
        if scope in {"all", "removable", "usb", "drive"} and self.removable_provider:
            try:
                result.extend(Path(p) for p in self.removable_provider())
            except Exception:
                pass
        if scope == "all":
            result.extend(root for root in self.system_roots if root.exists())
        if scope in {"computer", "pc", "system"}:
            if self.enable_full_pc_search:
                result.append(Path("/"))
            else:
                result.extend(root for root in self.system_roots if root.exists())
        unique = []
        seen = set()
        for root in result:
            try:
                resolved = root.resolve()
            except OSError:
                resolved = root
            key = str(resolved)
            if key not in seen and root.exists():
                seen.add(key)
                unique.append(root)
        return unique

    def _is_allowed(self, path: Path) -> bool:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return False
        # Validation is intentionally broader than filename search: once a model
        # knows an exact path, any regular file readable by the logged-in user may
        # be selected. The OS remains the permission boundary.
        validation_roots = self.roots("all")
        if self.enable_full_pc_search:
            validation_roots = [Path("/")]
        for root in validation_roots:
            try:
                resolved.relative_to(root.expanduser().resolve())
                return True
            except (ValueError, OSError):
                continue
        return False

    def validate_file(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        try:
            candidate = candidate.resolve()
        except OSError as exc:
            raise FileNotFoundError(path) from exc
        if not self._is_allowed(candidate):
            raise ValueError("File is outside Bemo's allowed home/removable-drive roots")
        if not candidate.is_file():
            raise FileNotFoundError(str(candidate))
        return candidate

    def validate_image(self, path: str | Path) -> Path:
        candidate = self.validate_file(path)
        if candidate.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("Selected file is not a supported image")
        if candidate.stat().st_size > self.max_image_bytes:
            raise ValueError("Selected image is larger than the configured safety limit")
        return candidate

    _NEVER_ENTER = {".cache", ".Trash", ".Trash-1000", "node_modules", ".git", "__pycache__",
                    "site-packages", "dist-packages"}

    def _walk_shallow_first(self, root: Path):
        """(folder, file names), top-level folders before deep ones.

        Live 2026-10-19: os.walk spent the whole 80,000-file budget in ~/.local (69k files) and
        BMO's Python environment in ~/Downloads (67k) and never reached ~/BMO-code (3 files), so
        "find snake_game" found nothing. Hidden folders and Python environments are not entered;
        hidden files in a folder that is searched still count.
        """
        from collections import deque
        queue = deque([Path(root)])
        while queue:
            folder = queue.popleft()
            try:
                entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
            except OSError:
                continue
            files, subdirs = [], []
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        subdirs.append(entry.name)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(entry.name)
                except OSError:
                    continue
            yield folder, files
            if "pyvenv.cfg" in files:
                continue  # a Python virtual environment: thousands of library files, never the owner's
            for name in subdirs:
                if name.startswith(".") or name in self.exclude_dirs or name in self._NEVER_ENTER:
                    continue
                queue.append(folder / name)

    def search(
        self,
        query: str,
        *,
        extensions: list[str] | tuple[str, ...] | None = None,
        scope: str = "all",
        limit: int = 12,
    ) -> list[dict]:
        terms = _tokens(query)
        wanted_exts = None
        if extensions:
            wanted_exts = {
                (str(ext).lower() if str(ext).startswith(".") else "." + str(ext).lower())
                for ext in extensions
            }
        limit = max(1, min(50, int(limit)))
        deadline = time.monotonic() + self.search_timeout_seconds
        scanned = 0
        matches: list[tuple[float, float, Path, Path]] = []

        for root in self.roots(scope):
            if time.monotonic() >= deadline or scanned >= self.max_scan_files:
                break
            for base, files in self._walk_shallow_first(root):
                for name in files:
                    scanned += 1
                    if scanned > self.max_scan_files or time.monotonic() >= deadline:
                        break
                    path = base / name
                    if wanted_exts is not None and path.suffix.lower() not in wanted_exts:
                        continue
                    try:
                        rel = path.relative_to(root)
                        stat = path.stat()
                    except OSError:
                        continue
                    haystack = (name + " " + rel.as_posix()).lower()
                    if terms:
                        hits = sum(1 for term in terms if term in haystack)
                        if hits == 0:
                            continue
                        exact_name = 2.0 if query.strip().lower() == name.lower() else 0.0
                        score = float(hits) + exact_name
                    else:
                        score = 0.0
                    matches.append((score, stat.st_mtime, path, root))
                if scanned > self.max_scan_files or time.monotonic() >= deadline:
                    break

        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        results = []
        for score, mtime, path, root in matches[:limit]:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            results.append(
                {
                    "path": str(path),
                    "root": str(root),
                    "name": path.name,
                    "extension": path.suffix.lower(),
                    "size": int(size),
                    "modified_at": float(mtime),
                    "score": float(score),
                    "is_image": path.suffix.lower() in IMAGE_EXTENSIONS,
                }
            )
        return results
