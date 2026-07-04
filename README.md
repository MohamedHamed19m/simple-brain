# Simple Brain — Personal Knowledge Base

A portable, standalone Python CLI compiled to a single executable that acts as your external memory — pure BM25 search over markdown files, no embedding models, no API keys, and no global Python dependencies required.

Designed specifically to work with Gemini CLI subagents (`@brain_memory_agent`) by isolating memory retrieval and curation from your main coding context.

---

## Installation & Portability

### 1. Compile to a Single Executable
Use PyInstaller via `uv` to build the standalone binary:
```bash
# Clean install dependencies
uv sync

# Compile to a single EXE
uv run pyinstaller --onefile --name brain --console brain/cli.py

# Install globally in editable mode using uv
uv tool install --editable .                              
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

## Subagent and Skill Configuration

This repository packages a unified subagent and skill inside the `.gemini/` folder:

1. **`@brain_memory_agent`** (`.gemini/agents/brain_memory_agent.md`): A single subagent handling both **retrieval** (searching, showing notes) and **curation** (saving, updating, merging, rebuilding). It operates in an isolated context window to avoid context pollution in the main coding thread.
2. **`brain-memory` Skill** (`.gemini/skills/brain-memory/SKILL.md`): Instructs the main orchestrator (primary model) to automatically delegate all note searches, memory lookups, and curation requests directly to the `brain_memory_agent` subagent.


Add this Section into your main contentx file (Gemini.md)
### 🧠 Memory Management & Strict Delegation

**CRITICAL:** You MUST delegate all memory tasks to the **`@brain_memory_agent`**,

**When to Invoke `@brain_memory_agent`:**
* **Save/Remember:** To store solutions, context, or snippets (e.g., "save this", "remember"). (Subagent runs `brain remember`).
* **Search/Retrieve:** To recall past knowledge or query notes. (Subagent runs `brain ask/show`).
* **Manage:** To list notes, delete (`brain forget`), or update (`brain rebuild`).

**Data Handoff Protocol (Strict Rules):**
* **Process First:** You (the main orchestrator) must read files and synthesize the data *before* calling the subagent. The subagent is blind to your workspace.
* **Pass Value, Not Reference:** Provide the exact text to save directly in the prompt. NEVER pass file paths or ask the subagent to read files.
9 * **✅ CORRECT Handoff:** `@brain_memory_agent Save note 'NvM Tool'. Body: 'This Tool handles SWC integration...' Tags: autosar, nvm`
10
11 * **❌ INCORRECT Handoff:** `@brain_memory_agent Read Readme_Nvm_Tool.md and save it.`