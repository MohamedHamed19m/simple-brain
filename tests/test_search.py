"""
tests/test_search.py
Edge-case stress tests for simple-brain's SQLite FTS5 + Python-side RRF search engine.
Validates sanitization, RRF math, category isolation, and find_similar thresholds.
"""
import pytest
from pathlib import Path
import sqlite3
from brain.config import ensure_structure
from brain.vault import Note
from brain.index import init_db, upsert_note, remove_note
from brain.search import search, find_similar

@pytest.fixture
def temp_vault(tmp_path: Path):
    """Fixture creating a mock vault directory structure and temporary DB."""
    vault_dir = tmp_path / "mock_brain"
    vault_dir.mkdir(parents=True, exist_ok=True)
    ensure_structure(vault_dir)
    index_path = vault_dir / ".brain_index.db"
    init_db(index_path)
    return vault_dir, index_path

# ============================================================
# 1. Core RRF & Strategy Logic
# ============================================================
class TestRRFStrategies:
    def test_rrf_exact_vs_prefix_ranking(self, temp_vault):
        """Verify that RRF surfaces exact phrase hits alongside partial word wildcard matches."""
        vault, idx = temp_vault
        n1 = Note(
            path=vault / "knowledge/vsomeip-core.md",
            title="vsomeip Core Setup",
            body="This cheat sheet documents how to configure vsomeip middleware firewalls.",
            category="knowledge"
        )
        n2 = Note(
            path=vault / "knowledge/vsomeip-ext.md",
            title="Extended systems",
            body="Notes on working with vsomeipextensions in legacy components.",
            category="knowledge"
        )
        upsert_note(n1, vault=vault, index_path=idx)
        upsert_note(n2, vault=vault, index_path=idx)

        results = search("vsomeip", vault=vault, index_path=idx)
        assert len(results) >= 1
        assert results[0].slug == "vsomeip-core"

    def test_rrf_prefix_wildcard_fallback(self, temp_vault):
        """Ensure partial searches (like 'open' for 'openssl') rank properly via the RRF prefix strategy."""
        vault, idx = temp_vault
        n = Note(
            path=vault / "skills/security.md",
            title="OpenSSL Configurations",
            body="Instructions on upgrading openssl and debugging TLS handshakes on dev environments.",
            category="skills"
        )
        upsert_note(n, vault=vault, index_path=idx)

        results = search("open", vault=vault, index_path=idx)
        assert len(results) == 1
        assert results[0].slug == "security"
        assert "openssl" in results[0].snippet.lower()
        
# ============================================================
# 2. Sanitization & Hostile Queries
# ============================================================
class TestSanitization:
    @pytest.mark.parametrize("hostile_query", [
        "ara::com",       # double colons (FTS5 column filter syntax)
        "C++11",          # plus signs
        "/dev/ttyUSB0",   # unix path
        "title:openssl",  # FTS5 column filter syntax
        '"unclosed',      # unbalanced quotes
        "***",            # pure wildcards
        "   ",            # whitespace
        "",               # empty
    ])
    def test_hostile_queries_do_not_crash(self, temp_vault, hostile_query):
        """
        GUARD AGAINST: sqlite3.OperationalError from FTS5 syntax errors.
        The _sanitize_query layer must neutralize these inputs safely 
        by stripping non-word characters and collapsing spaces.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/padding.md",
            title="Padding Note",
            body="Some content to ensure the index is non-empty.",
            category="knowledge"
        )
        upsert_note(n, vault=vault, index_path=idx)
        
        # Must not raise
        results = search(hostile_query, vault=vault, index_path=idx)
        assert isinstance(results, list)

    def test_fts5_operators_disable_prefix_strategy(self, temp_vault):
        """
        GUARD AGAINST: If a query contains 'AND', the prefix strategy must 
        be disabled to prevent FTS5 syntax errors (e.g., appending '*' to 'AND').
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/ops.md",
            title="Boolean Logic",
            body="Testing AND OR NOT operators in the query string.",
            category="knowledge"
        )
        upsert_note(n, vault=vault, index_path=idx)
        
        # Must not raise, and should correctly find the note using exact match
        results = search("testing AND operators", vault=vault, index_path=idx)
        assert isinstance(results, list)
        assert any(r.slug == "ops" for r in results)

# ============================================================
# 3. Category Filtering
# ============================================================
class TestCategoryFilter:
    def test_category_filtering(self, temp_vault):
        """Ensure that the category filter correctly isolates matches."""
        vault, idx = temp_vault
        n1 = Note(
            path=vault / "projects/wsl-setup.md",
            title="WSL2 Migration",
            body="Configuring compiling toolchains inside an active environment.",
            category="projects"
        )
        n2 = Note(
            path=vault / "journal/daily.md",
            title="Daily Standup",
            body="Worked on setting up cross-compiling toolchains on my platform environment.",
            category="journal"
        )
        upsert_note(n1, vault=vault, index_path=idx)
        upsert_note(n2, vault=vault, index_path=idx)

        results = search("toolchain", category="projects", vault=vault, index_path=idx)
        assert len(results) == 1
        assert results[0].category == "projects"
        assert results[0].slug == "wsl-setup"

    def test_nonexistent_category_returns_empty(self, temp_vault):
        """Ensure filtering by a non-existent category returns an empty list."""
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/test.md",
            title="Test Note",
            body="Some content here.",
            category="knowledge"
        )
        upsert_note(n, vault=vault, index_path=idx)
        
        results = search("content", category="nonexistent", vault=vault, index_path=idx)
        assert results == []

# ============================================================
# 4. find_similar Thresholds
# ============================================================
class TestFindSimilar:
    def test_find_similar_duplicate_detection(self, temp_vault):
        """Ensure find_similar returns duplicate titles using the modified RRF threshold limits."""
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/autosar-she.md",
            title="AUTOSAR Hardware Security",
            body="Deep dive on hardware secure extensions protocols.",
            category="knowledge"
        )
        upsert_note(n, vault=vault, index_path=idx)

        similar = find_similar("AUTOSAR Hardware Security Details", threshold=0.01, vault=vault, index_path=idx)
        assert len(similar) >= 1
        assert any(s.slug == "autosar-she" for s in similar)

    def test_find_similar_threshold_monotonicity(self, temp_vault):
        """
        GUARD AGAINST: As threshold increases, result set size must shrink or stay equal.
        A non-monotonic curve indicates a broken comparison operator in the final filter.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/autosar-base.md",
            title="AUTOSAR Setup",
            body="Setup instructions.",
            category="knowledge"
        )
        upsert_note(n, vault=vault, index_path=idx)
        
        loose = find_similar("AUTOSAR Setup Tips", threshold=0.0, vault=vault, index_path=idx)
        tight = find_similar("AUTOSAR Setup Tips", threshold=0.05, vault=vault, index_path=idx)
        
        assert len(loose) >= len(tight)

    def test_find_similar_empty_query(self, temp_vault):
        """GUARD AGAINST: Empty title query must not crash find_similar or hit the DB."""
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/any.md",
            title="Anything",
            body="Content.",
            category="knowledge"
        )
        upsert_note(n, vault=vault, index_path=idx)
        
        similar = find_similar("", threshold=0.5, vault=vault, index_path=idx)
        assert similar == []

# ============================================================
# 5. Index Integrity & Lifecycle
# ============================================================
class TestIndexIntegrity:
    def test_upsert_idempotent_no_duplicate_rows(self, temp_vault):
        """
        GUARD AGAINST: Calling upsert_note twice on the same slug must
        not create duplicate FTS5 rows. If it does, the Python RRF loop 
        will process the same slug twice, double-counting the score.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/idempotent.md",
            title="Idempotent Note",
            body="This note is upserted multiple times.",
            category="knowledge"
        )
        upsert_note(n, vault=vault, index_path=idx)
        upsert_note(n, vault=vault, index_path=idx)
        upsert_note(n, vault=vault, index_path=idx)

        results = search("idempotent", vault=vault, index_path=idx)
        matching = [r for r in results if r.slug == "idempotent"]
        assert len(matching) == 1
        
        # Direct DB-level check to ensure FTS5 didn't append rows
        conn = sqlite3.connect(idx)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM notes_fts WHERE slug = ?", ("idempotent",)
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_remove_clears_fts_index(self, temp_vault):
        """
        GUARD AGAINST: remove_note must delete from BOTH the FTS5 virtual
        table AND the structural notes table. A common bug deletes only
        the structural row, leaving the FTS5 row dangling.
        """
        vault, idx = temp_vault
        n = Note(
            path=vault / "knowledge/transient.md",
            title="Transient Note",
            body="This note will be deleted shortly.",
            category="knowledge"
        )
        n.save()
        upsert_note(n, vault=vault, index_path=idx)
        
        assert search("transient", vault=vault, index_path=idx)
            
        remove_note(n.slug, vault=vault, index_path=idx)
        n.path.unlink(missing_ok=True)
        
        leftover = search("transient", vault=vault, index_path=idx)
        assert not leftover