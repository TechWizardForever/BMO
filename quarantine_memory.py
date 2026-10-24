"""Move contaminated conversation rows out of Bemo's live memory, reversibly.

Rows are copied into a `quarantined_messages` table in the same SQLite file
and then removed from `messages` (the FTS index follows via its trigger), so
recall search can no longer surface them. Nothing is destroyed: --restore
puts them back with their original ids. Stop Bemo before running this.

  python quarantine_memory.py --ids 9 116 --reason "Whisper hallucination"
  python quarantine_memory.py --list
  python quarantine_memory.py --restore 9 116
"""
from __future__ import annotations

import argparse
import sqlite3
import time

import config

COLUMNS = ("id", "session_id", "created_at", "role", "content", "project_slug",
           "model_profile", "model_name", "last_used_at", "use_count", "heat_score")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.MEMORY_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS quarantined_messages ("
        + ", ".join(COLUMNS) + ", reason TEXT NOT NULL, quarantined_at TEXT NOT NULL)"
    )
    return conn


def quarantine(ids: list[int], reason: str) -> int:
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = config.MEMORY_DB_PATH.with_name(f"conversation.before-quarantine-{stamp}.sqlite3")
    n = 1
    while backup.exists():  # never overwrite an earlier safety copy
        n += 1
        backup = config.MEMORY_DB_PATH.with_name(f"conversation.before-quarantine-{stamp}-{n}.sqlite3")
    # Use SQLite's backup API so rows still in the WAL file are included.
    with sqlite3.connect(str(config.MEMORY_DB_PATH)) as src, sqlite3.connect(str(backup)) as dst:
        src.backup(dst)
    print(f"Database copy: {backup}")
    cols = ", ".join(COLUMNS)
    marks = ",".join("?" * len(ids))
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO quarantined_messages ({cols}, reason, quarantined_at) "
            f"SELECT {cols}, ?, ? FROM messages WHERE id IN ({marks})",
            [reason, time.strftime("%Y-%m-%dT%H:%M:%S%z"), *ids],
        )
        moved = conn.execute(f"DELETE FROM messages WHERE id IN ({marks})", ids).rowcount
    return moved


def restore(ids: list[int]) -> int:
    cols = ", ".join(COLUMNS)
    marks = ",".join("?" * len(ids))
    with _connect() as conn:
        conn.execute(f"INSERT INTO messages ({cols}) SELECT {cols} FROM quarantined_messages WHERE id IN ({marks})", ids)
        return conn.execute(f"DELETE FROM quarantined_messages WHERE id IN ({marks})", ids).rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", type=int, nargs="+")
    parser.add_argument("--reason", default="contaminated")
    parser.add_argument("--restore", type=int, nargs="+")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.ids:
        print(f"Quarantined {quarantine(args.ids, args.reason)} row(s).")
    if args.restore:
        print(f"Restored {restore(args.restore)} row(s).")
    if args.list or not (args.ids or args.restore):
        with _connect() as conn:
            for row in conn.execute("SELECT id, role, reason, substr(replace(content, char(10), ' '), 1, 70) FROM quarantined_messages ORDER BY id"):
                print(*row, sep=" | ")


if __name__ == "__main__":
    main()
