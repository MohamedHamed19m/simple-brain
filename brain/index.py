"""
index.py — SQLite FTS5 index for the vault.

Schema
------
  notes_fts  (FTS5 virtual table)
    slug      TEXT  — unique identifier (relative path stem)
    rel_path  TEXT  — path relative to vault root
    title     TEXT
    tags      TEXT  — space-separated
    body      TEXT  — markdown body (without frontmatter)
    category  TEXT

  notes_meta (regular table for non-text metadata)
    slug      TEXT PRIMARY KEY
    created   TEXT
    updated   TEXT

FTS5 is configured with the `porter` tokenizer to get basic stemming for
free, without any ML model.  BM25 ranking is built into SQLite FTS5.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from brain.config import get_index_path, get_vault_dir
from brain.vault import Note, iter_notes


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """\
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    slug UNINDEXED,
    rel_path UNINDEXED,
    title,
    tags,
    body,
    category UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS notes_meta (
    slug    TEXT PRIMARY KEY,
    created TEXT,
    updated TEXT
);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _connect(index_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(index_path))
    conn.row_factory = sqlite3.Row
    # Enable WAL for better concurrent read/write
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(index_path: Path | None = None) -> sqlite3.Connection:
    """Create the schema if it doesn't exist and return a connection."""
    ip = index_path or get_index_path()
    conn = _connect(ip)
    conn.executescript(_DDL)
    conn.commit()
    return conn


def rebuild_index(vault: Path | None = None, index_path: Path | None = None) -> int:
    """
    Drop and recreate the index from all markdown files in the vault.
    Returns the number of notes indexed.
    """
    ip = index_path or get_index_path(vault)
    conn = init_db(ip)
    conn.execute("DELETE FROM notes_fts")
    conn.execute("DELETE FROM notes_meta")
    count = 0
    for note in iter_notes(vault):
        _upsert(conn, note)
        count += 1
    conn.commit()
    conn.close()
    return count


def upsert_note(note: Note, vault: Path | None = None, index_path: Path | None = None) -> None:
    """Add or update a single note in the index."""
    ip = index_path or get_index_path(vault)
    conn = init_db(ip)
    _upsert(conn, note)
    conn.commit()
    conn.close()


def remove_note(slug: str, vault: Path | None = None, index_path: Path | None = None) -> None:
    """Remove a note from the index by slug."""
    ip = index_path or get_index_path(vault)
    conn = init_db(ip)
    conn.execute("DELETE FROM notes_fts WHERE slug = ?", (slug,))
    conn.execute("DELETE FROM notes_meta WHERE slug = ?", (slug,))
    conn.commit()
    conn.close()


def _upsert(conn: sqlite3.Connection, note: Note) -> None:
    # FTS5 doesn't support UPSERT natively — delete then insert
    conn.execute("DELETE FROM notes_fts WHERE slug = ?", (note.slug,))
    conn.execute(
        "INSERT INTO notes_fts (slug, rel_path, title, tags, body, category) VALUES (?,?,?,?,?,?)",
        (
            note.slug,
            note.rel_path,
            note.title,
            " ".join(note.tags),
            note.body,
            note.category,
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO notes_meta (slug, created, updated) VALUES (?,?,?)",
        (note.slug, note.created, note.updated),
    )
