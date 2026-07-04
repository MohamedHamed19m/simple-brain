"""
search.py — BM25 search over the FTS5 index + result ranking.

SQLite FTS5's bm25() function returns *negative* values — more negative
means more relevant.  We normalize them to a [0, 1] score for output.

Column weights passed to bm25():
  title  × 5.0   (most important)
  tags   × 3.0
  body   × 1.0   (base)
  (slug, rel_path, category are UNINDEXED — weight 0)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from brain.config import get_index_path, get_vault_dir
from brain.index import init_db
from brain.vault import Note, find_note


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    slug: str
    rel_path: str
    title: str
    category: str
    score: float                    # normalized [0, 1], higher = better
    snippet: str = ""               # short body preview
    tags: list[str] = field(default_factory=list)
    note: Note | None = None        # populated by read_top()


# ---------------------------------------------------------------------------
# Core search
# ---------------------------------------------------------------------------

def search(
    query: str,
    top_k: int = 10,
    category: str | None = None,
    vault: Path | None = None,
    index_path: Path | None = None,
) -> list[SearchResult]:
    """
    Run a BM25 full-text search and return ranked results.

    The query supports standard FTS5 syntax:
      - bare words:  openssl mingw
      - phrase:      "secure channel"
      - prefix:      open*
      - column:      title:openssl
      - AND/OR/NOT:  openssl AND mingw
    """
    ip = index_path or get_index_path(vault)
    if not ip.exists():
        return []

    conn = init_db(ip)
    try:
        return _run_search(conn, query, top_k, category)
    finally:
        conn.close()


def _run_search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int,
    category: str | None,
) -> list[SearchResult]:
    # bm25 column weights: slug=0, rel_path=0, title=5, tags=3, body=1, category=0
    where = "notes_fts MATCH ?"
    params: list = [_sanitize_query(query)]

    if category:
        where += " AND category = ?"
        params.append(category)

    sql = f"""
        SELECT
            slug,
            rel_path,
            title,
            tags,
            category,
            snippet(notes_fts, 4, '**', '**', '…', 20) AS snip,
            bm25(notes_fts, 0, 0, 5, 3, 1, 0)          AS raw_score
        FROM notes_fts
        WHERE {where}
        ORDER BY raw_score   -- more negative = better in FTS5 bm25
        LIMIT ?
    """
    params.append(top_k)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []

    # Normalize scores: map [min_raw, 0] → [1, 0]
    raw_scores = [r["raw_score"] for r in rows]
    min_raw = min(raw_scores)
    span = abs(min_raw) if min_raw != 0 else 1.0

    results = []
    for row in rows:
        norm = 1.0 - (abs(row["raw_score"]) / span) if min_raw != 0 else 1.0
        results.append(
            SearchResult(
                slug=row["slug"],
                rel_path=row["rel_path"],
                title=row["title"],
                category=row["category"],
                score=round(norm, 4),
                snippet=row["snip"] or "",
                tags=(row["tags"] or "").split(),
            )
        )
    return results


def _sanitize_query(q: str) -> str:
    """
    Make the query safe for FTS5 MATCH without disabling advanced syntax.
    Strips lone special chars that would cause parse errors.
    """
    # If the user wrote a valid FTS5 expression (contains operators), pass through
    fts5_ops = {"AND", "OR", "NOT"}
    if any(op in q.upper().split() for op in fts5_ops):
        return q
    # Otherwise wrap in implicit AND by leaving it as-is (FTS5 default)
    # but strip characters that cause parse errors when bare
    safe = q.replace('"', ' ').replace("'", " ")
    safe = safe.strip()
    return safe or '""'


# ---------------------------------------------------------------------------
# High-level: search + read top notes
# ---------------------------------------------------------------------------

def read_top(
    query: str,
    top_k: int = 5,
    vault: Path | None = None,
    index_path: Path | None = None,
) -> list[SearchResult]:
    """
    Search + load the full Note objects for the top results.
    Used by `brain ask` so the caller gets both ranking metadata and content.
    """
    results = search(query, top_k=top_k, vault=vault, index_path=index_path)
    root = vault or get_vault_dir()
    for r in results:
        r.note = find_note(r.slug, root)
    return results


# ---------------------------------------------------------------------------
# Similarity check (for duplicate detection before `brain remember`)
# ---------------------------------------------------------------------------

def find_similar(
    title: str,
    threshold: float = 0.3,
    vault: Path | None = None,
    index_path: Path | None = None,
) -> list[SearchResult]:
    """
    Search by title to surface potential duplicates.
    Returns results with score >= threshold.
    """
    results = search(title, top_k=5, vault=vault, index_path=index_path)
    return [r for r in results if r.score >= threshold]
