"""
config.py — paths and settings for the brain vault.

The vault directory is resolved in this priority order:
1. $BRAIN_VAULT env var
2. ~/.brain  (default)

The index is always stored inside the vault as `.brain_index.db`.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path


def get_vault_dir() -> Path:
    """Return the root directory of the knowledge vault."""
    env = os.environ.get("BRAIN_VAULT")
    if env:
        p = Path(env).expanduser().resolve()
    else:
        p = Path.home() / ".brain"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_index_path(vault: Path | None = None) -> Path:
    """Return the SQLite FTS5 index file path."""
    v = vault or get_vault_dir()
    return v / ".brain_index.db"


# Default category
DEFAULT_CATEGORY = "knowledge"

# Default sub-directories inside the vault (created by ensure_structure)
CATEGORIES = ("knowledge",)


def ensure_structure(vault: Path | None = None) -> None:
    """Create the standard sub-directory layout inside the vault."""
    v = vault or get_vault_dir()
    for cat in CATEGORIES:
        (v / cat).mkdir(exist_ok=True)
