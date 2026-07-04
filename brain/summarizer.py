"""
summarizer.py — compress retrieved notes into a token-budget response.

No LLM needed.  The algorithm:
  1. Split each note body into paragraphs.
  2. Score each paragraph by BM25-like term overlap with the original query.
  3. Pick paragraphs greedily until the char budget is exhausted.
  4. Return structured text with source attribution.

This keeps the returned context under ~500 tokens (≈2000 chars) so the
memory subagent can pass it back to the main agent cheaply.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from brain.search import SearchResult


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might must can could for and but or "
    "nor yet so at by in of on to up as if it its i you we they he she".split()
)


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


# ---------------------------------------------------------------------------
# Paragraph scorer
# ---------------------------------------------------------------------------

def _score_paragraph(para: str, query_tf: Counter) -> float:
    """
    Simple TF overlap score: sum of min(para_tf[t], query_tf[t]) for each
    query term found in the paragraph.  Normalized by query length.
    """
    if not para.strip():
        return 0.0
    para_tokens = _tokenize(para)
    para_tf = Counter(para_tokens)
    overlap = sum(min(para_tf[t], query_tf[t]) for t in query_tf if t in para_tf)
    return overlap / max(len(query_tf), 1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compress(
    results: list[SearchResult],
    query: str,
    char_budget: int = 2000,
    max_notes: int = 3,
) -> str:
    """
    Extract the most relevant paragraphs from the top search results and
    format them as a structured text block suitable for an AI agent.

    Returns plain text (not JSON) — the CLI caller wraps this in JSON.
    """
    query_tf = Counter(_tokenize(query))
    output_parts: list[str] = []
    remaining = char_budget

    for result in results[:max_notes]:
        if result.note is None:
            continue
        note = result.note

        # --- score paragraphs ---
        raw_paras = re.split(r"\n{2,}", note.body)
        scored = [
            (p.strip(), _score_paragraph(p, query_tf))
            for p in raw_paras
            if p.strip()
        ]
        # always include at least the first paragraph for context
        if scored:
            scored[0] = (scored[0][0], max(scored[0][1], 0.01))
        scored.sort(key=lambda x: x[1], reverse=True)

        # --- pick greedily ---
        selected: list[str] = []
        note_chars = 0
        note_budget = min(remaining, char_budget // max_notes)
        for para, score in scored:
            if score == 0.0:
                break
            if note_chars + len(para) > note_budget:
                continue
            selected.append(para)
            note_chars += len(para)

        if not selected:
            # fallback: first 300 chars
            selected = [note.body[:300]]

        header = f"### [{note.title}] ({result.rel_path})  score={result.score:.2f}"
        block = header + "\n\n" + "\n\n".join(selected)
        output_parts.append(block)
        remaining -= len(block)
        if remaining <= 0:
            break

    return "\n\n---\n\n".join(output_parts) if output_parts else ""
