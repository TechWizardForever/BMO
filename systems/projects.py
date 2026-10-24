"""Persistent project workspaces, retrieval, local Git snapshots, and USB export."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    # On POSIX, fsync the directory entry too so a sudden power loss is less
    # likely to lose the rename after the file contents themselves were synced.
    try:
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except (AttributeError, OSError):
        pass


def _atomic_write_json(path: Path, value: dict) -> None:
    _atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:64] or "project"


def _fts_query(text: str) -> str:
    tokens = []
    seen = set()
    for token in re.findall(r"[A-Za-z0-9_\-]{2,}", text.lower()):
        if token in seen:
            continue
        seen.add(token)
        tokens.append('"' + token.replace('"', '""') + '"')
        if len(tokens) >= 12:
            break
    return " OR ".join(tokens)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ProjectManager:
    def __init__(
        self,
        *,
        data_root: str | Path,
        projects_root: str | Path,
        index_extensions: tuple[str, ...],
        exclude_dirs: tuple[str, ...],
        max_file_bytes: int,
        context_char_budget: int,
        retrieval_chunks: int,
        reindex_interval_seconds: float,
        auto_git: bool = True,
        git_name: str = "Bemo",
        git_email: str = "bemo@local",
        export_root_override: str | Path | None = None,
        export_include_git: bool = True,
        export_create_zip: bool = True,
        memory_history_retain: int = 100,
    ):
        self.data_root = Path(data_root).expanduser()
        self.projects_root = Path(projects_root).expanduser()
        self.state_path = self.data_root / "project_state.json"
        self.import_staging_root = self.data_root / "imports"
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.import_staging_root.mkdir(parents=True, exist_ok=True)
        self.index_extensions = {ext.lower() for ext in index_extensions}
        self.exclude_dirs = set(exclude_dirs)
        self.max_file_bytes = int(max_file_bytes)
        self.context_char_budget = int(context_char_budget)
        self.retrieval_chunks = int(retrieval_chunks)
        self.reindex_interval_seconds = float(reindex_interval_seconds)
        self.auto_git = bool(auto_git)
        self.git_name = git_name
        self.git_email = git_email
        self.export_root_override = (
            Path(export_root_override).expanduser()
            if export_root_override
            else None
        )
        self.export_include_git = bool(export_include_git)
        self.export_create_zip = bool(export_create_zip)
        self.memory_history_retain = max(1, int(memory_history_retain))
        self._last_index_time: dict[str, float] = {}
        self._active_slug = self._load_active_slug()
        if self._active_slug and not self._project_dir(self._active_slug).exists():
            self._active_slug = None
            self._save_state()

    # ------------------------------------------------------------------
    # Project identity / persistence
    # ------------------------------------------------------------------
    def _load_active_slug(self) -> str | None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            slug = data.get("active_project")
            return str(slug) if slug else None
        except (OSError, ValueError, TypeError):
            return None

    def _save_state(self) -> None:
        _atomic_write_json(
            self.state_path,
            {"active_project": self._active_slug, "updated_at": _utc_now()},
        )

    def _project_dir(self, slug: str) -> Path:
        return self.projects_root / slug

    def _workspace(self, slug: str) -> Path:
        return self._project_dir(slug) / "workspace"

    def _metadata_path(self, slug: str) -> Path:
        return self._project_dir(slug) / "project.json"

    def _memory_path(self, slug: str) -> Path:
        return self._project_dir(slug) / "memory.md"

    def _index_path(self, slug: str) -> Path:
        return self._project_dir(slug) / "index.sqlite3"

    def _facts_path(self, slug: str) -> Path:
        return self._project_dir(slug) / "facts.json"

    @property
    def active_slug(self) -> str | None:
        return self._active_slug

    @property
    def active_workspace(self) -> Path | None:
        return self._workspace(self._active_slug) if self._active_slug else None

    def _read_metadata(self, slug: str) -> dict:
        try:
            return json.loads(self._metadata_path(slug).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"name": slug, "slug": slug, "project_summary_last_message_id": 0}

    def _write_metadata(self, slug: str, metadata: dict) -> None:
        _atomic_write_json(self._metadata_path(slug), metadata)

    def list_projects(self) -> list[dict]:
        projects = []
        for child in sorted(self.projects_root.iterdir() if self.projects_root.exists() else []):
            if not child.is_dir() or not (child / "project.json").exists():
                continue
            metadata = self._read_metadata(child.name)
            metadata["active"] = child.name == self._active_slug
            projects.append(metadata)
        return projects

    def resolve_project(self, name_or_slug: str) -> str | None:
        wanted = _slugify(name_or_slug)
        projects = self.list_projects()
        for project in projects:
            if project.get("slug") == wanted:
                return str(project["slug"])
        normalized = name_or_slug.strip().lower()
        for project in projects:
            if str(project.get("name", "")).strip().lower() == normalized:
                return str(project["slug"])
        # Voice recognition may alter punctuation/spaces; slug comparison is a
        # useful forgiving final pass.
        for project in projects:
            if _slugify(str(project.get("name", ""))) == wanted:
                return str(project["slug"])
        return None

    def create_project(self, name: str, import_from: str | Path | None = None) -> dict:
        base = _slugify(name)
        slug = base
        counter = 2
        while self._project_dir(slug).exists():
            slug = f"{base}-{counter}"
            counter += 1

        project_dir = self._project_dir(slug)
        workspace = self._workspace(slug)
        project_dir.mkdir(parents=True, exist_ok=False)

        if import_from is not None:
            source = Path(import_from).expanduser().resolve()
            if not source.is_dir():
                raise FileNotFoundError(f"Project import directory does not exist: {source}")
            shutil.copytree(source, workspace, symlinks=False)
        else:
            workspace.mkdir(parents=True, exist_ok=True)

        metadata = {
            "name": name.strip() or slug,
            "slug": slug,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "last_opened_at": _utc_now(),
            "project_summary_last_message_id": 0,
        }
        self._write_metadata(slug, metadata)
        _atomic_write_text(
            self._memory_path(slug),
            f"# Project Memory: {metadata['name']}\n\n"
            "## Current State\n\n"
            "Durable current state will be maintained here.\n\n"
            "## Locked Decisions\n\n"
            "## Requirements and Constraints\n\n"
            "## Known Issues\n\n"
            "## Important Files and Assets\n\n"
            "## Pending Work\n\n"
            "## Historical / Superseded\n\n",
        )
        _atomic_write_json(self._facts_path(slug), {"facts": [], "updated_at": _utc_now()})
        self._active_slug = slug
        self._save_state()
        if self.auto_git:
            self.ensure_git(slug)
            self.snapshot("Initial project", slug=slug)
        self.index_project(slug, force=True)
        return metadata

    def open_project(self, name_or_slug: str) -> dict:
        slug = self.resolve_project(name_or_slug)
        if not slug:
            raise KeyError(f"No project named {name_or_slug!r}")
        metadata = self._read_metadata(slug)
        metadata["last_opened_at"] = _utc_now()
        metadata["updated_at"] = _utc_now()
        self._write_metadata(slug, metadata)
        self._active_slug = slug
        self._save_state()
        if self.auto_git:
            self.ensure_git(slug)
        self.index_project(slug, force=False)
        return metadata

    def close_project(self) -> None:
        self._active_slug = None
        self._save_state()

    def active_metadata(self) -> dict | None:
        return self._read_metadata(self._active_slug) if self._active_slug else None

    # ------------------------------------------------------------------
    # Durable project memory
    # ------------------------------------------------------------------
    def project_memory(self, slug: str | None = None) -> str:
        slug = slug or self._active_slug
        if not slug:
            return ""
        try:
            return self._memory_path(slug).read_text(encoding="utf-8")
        except OSError:
            return ""

    def set_project_memory(self, text: str, slug: str | None = None) -> None:
        slug = slug or self._active_slug
        if not slug:
            raise RuntimeError("No active project")
        path = self._memory_path(slug)
        new_text = text.rstrip() + "\n"
        try:
            old_text = path.read_text(encoding="utf-8")
        except OSError:
            old_text = ""
        if old_text and old_text != new_text:
            history = self._project_dir(slug) / "memory_history"
            history.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            _atomic_write_text(history / f"memory-{stamp}.md", old_text)
            old_versions = sorted(history.glob("memory-*.md"))
            for old in old_versions[:-self.memory_history_retain]:
                try:
                    old.unlink()
                except OSError:
                    pass
        _atomic_write_text(path, new_text)
        metadata = self._read_metadata(slug)
        metadata["updated_at"] = _utc_now()
        self._write_metadata(slug, metadata)

    def remembered_notes(self, slug: str | None = None) -> list[str]:
        """The notes kept in the project's current memory, as plain sentences (the date and markdown dropped), so
        BMO can read them out himself (2026-10-23: Coding answered "what is the cup for?" with a guess
        although "the cup is a planter for basil" was in its prompt)."""
        notes = []
        for line in self.project_memory_for_context(slug).splitlines():
            line = line.strip()
            if not line.startswith(("- ", "* ")):
                continue
            text = re.sub(r"^[-*]\s+(?:\[\d{4}-\d{2}-\d{2}\]\s*)?", "", line).strip()
            text = re.sub(r"[*_`]+", "", text).strip()
            if text:
                notes.append(text)
        return notes

    def append_project_note(self, note: str, slug: str | None = None) -> None:
        slug = slug or self._active_slug
        if not slug:
            raise RuntimeError("No active project")
        current = self.project_memory(slug).rstrip()
        stamp = datetime.now().strftime("%Y-%m-%d")
        bullet = f"- [{stamp}] {note.strip()}"
        # Keep explicit notes in the current portion of structured memory rather
        # than accidentally appending them after Historical/Superseded, which is
        # intentionally excluded from default retrieval.
        match = re.search(r"(?im)^##\s+Current State\s*$", current)
        if match:
            next_heading = re.search(r"(?im)^##\s+", current[match.end():])
            insert_at = match.end() + (next_heading.start() if next_heading else len(current[match.end():]))
            prefix = current[:insert_at].rstrip()
            suffix = current[insert_at:].lstrip("\n")
            updated = prefix + "\n\n" + bullet + "\n\n" + suffix
        else:
            updated = current + f"\n\n{bullet}\n"
        self.set_project_memory(updated, slug)

    def _read_facts(self, slug: str) -> dict:
        try:
            data = json.loads(self._facts_path(slug).read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("facts"), list):
                return data
        except (OSError, ValueError, TypeError):
            pass
        return {"facts": [], "updated_at": ""}

    def _write_facts(self, slug: str, data: dict) -> None:
        data["updated_at"] = _utc_now()
        _atomic_write_json(self._facts_path(slug), data)

    @staticmethod
    def _fact_key(value: str) -> str:
        value = re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()
        return re.sub(r"\s+", " ", value)[:160]

    def set_project_fact(
        self,
        key: str,
        value: str,
        *,
        category: str = "decision",
        slug: str | None = None,
        source: str = "model",
    ) -> dict:
        """Set one authoritative current project fact, superseding same-key facts."""
        slug = slug or self._active_slug
        if not slug:
            raise RuntimeError("No active project")
        norm = self._fact_key(key)
        if not norm:
            raise ValueError("Fact key is empty")
        value = str(value).strip()
        if not value:
            raise ValueError("Fact value is empty")
        data = self._read_facts(slug)
        facts = data.setdefault("facts", [])
        now = _utc_now()
        previous_ids = []
        for fact in facts:
            if self._fact_key(fact.get("key", "")) == norm and fact.get("status") == "current":
                fact["status"] = "superseded"
                fact["superseded_at"] = now
                previous_ids.append(str(fact.get("id", "")))
        fact_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        new_fact = {
            "id": fact_id,
            "key": str(key).strip(),
            "value": value,
            "category": str(category or "decision").strip(),
            "status": "current",
            "source": str(source or "model"),
            "created_at": now,
            "updated_at": now,
            "supersedes": previous_ids,
        }
        for fact in facts:
            if str(fact.get("id")) in previous_ids:
                fact["superseded_by"] = fact_id
        facts.append(new_fact)
        self._write_facts(slug, data)
        return new_fact

    def supersede_project_fact(
        self, key: str, *, replacement: str = "", slug: str | None = None
    ) -> dict:
        slug = slug or self._active_slug
        if not slug:
            raise RuntimeError("No active project")
        norm = self._fact_key(key)
        data = self._read_facts(slug)
        changed = []
        now = _utc_now()
        for fact in data.get("facts", []):
            if self._fact_key(fact.get("key", "")) == norm and fact.get("status") == "current":
                fact["status"] = "superseded"
                fact["superseded_at"] = now
                if replacement:
                    fact["superseded_reason"] = str(replacement)
                changed.append(str(fact.get("id")))
        if changed:
            self._write_facts(slug, data)
        return {"ok": bool(changed), "superseded": changed}

    def project_facts(self, slug: str | None = None, *, include_superseded: bool = False) -> list[dict]:
        slug = slug or self._active_slug
        if not slug:
            return []
        facts = list(self._read_facts(slug).get("facts", []))
        if not include_superseded:
            facts = [fact for fact in facts if fact.get("status") == "current"]
        return facts

    def current_facts_text(self, slug: str | None = None) -> str:
        facts = self.project_facts(slug, include_superseded=False)
        if not facts:
            return ""
        lines = ["Authoritative current project facts (newer same-key facts supersede older ones):"]
        for fact in facts[-100:]:
            lines.append(
                f"- [{fact.get('category', 'fact')}] {fact.get('key')}: {fact.get('value')}"
            )
        return "\n".join(lines)

    def project_memory_for_context(self, slug: str | None = None) -> str:
        """Return current structured memory without the superseded-history section."""
        text = self.project_memory(slug).strip()
        if not text:
            return ""
        marker = re.search(r"(?im)^##\s+Historical\s*/\s*Superseded\s*$", text)
        if marker:
            text = text[: marker.start()].rstrip()
        return text

    def activate_slug(self, slug: str | None) -> dict | None:
        if slug is None:
            self.close_project()
            return None
        if not self._project_dir(slug).exists():
            raise KeyError(slug)
        metadata = self._read_metadata(slug)
        metadata["last_opened_at"] = _utc_now()
        metadata["updated_at"] = _utc_now()
        self._write_metadata(slug, metadata)
        self._active_slug = slug
        self._save_state()
        return metadata

    def classification_catalog(self, memory_chars: int = 1200) -> list[dict]:
        catalog = []
        for project in self.list_projects():
            slug = str(project.get("slug"))
            memory = self.project_memory_for_context(slug)
            facts = self.current_facts_text(slug)
            catalog.append(
                {
                    "slug": slug,
                    "name": str(project.get("name", slug)),
                    "active": bool(project.get("active")),
                    "description": (facts + "\n" + memory)[: max(0, int(memory_chars))],
                }
            )
        return catalog

    def heuristic_project_match(self, text: str) -> tuple[str | None, float, float]:
        """Return (best_slug, best_score, margin) using conservative lexical matching."""
        query = str(text).lower()
        q_tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{2,}", query))
        scored = []
        for item in self.classification_catalog(memory_chars=800):
            name = str(item["name"]).lower()
            slug = str(item["slug"]).lower()
            score = 0.0
            if name and name in query:
                score += 6.0
            if slug and slug in query.replace(" ", "-"):
                score += 5.0
            d_tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{2,}", (name + " " + item["description"]).lower()))
            overlap = q_tokens & d_tokens
            score += min(4.0, len(overlap) * 0.65)
            if item.get("active"):
                score += 0.5
            scored.append((score, str(item["slug"])))
        scored.sort(reverse=True)
        if not scored:
            return None, 0.0, 0.0
        best_score, best_slug = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        return best_slug, best_score, best_score - second

    def project_summary_cursor(self, slug: str | None = None) -> int:
        slug = slug or self._active_slug
        if not slug:
            return 0
        return int(self._read_metadata(slug).get("project_summary_last_message_id") or 0)

    def set_project_summary_cursor(self, message_id: int, slug: str | None = None) -> None:
        slug = slug or self._active_slug
        if not slug:
            return
        metadata = self._read_metadata(slug)
        metadata["project_summary_last_message_id"] = int(message_id)
        metadata["updated_at"] = _utc_now()
        self._write_metadata(slug, metadata)

    # ------------------------------------------------------------------
    # Fast local source index / retrieval
    # ------------------------------------------------------------------
    def _is_indexable(self, path: Path) -> bool:
        if path.suffix.lower() not in self.index_extensions:
            return False
        try:
            return path.stat().st_size <= self.max_file_bytes
        except OSError:
            return False

    def _iter_all_project_files(self, slug: str):
        workspace = self._workspace(slug)
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            root_path = Path(root)
            for filename in files:
                yield root_path / filename

    def _iter_project_files(self, slug: str):
        workspace = self._workspace(slug)
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            root_path = Path(root)
            for filename in files:
                path = root_path / filename
                if self._is_indexable(path):
                    yield path

    @staticmethod
    def _chunk_text(text: str, lines_per_chunk: int = 100, overlap: int = 15):
        lines = text.splitlines()
        if not lines:
            return []
        chunks = []
        step = max(1, lines_per_chunk - overlap)
        for start in range(0, len(lines), step):
            end = min(len(lines), start + lines_per_chunk)
            content = "\n".join(lines[start:end]).strip()
            if content:
                chunks.append((start + 1, end, content))
            if end >= len(lines):
                break
        return chunks

    def _open_index(self, slug: str):
        conn = sqlite3.connect(str(self._index_path(slug)), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files(
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assets(
                path TEXT PRIMARY KEY,
                extension TEXT NOT NULL,
                kind TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind, extension)")
        fts = True
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                    path UNINDEXED,
                    start_line UNINDEXED,
                    end_line UNINDEXED,
                    content
                )
                """
            )
        except sqlite3.OperationalError:
            fts = False
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks_plain(
                    path TEXT,
                    start_line INTEGER,
                    end_line INTEGER,
                    content TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_plain_path ON chunks_plain(path)"
            )
        conn.commit()
        return conn, fts

    def index_project(self, slug: str | None = None, *, force: bool = False) -> dict:
        slug = slug or self._active_slug
        if not slug:
            return {"changed": 0, "removed": 0, "files": 0}
        now = time.monotonic()
        if (
            not force
            and now - self._last_index_time.get(slug, 0.0) < self.reindex_interval_seconds
        ):
            return {"changed": 0, "removed": 0, "files": 0, "skipped": True}

        conn, fts = self._open_index(slug)
        try:
            existing = {
                row["path"]: (int(row["mtime_ns"]), int(row["size"]), row["sha256"])
                for row in conn.execute("SELECT path,mtime_ns,size,sha256 FROM files")
            }
            existing_assets = {
                row["path"]: (int(row["mtime_ns"]), int(row["size"]), row["extension"], row["kind"])
                for row in conn.execute("SELECT path,mtime_ns,size,extension,kind FROM assets")
            }
            seen = set()
            seen_assets = set()
            changed = 0
            workspace = self._workspace(slug)
            image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
            cad_exts = {".step", ".stp", ".stl", ".obj", ".3mf", ".iges", ".igs", ".dxf", ".dwg", ".fcstd"}
            doc_exts = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
            for path in self._iter_all_project_files(slug):
                rel = path.relative_to(workspace).as_posix()
                try:
                    stat = path.stat()
                except OSError:
                    continue
                seen_assets.add(rel)
                ext = path.suffix.lower()
                if ext in image_exts:
                    kind = "image"
                elif ext in cad_exts:
                    kind = "cad"
                elif ext in doc_exts:
                    kind = "document"
                elif ext in self.index_extensions:
                    kind = "text"
                else:
                    kind = "file"
                old_asset = existing_assets.get(rel)
                if force or not old_asset or old_asset[:2] != (stat.st_mtime_ns, stat.st_size) or old_asset[2:] != (ext, kind):
                    conn.execute(
                        """
                        INSERT INTO assets(path,extension,kind,mtime_ns,size) VALUES(?,?,?,?,?)
                        ON CONFLICT(path) DO UPDATE SET
                            extension=excluded.extension,
                            kind=excluded.kind,
                            mtime_ns=excluded.mtime_ns,
                            size=excluded.size
                        """,
                        (rel, ext, kind, stat.st_mtime_ns, stat.st_size),
                    )

                if not self._is_indexable(path):
                    continue
                seen.add(rel)
                old = existing.get(rel)
                if old and not force and old[0] == stat.st_mtime_ns and old[1] == stat.st_size:
                    continue
                try:
                    raw = path.read_bytes()
                    text = raw.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                if old and not force and old[2] == digest:
                    conn.execute(
                        "UPDATE files SET mtime_ns=?,size=? WHERE path=?",
                        (stat.st_mtime_ns, stat.st_size, rel),
                    )
                    continue

                table = "chunks" if fts else "chunks_plain"
                conn.execute(f"DELETE FROM {table} WHERE path=?", (rel,))
                for start_line, end_line, content in self._chunk_text(text):
                    conn.execute(
                        f"INSERT INTO {table}(path,start_line,end_line,content) VALUES(?,?,?,?)",
                        (rel, start_line, end_line, content),
                    )
                conn.execute(
                    """
                    INSERT INTO files(path,mtime_ns,size,sha256) VALUES(?,?,?,?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime_ns=excluded.mtime_ns,
                        size=excluded.size,
                        sha256=excluded.sha256
                    """,
                    (rel, stat.st_mtime_ns, stat.st_size, digest),
                )
                changed += 1

            removed_paths = set(existing) - seen
            table = "chunks" if fts else "chunks_plain"
            for rel in removed_paths:
                conn.execute("DELETE FROM files WHERE path=?", (rel,))
                conn.execute(f"DELETE FROM {table} WHERE path=?", (rel,))
            removed_assets = set(existing_assets) - seen_assets
            for rel in removed_assets:
                conn.execute("DELETE FROM assets WHERE path=?", (rel,))
            conn.commit()
            self._last_index_time[slug] = now
            return {
                "changed": changed,
                "removed": len(removed_paths),
                "files": len(seen),
                "assets": len(seen_assets),
                "removed_assets": len(removed_assets),
                "skipped": False,
            }
        finally:
            conn.close()

    def search_project(self, query: str, *, slug: str | None = None, limit: int | None = None) -> list[dict]:
        slug = slug or self._active_slug
        if not slug:
            return []
        self.index_project(slug, force=False)
        conn, fts = self._open_index(slug)
        limit = max(1, int(limit or self.retrieval_chunks))
        try:
            rows = []
            if fts:
                match = _fts_query(query)
                if match:
                    try:
                        rows = conn.execute(
                            """
                            SELECT path,start_line,end_line,content,bm25(chunks) AS score
                            FROM chunks WHERE chunks MATCH ?
                            ORDER BY score ASC LIMIT ?
                            """,
                            (match, limit),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
            if not rows:
                terms = re.findall(r"[A-Za-z0-9_\-]{3,}", query.lower())[:5]
                if terms:
                    table = "chunks" if fts else "chunks_plain"
                    where = " OR ".join("lower(content) LIKE ?" for _ in terms)
                    rows = conn.execute(
                        f"SELECT path,start_line,end_line,content,0 AS score FROM {table} WHERE {where} LIMIT ?",
                        [*(f"%{term}%" for term in terms), limit],
                    ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def list_assets(
        self,
        *,
        slug: str | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        slug = slug or self._active_slug
        if not slug:
            return []
        self.index_project(slug, force=False)
        conn, _ = self._open_index(slug)
        try:
            params: list[object] = []
            where = ""
            if kind:
                where = "WHERE kind=?"
                params.append(str(kind).lower())
            params.append(max(1, min(1000, int(limit))))
            rows = conn.execute(
                f"SELECT path,extension,kind,mtime_ns,size FROM assets {where} ORDER BY path LIMIT ?",
                params,
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def search_assets(
        self,
        query: str,
        *,
        slug: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        slug = slug or self._active_slug
        if not slug:
            return []
        self.index_project(slug, force=False)
        terms = re.findall(r"[A-Za-z0-9_.-]{2,}", str(query).lower())[:8]
        conn, _ = self._open_index(slug)
        try:
            clauses = []
            params: list[object] = []
            if terms:
                clauses.append("(" + " OR ".join("lower(path) LIKE ?" for _ in terms) + ")")
                params.extend(f"%{term}%" for term in terms)
            if kind:
                clauses.append("kind=?")
                params.append(str(kind).lower())
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            params.append(max(1, min(100, int(limit))))
            rows = conn.execute(
                f"SELECT path,extension,kind,mtime_ns,size FROM assets {where} ORDER BY mtime_ns DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def resolve_asset_path(self, relative_path: str, *, slug: str | None = None) -> Path:
        slug = slug or self._active_slug
        if not slug:
            raise RuntimeError("No active project")
        workspace = self._workspace(slug).resolve()
        candidate = (workspace / Path(str(relative_path))).resolve()
        try:
            candidate.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("Asset path escapes active project") from exc
        if not candidate.is_file():
            raise FileNotFoundError(str(candidate))
        return candidate

    def context_for_query(self, query: str) -> str:
        slug = self._active_slug
        if not slug:
            return ""
        metadata = self._read_metadata(slug)
        memory = self.project_memory_for_context(slug).strip()
        facts = self.current_facts_text(slug).strip()
        pieces = [
            f"Active project: {metadata.get('name', slug)} (slug: {slug})",
            f"Workspace: {self._workspace(slug)}",
        ]
        budget = max(1000, self.context_char_budget)
        used = sum(len(piece) for piece in pieces)

        if facts:
            fact_cap = max(800, min(len(facts), budget // 3))
            fact_piece = facts[:fact_cap]
            pieces.append(fact_piece)
            used += len(fact_piece)

        if memory:
            remaining = max(0, min(len(memory), max(800, budget // 3)))
            memory_piece = "Project memory:\n" + memory[:remaining]
            pieces.append(memory_piece)
            used += len(memory_piece)

        matching_assets = self.search_assets(query, slug=slug, limit=6)
        if matching_assets:
            asset_lines = ["Relevant project assets:"]
            for asset in matching_assets:
                asset_lines.append(
                    f"- {asset['path']} ({asset['kind']}, {asset['size']} bytes)"
                )
            asset_piece = "\n".join(asset_lines)
            if used + len(asset_piece) < budget:
                pieces.append(asset_piece)
                used += len(asset_piece)

        for item in self.search_project(query, slug=slug, limit=self.retrieval_chunks):
            header = f"File {item['path']} lines {item['start_line']}-{item['end_line']}:\n"
            remaining = budget - used - len(header)
            if remaining <= 200:
                break
            content = str(item["content"])
            snippet = header + content[:remaining]
            pieces.append(snippet)
            used += len(snippet)
            if used >= budget:
                break

        return "\n\n".join(pieces)

    # ------------------------------------------------------------------
    # Sandboxed project file tools used by coding models
    # ------------------------------------------------------------------
    def _safe_workspace_path(self, relative_path: str, *, must_exist: bool = False) -> Path:
        slug = self._active_slug
        if not slug:
            raise RuntimeError("No active project")
        relative = Path(str(relative_path).strip() or ".")
        if relative.is_absolute():
            raise ValueError("Absolute paths are not allowed")
        workspace = self._workspace(slug).resolve()
        candidate = (workspace / relative).resolve()
        try:
            candidate.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("Path escapes the active project workspace") from exc
        if must_exist and not candidate.exists():
            raise FileNotFoundError(str(relative_path))
        return candidate

    def list_workspace_files(self, subpath: str = "", max_items: int = 200) -> str:
        base = self._safe_workspace_path(subpath or ".", must_exist=True)
        workspace = self._workspace(self._active_slug).resolve()
        if base.is_file():
            return base.relative_to(workspace).as_posix()
        rows = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            root_path = Path(root)
            for filename in sorted(files):
                path = root_path / filename
                try:
                    rel = path.relative_to(workspace).as_posix()
                except ValueError:
                    continue
                rows.append(rel)
                if len(rows) >= max(1, int(max_items)):
                    return "\n".join(rows) + "\n[truncated]"
        return "\n".join(rows) if rows else "[no files]"

    def read_workspace_file(self, relative_path: str, start_line: int = 1, end_line: int = 240) -> str:
        path = self._safe_workspace_path(relative_path, must_exist=True)
        if not path.is_file():
            raise ValueError("Requested path is not a file")
        if path.stat().st_size > self.max_file_bytes:
            raise ValueError("File is larger than the project-tool read limit")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("File is not UTF-8 text") from exc
        lines = text.splitlines()
        start = max(1, int(start_line))
        end = max(start, min(int(end_line), start + 399))
        selected = lines[start - 1 : end]
        return "\n".join(f"{i}: {line}" for i, line in enumerate(selected, start=start))

    def write_workspace_file(self, relative_path: str, content: str) -> str:
        path = self._safe_workspace_path(relative_path, must_exist=False)
        if len(content.encode("utf-8")) > self.max_file_bytes * 2:
            raise ValueError("Refusing an unexpectedly large generated file")
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, content)
        self._last_index_time[self._active_slug] = 0.0
        return f"wrote {path.relative_to(self._workspace(self._active_slug)).as_posix()}"

    def replace_workspace_text(self, relative_path: str, old_text: str, new_text: str, count: int = 1) -> str:
        path = self._safe_workspace_path(relative_path, must_exist=True)
        text = path.read_text(encoding="utf-8")
        occurrences = text.count(old_text)
        if occurrences == 0:
            raise ValueError("Exact old_text was not found; re-read the file before editing")
        replace_count = max(1, int(count))
        updated = text.replace(old_text, new_text, replace_count)
        _atomic_write_text(path, updated)
        self._last_index_time[self._active_slug] = 0.0
        return f"replaced {min(occurrences, replace_count)} occurrence(s) in {relative_path}"

    def search_workspace_text(self, query: str, limit: int = 5) -> str:
        results = self.search_project(query, limit=max(1, min(int(limit), 10)))
        if not results:
            return "[no matching project text]"
        parts = []
        for item in results:
            content = str(item["content"])
            if len(content) > 2200:
                content = content[:2200] + "\n[truncated]"
            parts.append(
                f"{item['path']}:{item['start_line']}-{item['end_line']}\n{content}"
            )
        return "\n\n".join(parts)

    def git_diff(self, max_chars: int = 12000) -> str:
        slug = self._active_slug
        if not slug or not self.ensure_git(slug):
            return "[git unavailable]"
        result = self._git(slug, "diff", "--", ".", check=False)
        text = result.stdout or result.stderr or "[no uncommitted diff]"
        return text[:max_chars] + ("\n[truncated]" if len(text) > max_chars else "")

    def run_project_check(self, kind: str, timeout_seconds: int = 90) -> str:
        """Run one of a small fixed set of local project checks, never arbitrary shell."""
        slug = self._active_slug
        if not slug:
            return "No active project."
        normalized = re.sub(r"[^a-z0-9]+", "-", kind.strip().lower()).strip("-")
        commands = {
            "python-compile": [sys.executable, "-m", "compileall", "-q", "."],
            "pytest": [sys.executable, "-m", "pytest", "-q"],
            "npm-test": ["npm", "test", "--", "--runInBand"],
            "cargo-test": ["cargo", "test", "--quiet"],
            "go-test": ["go", "test", "./..."],
        }
        command = commands.get(normalized)
        if command is None:
            return "Unsupported check. Use python-compile, pytest, npm-test, cargo-test, or go-test."
        if shutil.which(command[0]) is None and command[0] != sys.executable:
            return f"{command[0]} is not installed."
        try:
            result = subprocess.run(
                command,
                cwd=self._workspace(slug),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=max(5, int(timeout_seconds)),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"Check timed out after {timeout_seconds} seconds."
        output = result.stdout.strip()
        if len(output) > 10000:
            output = output[-10000:] + "\n[output truncated to final 10000 characters]"
        return f"exit={result.returncode}\n{output or '[no output]'}"

    def undo_last_bemo_change(self) -> dict:
        slug = self._active_slug
        if not slug or not self.ensure_git(slug):
            return {"ok": False, "reason": "git-unavailable"}
        subject = self._git(slug, "log", "-1", "--pretty=%s", check=False).stdout.strip()
        if not subject.startswith("Bemo:"):
            return {"ok": False, "reason": "latest-commit-is-not-bemo", "subject": subject}
        result = self._git(slug, "revert", "--no-edit", "HEAD", check=False)
        if result.returncode != 0:
            return {"ok": False, "reason": result.stderr.strip() or "revert-failed"}
        self.index_project(slug, force=True)
        return {"ok": True, "reverted": subject}

    # ------------------------------------------------------------------
    # Offline local Git snapshots
    # ------------------------------------------------------------------
    @staticmethod
    def git_available() -> bool:
        return shutil.which("git") is not None

    def _git(self, slug: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self._workspace(slug),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=check,
        )

    def ensure_git(self, slug: str | None = None) -> bool:
        slug = slug or self._active_slug
        if not slug or not self.git_available():
            return False
        workspace = self._workspace(slug)
        if not (workspace / ".git").exists():
            self._git(slug, "init")
        # Local-only identity; no GitHub or network connection is required.
        self._git(slug, "config", "user.name", self.git_name)
        self._git(slug, "config", "user.email", self.git_email)
        return True

    def snapshot(self, message: str, *, slug: str | None = None) -> dict:
        slug = slug or self._active_slug
        if not slug:
            return {"ok": False, "reason": "no-active-project"}
        if not self.ensure_git(slug):
            return {"ok": False, "reason": "git-not-installed"}
        self._git(slug, "add", "-A")
        diff = self._git(slug, "diff", "--cached", "--quiet", check=False)
        if diff.returncode == 0:
            return {"ok": True, "committed": False, "reason": "no-changes"}
        result = self._git(slug, "commit", "-m", message, check=False)
        if result.returncode != 0:
            return {"ok": False, "reason": result.stderr.strip() or "git-commit-failed"}
        head = self._git(slug, "rev-parse", "--short", "HEAD").stdout.strip()
        return {"ok": True, "committed": True, "commit": head}

    # ------------------------------------------------------------------
    # Verified removable-drive project/file import
    # ------------------------------------------------------------------
    _PROJECT_IMPORT_CONTAINER_NAMES = {
        "bemo exports", "bemo export", "projects", "project", "repos",
        "repositories", "repository", "source", "sources",
    }
    _PROJECT_IMPORT_SKIP_NAMES = {
        "system volume information", "$recycle.bin", "lost+found",
        "games", "gamecube", "game cube", "roms", "bemo backups", "bemo backup",
    }

    @staticmethod
    def _normalized_label(value: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value).lower()).split())

    def _next_project_slug(self, name: str) -> str:
        base = _slugify(name)
        slug = base
        counter = 2
        while self._project_dir(slug).exists():
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    @staticmethod
    def _tree_manifest(root: Path) -> tuple[dict[str, str], int]:
        """Hash every file/symlink under root for copy verification."""
        hashes: dict[str, str] = {}
        total_bytes = 0
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = os.readlink(path)
                payload = ("SYMLINK\0" + target).encode("utf-8", errors="surrogateescape")
                hashes[rel] = hashlib.sha256(payload).hexdigest()
                total_bytes += len(payload)
            elif path.is_file():
                hashes[rel] = _sha256(path)
                try:
                    total_bytes += int(path.stat().st_size)
                except OSError:
                    pass
        return hashes, total_bytes

    @classmethod
    def _copytree_verified(cls, source: Path, destination: Path) -> dict:
        before, total_bytes = cls._tree_manifest(source)
        shutil.copytree(source, destination, symlinks=True)
        after, copied_bytes = cls._tree_manifest(destination)
        verified = before == after and total_bytes == copied_bytes
        return {
            "verified": verified,
            "file_count": len(before),
            "bytes": total_bytes,
        }

    @staticmethod
    def _validate_export_manifest(source: Path) -> bool | None:
        manifest = source / "SHA256SUMS.txt"
        if not manifest.is_file():
            return None
        try:
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line:
                    continue
                digest, rel = line.split("  ", 1)
                rel_path = Path(rel)
                if rel_path.is_absolute() or ".." in rel_path.parts:
                    return False
                target = source / rel_path
                if not target.is_file() or _sha256(target) != digest.strip().lower():
                    return False
            return True
        except (OSError, ValueError):
            return False

    def discover_project_candidates(self, roots: list[Path] | tuple[Path, ...]) -> list[dict]:
        """Find sensible top-level project folders without crawling the whole disk."""
        candidates: list[dict] = []
        seen: set[str] = set()

        def add_candidate(path: Path, drive: Path) -> None:
            try:
                resolved = path.resolve()
            except OSError:
                return
            key = str(resolved)
            if key in seen or not resolved.is_dir():
                return
            seen.add(key)
            bemo_bundle = (resolved / "project.json").is_file() and (resolved / "workspace").is_dir()
            name = resolved.name or drive.name or "USB Project"
            if bemo_bundle:
                try:
                    meta = json.loads((resolved / "project.json").read_text(encoding="utf-8"))
                    if isinstance(meta, dict) and str(meta.get("name") or "").strip():
                        name = str(meta["name"]).strip()
                except (OSError, ValueError, TypeError):
                    pass
            workspace = resolved / "workspace" if bemo_bundle else resolved
            candidates.append({
                "name": name,
                "source_name": resolved.name,
                "path": str(resolved),
                "drive": str(drive),
                "bemo_bundle": bemo_bundle,
                "has_git": (workspace / ".git").exists(),
            })

        for root in roots:
            try:
                drive = Path(root).expanduser().resolve()
            except OSError:
                continue
            if not drive.is_dir() or not os.access(drive, os.R_OK):
                continue
            if (drive / "project.json").is_file() and (drive / "workspace").is_dir():
                add_candidate(drive, drive)
                continue
            try:
                visible = [
                    child for child in sorted(drive.iterdir(), key=lambda p: p.name.lower())
                    if not child.name.startswith(".")
                ]
            except OSError:
                continue
            dirs = [child for child in visible if child.is_dir()]
            for child in dirs:
                label = self._normalized_label(child.name)
                if label in self._PROJECT_IMPORT_SKIP_NAMES or child.name.endswith(".partial"):
                    continue
                if label in self._PROJECT_IMPORT_CONTAINER_NAMES:
                    try:
                        nested = [
                            item for item in sorted(child.iterdir(), key=lambda p: p.name.lower())
                            if item.is_dir() and not item.name.startswith(".") and not item.name.endswith(".partial")
                        ]
                    except OSError:
                        nested = []
                    for item in nested:
                        add_candidate(item, drive)
                else:
                    add_candidate(child, drive)
            # A USB containing loose project files and no candidate directory is
            # itself a valid project source.
            if not dirs and any(child.is_file() for child in visible):
                add_candidate(drive, drive)
        return candidates

    def _select_project_candidate(self, candidates: list[dict], selection: str | None) -> tuple[dict | None, list[dict]]:
        if not selection:
            return (candidates[0], candidates) if len(candidates) == 1 else (None, candidates)
        wanted = self._normalized_label(selection)
        wanted_slug = _slugify(selection)
        exact = []
        partial = []
        for item in candidates:
            labels = {
                self._normalized_label(item.get("name", "")),
                self._normalized_label(item.get("source_name", "")),
            }
            slugs = {_slugify(item.get("name", "")), _slugify(item.get("source_name", ""))}
            if wanted in labels or wanted_slug in slugs:
                exact.append(item)
            elif any(wanted and (wanted in label or label in wanted) for label in labels):
                partial.append(item)
        matches = exact or partial
        return (matches[0], matches) if len(matches) == 1 else (None, matches)

    def _write_new_project_scaffold(self, project_dir: Path, *, name: str, slug: str, extra: dict | None = None) -> dict:
        metadata = {
            "name": name.strip() or slug,
            "slug": slug,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "last_opened_at": _utc_now(),
            "project_summary_last_message_id": 0,
            **dict(extra or {}),
        }
        _atomic_write_json(project_dir / "project.json", metadata)
        _atomic_write_text(
            project_dir / "memory.md",
            f"# Project Memory: {metadata['name']}\n\n"
            "## Current State\n\nDurable current state will be maintained here.\n\n"
            "## Locked Decisions\n\n## Requirements and Constraints\n\n"
            "## Known Issues\n\n## Important Files and Assets\n\n"
            "## Pending Work\n\n## Historical / Superseded\n\n",
        )
        _atomic_write_json(project_dir / "facts.json", {"facts": [], "updated_at": _utc_now()})
        return metadata

    def import_project_from_removable(
        self,
        roots: list[Path] | tuple[Path, ...],
        *,
        selection: str | None = None,
    ) -> dict:
        """Copy one removable-drive project into Bemo, verify it, and open it."""
        candidates = self.discover_project_candidates(roots)
        if not candidates:
            return {"ok": False, "reason": "no-project-candidates", "candidates": []}
        candidate, matches = self._select_project_candidate(candidates, selection)
        if candidate is None:
            reason = "selection-not-found" if selection and not matches else "multiple-candidates"
            return {
                "ok": False,
                "reason": reason,
                "candidate_count": len(matches if selection else candidates),
                "candidates": matches if selection else candidates,
            }

        source = Path(candidate["path"])
        is_bundle = bool(candidate.get("bemo_bundle"))
        source_manifest_ok = self._validate_export_manifest(source) if is_bundle else None
        if source_manifest_ok is False:
            return {"ok": False, "reason": "source-manifest-invalid", "candidate": candidate}

        imported_name = str(candidate.get("name") or source.name or "Imported Project").strip()
        slug = self._next_project_slug(imported_name)
        final_dir = self._project_dir(slug)
        stage = self.import_staging_root / f"{slug}-{uuid.uuid4().hex}.partial"
        shutil.rmtree(stage, ignore_errors=True)
        existing_git = False
        try:
            if is_bundle:
                result = self._copytree_verified(source, stage)
                if not result["verified"]:
                    raise RuntimeError("copy-verification-failed")
                workspace = stage / "workspace"
                existing_git = (workspace / ".git").exists()
                for stale in ("index.sqlite3", "index.sqlite3-wal", "index.sqlite3-shm"):
                    try:
                        (stage / stale).unlink()
                    except FileNotFoundError:
                        pass
                try:
                    metadata = json.loads((stage / "project.json").read_text(encoding="utf-8"))
                    if not isinstance(metadata, dict):
                        metadata = {}
                except (OSError, ValueError, TypeError):
                    metadata = {}
                metadata.update({
                    "name": imported_name,
                    "slug": slug,
                    "updated_at": _utc_now(),
                    "last_opened_at": _utc_now(),
                    "imported_at": _utc_now(),
                    "import_source": str(source),
                    "import_verified": True,
                    "imported_bemo_bundle": True,
                })
                metadata.setdefault("created_at", _utc_now())
                metadata.setdefault("project_summary_last_message_id", 0)
                _atomic_write_json(stage / "project.json", metadata)
                if not (stage / "memory.md").is_file():
                    self._write_new_project_scaffold(stage, name=imported_name, slug=slug, extra=metadata)
                elif not (stage / "facts.json").is_file():
                    _atomic_write_json(stage / "facts.json", {"facts": [], "updated_at": _utc_now()})
            else:
                stage.mkdir(parents=True, exist_ok=False)
                workspace = stage / "workspace"
                result = self._copytree_verified(source, workspace)
                if not result["verified"]:
                    raise RuntimeError("copy-verification-failed")
                existing_git = (workspace / ".git").exists()
                metadata = self._write_new_project_scaffold(
                    stage,
                    name=imported_name,
                    slug=slug,
                    extra={
                        "imported_at": _utc_now(),
                        "import_source": str(source),
                        "import_verified": True,
                        "imported_bemo_bundle": False,
                    },
                )

            os.replace(stage, final_dir)
            self._active_slug = slug
            self._save_state()
            if self.auto_git:
                self.ensure_git(slug)
                # Preserve an imported repository's exact commit history. Only a
                # folder without Git gets Bemo's initial snapshot.
                if not existing_git:
                    self.snapshot("Initial imported project", slug=slug)
            self.index_project(slug, force=True)
            metadata = self._read_metadata(slug)
            return {
                "ok": True,
                "slug": slug,
                "name": metadata.get("name", imported_name),
                "source": str(source),
                "verified": True,
                "file_count": int(result.get("file_count") or 0),
                "bytes": int(result.get("bytes") or 0),
                "preserved_git": existing_git,
                "bemo_bundle": is_bundle,
                "source_manifest_verified": source_manifest_ok is True,
            }
        except Exception as exc:
            shutil.rmtree(stage, ignore_errors=True)
            if final_dir.exists() and not (final_dir / "project.json").is_file():
                shutil.rmtree(final_dir, ignore_errors=True)
            return {"ok": False, "reason": str(exc) or "project-import-failed", "candidate": candidate}

    @staticmethod
    def _unique_import_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        counter = 2
        while True:
            candidate = path.with_name(f"{stem} (imported {counter}){suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def import_files_from_removable(
        self,
        roots: list[Path] | tuple[Path, ...],
        *,
        slug: str | None = None,
    ) -> dict:
        """Merge removable-drive files into an existing project without overwrites."""
        slug = slug or self._active_slug
        if not slug or not self._project_dir(slug).is_dir():
            return {"ok": False, "reason": "no-active-project"}
        workspace = self._workspace(slug)
        workspace.mkdir(parents=True, exist_ok=True)
        imported = duplicates = collisions = errors = skipped_symlinks = 0
        imported_bytes = 0
        imported_names: list[str] = []  # so BMO can say which (live 2026-10-23 a brain guessed them)
        readable_root_found = False

        skip_labels = set(self._PROJECT_IMPORT_SKIP_NAMES) | {"bemo exports", "bemo export"}

        def copy_file(source_file: Path, destination: Path) -> None:
            nonlocal imported, duplicates, collisions, errors, imported_bytes, skipped_symlinks
            try:
                if source_file.is_symlink():
                    skipped_symlinks += 1
                    return
                if destination.exists() and destination.is_file():
                    if _sha256(source_file) == _sha256(destination):
                        duplicates += 1
                        return
                    destination = self._unique_import_path(destination)
                    collisions += 1
                elif destination.exists() and destination.is_dir():
                    destination = self._unique_import_path(destination.with_name(source_file.name))
                    collisions += 1
                destination.parent.mkdir(parents=True, exist_ok=True)
                partial = destination.with_name(destination.name + f".bemo-partial-{uuid.uuid4().hex}")
                shutil.copy2(source_file, partial)
                if _sha256(source_file) != _sha256(partial):
                    partial.unlink(missing_ok=True)
                    errors += 1
                    return
                os.replace(partial, destination)
                imported += 1
                imported_names.append(destination.relative_to(workspace).as_posix())
                try:
                    imported_bytes += int(destination.stat().st_size)
                except OSError:
                    pass
            except Exception:
                errors += 1

        def copy_tree(source_dir: Path, dest_dir: Path) -> None:
            nonlocal collisions, errors
            try:
                if dest_dir.exists() and dest_dir.is_file():
                    dest_dir = self._unique_import_path(dest_dir)
                    collisions += 1
                dest_dir.mkdir(parents=True, exist_ok=True)
                children = sorted(source_dir.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                errors += 1
                return
            for source_path in children:
                if source_path.name == ".git":
                    continue
                if source_path.name.startswith(".") and source_path.name != ".env":
                    continue
                destination = dest_dir / source_path.name
                if source_path.is_dir() and not source_path.is_symlink():
                    copy_tree(source_path, destination)
                elif source_path.is_file() or source_path.is_symlink():
                    copy_file(source_path, destination)

        for raw_root in roots:
            try:
                root = Path(raw_root).expanduser().resolve()
            except OSError:
                continue
            if not root.is_dir() or not os.access(root, os.R_OK):
                continue
            readable_root_found = True
            try:
                visible = [
                    child for child in sorted(root.iterdir(), key=lambda p: p.name.lower())
                    if not child.name.startswith(".")
                    and self._normalized_label(child.name) not in skip_labels
                ]
            except OSError:
                visible = []
            dirs = [item for item in visible if item.is_dir()]
            files = [item for item in visible if item.is_file() or item.is_symlink()]
            # A single wrapper directory is treated as "the files on this USB"
            # and its contents land at the project root rather than adding an
            # unnecessary wrapper folder.
            if len(dirs) == 1 and not files:
                wrapper = dirs[0]
                try:
                    children = [child for child in sorted(wrapper.iterdir(), key=lambda p: p.name.lower()) if child.name != ".git"]
                except OSError:
                    children = []
                for child in children:
                    if child.is_dir() and not child.is_symlink():
                        copy_tree(child, workspace / child.name)
                    else:
                        copy_file(child, workspace / child.name)
            else:
                for item in visible:
                    if item.is_dir() and not item.is_symlink():
                        copy_tree(item, workspace / item.name)
                    else:
                        copy_file(item, workspace / item.name)

        if not readable_root_found:
            return {"ok": False, "reason": "no-removable-drive"}
        self.index_project(slug, force=True)
        return {
            "ok": errors == 0,
            "reason": "partial-errors" if errors else "",
            "project_slug": slug,
            "imported_count": imported,
            "duplicate_count": duplicates,
            "collision_count": collisions,
            "error_count": errors,
            "skipped_symlink_count": skipped_symlinks,
            "bytes": imported_bytes,
            "imported_files": imported_names[:200],
        }

    # ------------------------------------------------------------------
    # Verified selected file/folder export to removable media
    # ------------------------------------------------------------------
    @staticmethod
    def _unique_export_path(path: Path) -> Path:
        if not path.exists():
            return path
        if path.is_dir():
            stem, suffix = path.name, ""
        else:
            stem, suffix = path.stem, path.suffix
        counter = 2
        while True:
            name = f"{stem} (exported {counter}){suffix}"
            candidate = path.with_name(name)
            if not candidate.exists():
                return candidate
            counter += 1

    def _resolve_workspace_export_selector(self, slug: str, selector: str) -> dict:
        workspace = self._workspace(slug).resolve()
        raw = str(selector or "").strip().strip('"').strip("'")
        if not raw:
            return {"ok": False, "reason": "empty-selector", "matches": []}
        requested = Path(raw).expanduser()
        if requested.is_absolute() or ".." in requested.parts:
            return {"ok": False, "reason": "outside-project", "matches": []}

        direct = (workspace / requested).resolve()
        try:
            direct.relative_to(workspace)
        except ValueError:
            return {"ok": False, "reason": "outside-project", "matches": []}
        if direct.exists() and direct.name != ".git" and ".git" not in direct.relative_to(workspace).parts:
            return {"ok": True, "path": direct, "relative": direct.relative_to(workspace).as_posix()}

        wanted_text = raw.replace("\\", "/").strip("/").lower()
        wanted_name = Path(wanted_text).name
        wanted_norm = self._normalized_label(wanted_name)
        exact_rel: list[Path] = []
        exact_name: list[Path] = []
        normalized_name: list[Path] = []
        try:
            paths = sorted(workspace.rglob("*"), key=lambda q: q.relative_to(workspace).as_posix().lower())
        except OSError:
            paths = []
        for candidate in paths:
            try:
                rel = candidate.relative_to(workspace)
            except ValueError:
                continue
            if ".git" in rel.parts or candidate.name.startswith(".bemo-partial-"):
                continue
            rel_text = rel.as_posix().lower()
            if rel_text == wanted_text:
                exact_rel.append(candidate)
            elif candidate.name.lower() == wanted_name:
                exact_name.append(candidate)
            elif wanted_norm and self._normalized_label(candidate.name) == wanted_norm:
                normalized_name.append(candidate)
        matches = exact_rel or exact_name or normalized_name
        if len(matches) == 1:
            chosen = matches[0].resolve()
            return {"ok": True, "path": chosen, "relative": chosen.relative_to(workspace).as_posix()}
        if len(matches) > 1:
            return {
                "ok": False,
                "reason": "multiple-matches",
                "matches": [item.relative_to(workspace).as_posix() for item in matches[:12]],
            }
        return {"ok": False, "reason": "not-found", "matches": []}

    @staticmethod
    def _tree_has_symlink(root: Path) -> bool:
        if root.is_symlink():
            return True
        try:
            return any(path.is_symlink() for path in root.rglob("*"))
        except OSError:
            return True

    def export_project_items_to_removable(
        self,
        selectors: list[str] | tuple[str, ...],
        *,
        slug: str | None = None,
        roots: list[Path] | tuple[Path, ...] | None = None,
    ) -> dict:
        """Copy selected active-project files/folders to USB and verify every byte.

        Sources are always inside the active project workspace. Originals are never
        moved or modified. Existing identical destinations are skipped; different
        name collisions get a unique ``(exported N)`` destination.
        """
        slug = slug or self._active_slug
        if not slug or not self._project_dir(slug).is_dir():
            return {"ok": False, "reason": "no-active-project"}
        selectors = [str(item).strip() for item in selectors if str(item).strip()]
        if not selectors:
            return {"ok": False, "reason": "no-selection", "matches": []}

        if roots is None:
            drives = self.removable_drives()
        else:
            drives = []
            for raw in roots:
                try:
                    path = Path(raw).expanduser().resolve()
                except OSError:
                    continue
                if path.is_dir() and os.access(path, os.W_OK):
                    drives.append(path)
        if not drives:
            return {"ok": False, "reason": "no-removable-drive"}
        drive = drives[0]
        export_root = drive / "Bemo Files"
        export_root.mkdir(parents=True, exist_ok=True)

        resolved: list[dict] = []
        unresolved: list[dict] = []
        seen_sources: set[str] = set()
        for selector in selectors:
            match = self._resolve_workspace_export_selector(slug, selector)
            if not match.get("ok"):
                unresolved.append({"selector": selector, **match})
                continue
            source = Path(match["path"])
            key = str(source)
            if key in seen_sources:
                continue
            seen_sources.add(key)
            resolved.append({"selector": selector, "source": source, "relative": str(match["relative"])})

        if unresolved:
            # Deterministic export is all-or-nothing with respect to selection
            # resolution: never silently export only part of what the user named.
            return {
                "ok": False,
                "reason": "selection-error",
                "unresolved": unresolved,
                "resolved_count": len(resolved),
            }
        if not resolved:
            return {"ok": False, "reason": "no-selection"}

        exported: list[dict] = []
        duplicates = collisions = total_files = total_bytes = 0
        for item in resolved:
            source = item["source"]
            if source.is_symlink() or (source.is_dir() and self._tree_has_symlink(source)):
                return {
                    "ok": False,
                    "reason": "symlink-not-supported",
                    "source": item["relative"],
                    "exported": exported,
                }
            destination = export_root / source.name
            duplicate = False
            if destination.exists():
                try:
                    if source.is_file() and destination.is_file():
                        duplicate = _sha256(source) == _sha256(destination)
                    elif source.is_dir() and destination.is_dir():
                        src_manifest, src_bytes = self._tree_manifest(source)
                        dst_manifest, dst_bytes = self._tree_manifest(destination)
                        duplicate = src_manifest == dst_manifest and src_bytes == dst_bytes
                except OSError:
                    duplicate = False
                if duplicate:
                    duplicates += 1
                    exported.append({
                        "source": item["relative"],
                        "destination": str(destination),
                        "verified": True,
                        "duplicate": True,
                    })
                    continue
                destination = self._unique_export_path(destination)
                collisions += 1

            token = uuid.uuid4().hex
            if source.is_file():
                partial = destination.with_name(destination.name + f".bemo-partial-{token}")
                try:
                    shutil.copy2(source, partial)
                    source_hash = _sha256(source)
                    dest_hash = _sha256(partial)
                    if source_hash != dest_hash:
                        partial.unlink(missing_ok=True)
                        return {"ok": False, "reason": "verification-failed", "source": item["relative"], "exported": exported}
                    os.replace(partial, destination)
                    size = int(source.stat().st_size)
                    total_files += 1
                    total_bytes += size
                    exported.append({
                        "source": item["relative"],
                        "destination": str(destination),
                        "verified": True,
                        "duplicate": False,
                        "sha256": source_hash,
                        "files": 1,
                        "bytes": size,
                    })
                finally:
                    try:
                        partial.unlink()
                    except FileNotFoundError:
                        pass
            elif source.is_dir():
                partial = destination.with_name(destination.name + f".bemo-partial-{token}")
                shutil.rmtree(partial, ignore_errors=True)
                try:
                    before, source_bytes = self._tree_manifest(source)
                    shutil.copytree(source, partial, symlinks=False)
                    after, copied_bytes = self._tree_manifest(partial)
                    if before != after or source_bytes != copied_bytes:
                        shutil.rmtree(partial, ignore_errors=True)
                        return {"ok": False, "reason": "verification-failed", "source": item["relative"], "exported": exported}
                    os.replace(partial, destination)
                    total_files += len(before)
                    total_bytes += source_bytes
                    exported.append({
                        "source": item["relative"],
                        "destination": str(destination),
                        "verified": True,
                        "duplicate": False,
                        "files": len(before),
                        "bytes": source_bytes,
                    })
                finally:
                    shutil.rmtree(partial, ignore_errors=True)
            else:
                return {"ok": False, "reason": "unsupported-source", "source": item["relative"], "exported": exported}

        return {
            "ok": True,
            "project_slug": slug,
            "drive": str(drive),
            "export_root": str(export_root),
            "exported": exported,
            "selection_count": len(resolved),
            "new_export_count": len([item for item in exported if not item.get("duplicate")]),
            "duplicate_count": duplicates,
            "collision_count": collisions,
            "file_count": total_files,
            "bytes": total_bytes,
        }

    # ------------------------------------------------------------------
    # Removable-drive export with verification
    # ------------------------------------------------------------------
    def removable_drives(self) -> list[Path]:
        if self.export_root_override:
            self.export_root_override.mkdir(parents=True, exist_ok=True)
            return [self.export_root_override]

        found: list[Path] = []
        if os.name == "nt":
            DRIVE_REMOVABLE = 2
            for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
                root = f"{letter}:\\"
                try:
                    if ctypes.windll.kernel32.GetDriveTypeW(root) == DRIVE_REMOVABLE:
                        found.append(Path(root))
                except Exception:
                    continue
        elif sys.platform == "darwin":
            volumes = Path("/Volumes")
            if volumes.exists():
                for child in volumes.iterdir():
                    if child.is_dir():
                        found.append(child)
        elif psutil is not None:
            try:
                for part in psutil.disk_partitions(all=False):
                    mount = Path(part.mountpoint)
                    text = str(mount)
                    if text.startswith("/media/") or text.startswith("/run/media/"):
                        found.append(mount)
            except Exception:
                pass

        # Preserve order while removing aliases/duplicates.
        unique = []
        seen = set()
        for path in found:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key not in seen and path.exists() and os.access(path, os.W_OK):
                seen.add(key)
                unique.append(path)
        return unique

    @staticmethod
    def _export_ignore(include_git: bool):
        ignored_names = {"index.sqlite3", "index.sqlite3-wal", "index.sqlite3-shm"}

        def ignore(directory: str, names: list[str]):
            ignored = {name for name in names if name in ignored_names}
            if not include_git and ".git" in names:
                ignored.add(".git")
            return ignored

        return ignore

    @staticmethod
    def _write_manifest(root: Path) -> tuple[Path, dict[str, str]]:
        hashes = {}
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS.txt":
                rel = path.relative_to(root).as_posix()
                hashes[rel] = _sha256(path)
        manifest = root / "SHA256SUMS.txt"
        _atomic_write_text(
            manifest,
            "".join(f"{digest}  {rel}\n" for rel, digest in hashes.items()),
        )
        return manifest, hashes

    @staticmethod
    def _verify_hashes(root: Path, hashes: dict[str, str]) -> bool:
        return all((root / rel).is_file() and _sha256(root / rel) == digest for rel, digest in hashes.items())

    def export_active_project(self) -> dict:
        slug = self._active_slug
        if not slug:
            return {"ok": False, "reason": "no-active-project"}
        drives = self.removable_drives()
        if not drives:
            return {"ok": False, "reason": "no-removable-drive"}

        self.index_project(slug, force=True)
        snapshot = self.snapshot(
            f"Export snapshot {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            slug=slug,
        ) if self.auto_git else {"ok": True, "committed": False}

        drive = drives[0]
        exports_root = drive / "Bemo Exports"
        exports_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        base_name = f"{slug}_{stamp}"
        final = exports_root / base_name
        partial = exports_root / (base_name + ".partial")
        if partial.exists():
            shutil.rmtree(partial)

        shutil.copytree(
            self._project_dir(slug),
            partial,
            symlinks=False,
            ignore=self._export_ignore(self.export_include_git),
        )
        _, hashes = self._write_manifest(partial)
        if not self._verify_hashes(partial, hashes):
            shutil.rmtree(partial, ignore_errors=True)
            return {"ok": False, "reason": "verification-failed"}
        os.replace(partial, final)

        zip_path = None
        if self.export_create_zip:
            zip_path = Path(shutil.make_archive(str(final), "zip", root_dir=final))
            # Write a checksum next to the ZIP so corruption after copying is visible.
            _atomic_write_text(
                zip_path.with_suffix(zip_path.suffix + ".sha256"),
                f"{_sha256(zip_path)}  {zip_path.name}\n",
            )

        return {
            "ok": True,
            "drive": str(drive),
            "folder": str(final),
            "zip": str(zip_path) if zip_path else None,
            "files": len(hashes),
            "git_snapshot": snapshot,
        }
