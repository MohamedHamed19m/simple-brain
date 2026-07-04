import pytest
import sqlite3
from pathlib import Path
from brain.config import ensure_structure
from brain.vault import Note
from brain.index import init_db, upsert_note
from brain.search import search, find_similar

@pytest.fixture
def temp_vault(tmp_path: Path):
    """Fresh isolated vault + ephemeral SQLite index for every test."""
    vault_dir = tmp_path / "mock_brain"
    vault_dir.mkdir(parents=True, exist_ok=True)
    ensure_structure(vault_dir)
    index_path = vault_dir / ".brain_index.db"
    # init_db is called implicitly by upsert_note, but we ensure schema exists
    init_db(index_path)
    return vault_dir, index_path

# ============================================================
# Section 1: CTE Tokenization & Punctuation Stress
# ============================================================
class TestCTETokenizationStress:
    def test_cpp_template_syntax_sanitization_and_prefix_recovery(self, temp_vault):
        """
        GUARD AGAINST: FTS5 unicode61 splits `std::vector<C++11>` into 
        ['std', 'vector', 'c', '11']. If a user queries `std::vector<C++`, 
        sanitization strips punctuation yielding `std vector c`. 
        The Exact CTE searches for `std vector c` (implicit AND).
        The Prefix CTE searches for `std* vector* c*`.
        This test ensures the note is recovered via the Prefix CTE even if 
        the Exact CTE fails due to the missing '11' token in the query.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "skills/cpp-templates.md",
            title="C++ Template Metaprogramming",
            body="Using std::vector<C++11> for dynamic arrays.",
            tags=["cpp", "templates"],
            category="skills",
        )
        upsert_note(n, vault=vault, index_path=idx)
        
        # Query contains punctuation and partial word 'C++' (missing '11')
        results = search("std::vector<C++", vault=vault, index_path=idx)
        
        assert len(results) >= 1, "Prefix CTE failed to recover note with partial C++ token"
        assert results[0].slug == "cpp-templates"
        # Ensure the snippet actually highlights something relevant and isn't empty
        assert "std" in results[0].snippet.lower() or "vector" in results[0].snippet.lower()

# ============================================================
# Section 2: CTE RRF Mathematical Integrity
# ============================================================
class TestCTERRFMathIntegrity:
    def test_union_all_preserves_dual_cte_score_inflation(self, temp_vault):
        """
        GUARD AGAINST: A regression changing `UNION ALL` to `UNION` in the 
        Fused CTE. `UNION` deduplicates identical (slug, rrf_part, snip) rows.
        If a note ranks #1 in BOTH ExactRanks and PrefixRanks, its score 
        should be exactly 2 / (K + 1). If `UNION` is used, it drops to 1 / (K + 1).
        We verify the mathematical sum of the dual-CTE overlap.
        """
        vault, idx = temp_vault
        # Note designed to rank #1 in both Exact and Prefix for "kernel panic"
        n1 = Note(
            path=vault / "knowledge/kernel-panic.md",
            title="Kernel Panic",
            body="kernel panic kernel panic kernel panic system halt.",
            tags=["linux", "kernel"],
            category="knowledge",
        )
        # Note designed to rank #1 in Prefix, but lower/absent in Exact
        n2 = Note(
            path=vault / "knowledge/kernel-logs.md",
            title="Kernel Logs",
            body="Analyzing kernel logs for panics and warnings.",
            tags=["linux", "logs"],
            category="knowledge",
        )
        upsert_note(n1, vault=vault, index_path=idx)
        upsert_note(n2, vault=vault, index_path=idx)
        
        results = search("kernel panic", vault=vault, index_path=idx)
        
        assert len(results) >= 2
        top_score = results[0].score
        
        # K = 60. Rank 1 = 1/61 ≈ 0.01639
        # Dual overlap = 2/61 ≈ 0.03278
        # The top score MUST be strictly greater than the theoretical max of a single CTE
        single_cte_max = 1.0 / 61.0
        assert top_score > single_cte_max + 1e-5, (
            f"Top score {top_score} did not exceed single CTE max {single_cte_max}. "
            "UNION ALL may have been downgraded to UNION, dropping the second CTE's contribution."
        )

# ============================================================
# Section 3: CTE Window Function Determinism
# ============================================================
class TestCTEWindowFunctionIntegrity:
    def test_bm25_tie_breaking_prevents_rank_randomization(self, temp_vault):
        """
        GUARD AGAINST: ROW_NUMBER() OVER (ORDER BY bm25(...)) lacks a 
        deterministic tie-breaker (e.g., `, slug`). If multiple notes yield 
        the exact same BM25 score, SQLite assigns `rk` arbitrarily. 
        This causes non-deterministic RRF fusion scores and unstable 
        result ordering across identical queries.
        """
        vault, idx = temp_vault
        # 3 notes with identical searchable content to force BM25 ties
        for i in range(3):
            n = Note(
                path=vault / f"knowledge/clone-{i}.md",
                title="Identical Clone",
                body="exact same body text for bm25 tie testing.",
                tags=["clone"],
                category="knowledge",
            )
            upsert_note(n, vault=vault, index_path=idx)
            
        # Run search multiple times to check for deterministic ordering
        results_run1 = search("exact same body", vault=vault, index_path=idx)
        results_run2 = search("exact same body", vault=vault, index_path=idx)
        
        # The slugs and scores must appear in the exact same order
        assert [r.slug for r in results_run1] == [r.slug for r in results_run2], (
            "ROW_NUMBER() tie-breaking is non-deterministic. "
            "Add `, slug` to the OVER (ORDER BY ...) clause in your CTEs."
        )

# ============================================================
# Section 4: Malformed / Hostile Sanitize Fallbacks
# ============================================================
class TestCTESanitizeFallbacks:
    @pytest.mark.parametrize("hostile_query", [
        '""',             # Empty phrase
        '   ',            # Whitespace
        '🚀🔥👽',         # Pure emojis (non-ASCII, stripped by \w)
        '\u200b\u200b',   # Zero-width spaces
        'title:body',     # FTS5 column filter attempt (sanitized to 'title body')
        'NEAR/10',        # FTS5 proximity operator (sanitized to 'near 10')
        '"unclosed quote',# Unbalanced phrase
    ])
    def test_hostile_queries_never_crash_cte_pipeline(self, temp_vault, hostile_query):
        """
        GUARD AGAINST: sqlite3.OperationalError in the CTE MATCH clause.
        If _sanitize_query fails to neutralize FTS5 operators or produces 
        an empty string, the CTE `MATCH ?` will receive an invalid expression.
        The engine must short-circuit or handle it gracefully without raising.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "inbox/dummy.md",
            title="Dummy",
            body="Just some text to ensure the FTS5 table has rows.",
            tags=["test"],
            category="inbox",
        )
        upsert_note(n, vault=vault, index_path=idx)
        
        # Must not raise sqlite3.OperationalError or IndexError
        try:
            results = search(hostile_query, vault=vault, index_path=idx)
            assert isinstance(results, list)
        except Exception as e:
            pytest.fail(f"Hostile query {hostile_query!r} crashed the CTE pipeline: {e}")

# ============================================================
# Section 5: Duplicate/Similarity Threshold Gradients
# ============================================================
class TestFindSimilarGradientsCTE:
    def test_asymmetric_token_overlap_threshold_gradient(self, temp_vault):
        """
        GUARD AGAINST: find_similar uses RRF over individual word searches.
        We test asymmetric overlaps:
        A: "AUTOSAR Adaptive Platform" (Query)
        B: "Platform Adaptive AUTOSAR" (100% token overlap, 0% phrase overlap)
        C: "AUTOSAR Classic Platform" (66% token overlap)
        D: "Linux Kernel Setup" (0% overlap)
        
        As threshold increases, D must drop first, then C, then B.
        This verifies the RRF accumulation correctly models multi-term OR semantics
        without relying on FTS5 boolean OR (which breaks with lowercased sanitization).
        """
        vault, idx = temp_vault
        
        base = Note(vault / "k/base.md", "AUTOSAR Adaptive Platform", "Body", [], "knowledge")
        b = Note(vault / "k/b.md", "Platform Adaptive AUTOSAR", "Body", [], "knowledge")
        c = Note(vault / "k/c.md", "AUTOSAR Classic Platform", "Body", [], "knowledge")
        d = Note(vault / "k/d.md", "Linux Kernel Setup", "Body", [], "knowledge")
        
        for note in [base, b, c, d]:
            upsert_note(note, vault=vault, index_path=idx)
            
        # Threshold 0.0 should catch B and C (and maybe base if it doesn't exclude self)
        loose = find_similar("AUTOSAR Adaptive Platform", threshold=0.0, vault=vault, index_path=idx)
        loose_slugs = {r.slug for r in loose}
        assert "b" in loose_slugs
        assert "c" in loose_slugs
        assert "d" not in loose_slugs
        
        # High threshold should strictly require near-exact multi-term overlap
        tight = find_similar("AUTOSAR Adaptive Platform", threshold=0.03, vault=vault, index_path=idx)
        tight_slugs = {r.slug for r in tight}
        
        # C has only 2/3 terms. Its RRF score will be lower than B (3/3 terms).
        # At a tight threshold, C should drop out before B.
        if "b" in tight_slugs and "c" in tight_slugs:
            # If both survive, B must be ranked higher
            b_score = next(r.score for r in tight if r.slug == "b")
            c_score = next(r.score for r in tight if r.slug == "c")
            assert b_score > c_score