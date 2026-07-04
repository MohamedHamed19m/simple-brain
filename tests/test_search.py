import pytest
from pathlib import Path
import sqlite3

from brain.config import ensure_structure
from brain.vault import Note
from brain.index import init_db, upsert_note
from brain.search import search, find_similar

@pytest.fixture
def temp_vault(tmp_path: Path):
    """Fixture creating a mock vault directory structure and temporary DB."""
    vault_dir = tmp_path / "mock_brain"
    ensure_structure(vault_dir)
    
    index_path = vault_dir / ".brain_index.db"
    return vault_dir, index_path

def test_rrf_exact_vs_prefix_ranking(temp_vault):
    """Verify that RRF surfaces exact phrase hits alongside partial word wildcard matches."""
    vault, idx = temp_vault

    # Note 1: Contains exact targeted technical phrase "vsomeip"
    n1 = Note(
        path=vault / "knowledge/vsomeip-core.md",
        title="vsomeip Core Setup",
        body="This cheat sheet documents how to configure vsomeip middleware firewalls.",
        category="knowledge"
    )
    # Note 2: Contains word starting with target ("vsomeip-extensions") but not pure string
    n2 = Note(
        path=vault / "knowledge/vsomeip-ext.md",
        title="Extended systems",
        body="Notes on working with vsomeipextensions in legacy components.",
        category="knowledge"
    )

    upsert_note(n1, vault=vault, index_path=idx)
    upsert_note(n2, vault=vault, index_path=idx)

    # Search for "vsomeip" -> should rank the exact body match higher or equal to the extension
    results = search("vsomeip", vault=vault, index_path=idx)
    assert len(results) >= 1
    assert results[0].slug == "vsomeip-core"


def test_rrf_prefix_wildcard_fallback(temp_vault):
    """Ensure partial searches (like 'open' for 'openssl') rank properly via the RRF prefix strategy."""
    vault, idx = temp_vault

    n = Note(
        path=vault / "skills/security.md",
        title="OpenSSL Configurations",
        body="Instructions on upgrading openssl and debugging TLS handshakes on dev environments.",
        category="skills"
    )
    upsert_note(n, vault=vault, index_path=idx)

    # Search for "open" -> standard BM25 will miss "openssl", but the prefix branch captures it
    results = search("open", vault=vault, index_path=idx)
    assert len(results) == 1
    assert results[0].slug == "security"
    assert "openssl" in results[0].snippet.lower()


def test_category_filtering(temp_vault):
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

    # Restrict search strictly to 'projects'
    results = search("toolchain", category="projects", vault=vault, index_path=idx)
    assert len(results) == 1
    assert results[0].category == "projects"
    assert results[0].slug == "wsl-setup"


def test_find_similar_duplicate_detection(temp_vault):
    """Ensure find_similar returns duplicate titles using the modified RRF threshold limits."""
    vault, idx = temp_vault

    n = Note(
        path=vault / "knowledge/autosar-she.md",
        title="AUTOSAR Hardware Security",
        body="Deep dive on hardware secure extensions protocols.",
        category="knowledge"
    )
    upsert_note(n, vault=vault, index_path=idx)

    # Checking for similarity against a nearby structural match
    similar = find_similar("AUTOSAR Hardware Security Details", threshold=0.01, vault=vault, index_path=idx)
    assert len(similar) == 1
    assert similar[0].slug == "autosar-she"