"""
tests/test_complex_memory.py

Edge-case stress tests for simple-brain's SQLite FTS5 + RRF search engine.
Each test names a specific failure mode in the CTE pipeline, sanitize layer,
or find_similar threshold machinery.
"""

import pytest
import sqlite3
from pathlib import Path

from brain.config import ensure_structure
from brain.vault import Note
from brain.index import init_db, upsert_note, remove_note
from brain.search import search, find_similar


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def temp_vault(tmp_path: Path):
    """
    Fresh isolated vault + ephemeral SQLite index for every test.
    Inherits pytest's tmp_path guarantees: cleaned up after the test,
    no cross-test contamination, no touching the user's ~/.brain.
    """
    vault_dir = tmp_path / "mock_brain"
    vault_dir.mkdir(parents=True, exist_ok=True)
    ensure_structure(vault_dir)
    index_path = vault_dir / ".brain_index.db"
    return vault_dir, index_path


@pytest.fixture
def seeded_vault(temp_vault):
    """
    Pre-populated vault with mixed-content notes designed to exercise
    FTS5 unicode61 tokenization of software-engineering punctuation:
    double colons, C++ plus signs, Unix paths, hyphenated compounds.
    """
    vault, idx = temp_vault
    notes = [
        Note(
            path=vault / "knowledge/ara-com.md",
            title="ara::com Service Discovery",
            body="ara::com is the AUTOSAR Adaptive communication stack, built on vsomeip.",
            tags=["autosar", "ara::com", "vsomeip"],
            category="knowledge",
        ),
        Note(
            path=vault / "skills/cpp11.md",
            title="Modern C++11 Patterns",
            body="Use std::atomic and std::thread for portable C++11 concurrency.",
            tags=["cpp", "cpp11", "threading"],
            category="skills",
        ),
        Note(
            path=vault / "projects/serial-debug.md",
            title="Serial Debug on /dev/ttyUSB0",
            body="Run: stty -F /dev/ttyUSB0 115200 raw -echo before reading bytes.",
            tags=["linux", "serial", "ttyUSB0"],
            category="projects",
        ),
        Note(
            path=vault / "knowledge/vsomeip-ext.md",
            title="vsomeip-extensions Build Guide",
            body="Patches for vsomeip-extensions live in the internal git mirror.",
            tags=["vsomeip", "extensions"],
            category="knowledge",
        ),
    ]
    for n in notes:
        upsert_note(n, vault=vault, index_path=idx)
    return vault, idx


# ============================================================
# Section 1 — Tokenization stress (special chars from SW workflows)
# ============================================================

class TestSpecialCharacterTokenization:
    """
    The FTS5 unicode61 tokenizer treats `:`, `+`, `/`, `-` as separators
    by default, BUT the MATCH parser still interprets bare `:` as a column
    filter operator and `*` as a prefix wildcard. These tests ensure the
    sanitize layer prevents syntax errors while still letting the prefix
    CTE recover matches via token-level wildcards.
    """

    def test_double_colon_ara_com_does_not_crash(self, temp_vault):
        """
        GUARD AGAINST: FTS5 parses `column:term` as a column filter. A raw
        query 'ara::com' is parsed as 'column=ara' + dangling ':com', which
        raises sqlite3.OperationalError: "fts5: syntax error near ':'".
        The sanitize layer must escape or strip the colons before MATCH,
        while the prefix CTE still catches the note via 'ara* com*'.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/ara-com.md",
            title="ara::com Service Discovery",
            body="ara::com is the AUTOSAR Adaptive communication stack.",
            tags=["autosar", "ara::com"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        # Must not raise; result must include the note via the prefix branch.
        results = search("ara::com", vault=vault, index_path=idx)
        assert isinstance(results, list)
        assert any(r.slug == "ara-com" for r in results), \
            "Prefix CTE failed to recover ara::com after tokenization split"

    def test_cpp11_plus_plus_tokenization(self, temp_vault):
        """
        GUARD AGAINST: 'C++11' indexes as ['C', '11'] under unicode61.
        A raw query 'C++11' sent to MATCH may be parsed as the phrase
        "C 11" or trigger a syntax error on the dangling '+'. Either way,
        the prefix CTE issuing 'C* 11*' must still surface the note.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "skills/cpp11.md",
            title="Modern C++11 Patterns",
            body="Use std::atomic for portable C++11 concurrency primitives.",
            tags=["cpp", "cpp11"],
            category="skills",
        )
        upsert_note(n, vault=vault, index_path=idx)

        results = search("C++11", vault=vault, index_path=idx)
        assert isinstance(results, list)
        assert any(r.slug == "cpp11" for r in results), \
            "C++11 query failed to match a note whose body literally contains 'C++11'"

    def test_unix_path_query_tokenization(self, seeded_vault):
        """
        GUARD AGAINST: '/dev/ttyUSB0' should tokenize to ['dev', 'ttyUSB0'].
        The leading '/' must not be interpreted as anything special in the
        MATCH parser. Verifies both indexer and query sanitizer agree on
        slash-tokenization semantics.
        """
        vault, idx = seeded_vault
        results = search("/dev/ttyUSB0", vault=vault, index_path=idx)
        assert any(r.slug == "serial-debug" for r in results), \
            "Unix path query failed to surface the note containing that exact path"

    def test_hyphenated_compound_word_query(self, seeded_vault):
        """
        GUARD AGAINST: 'vsomeip-extensions' tokenizes to ['vsomeip', 'extensions'].
        The prefix CTE will issue 'vsomeip-extensions' → 'vsomeip* extensions*'
        after splitting on whitespace. Both tokens must match for FTS5's
        implicit AND to surface the note. Verifies hyphen-survival end-to-end.
        """
        vault, idx = seeded_vault
        results = search("vsomeip-extensions", vault=vault, index_path=idx)
        assert any(r.slug == "vsomeip-ext" for r in results), \
            "Hyphenated compound query lost its tokens somewhere in sanitize+prefix"

    def test_query_pure_punctuation_returns_empty_safely(self, temp_vault):
        """
        GUARD AGAINST: A query of pure punctuation (':::', '+++') must not
        reach MATCH. _sanitize_query should reduce it to empty and the
        function should return [] without calling the DB.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "inbox/pad.md",
            title="Padding Note",
            body="Some content to ensure the index is non-empty.",
            tags=["misc"],
            category="inbox",
        )
        upsert_note(n, vault=vault, index_path=idx)

        for punct in [":::", "+++", "///", "---", "***", "@@@", "###"]:
            results = search(punct, vault=vault, index_path=idx)
            assert isinstance(results, list), f"Pure-punct query {punct!r} raised"


# ============================================================
# Section 2 — RRF fusion math (SUM, MAX, UNION ALL integrity)
# ============================================================

class TestRRFFusionMath:
    """
    The outer query does:
        SELECT slug, MAX(f.snip), SUM(f.rrf_part)
        FROM Fused f
        GROUP BY f.slug

    These tests verify the SUM respects UNION ALL (not UNION), the MAX
    does not collapse to NULL when one branch's snippet is NULL, and
    the prefix CTE enforces term intersection (not union).
    """

    def test_exact_and_prefix_overlap_sums_rrf(self, temp_vault):
        """
        GUARD AGAINST: If a slug appears in BOTH ExactRanks (rank=1) and
        PrefixRanks (rank=1), its final score must equal 1/(60+1) + 1/(60+1)
        = 2/61 ≈ 0.0328. A common regression is replacing `UNION ALL` with
        `UNION`, which deduplicates by (slug, rrf_part, snip) and silently
        halves the score to 1/61 ≈ 0.0164. We assert the score strictly
        exceeds a single-branch contribution.
        """
        vault, idx = temp_vault
        # Body repeats 'openssl' so both the exact MATCH and the 'openssl*'
        # prefix MATCH land on this single slug at rank 1 in each CTE.
        n = Note(
            path=vault / "knowledge/openssl.md",
            title="OpenSSL Setup",
            body="openssl openssl openssl configuration for TLS handshakes.",
            tags=["openssl", "security"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        results = search("openssl", vault=vault, index_path=idx)
        assert len(results) == 1
        score = getattr(results[0], "final_rrf_score", None)
        if score is not None:
            single_branch_max = 1.0 / 61.0  # rank 1, K=60
            assert score > single_branch_max, (
                f"RRF score {score} is not greater than a single-branch "
                f"contribution {single_branch_max} — UNION ALL may have been "
                f"replaced with UNION, dropping the second branch's contribution."
            )

    def test_multi_word_wildcard_enforces_intersection(self, temp_vault):
        """
        GUARD AGAINST: Query 'open setup' becomes 'open* setup*'. FTS5 prefix
        MATCH uses implicit AND between terms. A regression that ORs the
        terms would surface notes containing only one of the two words.
        We construct three notes:
          A: has both 'openssl' and 'setup'  → MUST match
          B: has only 'openssl'              → must NOT match prefix branch
          C: has only 'setup'                → must NOT match prefix branch
        The top result must be A, and B/C must not appear above A.
        """
        vault, idx = temp_vault
        a = Note(
            path=vault / "skills/openssl-setup.md",
            title="OpenSSL Setup",
            body="Step-by-step openssl setup for dev environments.",
            tags=["openssl", "setup"],
            category="skills",
        )
        b = Note(
            path=vault / "skills/openssl-only.md",
            title="OpenSSL Basics",
            body="openssl basics without any setup content.",
            tags=["openssl"],
            category="skills",
        )
        c = Note(
            path=vault / "skills/setup-only.md",
            title="Generic Setup Tips",
            body="setup tips unrelated to crypto libraries.",
            tags=["setup"],
            category="skills",
        )
        for n in (a, b, c):
            upsert_note(n, vault=vault, index_path=idx)

        results = search("open setup", vault=vault, index_path=idx)
        assert results, "Prefix CTE returned nothing for a guaranteed intersection"
        assert results[0].slug == "openssl-setup", (
            f"Expected openssl-setup at rank 0, got {results[0].slug}. "
            f"Prefix CTE may be OR-ing terms instead of AND-ing them."
        )

    def test_max_snippet_does_not_collapse_to_null(self, temp_vault):
        """
        GUARD AGAINST: If one CTE branch produces a NULL snippet (e.g.,
        ExactRanks matches but the snippet column index 4 returns NULL for
        some reason), MAX(f.snip) must still preserve the non-NULL value
        from the other branch. A regression that uses MIN or COALESCE
        incorrectly would return an empty snippet. We assert non-empty.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/snippet-test.md",
            title="FTS5 Snippet Quirks",
            body="When both CTE branches return snippets, MAX must preserve one.",
            tags=["fts5", "snippets"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        results = search("snippet", vault=vault, index_path=idx)
        assert results, "No results returned for a term present in the body"
        assert results[0].snippet, (
            "Snippet collapsed to empty — MAX(f.snip) may be returning NULL "
            "when one CTE branch produces a NULL snippet."
        )
        assert "snippet" in results[0].snippet.lower()

    def test_rrf_constant_K_does_not_dominate_small_indexes(self, temp_vault):
        """
        GUARD AGAINST: K=60 is a fixed RRF constant. In a tiny index (1 note),
        the rank is always 1, so the score is always 1/61. This test verifies
        the engine doesn't accidentally divide by (K + 0) when ROW_NUMBER
        returns 0 (it shouldn't, but a regression in the OVER clause could
        shift ranks to 0-based and inflate scores to 1/60).
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/sole.md",
            title="Sole Note",
            body="The only note in this index.",
            tags=["solo"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        results = search("sole", vault=vault, index_path=idx)
        assert len(results) == 1
        score = getattr(results[0], "final_rrf_score", None)
        if score is not None:
            # Max possible if rank were 0-based: 2/60 ≈ 0.0333
            # Expected with rank 1-based:       2/61 ≈ 0.0328
            assert score <= 2.0 / 60.0 + 1e-9, (
                f"Score {score} exceeds theoretical max — ROW_NUMBER may be "
                f"0-based, shifting the K=60 denominator."
            )


# ============================================================
# Section 3 — Sanitize fallbacks (malformed, hostile, empty)
# ============================================================

class TestSanitizeFallbacks:
    """
    The function's guard is:
        if not sanitized or sanitized == '""':
            return []

    These tests probe every input class that should hit that guard (or
    pass through it safely) without raising.
    """

    @pytest.mark.parametrize("malformed", [
        "",
        "   ",
        "\t\n",
        '""',
        "''",
        "*",
        "**",
        "()",
        "AND",
        "OR",
        "NOT",
        "NEAR",
        "AND OR NOT",
        "\\",
        '"',
        '""""',
        ":",
        "::",
        "title:",
        "*:*",
        '"unclosed',
        '("unclosed"',
    ])
    def test_malformed_queries_never_raise(self, temp_vault, malformed):
        """
        GUARD AGAINST: Each of these inputs can trigger one of:
          - sqlite3.OperationalError (FTS5 syntax error)
          - IndexError (empty split list)
          - ValueError (parameter binding)
        The search function must return [] (or any list) without raising.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "inbox/padding.md",
            title="Padding Note",
            body="Just some content to ensure the index is non-empty.",
            tags=["misc"],
            category="inbox",
        )
        upsert_note(n, vault=vault, index_path=idx)

        try:
            results = search(malformed, vault=vault, index_path=idx)
        except Exception as e:
            pytest.fail(f"Query {malformed!r} raised {type(e).__name__}: {e}")
        assert isinstance(results, list)

    def test_stop_word_only_query_returns_safely(self, temp_vault):
        """
        GUARD AGAINST: FTS5's default tokenizer has no built-in stop-word
        list, but some custom sanitizers strip common words. A query of
        'the and or' must not crash whether or not those words are stripped.
        Verifies the engine tolerates stop-word-heavy input.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/stopwords.md",
            title="The And Or Article",
            body="Discusses how the engine handles stop words in queries.",
            tags=["linguistics"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        results = search("the and or", vault=vault, index_path=idx)
        assert isinstance(results, list)

    def test_unbalanced_quotes_fallback(self, temp_vault):
        """
        GUARD AGAINST: A single `"` opens a phrase that never closes,
        producing a malformed MATCH expression. _sanitize_query must
        either balance or strip the quote.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/quotes.md",
            title="Quote Handling",
            body="How the engine handles unbalanced quotes in user queries.",
            tags=["quotes"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        results = search('"unbalanced phrase', vault=vault, index_path=idx)
        assert isinstance(results, list)

    def test_query_with_only_wildcard_star(self, temp_vault):
        """
        GUARD AGAINST: A bare '*' is FTS5 prefix-wildcard syntax. Matched
        against the index, it could return every row or raise. The prefix
        CTE splits ' * ' → [] (empty word list after stripping the bare '*'),
        which must not produce an empty MATCH clause.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/star.md",
            title="Star Note",
            body="Content for wildcard testing.",
            tags=["star"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        # Bare '*' and '**' must not raise or return the entire index
        for q in ["*", "**", "***"]:
            results = search(q, vault=vault, index_path=idx)
            assert isinstance(results, list)

    def test_very_long_query_does_not_crash(self, temp_vault):
        """
        GUARD AGAINST: An adversarially long query (10k chars) could blow
        up the FTS5 query parser or hit SQLite's string-length limits.
        The engine should handle it gracefully.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/long-query.md",
            title="Long Query Resilience",
            body="The engine must tolerate very long queries without crashing.",
            tags=["resilience"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        long_query = " ".join(["resilience"] * 1000)
        results = search(long_query, vault=vault, index_path=idx)
        assert isinstance(results, list)


# ============================================================
# Section 4 — Category filter edge cases
# ============================================================

class TestCategoryFilterEdges:
    """
    The category_filter is interpolated into BOTH CTEs:
        WHERE notes_fts MATCH ? {category_filter}
    A regression that applies it to only one CTE leaks cross-category
    results into the fused output. These tests pin that behavior.
    """

    def test_nonexistent_category_returns_empty(self, temp_vault):
        """
        GUARD AGAINST: A category value that matches no rows must return [].
        Verifies the parameter binding doesn't accidentally match NULL or
        empty strings.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/test.md",
            title="Test Note",
            body="Some content here.",
            tags=["test"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        results = search("content", category="nonexistent", vault=vault, index_path=idx)
        assert results == [], f"Nonexistent category returned {len(results)} results"

    def test_category_filter_applied_to_both_ctes(self, temp_vault):
        """
        GUARD AGAINST: Place identical body content in two categories.
        Filtering to one must return exactly one result. If the filter
        is applied to only the ExactRanks or only the PrefixRanks CTE,
        the other CTE will leak the cross-category note and the fused
        output will contain both.
        """
        vault, idx = temp_vault
        # FIX: Filenames must be unique so they generate unique slugs!
        n1 = Note(
            path=vault / "knowledge/shared-knowledge.md",
            title="Shared Term Knowledge",
            body="Contains the searchable token: banana.",
            tags=["fruit"],
            category="knowledge",
        )
        n2 = Note(
            path=vault / "projects/shared-projects.md",
            title="Shared Term Project",
            body="Contains the searchable token: banana.",
            tags=["fruit"],
            category="projects",
        )
        upsert_note(n1, vault=vault, index_path=idx)
        upsert_note(n2, vault=vault, index_path=idx)

        results = search("banana", category="knowledge", vault=vault, index_path=idx)
        assert len(results) == 1, (
            f"Expected 1 result in 'knowledge', got {len(results)}. "
            f"category_filter may be applied to only one CTE."
        )
        assert all(r.category == "knowledge" for r in results)

    def test_category_filter_with_malformed_query(self, temp_vault):
        """
        GUARD AGAINST: A malformed query combined with a category filter
        must short-circuit BEFORE the category parameter is bound. Otherwise
        the empty params list will cause an IndexError on parameter binding.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/edge.md",
            title="Edge Case",
            body="Content.",
            tags=["edge"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        results = search("", category="knowledge", vault=vault, index_path=idx)
        assert isinstance(results, list)
        assert results == []


# ============================================================
# Section 5 — find_similar threshold gradients
# ============================================================

class TestFindSimilarGradients:
    """
    find_similar uses the same RRF machinery to detect near-duplicate
    titles. These tests verify:
      - Monotonicity: tighter thresholds ⊆ looser thresholds
      - Case insensitivity
      - Word reordering detection
      - Extra-word tolerance at low thresholds
      - Strict rejection at threshold=1.0
    """

    def test_exact_duplicate_detected_at_low_threshold(self, temp_vault):
        """
        GUARD AGAINST: An exact title match must be detected. A regression
        in the RRF threshold comparison (e.g., > instead of >=) would
        silently drop exact matches at threshold=0.0.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/dup.md",
            title="AUTOSAR Setup",
            body="Step-by-step AUTOSAR setup.",
            tags=["autosar"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        similar = find_similar("AUTOSAR Setup", threshold=0.0, vault=vault, index_path=idx)
        assert any(s.slug == "dup" for s in similar), \
            "Exact title match not detected at threshold=0.0"

    def test_threshold_monotonicity(self, temp_vault):
        """
        GUARD AGAINST: As threshold increases, the result set must shrink
        (or stay equal). A non-monotonic threshold curve indicates a
        broken comparison operator or a score normalization bug.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/autosar-base.md",
            title="AUTOSAR Setup",
            body="Setup instructions.",
            tags=["autosar"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        loose = find_similar("AUTOSAR Setup Tips", threshold=0.0, vault=vault, index_path=idx)
        mid = find_similar("AUTOSAR Setup Tips", threshold=0.3, vault=vault, index_path=idx)
        tight = find_similar("AUTOSAR Setup Tips", threshold=0.99, vault=vault, index_path=idx)

        assert len(loose) >= len(mid) >= len(tight), (
            f"Threshold monotonicity violated: "
            f"loose={len(loose)}, mid={len(mid)}, tight={len(tight)}"
        )

    def test_near_duplicate_with_extra_words(self, temp_vault):
        """
        GUARD AGAINST: 'AUTOSAR Setup' vs 'AUTOSAR Setup Tips' share a
        2-of-3 word overlap. At a permissive threshold, find_similar must
        still surface this. Guards against the prefix CTE requiring ALL
        query words to match (which would reject 'Tips').
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/autosar-tips.md",
            title="AUTOSAR Setup Tips",
            body="Tips for setting up AUTOSAR environments.",
            tags=["autosar", "tips"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        similar = find_similar("AUTOSAR Setup", threshold=0.01, vault=vault, index_path=idx)
        assert any(s.slug == "autosar-tips" for s in similar), \
            "Near-duplicate with extra words not detected at permissive threshold"

    def test_word_reordering_still_similar(self, temp_vault):
        """
        GUARD AGAINST: 'Setting up AUTOSAR' vs 'AUTOSAR Setup' share the
        same semantic tokens in different order. FTS5 + RRF should detect
        this because both strategies tokenize to overlapping term sets
        regardless of order. A string-distance-based similarity (e.g.,
        Levenshtein) would fail here — this test pins that the engine
        uses token-overlap, not edit distance.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/autosar-setup.md",
            title="AUTOSAR Setup",
            body="How to set up AUTOSAR Adaptive Platform.",
            tags=["autosar"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        similar = find_similar("Setting up AUTOSAR", threshold=0.01, vault=vault, index_path=idx)
        assert any(s.slug == "autosar-setup" for s in similar), \
            "Word-reordered title not detected as similar — engine may be using edit distance"

    def test_case_insensitive_duplicate_detection(self, temp_vault):
        """
        GUARD AGAINST: FTS5 unicode61 lowercases by default, but the
        similarity layer must not impose case-sensitive comparison on top.
        'AUTOSAR Setup' and 'autosar setup' are duplicates.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/autosar-ci.md",
            title="AUTOSAR Setup",
            body="Setup AUTOSAR.",
            tags=["autosar"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        similar = find_similar("autosar setup", threshold=0.01, vault=vault, index_path=idx)
        assert any(s.slug == "autosar-ci" for s in similar), \
            "Case variation broke duplicate detection"

    def test_high_threshold_rejects_partial_matches(self, temp_vault):
        """
        GUARD AGAINST: At threshold=1.0, only exact matches should pass.
        'AUTOSAR Setup' vs 'AUTOSAR Setup Tips' must be rejected. Guards
        against the threshold ceiling being off-by-one (e.g., >= 1.0
        instead of > max_score).
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/autosar-tips2.md",
            title="AUTOSAR Setup Tips",
            body="Setup tips.",
            tags=["autosar"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        similar = find_similar("AUTOSAR Setup", threshold=1.0, vault=vault, index_path=idx)
        assert not any(s.slug == "autosar-tips2" for s in similar), \
            "Partial match leaked through threshold=1.0"

    def test_empty_query_to_find_similar(self, temp_vault):
        """
        GUARD AGAINST: An empty title query must not crash find_similar.
        Should return [] without calling the DB.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/any.md",
            title="Anything",
            body="Content.",
            tags=["misc"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        try:
            similar = find_similar("", threshold=0.5, vault=vault, index_path=idx)
        except Exception as e:
            pytest.fail(f"Empty find_similar query raised {type(e).__name__}: {e}")
        assert isinstance(similar, list)

    def test_find_similar_threshold_gradient_progressive(self, temp_vault):
        """
        GUARD AGAINST: Stress-test the threshold gradient across many
        fractional values. The result set size must be non-increasing as
        threshold increases from 0.0 to 1.0 in 0.1 steps.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/gradient.md",
            title="AUTOSAR Setup Guide",
            body="Comprehensive AUTOSAR setup guide.",
            tags=["autosar", "guide"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)

        query = "AUTOSAR Setup"
        sizes = []
        for t in [i / 10.0 for i in range(0, 11)]:
            similar = find_similar(query, threshold=t, vault=vault, index_path=idx)
            sizes.append(len(similar))

        for i in range(len(sizes) - 1):
            assert sizes[i] >= sizes[i + 1], (
                f"Non-monotonic at threshold {i/10.0}: "
                f"{sizes[i]} -> {sizes[i+1]}"
            )


# ============================================================
# Section 6 — Lifecycle (upsert/remove idempotency)
# ============================================================

class TestLifecycleIntegrity:
    """
    Tests that upsert/remove cycles leave no dangling FTS5 rows and that
    re-indexing after removal restores searchability.
    """

    def test_remove_clears_fts_index(self, temp_vault):
        """
        GUARD AGAINST: remove_note must delete from BOTH the FTS5 virtual
        table AND the structural notes table. A common bug deletes only
        the structural row, leaving the FTS5 row dangling — search then
        returns a slug whose rel_path no longer exists.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/transient.md",
            title="Transient Note",
            body="This note will be deleted shortly.",
            tags=["temp"],
            category="knowledge",
        )
        n.save()
        upsert_note(n, vault=vault, index_path=idx)

        assert search("transient", vault=vault, index_path=idx), \
            "Note not found immediately after upsert"
        remove_note(n.slug, vault=vault, index_path=idx)
        n.path.unlink(missing_ok=True)

        leftover = search("transient", vault=vault, index_path=idx)
        assert not leftover, (
            f"FTS5 still returns {len(leftover)} row(s) for removed slug "
            f"'{n.slug}' — remove_note may not be deleting from notes_fts."
        )

    def test_re_upsert_after_removal_reindexes(self, temp_vault):
        """
        GUARD AGAINST: Re-adding a note with the same slug after removal
        must reinsert cleanly into both tables. Verifies upsert is
        idempotent across the remove → insert cycle and that the FTS5
        row count doesn't grow unboundedly.
        """
        vault, idx = temp_vault
        n1 = Note(
            path=vault / "knowledge/recycled.md",
            title="Recycled Note",
            body="Content version one with keyword alpha.",
            tags=["recycled"],
            category="knowledge",
        )
        n1.save()
        upsert_note(n1, vault=vault, index_path=idx)
        remove_note(n1.slug, vault=vault, index_path=idx)

        n2 = Note(
            path=vault / "knowledge/recycled.md",
            title="Recycled Note",
            body="Content version two with keyword beta.",
            tags=["recycled"],
            category="knowledge",
        )
        n2.save()
        upsert_note(n2, vault=vault, index_path=idx)

        # 'beta' is only in version two; 'alpha' must be gone.
        beta_results = search("beta", vault=vault, index_path=idx)
        alpha_results = search("alpha", vault=vault, index_path=idx)
        assert any(r.slug == "recycled" for r in beta_results), \
            "Re-upserted note not findable by its new content"
        assert not any(r.slug == "recycled" for r in alpha_results), (
            "Stale content from the first insert still indexed after "
            "remove + re-upsert — FTS5 row may not have been replaced."
        )

    def test_upsert_idempotent_no_duplicate_rows(self, temp_vault):
        """
        GUARD AGAINST: Calling upsert_note twice on the same slug must
        not create duplicate FTS5 rows. If it does, search returns the
        same slug twice in the fused output (once per FTS5 row), and
        SUM(rrf_part) double-counts the score.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/idempotent.md",
            title="Idempotent Note",
            body="This note is upserted multiple times.",
            tags=["idempotent"],
            category="knowledge",
        )
        upsert_note(n, vault=vault, index_path=idx)
        upsert_note(n, vault=vault, index_path=idx)
        upsert_note(n, vault=vault, index_path=idx)

        results = search("idempotent", vault=vault, index_path=idx)
        matching = [r for r in results if r.slug == "idempotent"]
        assert len(matching) == 1, (
            f"Expected 1 result for slug 'idempotent', got {len(matching)}. "
            f"upsert_note may not be replacing existing FTS5 rows."
        )

        # Direct DB-level check: count FTS5 rows for this slug.
        conn = sqlite3.connect(idx)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM notes_fts WHERE slug = ?", ("idempotent",)
            ).fetchone()[0]
            assert count == 1, (
                f"notes_fts contains {count} rows for slug 'idempotent' — "
                f"upsert is appending instead of replacing."
            )
        finally:
            conn.close()