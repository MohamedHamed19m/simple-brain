# Simple Brain — Personal Knowledge Base

A portable Python CLI compiled to a single executable that acts as your external memory — pure BM25 search over markdown files, no embedding models, no API keys, and no global Python dependencies required.

Designed specifically to work with Gemini CLI subagents (`@memory` and `@memory-curator`) by isolating memory retrieval from your main coding context.

---

## Installation & Portability

### 1. Compile to a Single Executable
Use PyInstaller via `uv` to build the standalone binary:
```bash
# Clean install dependencies
uv sync

# Compile to a single EXE
uv run pyinstaller --onefile --name brain --console brain/cli.py
```
The compiled executable will be generated at `./dist/brain.exe`.

### 2. Make it Global
Move or copy `dist/brain.exe` to any directory included in your system's `PATH` environment variable (e.g., `C:\Users\user\bin` or similar).

Once added, you can call it from any folder:
```bash
# Initialize your vault (defaults to ~/.brain/)
brain init

# Add a note
brain remember "OpenSSL MinGW Linking" --body "Mixing MSVC OpenSSL with MinGW causes linker errors. Use MinGW64 binaries consistently." --tags "c++,build,openssl" --category knowledge

# Query it
brain ask "how do I fix openssl linking"
```

---

## Commands

| Command | Description |
|---------|-------------|
| `brain init` | Create vault structure and initialize index |
| `brain ask <query>` | BM25 search + compress → structured answer |
| `brain remember <title>` | Add a note (opens `$EDITOR` or uses `--body`) |
| `brain forget <slug>` | Delete a note from the vault and the index |
| `brain show <slug>` | Print full note content |
| `brain list` | List all notes currently in the vault |
| `brain rebuild` | Rebuild the SQLite FTS5 search index from files |
| `brain stats` | View vault statistics and category counts |

All commands support the `--json` flag for machine-readable outputs.

---

## Vault Structure

The vault defaults to `~/.brain/`. You can override this location by setting the `BRAIN_VAULT` environment variable.

```
~/.brain/
├── knowledge/      # Permanent facts, solutions
├── skills/         # How-to guides, workflows
├── journal/        # Time-stamped reflections, logs
├── projects/       # Project-specific context
├── inbox/          # Unclassified / incoming notes (default)
└── .brain_index.db # SQLite FTS5 index (auto-managed)
```

---

## Search Syntax

`brain ask` supports full SQLite FTS5 syntax:

```bash
brain ask "openssl mingw"              # Implicit AND
brain ask "openssl AND mingw"          # Explicit AND
brain ask '"secure channel"'           # Phrase search
brain ask "open*"                      # Prefix match
brain ask "title:openssl"              # Field-specific search
```

*Column weights: Title × 5.0, Tags × 3.0, Body × 1.0*

---

## Subagents for Gemini CLI

Two custom subagent definitions are configured in `~/.gemini/agents/`:

1. **`@memory`** (`memory.md`): A read-only retrieval subagent that handles matching, reading, and summarizing notes. It has no write permissions.
2. **`@memory-curator`** (`memory_curator.md`): A read/write curator agent that checks for duplicates, updates files, and maintains index integrity.

Because both subagents use the global `brain` command, they run seamlessly as long as `brain.exe` is added to your environment `PATH`.
