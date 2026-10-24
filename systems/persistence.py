"""Durable, model-independent conversation storage for Bemo.

Raw conversation is never used as the prompt wholesale.  It is stored on disk in
SQLite (WAL + FULL synchronous writes), while the chat layer keeps only a small
recent window in RAM and retrieves a few relevant older snippets when needed.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_utc_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _decayed_heat(
    stored_heat: float,
    last_used_at: str | datetime | None,
    *,
    half_life_days: float,
    now: datetime | None = None,
) -> float:
    """Return heat after time decay without mutating persistent state.

    Heat has a half-life rather than a hard age cutoff. A memory that was never
    meaningfully reused has no heat. Old but highly relevant memories can still
    be retrieved because heat is only a bounded reranking signal.
    """
    heat = max(0.0, float(stored_heat or 0.0))
    if heat <= 0.0:
        return 0.0
    last = _as_utc_datetime(last_used_at)
    if last is None:
        return heat
    current = _as_utc_datetime(now) or datetime.now(timezone.utc)
    elapsed_seconds = max(0.0, (current - last).total_seconds())
    half_life_seconds = max(1.0, float(half_life_days)) * 86400.0
    return heat * math.pow(0.5, elapsed_seconds / half_life_seconds)


def _memory_tier(heat: float, *, warm_threshold: float, hot_threshold: float) -> str:
    if heat >= float(hot_threshold):
        return "hot"
    if heat >= float(warm_threshold):
        return "warm"
    return "cold"


def _fts_query(text: str) -> str:
    # FTS5 query syntax is intentionally kept boring so dictated punctuation or
    # code operators cannot turn into a malformed MATCH expression.
    terms = []
    seen = set()
    for token in re.findall(r"[A-Za-z0-9_\-]{2,}", text.lower()):
        if token in seen:
            continue
        seen.add(token)
        terms.append('"' + token.replace('"', '""') + '"')
        if len(terms) >= 12:
            break
    return " OR ".join(terms)


class MemoryStore:
    """Transactional conversation archive plus lightweight retrieval."""

    def __init__(self, db_path: str | Path, backup_dir: str | Path | None = None):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir = (
            Path(backup_dir).expanduser()
            if backup_dir is not None
            else self.db_path.parent / "backups"
        )
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._fts_enabled = False
        try:
            self._initialize()
        except sqlite3.DatabaseError:
            # Preserve the damaged files for manual recovery, then fall back to
            # the newest known-good SQLite backup.  A corrupt archive should
            # never make Bemo unbootable or silently overwrite the evidence.
            self._conn.close()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            for candidate in (
                self.db_path,
                Path(str(self.db_path) + "-wal"),
                Path(str(self.db_path) + "-shm"),
            ):
                if candidate.exists():
                    try:
                        shutil.move(
                            str(candidate),
                            str(candidate.with_name(candidate.name + f".corrupt-{stamp}")),
                        )
                    except OSError:
                        pass
            backups = sorted(self.backup_dir.glob("conversation-*.sqlite3"))
            if backups:
                shutil.copy2(backups[-1], self.db_path)
            self._conn = self._connect()
            self._initialize()

    def _connect(self):
        conn = sqlite3.connect(
            str(self.db_path), timeout=10.0, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    cpu_name TEXT,
                    app_version TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                    content TEXT NOT NULL,
                    project_slug TEXT,
                    model_profile TEXT,
                    model_name TEXT,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    heat_score REAL NOT NULL DEFAULT 0.0,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_created
                    ON messages(created_at);
                CREATE INDEX IF NOT EXISTS idx_messages_project
                    ON messages(project_slug, id);

                CREATE TABLE IF NOT EXISTS conversation_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    summary TEXT NOT NULL DEFAULT '',
                    summary_last_message_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO conversation_state
                    (singleton, summary, summary_last_message_id, updated_at)
                    VALUES (1, '', 0, '');

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

            # In-place migration for archives created before adaptive memory heat.
            # ALTER TABLE is intentionally additive so an existing multi-year
            # conversation database remains readable without export/reimport.
            columns = {
                str(row["name"])
                for row in self._conn.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "last_used_at" not in columns:
                self._conn.execute("ALTER TABLE messages ADD COLUMN last_used_at TEXT")
            if "use_count" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0"
                )
            if "heat_score" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN heat_score REAL NOT NULL DEFAULT 0.0"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_last_used ON messages(last_used_at)"
            )
            try:
                self._conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                        content,
                        content='messages',
                        content_rowid='id'
                    );

                    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                        INSERT INTO messages_fts(rowid, content)
                        VALUES (new.id, new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content)
                        VALUES('delete', old.id, old.content);
                    END;
                    DROP TRIGGER IF EXISTS messages_au;
                    CREATE TRIGGER messages_au AFTER UPDATE OF content ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content)
                        VALUES('delete', old.id, old.content);
                        INSERT INTO messages_fts(rowid, content)
                        VALUES (new.id, new.content);
                    END;
                    """
                )
                # Rebuild once in case the DB predates the FTS table.
                count = self._conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0]
                message_count = self._conn.execute("SELECT count(*) FROM messages").fetchone()[0]
                if count != message_count:
                    self._conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False
            self._conn.commit()

    @property
    def fts_enabled(self) -> bool:
        return self._fts_enabled

    def start_session(self, *, cpu_name: str = "", app_version: str = "") -> str:
        session_id = uuid.uuid4().hex
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions(id, started_at, cpu_name, app_version) VALUES(?,?,?,?)",
                (session_id, _utc_now(), cpu_name, app_version),
            )
        return session_id

    def end_session(self, session_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET ended_at=? WHERE id=?",
                (_utc_now(), session_id),
            )

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        project_slug: str | None,
        model_profile: str,
        model_name: str,
    ) -> int:
        content = str(content).strip()
        if not content:
            return 0
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO messages(
                    session_id, created_at, role, content, project_slug,
                    model_profile, model_name
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    _utc_now(),
                    role,
                    content,
                    project_slug,
                    model_profile,
                    model_name,
                ),
            )
            return int(cursor.lastrowid)

    def load_recent(self, limit: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, role, content, project_slug, created_at
                FROM messages
                WHERE role IN ('user','assistant')
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def last_message_like(self, role: str, pattern: str) -> dict | None:
        """The newest message of `role` whose content matches the SQL LIKE `pattern`."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, role, content, created_at FROM messages WHERE role = ? AND content LIKE ? "
                "ORDER BY id DESC LIMIT 1", (role, pattern)).fetchone()
        return dict(row) if row else None

    def conversation_summary(self) -> tuple[str, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT summary, summary_last_message_id FROM conversation_state WHERE singleton=1"
            ).fetchone()
        return (str(row[0] or ""), int(row[1] or 0)) if row else ("", 0)

    def set_conversation_summary(self, summary: str, last_message_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE conversation_state
                SET summary=?, summary_last_message_id=?, updated_at=?
                WHERE singleton=1
                """,
                (summary.strip(), int(last_message_id), _utc_now()),
            )

    def messages_after(
        self,
        after_id: int,
        *,
        limit: int,
        before_id: int | None = None,
        project_slug: str | None = None,
    ) -> list[dict]:
        clauses = ["id > ?", "role IN ('user','assistant')"]
        params: list[object] = [int(after_id)]
        if before_id is not None:
            clauses.append("id <= ?")
            params.append(int(before_id))
        if project_slug is not None:
            clauses.append("project_slug = ?")
            params.append(project_slug)
        params.append(max(1, int(limit)))
        sql = f"""
            SELECT id, role, content, project_slug, created_at
            FROM messages
            WHERE {' AND '.join(clauses)}
            ORDER BY id ASC LIMIT ?
        """
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def search_messages(
        self,
        query: str,
        *,
        limit: int = 4,
        active_project_slug: str | None = None,
        exclude_ids: set[int] | None = None,
        heat_half_life_days: float = 30.0,
        heat_warm_threshold: float = 2.5,
        heat_hot_threshold: float = 6.0,
        heat_rank_max_boost: float = 2.0,
        candidate_multiplier: int = 8,
        now: datetime | None = None,
    ) -> list[dict]:
        """Retrieve relevant older messages with bounded adaptive-memory reranking.

        FTS/lexical relevance chooses the candidate pool first. Memory heat can
        only move a candidate a small number of rank positions, so relevance
        overrides age/frequency. Searching alone never changes heat; the caller
        must explicitly reinforce only snippets that were actually used.
        """
        exclude_ids = exclude_ids or set()
        requested = max(1, int(limit))
        multiplier = max(2, int(candidate_multiplier))
        fetch_limit = max(requested * multiplier, requested + len(exclude_ids) + 6)
        rows = []

        with self._lock:
            if self._fts_enabled:
                match = _fts_query(query)
                if match:
                    clauses = ["messages_fts MATCH ?", "m.role IN ('user','assistant')"]
                    params: list[object] = [match]
                    if active_project_slug:
                        clauses.append("(m.project_slug IS NULL OR m.project_slug = ?)")
                        params.append(active_project_slug)
                    params.append(fetch_limit)
                    try:
                        rows = self._conn.execute(
                            f"""
                            SELECT m.id, m.role, m.content, m.project_slug, m.created_at,
                                   m.last_used_at, m.use_count, m.heat_score,
                                   bm25(messages_fts) AS score
                            FROM messages_fts
                            JOIN messages m ON m.id = messages_fts.rowid
                            WHERE {' AND '.join(clauses)}
                            ORDER BY score ASC, m.id DESC
                            LIMIT ?
                            """,
                            params,
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = []

            if not rows:
                terms = re.findall(r"[A-Za-z0-9_\-]{3,}", query.lower())[:5]
                if terms:
                    clauses = ["role IN ('user','assistant')"]
                    params = []
                    term_clause = " OR ".join("lower(content) LIKE ?" for _ in terms)
                    clauses.append(f"({term_clause})")
                    params.extend(f"%{term}%" for term in terms)
                    if active_project_slug:
                        clauses.append("(project_slug IS NULL OR project_slug = ?)")
                        params.append(active_project_slug)
                    params.append(fetch_limit)
                    rows = self._conn.execute(
                        f"""
                        SELECT id, role, content, project_slug, created_at,
                               last_used_at, use_count, heat_score, 0 AS score
                        FROM messages
                        WHERE {' AND '.join(clauses)}
                        ORDER BY id DESC LIMIT ?
                        """,
                        params,
                    ).fetchall()

        candidates = []
        max_boost = max(0.0, float(heat_rank_max_boost))
        hot_threshold = max(0.001, float(heat_hot_threshold))
        for relevance_rank, row in enumerate(rows):
            item = dict(row)
            if int(item["id"]) in exclude_ids:
                continue
            effective_heat = _decayed_heat(
                item.get("heat_score", 0.0),
                item.get("last_used_at"),
                half_life_days=heat_half_life_days,
                now=now,
            )
            item["effective_heat"] = effective_heat
            item["memory_tier"] = _memory_tier(
                effective_heat,
                warm_threshold=heat_warm_threshold,
                hot_threshold=heat_hot_threshold,
            )
            # A hot memory may rise only a bounded number of positions. This
            # lets repeated usefulness matter without allowing familiarity to
            # overpower a much better semantic match.
            heat_fraction = min(1.0, effective_heat / hot_threshold)
            heat_boost = max_boost * heat_fraction
            project_boost = (
                0.75
                if active_project_slug and item.get("project_slug") == active_project_slug
                else 0.0
            )
            item["_adaptive_rank"] = float(relevance_rank) - heat_boost - project_boost
            item["_relevance_rank"] = relevance_rank
            candidates.append(item)

        candidates.sort(
            key=lambda item: (
                float(item["_adaptive_rank"]),
                int(item["_relevance_rank"]),
                -int(item["id"]),
            )
        )
        results = []
        for item in candidates[:requested]:
            item.pop("_adaptive_rank", None)
            item.pop("_relevance_rank", None)
            results.append(item)
        return results

    def reinforce_messages(
        self,
        message_ids: list[int] | tuple[int, ...] | set[int],
        *,
        amount: float = 1.0,
        half_life_days: float = 30.0,
        hot_threshold: float = 6.0,
        max_heat: float = 10.0,
        now: datetime | None = None,
    ) -> None:
        """Proportionally reheat memories that materially contributed to work.

        One meaningful reuse adds some heat but normally leaves a cold memory
        cold. Repeated useful reuse accumulates toward warm/hot. Reinforcement
        has diminishing returns as current heat rises, and heat decays between
        uses. This method is intentionally separate from search so merely being
        scanned as a candidate never keeps a memory artificially hot.
        """
        ids = sorted({int(message_id) for message_id in message_ids if int(message_id) > 0})
        if not ids:
            return
        base_amount = max(0.0, float(amount))
        if base_amount <= 0.0:
            return
        threshold = max(0.001, float(hot_threshold))
        heat_cap = max(threshold, float(max_heat))
        current = _as_utc_datetime(now) or datetime.now(timezone.utc)
        current_text = current.isoformat(timespec="seconds")
        placeholders = ",".join("?" for _ in ids)

        with self._lock, self._conn:
            rows = self._conn.execute(
                f"""
                SELECT id, last_used_at, use_count, heat_score
                FROM messages
                WHERE id IN ({placeholders})
                """,
                ids,
            ).fetchall()
            for row in rows:
                heat = _decayed_heat(
                    row["heat_score"],
                    row["last_used_at"],
                    half_life_days=half_life_days,
                    now=current,
                )
                # Diminishing returns are based on present heat rather than
                # lifetime use_count. A memory can therefore cool naturally and
                # later become easy to reheat again if it becomes useful.
                reinforcement = base_amount / (1.0 + heat / threshold)
                new_heat = min(heat_cap, heat + reinforcement)
                self._conn.execute(
                    """
                    UPDATE messages
                    SET last_used_at=?, use_count=?, heat_score=?
                    WHERE id=?
                    """,
                    (
                        current_text,
                        int(row["use_count"] or 0) + 1,
                        float(new_heat),
                        int(row["id"]),
                    ),
                )

    def integrity_check(self) -> str:
        with self._lock:
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"

    def maybe_backup(self, *, interval_hours: float = 24.0, retain: int = 7) -> Path | None:
        """Create a consistent SQLite backup without stopping the application."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.backup_dir.glob("conversation-*.sqlite3"))
        if existing:
            age = time.time() - existing[-1].stat().st_mtime
            if age < max(1.0, float(interval_hours)) * 3600:
                return None

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir / f"conversation-{stamp}.sqlite3"
        with self._lock:
            backup_conn = sqlite3.connect(str(target))
            try:
                self._conn.backup(backup_conn)
            finally:
                backup_conn.close()

        for old in sorted(self.backup_dir.glob("conversation-*.sqlite3"))[:-max(1, retain)]:
            try:
                old.unlink()
            except OSError:
                pass
        return target

    def backup_to(self, target: str | Path) -> Path:
        """Write a transactionally consistent copy of the live archive.

        Used by full removable-drive backups so the destination never depends on
        copying a live SQLite WAL/shm pair correctly.
        """
        target = Path(target).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        try:
            tmp.unlink()
        except OSError:
            pass
        with self._lock:
            backup_conn = sqlite3.connect(str(tmp))
            try:
                self._conn.backup(backup_conn)
            finally:
                backup_conn.close()
        # Verify before promoting the backup into place.
        verify = sqlite3.connect(str(tmp))
        try:
            row = verify.execute("PRAGMA integrity_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                raise sqlite3.DatabaseError("backup integrity check failed")
        finally:
            verify.close()
        os.replace(tmp, target)
        return target

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(FULL)")
            except sqlite3.Error:
                pass
            self._conn.close()
