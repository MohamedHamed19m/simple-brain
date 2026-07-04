"""
vault.py — read/write markdown notes with YAML frontmatter.

Each note is a `.md` file anywhere under the vault directory.
Frontmatter fields:
  title:    str         (required)
  tags:     list[str]   (optional)
  created:  ISO date    (auto-set on first write)
  updated:  ISO date    (auto-set on every write)
  category: str         (optional, defaults to 'inbox')
"""

from __future__ import annotations

import re
import textwrap
from datetime import date, datetime
from pathlib import Path
from typing import Generator

import frontmatter

from brain.config import get_vault_dir


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Note:
    """In-memory representation of a single markdown note."""

    def __init__(
        self,
        path: Path,
        title: str,
        body: str,
        tags: list[str] | None = None,
        category: str = "inbox",
        created: str | None = None,
        updated: str | None = None,
        extra: dict | None = None,
    ) -> None:
        self.path = path
        self.title = title
        self.body = body.strip()
        self.tags = tags or []
        self.category = category
        self.created = created or date.today().isoformat()
        self.updated = updated or datetime.now().isoformat(timespec="seconds")
        self.extra = extra or {}

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def to_post(self) -> frontmatter.Post:
        meta: dict = {
            "title": self.title,
            "tags": self.tags,
            "category": self.category,
            "created": self.created,
            "updated": self.updated,
            **self.extra,
        }
        return frontmatter.Post(self.body, **meta)

    def save(self) -> None:
        """Write (or overwrite) the note to disk."""
        self.updated = datetime.now().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            frontmatter.dumps(self.to_post()), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def slug(self) -> str:
        return self.path.stem

    @property
    def rel_path(self) -> str:
        """Path relative to the vault root."""
        try:
            return str(self.path.relative_to(get_vault_dir()))
        except ValueError:
            return str(self.path)

    def snippet(self, max_chars: int = 300) -> str:
        """Return a short preview of the body, stripping markdown noise."""
        text = re.sub(r"#+\s*", "", self.body)   # headings
        text = re.sub(r"\*+", "", text)            # bold/italic
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links
        text = " ".join(text.split())
        return textwrap.shorten(text, width=max_chars, placeholder="…")

    def full_text(self) -> str:
        """Combined title + tags + body for indexing."""
        tag_str = " ".join(self.tags)
        return f"{self.title}\n{tag_str}\n{self.body}"


# ---------------------------------------------------------------------------
# Factory — load from file
# ---------------------------------------------------------------------------

def load_note(path: Path) -> Note:
    post = frontmatter.load(str(path))
    return Note(
        path=path,
        title=str(post.get("title", path.stem)),
        body=post.content,
        tags=list(post.get("tags") or []),
        category=str(post.get("category", "inbox")),
        created=str(post.get("created", "")),
        updated=str(post.get("updated", "")),
        extra={
            k: v
            for k, v in post.metadata.items()
            if k not in ("title", "tags", "category", "created", "updated")
        },
    )


# ---------------------------------------------------------------------------
# Vault scanning
# ---------------------------------------------------------------------------

def iter_notes(vault: Path | None = None) -> Generator[Note, None, None]:
    """Yield every note in the vault, skipping hidden files."""
    root = vault or get_vault_dir()
    for md in sorted(root.rglob("*.md")):
        # skip dotfiles / hidden dirs
        if any(part.startswith(".") for part in md.parts):
            continue
        try:
            yield load_note(md)
        except Exception:
            # malformed frontmatter — skip silently
            pass


def find_note(slug_or_path: str, vault: Path | None = None) -> Note | None:
    """Find a note by slug (stem) or relative path."""
    root = vault or get_vault_dir()
    # exact path match
    p = root / slug_or_path
    if p.exists():
        return load_note(p)
    # stem match — search recursively
    for md in root.rglob("*.md"):
        if md.stem == slug_or_path:
            return load_note(md)
    return None


def note_path(title: str, category: str = "inbox", vault: Path | None = None) -> Path:
    """Generate a filesystem path for a new note based on title + category."""
    root = vault or get_vault_dir()
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return root / category / f"{slug}.md"


def delete_note(slug_or_path: str, vault: Path | None = None) -> bool:
    """Delete a note. Returns True if it was deleted."""
    note = find_note(slug_or_path, vault)
    if note and note.path.exists():
        note.path.unlink()
        return True
    return False
