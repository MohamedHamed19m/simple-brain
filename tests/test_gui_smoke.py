"""
tests/test_gui_smoke.py — Smoke tests for the customtkinter GUI.

customtkinter requires a display (Tk root).  On headless CI runners these
tests are skipped gracefully.

Note: tkinter allows only ONE Tk root per process.  Once destroyed, the Tcl
interpreter is in an undefined state and no new root can be created.  We
use a single module-scoped fixture so all tests share one app instance.
Non-GUI data-layer tests (save, delete, new) are covered by test_search.py
and test_complex_memory.py — the GUI is a thin widget wrapper.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Module-level display check (cached, runs once per process)
# ---------------------------------------------------------------------------

_HAS_DISPLAY: bool | None = None


def check_display() -> bool:
    global _HAS_DISPLAY
    if _HAS_DISPLAY is not None:
        return _HAS_DISPLAY
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        _HAS_DISPLAY = False
        return False
    try:
        import tkinter as tk

        r = tk.Tk()
        r.destroy()
        _HAS_DISPLAY = True
    except Exception:
        _HAS_DISPLAY = False
    return _HAS_DISPLAY


# ---------------------------------------------------------------------------
# Module-scoped fixture: one vault + one app for all tests in this file
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def seeded_vault(tmp_path_factory: pytest.TempPathFactory):
    """Pre-populated vault used by the app fixture."""
    from brain.config import CATEGORIES, ensure_structure, get_index_path
    from brain.index import init_db, upsert_note
    from brain.vault import Note

    vault_dir = tmp_path_factory.mktemp("mock_brain")
    ensure_structure(vault_dir)
    index_path = get_index_path(vault_dir)
    init_db(index_path)

    notes = [
        Note(
            path=vault_dir / "knowledge/openssl.md",
            title="OpenSSL Setup",
            body="Configuration for OpenSSL on dev machines.",
            tags=["openssl", "security"],
            category="knowledge",
        ),
        Note(
            path=vault_dir / "skills/cpp11.md",
            title="C++11 Patterns",
            body="Modern C++11 concurrency patterns.",
            tags=["cpp", "cpp11"],
            category="skills",
        ),
        Note(
            path=vault_dir / "journal/daily.md",
            title="Daily Log",
            body="Worked on the TLS handshake issue.",
            tags=["daily"],
            category="journal",
        ),
        Note(
            path=vault_dir / "projects/brain-gui.md",
            title="Brain GUI Project",
            body="Planning the customtkinter interface.",
            tags=["gui", "planning"],
            category="projects",
        ),
        Note(
            path=vault_dir / "inbox/random.md",
            title="Random Idea",
            body="A random thought for later.",
            tags=["idea"],
            category="inbox",
        ),
    ]
    for n in notes:
        n.save()
        upsert_note(n, vault=vault_dir, index_path=index_path)

    return vault_dir, index_path


@pytest.fixture(scope="module")
def app(seeded_vault):
    """Single BrainApp shared by all tests in this module."""
    vault, _idx = seeded_vault
    if not check_display():
        pytest.skip("customtkinter or display not available")
    from brain.gui import BrainApp

    ap = BrainApp(vault=vault)
    yield ap
    # Don't destroy on teardown — Tcl interpreter can't be recreated.
    # The OS cleans up when the process exits.
    pass


# ---------------------------------------------------------------------------
# Tests  (all read-only, non-destructive, share one app)
# ---------------------------------------------------------------------------

class TestGUISmoke:
    """Read-only smoke tests for the BrainApp widget tree."""

    def test_app_loads_all_notes(self, app):
        """All 5 seeded notes are loaded and visible."""
        assert len(app.all_notes) == 5
        assert len(app.filtered_notes) == 5

    def test_sidebar_shows_categories_with_counts(self, app):
        """Sidebar has 'All' and all 5 standard categories with '1'."""
        cat_widgets = app.cat_scroll.winfo_children()
        cat_texts = [w.cget("text") for w in cat_widgets if hasattr(w, "cget")]

        assert any("All" in t for t in cat_texts[0:1]), "Missing 'All' button"
        for cat in ("knowledge", "skills", "journal", "projects", "inbox"):
            assert any(
                cat in t and "1" in t for t in cat_texts
            ), f"Missing {cat} category"

    def test_note_list_has_content(self, app):
        """Note list scrollable frame has visible children."""
        note_rows = app.note_scroll.winfo_children()
        assert len(note_rows) > 0, "Note list is empty"

    def test_sidebar_click_selects_category(self, app):
        """Clicking a category filters the note list."""
        # Click "knowledge"
        for w in app.cat_scroll.winfo_children():
            if hasattr(w, "cget") and "knowledge" in w.cget("text"):
                w.invoke()
                break

        assert app.current_category == "knowledge"
        assert len(app.filtered_notes) == 1
        assert app.filtered_notes[0].category == "knowledge"
        assert app.filtered_notes[0].slug == "openssl"

        # Reset to "All"
        for w in app.cat_scroll.winfo_children():
            if hasattr(w, "cget") and "All" in w.cget("text"):
                w.invoke()
                break
        assert app.current_category is None

    def test_search_finds_notes(self, app):
        """Search box + _do_search filters by content."""
        app.search_var.set("openssl")
        app._do_search()
        assert len(app.filtered_notes) >= 1
        assert any("openssl" in n.title.lower() for n in app.filtered_notes)

        app._clear_search()
        assert len(app.filtered_notes) == 5

    def test_select_note_loads_editor(self, app):
        """Clicking a note fills title/tags/category/body in the editor."""
        openssl = next(n for n in app.all_notes if n.slug == "openssl")
        app._select_note(openssl)

        assert app.title_entry.get() == "OpenSSL Setup"
        assert "Configuration for OpenSSL" in app.body_text.get("0.0", "end")
        assert app.cat_combo.get() == "knowledge"

    def test_clear_editor_resets(self, app):
        """'Clear' empties the editor and sets current_note to None."""
        app._clear_editor()
        assert app.title_entry.get() == ""
        assert app.body_text.get("0.0", "end").strip() == ""
        assert app.current_note is None

    def test_stats_data_is_correct(self, app):
        """The data behind the Stats modal matches expectations."""
        from collections import Counter

        counts = Counter(n.category for n in app.all_notes)
        for cat in ("knowledge", "skills", "journal", "projects", "inbox"):
            assert counts[cat] == 1
        assert len(app.all_notes) == 5


class TestGUIEdgeCases:
    """Non-GUI edge-cases exercised by the GUI path (no Tk root needed)."""

    def test_rebuild_index_then_search(self, seeded_vault):
        """Rebuild doesn't break searchability (tested via brain.* API)."""
        from brain.index import rebuild_index
        from brain.search import search

        vault, idx = seeded_vault
        count = rebuild_index(vault=vault, index_path=idx)
        assert count >= 5

        results = search("openssl", vault=vault, index_path=idx)
        assert len(results) >= 1