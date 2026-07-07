"""
cli.py — Typer CLI for the brain knowledge base.

Commands
--------
  brain ask       <query>          Search + compress → JSON (for agents)
  brain remember  <title>          Add a new note (editor, --body, or --body-file)
  brain import    <spec.json>      Add a note from a JSON spec (large/multiline bodies)
  brain forget    <slug>           Delete a note
  brain list                       List all notes
  brain show      <slug>           Print a note
  brain rebuild                    Rebuild the FTS5 index
  brain stats                      Print vault statistics
  brain init                       Create vault structure

All commands support --json for machine-readable output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from brain.config import ensure_structure, get_index_path, get_vault_dir
from brain.index import init_db, rebuild_index, remove_note, upsert_note
from brain.search import find_similar, read_top, search
from brain.summarizer import compress
from brain.vault import Note, delete_note, find_note, iter_notes, note_path

app = typer.Typer(
    name="brain",
    help="Personal knowledge base — BM25 search over markdown, no embeddings.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console(stderr=True)  # status/errors → stderr, data → stdout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _out(data: object, as_json: bool) -> None:
    """Print data either as JSON (stdout) or pretty-printed (rich)."""
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        rprint(data)


def _ensure_index() -> None:
    """Auto-initialize the index if it doesn't exist yet."""
    vault = get_vault_dir()
    ip = get_index_path(vault)
    if not ip.exists():
        console.print("[dim]Index not found — running first-time rebuild…[/dim]")
        ensure_structure(vault)
        n = rebuild_index(vault)
        console.print(f"[green]Indexed {n} notes.[/green]")


def _open_editor(initial: str = "") -> str:
    """Open $EDITOR and return the text the user saved."""
    editor = os.environ.get("EDITOR", "notepad" if sys.platform == "win32" else "nano")
    with tempfile.NamedTemporaryFile(
        suffix=".md", mode="w", encoding="utf-8", delete=False
    ) as f:
        f.write(initial)
        tmp = f.name
    subprocess.call([editor, tmp])
    content = Path(tmp).read_text(encoding="utf-8")
    Path(tmp).unlink(missing_ok=True)
    return content


# ---------------------------------------------------------------------------
# brain init
# ---------------------------------------------------------------------------

@app.command()
def init() -> None:
    """Create the vault directory structure and initialize the index."""
    vault = get_vault_dir()
    ensure_structure(vault)
    init_db(get_index_path(vault))
    n = rebuild_index(vault)
    console.print(f"[bold green]✓[/bold green] Vault ready at [cyan]{vault}[/cyan]")
    console.print(f"  Indexed [bold]{n}[/bold] existing notes.")


# ---------------------------------------------------------------------------
# brain ask
# ---------------------------------------------------------------------------
@app.command()
def ask(
    query: str = typer.Argument(..., help="Natural language question or keyword string"),
    top: int = typer.Option(5, "--top", "-k", help="Number of notes to retrieve"),
    budget: int = typer.Option(2000, "--budget", "-b", help="Max chars in compressed answer"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filter by category"),
    json_out: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """
    [bold]Search the vault and return a compressed, agent-friendly answer.[/bold]

    This is the primary command for AI memory retrieval.
    """
    _ensure_index()
    results = read_top(query, top_k=top, category=category)  # <-- FIX HERE

    if not results:
        data: dict = {"query": query, "answer": None, "sources": [], "count": 0}
        if json_out:
            print(json.dumps(data, indent=2))
        else:
            console.print("[yellow]No results found.[/yellow]")
        return

    answer = compress(results, query, char_budget=budget)

    data = {
        "query": query,
        "answer": answer,
        "sources": [
            {
                "slug": r.slug,
                "path": r.rel_path,
                "title": r.title,
                "score": r.score,
                "snippet": r.snippet,
                "tags": r.tags,
            }
            for r in results
        ],
        "count": len(results),
    }

    if json_out:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        console.print(
            Panel(answer, title=f"[bold cyan]brain ask[/bold cyan]: {query}", expand=False)
        )
        console.print("\n[dim]Sources:[/dim]")
        for r in results:
            console.print(
                f"  [green]{r.score:.2f}[/green]  [bold]{r.title}[/bold]  [dim]{r.rel_path}[/dim]"
            )


# ---------------------------------------------------------------------------
# Shared save helper
# ---------------------------------------------------------------------------

def _save_note(
    title: str,
    body: str,
    tag_list: list[str],
    category: str = "inbox",
    force: bool = False,
    json_out: bool = False,
) -> None:
    """Shared save flow for `remember` and `import`: duplicate detection → save → upsert."""
    _ensure_index()
    vault = get_vault_dir()

    # --- duplicate detection ---
    similar = find_similar(title)
    if similar and not force:
        if json_out:
            print(
                json.dumps(
                    {
                        "status": "duplicate_warning",
                        "similar": [
                            {"slug": r.slug, "title": r.title, "score": r.score}
                            for r in similar
                        ],
                    },
                    indent=2,
                )
            )
            return
        console.print("[yellow]Similar notes found:[/yellow]")
        for r in similar:
            console.print(
                f"  [green]{r.score:.2f}[/green]  [bold]{r.title}[/bold]  [dim]{r.rel_path}[/dim]"
            )
        if not typer.confirm("Save anyway?"):
            raise typer.Abort()

    path = note_path(title, category, vault)

    if path.exists() and not force:
        if json_out:
            print(json.dumps({"status": "error", "error": f"Note already exists: {path}", "slug": path.stem}, indent=2))
        else:
            console.print(f"[red]Note already exists:[/red] {path}")
            console.print("Use --force to overwrite.")
        raise typer.Exit(1)

    note = Note(
        path=path,
        title=title,
        body=body,
        tags=tag_list,
        category=category,
    )
    note.save()
    upsert_note(note, vault)

    data = {
        "status": "saved",
        "slug": note.slug,
        "path": note.rel_path,
        "title": title,
    }
    if json_out:
        print(json.dumps(data, indent=2))
    else:
        console.print(f"[bold green]✓[/bold green] Saved → [cyan]{note.rel_path}[/cyan]")


# ---------------------------------------------------------------------------
# brain remember
# ---------------------------------------------------------------------------

@app.command()
def remember(
    title: str = typer.Argument(..., help="Title of the new note"),
    body: Optional[str] = typer.Option(None, "--body", help="Note body (skip editor). Use --body-file for large/multiline bodies."),
    body_file: Optional[Path] = typer.Option(None, "--body-file", help="Read body from a file (avoids shell quoting for large/multiline text). Takes precedence over --body."),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags"),
    category: str = typer.Option("inbox", "--category", "-c", help="Category sub-directory"),
    force: bool = typer.Option(False, "--force", help="Overwrite if slug already exists"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """
    [bold]Save a new note to the vault.[/bold]

    Opens your $EDITOR unless --body (or --body-file) is provided.
    Warns about potential duplicates before saving.
    """
    # --- get body ---
    if body_file:
        body = body_file.read_text(encoding="utf-8")
    elif body == "-":
        body = sys.stdin.read()
    elif body is None:
        if not sys.stdin.isatty():
            body = sys.stdin.read()
        else:
            template = f"# {title}\n\n\n"
            body = _open_editor(template)
            # strip the heading if the user left it
            body = body.strip()
            if body.startswith(f"# {title}"):
                body = body[len(f"# {title}"):].strip()

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    _save_note(title, body, tag_list, category, force, json_out)


# ---------------------------------------------------------------------------
# brain import
# ---------------------------------------------------------------------------

@app.command(name="import")
def import_note(
    spec: str = typer.Argument(..., help="Path to JSON spec file, or '-' for stdin. Fields: title (required), body, tags (list or comma-string), category, force."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """
    [bold]Save a note from a JSON spec file.[/bold]

    Ideal for agents with large/multiline bodies — the entire note
    specification lives in a file, so nothing big goes on the command line.

    Example spec:
    \b
      {"title": "My Note", "body": "Content here", "tags": ["t1","t2"], "category": "knowledge", "force": false}
    """
    _ensure_index()

    # --- read spec ---
    if spec == "-":
        raw = sys.stdin.read()
    else:
        try:
            raw = Path(spec).read_text(encoding="utf-8")
        except OSError as e:
            data = {"status": "error", "error": f"Cannot read spec file: {e}"}
            _out(data, json_out)
            raise typer.Exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        data = {"status": "error", "error": f"Invalid JSON in spec: {e}"}
        _out(data, json_out)
        raise typer.Exit(1)

    title = data.get("title")
    if not title:
        data = {"status": "error", "error": "Spec must include a 'title' field (string)"}
        _out(data, json_out)
        raise typer.Exit(1)

    body = data.get("body", "")

    tags_raw = data.get("tags", [])
    if isinstance(tags_raw, list):
        tag_list = [str(t).strip() for t in tags_raw if str(t).strip()]
    elif isinstance(tags_raw, str):
        tag_list = [t.strip() for t in tags_raw.split(",") if t.strip()]
    else:
        tag_list = []

    category = data.get("category", "inbox")
    force = data.get("force", False)

    _save_note(title, body, tag_list, category, force, json_out)


# ---------------------------------------------------------------------------
# brain forget
# ---------------------------------------------------------------------------

@app.command()
def forget(
    slug: str = typer.Argument(..., help="Note slug or relative path"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]Delete a note from the vault and the index.[/bold]"""
    _ensure_index()
    note = find_note(slug)
    if not note:
        # Try a quick fuzzy fallback
        similar = find_similar(slug, threshold=0.01)
        console.print(f"[red]Note not found:[/red] {slug}")
        if similar:
            console.print(f"[dim]Did you mean:[/dim] [cyan]{similar[0].slug}[/cyan]?")
        raise typer.Exit(1)

    if not yes:
        typer.confirm(f"Delete '{note.title}'?", abort=True)

    deleted = delete_note(slug)
    if deleted:
        remove_note(note.slug)

    data = {"status": "deleted" if deleted else "error", "slug": slug}
    if json_out:
        print(json.dumps(data, indent=2))
    else:
        console.print(f"[bold green]✓[/bold green] Deleted [cyan]{note.rel_path}[/cyan]")


# ---------------------------------------------------------------------------
# brain show
# ---------------------------------------------------------------------------

@app.command()
def show(
    slug: str = typer.Argument(..., help="Note slug or relative path"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]Display a note's full content.[/bold]"""
    note = find_note(slug)
    if not note:
        console.print(f"[red]Note not found:[/red] {slug}")
        raise typer.Exit(1)

    if json_out:
        print(
            json.dumps(
                {
                    "slug": note.slug,
                    "title": note.title,
                    "category": note.category,
                    "tags": note.tags,
                    "created": note.created,
                    "updated": note.updated,
                    "path": note.rel_path,
                    "body": note.body,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        rprint(
            Panel(
                note.body,
                title=f"[bold]{note.title}[/bold]  [dim]{note.rel_path}[/dim]",
                subtitle=f"[dim]tags: {', '.join(note.tags) or 'none'}  category: {note.category}[/dim]",
            )
        )


# ---------------------------------------------------------------------------
# brain list
# ---------------------------------------------------------------------------

@app.command(name="list")
def list_notes(
    category: Optional[str] = typer.Option(None, "--category", "-c"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]List all notes in the vault.[/bold]"""
    notes = list(iter_notes())
    if category:
        notes = [n for n in notes if n.category == category]
    if tag:
        notes = [n for n in notes if tag in n.tags]

    if json_out:
        print(
            json.dumps(
                [
                    {
                        "slug": n.slug,
                        "title": n.title,
                        "category": n.category,
                        "tags": n.tags,
                        "updated": n.updated,
                        "path": n.rel_path,
                    }
                    for n in notes
                ],
                indent=2,
            )
        )
        return

    table = Table(title="Brain Vault", show_header=True, header_style="bold cyan")
    table.add_column("Category", style="dim", width=12)
    table.add_column("Title", style="bold")
    table.add_column("Tags", style="green")
    table.add_column("Updated", style="dim", width=20)

    for n in sorted(notes, key=lambda x: x.updated or "", reverse=True):
        table.add_row(
            n.category,
            n.title,
            ", ".join(n.tags),
            n.updated[:10] if n.updated else "",
        )

    console.print(table)
    console.print(f"\n[dim]{len(notes)} notes total[/dim]")


# ---------------------------------------------------------------------------
# brain rebuild
# ---------------------------------------------------------------------------

@app.command()
def rebuild(
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]Rebuild the FTS5 search index from scratch.[/bold]"""
    vault = get_vault_dir()
    ensure_structure(vault)
    console.print("[dim]Rebuilding index…[/dim]")
    count = rebuild_index(vault)
    data = {"status": "ok", "notes_indexed": count}
    if json_out:
        print(json.dumps(data, indent=2))
    else:
        console.print(f"[bold green]✓[/bold green] Indexed [bold]{count}[/bold] notes.")


# ---------------------------------------------------------------------------
# brain stats
# ---------------------------------------------------------------------------

@app.command()
def stats(
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]Print vault statistics.[/bold]"""
    vault = get_vault_dir()
    notes = list(iter_notes(vault))
    categories: dict[str, int] = {}
    all_tags: list[str] = []
    total_chars = 0

    for n in notes:
        categories[n.category] = categories.get(n.category, 0) + 1
        all_tags.extend(n.tags)
        total_chars += len(n.body)

    tag_counts: dict[str, int] = {}
    for t in all_tags:
        tag_counts[t] = tag_counts.get(t, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    data = {
        "vault": str(vault),
        "notes": len(notes),
        "total_chars": total_chars,
        "categories": categories,
        "top_tags": dict(top_tags),
        "index_exists": get_index_path(vault).exists(),
    }

    if json_out:
        print(json.dumps(data, indent=2))
        return

    console.print(Panel(
        f"[bold]Vault:[/bold] {vault}\n"
        f"[bold]Notes:[/bold] {len(notes)}\n"
        f"[bold]Total chars:[/bold] {total_chars:,}\n"
        f"[bold]Index:[/bold] {'✓' if data['index_exists'] else '✗ (run brain rebuild)'}",
        title="[bold cyan]Brain Stats[/bold cyan]",
    ))

    cat_table = Table(title="By Category")
    cat_table.add_column("Category")
    cat_table.add_column("Notes", justify="right")
    for cat, cnt in sorted(categories.items()):
        cat_table.add_row(cat, str(cnt))
    console.print(cat_table)

    if top_tags:
        tag_table = Table(title="Top Tags")
        tag_table.add_column("Tag")
        tag_table.add_column("Count", justify="right")
        for tag, cnt in top_tags:
            tag_table.add_row(tag, str(cnt))
        console.print(tag_table)


# ---------------------------------------------------------------------------
# brain gui
# ---------------------------------------------------------------------------

@app.command()
def gui(
    font_size: int = typer.Option(
        20, "--font-size", "-s",
        help="Base font size (default 20). Decrease for smaller screens, e.g. --font-size 13.",
        min=8, max=40,
    ),
) -> None:
    """
    [bold]Launch the customtkinter vault browser GUI.[/bold]

    Opens a desktop window showing all notes grouped by category,
    with inline editing, search, delete, and index rebuild.

    Use [bold]--font-size[/bold] to scale the UI for large monitors:
    \b
      brain gui --font-size 20
    """
    _ensure_index()
    vault = get_vault_dir()
    ensure_structure(vault)
    # lazy import — customtkinter is heavy; only loaded when `brain gui` runs
    from brain.gui import run as gui_run

    gui_run(vault=vault, font_size=font_size)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
