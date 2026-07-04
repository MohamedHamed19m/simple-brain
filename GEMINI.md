# Simple Brain — Project Context

A portable, standalone Python CLI acting as an external memory system. It uses pure mathematical ranking (BM25 via SQLite FTS5) to search local markdown notes, completely avoiding API keys and ML embedding models. It integrates directly with Gemini CLI subagents.

## Key Directories

- `brain/` — Python package source code
  - `cli.py` — Typer CLI entrypoint
  - `search.py` — BM25 search and score normalization logic
  - `summarizer.py` — Char-budget paragraph text compressor (TF overlap scoring)
  - `vault.py` — Notes scanner and markdown metadata processor
  - `config.py` — Vault directory layout and index paths
- `dist/` — PyInstaller executable destination containing `brain.exe`
- `.agents/skills/` — Antigravity skill definitions (`memory`, `memory-curator`)

## Coding & Architecture Standards

- **No Embeddings / ML**: Search must rely purely on SQLite FTS5 and BM25 math.
- **Portability**: All logic must package into a single standalone `.exe` using PyInstaller. Avoid runtime dependencies on Python or virtual environments inside the built binary.
- **Agent Integration**: Keep output commands clean. Commands like `ask`, `remember`, `forget`, `show`, `list`, `rebuild`, and `stats` must support a `--json` option for clean, token-efficient parser communication with Gemini CLI subagents.
- **SQLite Optimization**: Use WAL journal mode and normal synchronous flags for safe, concurrent SQLite access.

## Common Commands

### Development, Build & Installation
```powershell
uv sync                                                   # Sync virtual environment dependencies
uv tool install --editable .                              # Install globally in editable mode using uv
uv tool install .                                         # Install globally using uv (adds to uv tool PATH)
uv run pyinstaller --onefile --name brain --console brain/cli.py # Build the portable standalone brain.exe binary
```

it depend on the user does it want exe (pyinstaller) or want global installed tool (uv tool install --editable .)

### Local Testing (via uv)
```powershell
uv run brain init                                         # Init local vault directories
uv run brain ask "how to fix openssl"                     # Run standard search
uv run brain ask "vsomeip service discovery" --json       # Run search with JSON output
uv run brain stats                                        # Inspect vault stats
```

### Executable Run (After building & adding to PATH)
```powershell
brain remember "My Title" --body "Content" --tags "t1,t2" # Save a note
brain ask "My query"                                      # Run FTS5 BM25 search
brain rebuild                                             # Force re-index of markdown files
```
