"""
tests/test_cli.py — CLI-layer tests for `brain remember --body-file` and `brain import`.

Uses typer.testing.CliRunner with a temporary vault (BRAIN_VAULT) so no real
notes are touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from brain.cli import app
from brain.config import ensure_structure, get_vault_dir
from brain.index import init_db, rebuild_index, upsert_note
from brain.vault import Note, load_note, note_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture(autouse=True)
def temp_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point BRAIN_VAULT to a temporary directory and initialise the index."""
    vault = tmp_path / ".brain"
    vault.mkdir(exist_ok=True)
    monkeypatch.setenv("BRAIN_VAULT", str(vault))
    ensure_structure(vault)
    init_db(get_vault_dir() / ".brain_index.db")
    return vault


def invoke(*args: str, input: str | None = None) -> str:
    """Run a brain CLI command via CliRunner and return stdout."""
    result = runner.invoke(app, args, input=input)
    assert result.exit_code == 0, f"CLI exit {result.exit_code}: {result.stdout}\n{result.stderr}"
    return result.stdout


# ---------------------------------------------------------------------------
# Tests: brain remember --body-file
# ---------------------------------------------------------------------------


class TestRememberBodyFile:
    def test_simple_text(self, tmp_path: Path) -> None:
        """A small body file is saved correctly."""
        body_file = tmp_path / "body.txt"
        body_file.write_text("Hello world", encoding="utf-8")
        out = invoke("remember", "Simple note", "--body-file", str(body_file), "--json")
        data = json.loads(out)
        assert data["status"] == "saved"
        # verify the persisted note
        note = load_note(Path(get_vault_dir()) / data["path"])
        assert note.body == "Hello world"

    def test_multiline_with_special_chars(self, tmp_path: Path) -> None:
        """Multiline body with quotes, backticks, dollars, and code fences."""
        body = (
            '# Heading\n\n'
            'Some `code` and a "$VAR" reference.\n\n'
            '```python\n'
            'def hello():\n'
            '    print("hello")\n'
            '```\n\n'
            'Line with "double quotes" and \'single quotes\'.'
        )
        body_file = tmp_path / "body.md"
        body_file.write_text(body, encoding="utf-8")
        out = invoke("remember", "Special chars", "--body-file", str(body_file), "--json")
        data = json.loads(out)
        assert data["status"] == "saved"
        note = load_note(Path(get_vault_dir()) / data["path"])
        assert note.body == body, f"Body mismatch:\n  expected:\n{body}\n  got:\n{note.body}"

    def test_takes_precedence_over_body(self, tmp_path: Path) -> None:
        """When both --body-file and --body are given, --body-file wins."""
        body_file = tmp_path / "body.txt"
        body_file.write_text("from file", encoding="utf-8")
        out = invoke(
            "remember", "Precedence",
            "--body-file", str(body_file),
            "--body", "from arg",
            "--json",
        )
        data = json.loads(out)
        assert data["status"] == "saved"
        note = load_note(Path(get_vault_dir()) / data["path"])
        assert note.body == "from file"


# ---------------------------------------------------------------------------
# Tests: brain import
# ---------------------------------------------------------------------------


class TestImport:
    def test_valid_spec(self, tmp_path: Path) -> None:
        """A valid JSON spec file is saved correctly."""
        spec = {
            "title": "Imported note",
            "body": "Body from import\nwith a second line.",
            "tags": ["tag1", "tag2"],
            "category": "knowledge",
        }
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        out = invoke("import", str(spec_file), "--json")
        data = json.loads(out)
        assert data["status"] == "saved"
        assert data["title"] == "Imported note"
        note = load_note(Path(get_vault_dir()) / data["path"])
        assert note.body == "Body from import\nwith a second line."
        assert note.tags == ["tag1", "tag2"]
        assert note.category == "knowledge"

    def test_stdin(self) -> None:
        """Passing '-' reads the spec from stdin."""
        spec = json.dumps({"title": "Stdin note", "body": "from stdin"})
        out = invoke("import", "-", "--json", input=spec)
        data = json.loads(out)
        assert data["status"] == "saved"
        assert data["title"] == "Stdin note"

    def test_tags_as_comma_string(self, tmp_path: Path) -> None:
        """Tags can be a comma-separated string instead of a list."""
        spec = {"title": "String tags", "body": "body", "tags": "a,b,c"}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        out = invoke("import", str(spec_file), "--json")
        data = json.loads(out)
        assert data["status"] == "saved"
        note = load_note(Path(get_vault_dir()) / data["path"])
        assert note.tags == ["a", "b", "c"]

    def test_defaults(self, tmp_path: Path) -> None:
        """Minimal spec uses defaults (category=inbox, body='', tags=[])."""
        spec = {"title": "Minimal note"}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        out = invoke("import", str(spec_file), "--json")
        data = json.loads(out)
        assert data["status"] == "saved"
        note = load_note(Path(get_vault_dir()) / data["path"])
        assert note.body == ""
        assert note.tags == []
        assert note.category == "inbox"

    def test_duplicate_warning(self, tmp_path: Path) -> None:
        """Importing a note with a similar title emits duplicate_warning."""
        # save a first note and index it
        note = Note(
            path=note_path("Duplicate test", "inbox", get_vault_dir()),
            title="Duplicate test",
            body="original",
        )
        note.save()
        upsert_note(note, get_vault_dir())
        # import a second note with the same title (no force)
        spec = {"title": "Duplicate test", "body": "copy"}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        out = invoke("import", str(spec_file), "--json")
        data = json.loads(out)
        assert data["status"] == "duplicate_warning"
        assert len(data["similar"]) >= 1

    def test_duplicate_force(self, tmp_path: Path) -> None:
        """Importing with force: true overwrites despite duplicates."""
        # save a first note and index it
        note = Note(
            path=note_path("Force test", "inbox", get_vault_dir()),
            title="Force test",
            body="original",
        )
        note.save()
        upsert_note(note, get_vault_dir())
        # import with force: true
        spec = {"title": "Force test", "body": "overwritten", "force": True}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        out = invoke("import", str(spec_file), "--json")
        data = json.loads(out)
        assert data["status"] == "saved"
        note = load_note(Path(get_vault_dir()) / data["path"])
        assert note.body == "overwritten"

    def test_parse_error(self, tmp_path: Path) -> None:
        """Malformed JSON produces a clear error message."""
        spec_file = tmp_path / "bad.json"
        spec_file.write_text("{bad json}", encoding="utf-8")
        result = runner.invoke(app, ["import", str(spec_file), "--json"])
        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert data["status"] == "error"
        assert "Invalid JSON" in data["error"]

    def test_missing_file(self) -> None:
        """Missing spec file produces a clear error message."""
        result = runner.invoke(app, ["import", "nonexistent.json", "--json"])
        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert data["status"] == "error"
        assert "Cannot read" in data["error"]

    def test_missing_title(self, tmp_path: Path) -> None:
        """Spec without title produces a clear error message."""
        spec = {"body": "no title"}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        result = runner.invoke(app, ["import", str(spec_file), "--json"])
        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert data["status"] == "error"
        assert "title" in data["error"].lower()