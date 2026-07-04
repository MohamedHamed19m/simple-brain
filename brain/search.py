"""
search.py — BM25 search over the FTS5 index + result ranking.

SQLite FTS5's bm25() function returns *negative* values — more negative
means more relevant.  We normalize them to a [0, 1] score for output.

Column weights passed to bm25():
  title  × 5.0   (most important)
  tags   × 3.0
  body   × 1.0   (base)
  (slug, rel_path, category are UNINDEXED — weight 0)

Natural-language queries ("how do I fix openssl") are pre-processed:
stop-words are stripped and only content words are passed to FTS5.
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

    # Normalize scores to [0, 1].
    # bm25 returns negatives; more negative = better.
    # With a single result min_raw == max_raw → give it score 1.0.
    raw_scores = [r["raw_score"] for r in rows]
    min_raw = min(raw_scores)  # most-relevant (most negative)
    max_raw = max(raw_scores)  # least-relevant
    span = abs(min_raw - max_raw) if min_raw != max_raw else 1.0

    results = []
    for row in rows:
        if min_raw == max_raw:
            # Only one result (or all tied) → perfect score
            norm = 1.0
        else:
            # Map: min_raw (best) → 1.0, max_raw (worst) → 0.0
            norm = (abs(row["raw_score"]) - abs(max_raw)) / span
            norm = round(max(0.0, min(1.0, norm)), 4)
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


# Common English stop words to strip from natural-language queries
_STOP = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might must can could for and but or "
    "nor yet so at by in of on to up as if it its i you we they he she "
    "how what when where why who which that this these those".split()
)


def _sanitize_query(q: str) -> str:
    """
    Prepare a query string for SQLite FTS5 MATCH.

    - If the query looks like an FTS5 expression (has AND/OR/NOT or column:
      syntax), pass it through unchanged.
    - Otherwise treat it as natural language: strip stop-words and punctuation,
      then let FTS5 do an implicit AND across the remaining content words.
    """
    fts5_ops = {"AND", "OR", "NOT"}
    words = q.split()

    # Pass through explicit FTS5 expressions
    if any(op in (w.upper() for w in words) for op in fts5_ops):
        return q
    if any(":" in w for w in words):  # column:term syntax
        return q

    # Strip punctuation and stop-words
    import re as _re
    tokens = _re.findall(r"[a-zA-Z0-9]+", q)
    content = [t for t in tokens if t.lower() not in _STOP and len(t) > 1]

    if not content:
        # Fallback: use original stripped of bare special chars
        safe = q.replace('"', ' ').replace("'", " ").strip()
        return safe or '""'

    # Join as implicit AND (FTS5 default for space-separated terms)
    return " ".join(content)


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
