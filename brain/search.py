"""
search.py — Lexical Reciprocal Rank Fusion (RRF) search over the FTS5 index.

This module implements text-only RRF to blend two different retrieval strategies
without external embedding models:
  1. Exact Phrase/Term Match: High structural match weight.
  2. Prefix Wildcard Match: Catches partial word forms (e.g., "open" -> "open*").

SQLite's window function ROW_NUMBER() tracks rankings per strategy, and the 
Reciprocal Rank Fusion formula aggregates them into a final normalized score.
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
    score: float                    # Fused RRF score, higher = better
    snippet: str = ""               # short body preview
    tags: list[str] = field(default_factory=list)
    note: Note | None = None        # populated by read_top()


# ---------------------------------------------------------------------------
# Core search (RRF Lexical Fusion)
# ---------------------------------------------------------------------------

def search(
    query: str,
    top_k: int = 10,
    category: str | None = None,
    vault: Path | None = None,
    index_path: Path | None = None,
) -> list[SearchResult]:
    """
    Run a multi-strategy lexical search blended via Reciprocal Rank Fusion (RRF).
    """
    ip = index_path or get_index_path(vault)
    if not ip.exists():
        return []

    conn = init_db(ip)
    try:
        return _run_search_rrf(conn, query, top_k, category)
    finally:
        conn.close()


def _run_search_rrf(
    conn: sqlite3.Connection,
    query: str,
    top_k: int,
    category: str | None,
) -> list[SearchResult]:
    sanitized = _sanitize_query(query)
    if not sanitized or sanitized == '""':
        return []

    # Strategy 1: Exact terms (e.g., "vsomeip")
    q_exact = sanitized

    # Strategy 2: Prefix wildcard matching (e.g., "open" -> "open*")
    # Skip prefix generation when the query already contains FTS5 operators.
    fts5_ops = {"AND", "OR", "NOT"}
    has_operators = any(op in (w.upper() for w in sanitized.split()) for op in fts5_ops)
    if has_operators:
        q_prefix = ""
    else:
        q_prefix = " ".join(
            f"{word}*" for word in sanitized.split() if not word.endswith("*")
        )

    # Optional category filter clause
    category_filter = "AND category = ?" if category else ""

    # RRF Hyperparameter constant (standard default is 60.0)
    K = 60.0

    # ------------------------------------------------------------------
    # Run each strategy as a standalone query.
    # CRITICAL: Do NOT use ROW_NUMBER() — SQLite FTS5's snippet() cannot be
    # used in the same query as a window function.  We ORDER BY bm25() and
    # assign ranks in Python instead.
    # ------------------------------------------------------------------
    def _strategy(match_query: str) -> list[tuple[str, int, str]]:
        """Return list of (slug, rank, snippet) ordered by bm25."""
        if not match_query:
            return []
        sql = f"""
        SELECT
            slug,
            bm25(notes_fts, 0, 0, 5, 3, 1, 0) AS bm25_score,
            snippet(notes_fts, 4, '**', '**', '…', 20) AS snip
        FROM notes_fts
        WHERE notes_fts MATCH ? {category_filter}
        ORDER BY bm25_score
        """
        params = [match_query]
        if category:
            params.append(category)
        rows = conn.execute(sql, params).fetchall()
        return [(r["slug"], i + 1, r["snip"]) for i, r in enumerate(rows)]

    exact_rows = _strategy(q_exact)
    prefix_rows = _strategy(q_prefix)

    # ------------------------------------------------------------------
    # Fuse the two rank lists in Python using standard RRF.
    # ------------------------------------------------------------------
    scores: dict[str, float] = {}
    snippets: dict[str, str] = {}

    for slug, rank, snip in exact_rows:
        scores[slug] = scores.get(slug, 0.0) + 1.0 / (K + rank)
        if snip:
            snippets[slug] = snip

    for slug, rank, snip in prefix_rows:
        scores[slug] = scores.get(slug, 0.0) + 1.0 / (K + rank)
        if slug not in snippets and snip:
            snippets[slug] = snip

    if not scores:
        return []

    # Sort by fused score and take top_k
    sorted_slugs = sorted(scores.keys(), key=lambda s: scores[s], reverse=True)[:top_k]

    # Pull non-FTS metadata for the winning slugs
    placeholders = ",".join("?" * len(sorted_slugs))
    meta_sql = f"""
    SELECT slug, rel_path, title, tags, category
    FROM notes_fts
    WHERE slug IN ({placeholders})
    """
    meta_rows = {r["slug"]: r for r in conn.execute(meta_sql, sorted_slugs).fetchall()}

    results: list[SearchResult] = []
    for slug in sorted_slugs:
        row = meta_rows.get(slug)
        if row:
            results.append(
                SearchResult(
                    slug=slug,
                    rel_path=row["rel_path"],
                    title=row["title"],
                    category=row["category"],
                    score=round(scores[slug], 4),
                    snippet=snippets.get(slug, ""),
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
import re

def _sanitize_query(query: str) -> str:
    """
    Sanitize the raw search query to prevent FTS5 syntax errors.
    Strips all non-alphanumeric characters (except spaces) to neutralize
    FTS5 special operators like *, :, (), and \\.
    Lowercases the string to neutralize boolean operators (AND, OR, NOT, NEAR).
    """
    if not query:
        return ""
    
    # Lowercase to neutralize FTS5 boolean operators (must be uppercase to trigger)
    query = query.lower()
    
    # Replace any character that is not a letter, number, or whitespace with a space.
    # This safely removes *, :, (), ", \, etc., preventing syntax errors.
    sanitized = re.sub(r'[^\w\s]', ' ', query)
    
    # Collapse multiple spaces into one and strip leading/trailing spaces
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    
    # If the query was just punctuation, it becomes empty here
    return sanitized

# ---------------------------------------------------------------------------
# High-level: search + read top notes
# ---------------------------------------------------------------------------

def read_top(
    query: str,
    top_k: int = 5,
    category: str | None = None,
    vault: Path | None = None,
    index_path: Path | None = None,
) -> list[SearchResult]:
    """
    Search + load the full Note objects for the top results.
    Used by `brain ask` so the caller gets both ranking metadata and content.
    """
    results = search(query, top_k=top_k, category=category, vault=vault, index_path=index_path)
    root = vault or get_vault_dir()
    for r in results:
        r.note = find_note(r.slug, root)
    return results


# ---------------------------------------------------------------------------
# Similarity check
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Similarity check
# ---------------------------------------------------------------------------

def find_similar(
    title: str,
    threshold: float = 0.01,
    vault: Path | None = None,
    index_path: Path | None = None,
) -> list[SearchResult]:
    """
    Find notes with similar titles using per-term search and RRF fusion.
    
    Instead of relying on FTS5 boolean OR (which breaks when _sanitize_query
    lowercases operators), we run search() for each term independently and
    fuse results with RRF. This achieves OR semantics by composition.
    """
    sanitized = _sanitize_query(title)
    words = sanitized.split()
    
    if not words:
        return []
    
    # Single-word: delegate directly to search
    if len(words) == 1:
        results = search(sanitized, top_k=5, vault=vault, index_path=index_path)
        return [r for r in results if r.score >= threshold]
    
    # Multi-word: run search per term and fuse with RRF
    # This achieves OR semantics without FTS5 boolean operators
    K = 60.0
    scores: dict[str, float] = {}
    snippets: dict[str, str] = {}
    meta_cache: dict[str, dict] = {}
    
    for word in words:
        word_results = search(word, top_k=10, vault=vault, index_path=index_path)
        for rank, r in enumerate(word_results, start=1):
            # Accumulate RRF score — notes matching more terms rank higher
            scores[r.slug] = scores.get(r.slug, 0.0) + 1.0 / (K + rank)
            if r.snippet and r.slug not in snippets:
                snippets[r.slug] = r.snippet
            if r.slug not in meta_cache:
                meta_cache[r.slug] = {
                    "rel_path": r.rel_path,
                    "title": r.title,
                    "category": r.category,
                    "tags": r.tags,
                }
    
    # Build final results sorted by fused score
    results: list[SearchResult] = []
    for slug in sorted(scores.keys(), key=lambda s: scores[s], reverse=True)[:5]:
        if scores[slug] >= threshold:
            meta = meta_cache[slug]
            results.append(
                SearchResult(
                    slug=slug,
                    rel_path=meta["rel_path"],
                    title=meta["title"],
                    category=meta["category"],
                    score=round(scores[slug], 4),
                    snippet=snippets.get(slug, ""),
                    tags=meta["tags"],
                )
            )
    
    return results