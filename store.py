"""Database connection, shared by fetch.py and news.py so they cannot drift."""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "news.db"
SCHEMA = Path(__file__).parent / "schema.sql"

# Columns added after the first release. SQLite has no "ADD COLUMN IF NOT
# EXISTS", so they are applied here rather than in schema.sql, which runs on
# every start and must stay idempotent.
MIGRATIONS = [
    ("sources", "etag",          "TEXT"),
    ("sources", "last_modified", "TEXT"),
    # Fetch health. Without these the page cannot tell "this feed is broken"
    # from "this feed had nothing new", which look identical at 0 candidates.
    ("sources", "last_error",    "TEXT"),
    ("sources", "last_success",  "TEXT"),
    # Section pages (Phase 2). Deliberately NOT indexed: prune() bounds
    # `articles` at twice the longest recency window -- under a thousand
    # rows -- so a scan costs less than the index would.
    ("articles", "section_slot", "INTEGER NOT NULL DEFAULT 0"),
]


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA.read_text())
    for table, col, decl in MIGRATIONS:
        have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    db.commit()
    return db
