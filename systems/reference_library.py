"""Local technical-document library with lexical full-text search.

The library is intentionally local and user-populated. It copies only explicitly
added documents into ~/.bemo/references/files and builds an SQLite FTS5 index so
Engineering can ground answers in manuals/datasheets without an embedding model.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ReferenceLibraryError(ValueError):
    pass


def _safe_basename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._() +-]+", "_", Path(name).name).strip()
    return cleaned or "reference"


@dataclass
class TechnicalReferenceLibrary:
    root: Path
    max_file_bytes: int = 100 * 1024 * 1024
    chunk_chars: int = 3500

    def __post_init__(self):
        self.root = Path(self.root).expanduser().resolve()
        self.files_root = self.root / "files"
        self.files_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "index.sqlite3"
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT NOT NULL, added_at REAL NOT NULL, mtime REAL NOT NULL, size INTEGER NOT NULL)")
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(doc_path UNINDEXED, locator UNINDEXED, text, tokenize='porter unicode61')")

    def _source(self, raw: str) -> Path:
        path = Path(raw).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ReferenceLibraryError(f"File not found: {path}")
        if path.stat().st_size > self.max_file_bytes:
            raise ReferenceLibraryError("Reference file exceeds configured size limit")
        if path.suffix.lower() not in {".pdf", ".txt", ".md", ".rst", ".csv", ".tsv", ".json"}:
            raise ReferenceLibraryError("Supported references: PDF, TXT, MD, RST, CSV, TSV, JSON")
        return path

    def add(self, source: str, title: str = "") -> dict[str, Any]:
        src = self._source(source)
        name = _safe_basename(src.name)
        dest = self.files_root / name
        if dest.exists() and dest.resolve() != src.resolve():
            counter = 2
            while (self.files_root / f"{dest.stem}-{counter}{dest.suffix}").exists():
                counter += 1
            dest = self.files_root / f"{dest.stem}-{counter}{dest.suffix}"
        if dest.resolve() != src.resolve():
            shutil.copy2(src, dest)
        actual_title = title.strip() or dest.stem
        self._index_one(dest, actual_title)
        return {"added": True, "path": str(dest), "title": actual_title, "note": "Document copied into Bemo's local technical reference library and indexed."}

    def _extract_sections(self, path: Path) -> list[tuple[str, str]]:
        suffix = path.suffix.lower()
        sections: list[tuple[str, str]] = []
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            for idx, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                sections.append((f"page {idx + 1}", text))
        elif suffix in {".txt", ".md", ".rst"}:
            sections.append(("text", path.read_text(encoding="utf-8", errors="replace")))
        elif suffix == ".json":
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            sections.append(("json", json.dumps(obj, indent=2, ensure_ascii=False)))
        elif suffix in {".csv", ".tsv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
                rows = list(csv.reader(fh, delimiter=delimiter))
            for start in range(0, len(rows), 50):
                block = rows[start:start + 50]
                sections.append((f"rows {start + 1}-{start + len(block)}", "\n".join(delimiter.join(row) for row in block)))
        return sections

    def _chunk(self, locator: str, text: str):
        normalized = re.sub(r"\r\n?", "\n", text)
        if len(normalized) <= self.chunk_chars:
            if normalized.strip():
                yield locator, normalized
            return
        start = 0
        part = 1
        while start < len(normalized):
            end = min(len(normalized), start + self.chunk_chars)
            if end < len(normalized):
                split = normalized.rfind("\n", start, end)
                if split > start + self.chunk_chars // 2:
                    end = split
            piece = normalized[start:end].strip()
            if piece:
                yield f"{locator}, chunk {part}", piece
                part += 1
            start = max(end, start + 1)

    def _index_one(self, path: Path, title: str | None = None):
        sections = self._extract_sections(path)
        with self._connect() as conn:
            row = conn.execute("SELECT title FROM docs WHERE path=?", (str(path),)).fetchone()
            doc_title = (title or (row["title"] if row else path.stem)).strip()
            conn.execute("DELETE FROM chunks WHERE doc_path=?", (str(path),))
            for locator, text in sections:
                for chunk_locator, chunk in self._chunk(locator, text):
                    conn.execute("INSERT INTO chunks(doc_path, locator, text) VALUES(?,?,?)", (str(path), chunk_locator, chunk))
            stat = path.stat()
            conn.execute(
                "INSERT INTO docs(path,title,added_at,mtime,size) VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET title=excluded.title,mtime=excluded.mtime,size=excluded.size",
                (str(path), doc_title, time.time(), stat.st_mtime, stat.st_size),
            )

    def reindex(self) -> dict[str, Any]:
        count = 0
        errors = []
        for path in sorted(self.files_root.iterdir()):
            if not path.is_file():
                continue
            try:
                self._index_one(path)
                count += 1
            except Exception as exc:
                errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
        return {"indexed": count, "errors": errors}

    def list_docs(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT path,title,added_at,mtime,size FROM docs ORDER BY title COLLATE NOCASE").fetchall()
        return {"documents": [dict(row) for row in rows]}

    def search(self, query: str, limit: int = 6) -> dict[str, Any]:
        q = query.strip()
        if not q:
            raise ReferenceLibraryError("query is required")
        # Quote punctuation-heavy terms but otherwise let FTS handle words.
        tokens = re.findall(r"[A-Za-z0-9_.+-]+", q)
        fts = " OR ".join(f'"{t.replace(chr(34), "")}"' for t in tokens[:20])
        if not fts:
            raise ReferenceLibraryError("query contains no searchable terms")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT doc_path, locator, snippet(chunks,2,'[',']',' … ',24) AS snippet, bm25(chunks) AS score FROM chunks WHERE chunks MATCH ? ORDER BY score LIMIT ?",
                (fts, max(1, min(20, int(limit)))),
            ).fetchall()
            titles = {row["path"]: row["title"] for row in conn.execute("SELECT path,title FROM docs").fetchall()}
        return {"query": q, "results": [{"path": row["doc_path"], "title": titles.get(row["doc_path"], Path(row["doc_path"]).stem), "locator": row["locator"], "snippet": row["snippet"], "score": row["score"]} for row in rows]}

    def read(self, path: str, locator: str = "", max_chars: int = 12_000) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.relative_to(self.files_root)
        except ValueError as exc:
            raise ReferenceLibraryError("read path must be inside Bemo's reference library") from exc
        if not resolved.exists():
            raise ReferenceLibraryError("Reference document not found")
        sections = self._extract_sections(resolved)
        if locator:
            selected = [(loc, text) for loc, text in sections if locator.lower() in loc.lower()]
            if not selected:
                raise ReferenceLibraryError(f"Locator not found: {locator}")
        else:
            selected = sections[:5]
        text = "\n\n".join(f"--- {loc} ---\n{text}" for loc, text in selected)
        return {"path": str(resolved), "locator": locator or "first sections", "text": text[:max_chars], "truncated": len(text) > max_chars, "citation_hint": "When answering, name this local document and page/locator for any fact taken from it."}

    def execute(self, arguments: dict[str, Any]) -> str:
        try:
            action = str(arguments.get("action", "search")).lower()
            if action == "add": result = self.add(str(arguments.get("path", "")), str(arguments.get("title", "")))
            elif action == "reindex": result = self.reindex()
            elif action == "list": result = self.list_docs()
            elif action == "search": result = self.search(str(arguments.get("query", "")), int(arguments.get("limit", 6)))
            elif action == "read": result = self.read(str(arguments.get("path", "")), str(arguments.get("locator", "")), int(arguments.get("max_chars", 12_000)))
            else: raise ReferenceLibraryError(f"Unknown reference-library action: {action}")
            return json.dumps(result, indent=2, ensure_ascii=False, default=str)
        except Exception as exc:
            return f"Tool error: {type(exc).__name__}: {exc}"


def reference_library_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "technical_reference_library",
            "description": "Search/read Bemo's local user-supplied manuals, datasheets, and technical documents before web lookup. action=add copies and indexes a document only when the user explicitly asks to add it; search/read/list/reindex are read-only library operations.",
            "parameters": {
                "type": "object", "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["add", "reindex", "list", "search", "read"]},
                    "path": {"type": "string"}, "title": {"type": "string"}, "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20}, "locator": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 30000},
                },
            },
        },
    }
