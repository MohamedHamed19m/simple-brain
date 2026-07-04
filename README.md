# Brain — Personal Knowledge Base

A Python CLI that acts as your external memory — pure BM25 search over markdown files, no embedding models, no API keys required.

## Quick Start

```bash
# Install globally with uv
uv tool install simple-brain

# Or editable install for development
uv pip install -e .

# Initialize your vault
brain init

# Add a note
brain remember "OpenSSL MinGW Linking" --body "Mixing MSVC OpenSSL with MinGW causes linker errors. Use MinGW64 binaries consistently." --tags "c++,build,openssl" --category knowledge

# Query it
brain ask "how do I fix openssl linking"

# Machine-readable JSON (for AI agents)
brain ask "openssl mingw" --json
```

## Commands

| Command | Description |
|---------|-------------|
| `brain init` | Create vault structure and initialize index |
| `brain ask <query>` | BM25 search + compress → structured answer |
| `brain remember <title>` | Add a new note (opens `$EDITOR` or use `--body`) |
| `brain forget <slug>` | Delete a note |
| `brain show <slug>` | Print full note content |
| `brain list` | List all notes |
| `brain rebuild` | Rebuild FTS5 index from disk |
| `brain stats` | Vault statistics |

All commands support `--json` for machine-readable output.

## Vault Structure

The vault defaults to `~/.brain/`. Override with `$BRAIN_VAULT`:

```
~/.brain/
├── knowledge/      # permanent facts, solutions
├── skills/         # how-to guides
├── journal/        # time-stamped reflections
├── projects/       # project-specific context
├── inbox/          # unclassified (default)
└── .brain_index.db # SQLite FTS5 index (auto-managed)
```

## Search Syntax

`brain ask` supports full SQLite FTS5 syntax:

```bash
brain ask "openssl mingw"              # implicit AND
brain ask "openssl AND mingw"          # explicit AND
brain ask '"secure channel"'           # phrase search
brain ask "open*"                      # prefix match
brain ask "title:openssl"              # field-specific
```

Column weights: `title × 5`, `tags × 3`, `body × 1`

## JSON Output for AI Agents

```bash
brain ask "how to build vsomeip" --json
```

```json
{
  "query": "how to build vsomeip",
  "answer": "### [Building vSomeIP] (knowledge/build-vsomeip.md)  score=0.98\n\n...",
  "sources": [
    {
      "slug": "build-vsomeip",
      "path": "knowledge/build-vsomeip.md",
      "title": "Building vSomeIP",
      "score": 0.98,
      "snippet": "**vSomeIP** requires Boost ≥ 1.66…",
      "tags": ["c++", "someip", "build"]
    }
  ],
  "count": 1
}
```

## Subagent Skills

Two Antigravity skills are included in `.agents/skills/`:

- **`@memory`** — Read-only retrieval agent
- **`@memory-curator`** — Read/write curation agent

See `.agents/skills/memory/SKILL.md` and `.agents/skills/memory-curator/SKILL.md`.

## Note Format

Every note is a standard markdown file with YAML frontmatter:

```markdown
---
title: OpenSSL MinGW Linking
tags: [c++, build, openssl]
category: knowledge
created: 2026-07-04
updated: 2026-07-04T14:30:00
---

Mixing MSVC OpenSSL with MinGW causes linker errors.
Use MinGW64 binaries consistently.
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BRAIN_VAULT` | `~/.brain` | Vault root directory |
| `EDITOR` | `notepad` / `nano` | Editor for `brain remember` |

## Architecture

```
brain ask "query"
     │
     ▼
SQLite FTS5 (BM25)        ← pure math, no ML
     │
     ▼
Load top-k markdown files
     │
     ▼
TF-overlap paragraph scorer
     │
     ▼
Compress to char budget
     │
     ▼
JSON response (~300-500 tokens)
```
