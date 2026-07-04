"""
gui.py — CustomTkinter desktop GUI for the Simple Brain vault.

Three-pane layout:
  Left:    Search box, category list with counts, action buttons
  Middle:  Note list (filtered by category or search results)
  Right:   Inline editor (title, tags, category, body, Save/Delete/New)

All vault operations import brain.* Python API directly.
Launch via:  uv run brain gui   or   brain gui
"""

from __future__ import annotations

import json
import tkinter as tk
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from brain.config import CATEGORIES, ensure_structure, get_index_path, get_vault_dir
from brain.index import rebuild_index, remove_note, upsert_note
from brain.search import read_top
from brain.vault import Note, delete_note, find_note, iter_notes, note_path

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Colour / theme constants
# ---------------------------------------------------------------------------

THEME_COLOUR = "blue"  # customtkinter built-in theme
FONT_FAMILY = "Segoe UI" if __import__("sys").platform == "win32" else "Helvetica"


def _resolve_vault(vault: Path | None) -> Path:
    return vault or get_vault_dir()


# ===================================================================
# Main application
# ===================================================================

class BrainApp(ctk.CTk):
    """CustomTkinter GUI for browsing, searching, and editing the vault."""

    def __init__(self, vault: Path | None = None, font_size: int = 17) -> None:
        super().__init__()

        self.vault = _resolve_vault(vault)
        self.index_path = get_index_path(self.vault)

        # ---------- font sizing ----------
        self.font_size = font_size
        self._fs = font_size  # shorthand alias

        # ---------- state ----------
        self.all_notes: list[Note] = []
        self.filtered_notes: list[Note] = []
        self.current_note: Note | None = None
        self.current_category: str | None = None  # None = "All"
        self.search_query: str = ""
        self._theme_is_dark = True

        # ---------- window (scale with font) ----------
        self.title("Simple Brain Vault")
        scale = self._fs / 13.0
        w, h = max(900, int(1200 * scale)), max(500, int(720 * scale))
        self.geometry(f"{w}x{h}")
        self.minsize(int(900 * scale * 0.75), max(500, int(500 * scale * 0.7)))

        # ---------- layout grid ----------
        self.grid_columnconfigure(0, weight=0, minsize=200)   # sidebar
        self.grid_columnconfigure(1, weight=1, minsize=280)   # note list
        self.grid_columnconfigure(2, weight=2, minsize=360)   # editor
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0, minsize=28)       # status bar

        self._build_sidebar()
        self._build_note_list()
        self._build_editor()
        self._build_status_bar()

        self._load_notes()
        self._apply_filter()
        self._refresh_sidebar()
        self._refresh_note_list()

    # ================================================================
    # Layout builders  (called once at init)
    # ================================================================

    def _build_sidebar(self) -> None:
        """Left pane: search, categories, actions."""
        fs = self._fs
        sidebar = ctk.CTkFrame(self, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 1))
        sidebar.grid_rowconfigure(2, weight=1)  # category list stretches

        # --- search ---
        self.search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(
            sidebar, placeholder_text="🔍  Search vault…",
            textvariable=self.search_var,
            font=ctk.CTkFont(size=fs),
        )
        search_entry.grid(row=0, column=0, sticky="ew", padx=10, pady=(12, 4))
        search_entry.bind("<Return>", lambda _: self._do_search())

        search_btn = ctk.CTkButton(
            sidebar, text="Search", command=self._do_search,
            font=ctk.CTkFont(size=fs - 1), height=max(24, fs + 15),
        )
        search_btn.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        # --- category list ---
        cat_header = ctk.CTkLabel(
            sidebar, text="CATEGORIES",
            font=ctk.CTkFont(size=fs - 2, weight="bold"),
            anchor="w",
        )
        cat_header.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 2))

        self.cat_scroll = ctk.CTkScrollableFrame(sidebar, corner_radius=0)
        self.cat_scroll.grid(row=3, column=0, sticky="nsew", padx=6, pady=(0, 4))

        # --- action buttons at bottom ---
        actions = ctk.CTkFrame(sidebar, corner_radius=0, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", padx=8, pady=(4, 10))
        actions.grid_columnconfigure((0, 1), weight=1)

        btn_h = max(24, fs + 15)
        self.btn_rebuild = ctk.CTkButton(
            actions, text="⚙  Rebuild", command=self._do_rebuild,
            font=ctk.CTkFont(size=fs - 2), height=btn_h,
        )
        self.btn_rebuild.grid(row=0, column=0, sticky="ew", padx=2, pady=2)

        self.btn_stats = ctk.CTkButton(
            actions, text="📊  Stats", command=self._show_stats,
            font=ctk.CTkFont(size=fs - 2), height=btn_h,
        )
        self.btn_stats.grid(row=0, column=1, sticky="ew", padx=2, pady=2)

        self.btn_theme = ctk.CTkButton(
            actions, text="🌙  Dark", command=self._toggle_theme,
            font=ctk.CTkFont(size=fs - 2), height=btn_h,
        )
        self.btn_theme.grid(row=1, column=0, columnspan=2, sticky="ew", padx=2, pady=2)

    def _build_note_list(self) -> None:
        """Middle pane: scrollable list of note titles."""
        fs = self._fs
        container = ctk.CTkFrame(self, corner_radius=0)
        container.grid(row=0, column=1, sticky="nsew", padx=(1, 1))
        container.grid_rowconfigure(1, weight=1)
        container.grid_columnconfigure(0, weight=1)

        # header
        self.note_list_header = ctk.CTkLabel(
            container, text="All Notes", anchor="w",
            font=ctk.CTkFont(size=fs + 1, weight="bold"),
        )
        self.note_list_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))

        self.clear_search_btn = ctk.CTkButton(
            container, text="✕  Clear search", command=self._clear_search,
            font=ctk.CTkFont(size=fs - 3), height=max(18, fs + 9), width=int(fs * 8),
        )
        # hidden by default — shown only during search

        self.note_scroll = ctk.CTkScrollableFrame(container, corner_radius=0)
        self.note_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

    def _build_editor(self) -> None:
        """Right pane: note detail / editor."""
        fs = self._fs
        editor = ctk.CTkFrame(self, corner_radius=0)
        editor.grid(row=0, column=2, sticky="nsew", padx=(1, 0))
        editor.grid_columnconfigure(0, weight=1)
        editor.grid_rowconfigure(4, weight=1)  # body textbox stretches

        pad = {"padx": 12, "pady": (6, 2)}

        # --- title ---
        ctk.CTkLabel(editor, text="Title", font=ctk.CTkFont(size=fs - 2, weight="bold"),
                      anchor="w").grid(row=0, column=0, sticky="ew", **pad)
        self.title_entry = ctk.CTkEntry(editor, font=ctk.CTkFont(size=fs))
        self.title_entry.grid(row=1, column=0, sticky="ew", **pad)

        # --- tags + category row ---
        info_frame = ctk.CTkFrame(editor, fg_color="transparent")
        info_frame.grid(row=2, column=0, sticky="ew", **pad)
        info_frame.grid_columnconfigure(0, weight=3)
        info_frame.grid_columnconfigure(1, weight=2)

        ctk.CTkLabel(info_frame, text="Tags (comma-sep)",
                      font=ctk.CTkFont(size=fs - 3)).grid(row=0, column=0, sticky="sw")
        self.tags_entry = ctk.CTkEntry(info_frame, font=ctk.CTkFont(size=fs - 1))
        self.tags_entry.grid(row=1, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkLabel(info_frame, text="Category",
                      font=ctk.CTkFont(size=fs - 3)).grid(row=0, column=1, sticky="sw")
        self.cat_combo = ctk.CTkComboBox(
            info_frame, values=list(CATEGORIES), state="normal",
            font=ctk.CTkFont(size=fs - 1),
        )
        self.cat_combo.grid(row=1, column=1, sticky="ew")

        # --- body ---
        ctk.CTkLabel(editor, text="Body (Markdown)",
                      font=ctk.CTkFont(size=fs - 2, weight="bold"),
                      anchor="w").grid(row=3, column=0, sticky="ew", **pad)
        self.body_text = ctk.CTkTextbox(editor, wrap="word", font=ctk.CTkFont(size=fs - 1))
        self.body_text.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 8))

        # --- action buttons ---
        btn_frame = ctk.CTkFrame(editor, fg_color="transparent")
        btn_frame.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 10))
        btn_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        btn_h = max(28, fs + 19)
        self.btn_save = ctk.CTkButton(
            btn_frame, text="💾  Save", command=self._do_save,
            font=ctk.CTkFont(size=fs - 1), height=btn_h,
        )
        self.btn_save.grid(row=0, column=0, sticky="ew", padx=2)

        self.btn_delete = ctk.CTkButton(
            btn_frame, text="🗑  Delete", command=self._do_delete,
            font=ctk.CTkFont(size=fs - 1), height=btn_h,
        )
        self.btn_delete.grid(row=0, column=1, sticky="ew", padx=2)

        self.btn_new = ctk.CTkButton(
            btn_frame, text="➕  New Note", command=self._do_new,
            font=ctk.CTkFont(size=fs - 1), height=btn_h,
        )
        self.btn_new.grid(row=0, column=2, sticky="ew", padx=2)

        self.btn_clear = ctk.CTkButton(
            btn_frame, text="✕  Clear", command=self._clear_editor,
            font=ctk.CTkFont(size=fs - 1), height=btn_h,
        )
        self.btn_clear.grid(row=0, column=3, sticky="ew", padx=2)

    def _build_status_bar(self) -> None:
        """Thin status bar at the bottom."""
        self.status_var = ctk.StringVar(value="Ready")
        status = ctk.CTkLabel(
            self, textvariable=self.status_var, anchor="w",
            font=ctk.CTkFont(size=self._fs - 2),
            fg_color=("gray85", "gray20"),
        )
        status.grid(row=1, column=0, columnspan=3, sticky="ew")

    # ================================================================
    # Data loading
    # ================================================================

    def _load_notes(self) -> None:
        """Reload all notes from disk into self.all_notes."""
        self.all_notes = sorted(
            list(iter_notes(self.vault)),
            key=lambda n: n.updated or "",
            reverse=True,
        )

    # ================================================================
    # Sidebar
    # ================================================================

    def _refresh_sidebar(self) -> None:
        """Rebuild category buttons + counts."""
        for w in self.cat_scroll.winfo_children():
            w.destroy()

        fs = self._fs
        counts: Counter[str] = Counter(n.category for n in self.all_notes)
        # collect all categories: defaults + any custom ones found in vault
        all_cats = list(CATEGORIES)
        for cat in counts:
            if cat not in all_cats:
                all_cats.append(cat)

        # "All" button
        total = len(self.all_notes)
        btn_all = ctk.CTkButton(
            self.cat_scroll, text=f"📂  All  ({total})", anchor="w",
            font=ctk.CTkFont(size=fs - 1),
            fg_color="transparent" if self.current_category is not None else None,
            command=lambda: self._select_category(None),
        )
        btn_all.pack(fill="x", padx=2, pady=1)

        for cat in all_cats:
            cnt = counts.get(cat, 0)
            selected = cat == self.current_category
            btn = ctk.CTkButton(
                self.cat_scroll,
                text=f"📁  {cat}  ({cnt})",
                anchor="w",
                font=ctk.CTkFont(size=fs - 1),
                fg_color="transparent" if not selected else None,
                command=lambda c=cat: self._select_category(c),
            )
            btn.pack(fill="x", padx=2, pady=1)

    # ================================================================
    # Note list
    # ================================================================

    def _refresh_note_list(self, notes: list[Note] | None = None) -> None:
        """Rebuild the scrollable note list.  Accepts filtered or searched notes."""
        for w in self.note_scroll.winfo_children():
            w.destroy()

        display = notes if notes is not None else self.filtered_notes

        # update header
        if self.search_query:
            self.note_list_header.configure(
                text=f'Search: "{self.search_query}"  ({len(display)} results)'
            )
            self.clear_search_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-10, y=8)
        elif self.current_category:
            self.note_list_header.configure(
                text=f"📁  {self.current_category}  ({len(display)} notes)"
            )
            self.clear_search_btn.place_forget()
        else:
            self.note_list_header.configure(text=f"All Notes  ({len(display)} total)")
            self.clear_search_btn.place_forget()

        fs = self._fs
        if not display:
            ctk.CTkLabel(
                self.note_scroll, text="No notes found.",
                font=ctk.CTkFont(size=fs - 1),
                text_color="gray",
            ).pack(pady=30)
            return

        # Group by category when showing all
        if not self.current_category and not self.search_query:
            grouped: dict[str, list[Note]] = defaultdict(list)
            for n in display:
                grouped[n.category].append(n)
            for cat, cat_notes in sorted(grouped.items()):
                # category heading
                heading = ctk.CTkLabel(
                    self.note_scroll, text=f"▸  {cat}  ({len(cat_notes)})",
                    anchor="w",
                    font=ctk.CTkFont(size=fs - 2, weight="bold"),
                    fg_color=("gray85", "gray25"),
                )
                heading.pack(fill="x", padx=2, pady=(6, 0))
                for note in cat_notes:
                    self._add_note_row(note)
        else:
            for note in display:
                self._add_note_row(note)

    def _add_note_row(self, note: Note) -> None:
        """A single clickable row in the note list."""
        fs = self._fs
        frame = ctk.CTkFrame(self.note_scroll, corner_radius=4)
        frame.pack(fill="x", padx=4, pady=1)
        frame.grid_columnconfigure(0, weight=1)

        title_lbl = ctk.CTkLabel(
            frame, text=note.title, anchor="w",
            font=ctk.CTkFont(size=fs - 1, weight="bold"),
        )
        title_lbl.grid(row=0, column=0, sticky="ew", padx=6, pady=(2, 0))

        meta_parts = []
        if note.tags:
            meta_parts.append(", ".join(note.tags))
        if note.updated:
            meta_parts.append(note.updated[:10])
        if meta_parts:
            meta_lbl = ctk.CTkLabel(
                frame, text="  •  ".join(meta_parts), anchor="w",
                font=ctk.CTkFont(size=fs - 3),
                text_color=("gray40", "gray60"),
            )
            meta_lbl.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 2))

        # click binding — bind to EVERY widget in the row so no dead zones
        def _click(e):
            self._select_note(note)
            return "break"
        for child in frame.winfo_children():
            child.bind("<Button-1>", _click)
            try:
                child.configure(cursor="hand2")
            except Exception:
                pass
        frame.bind("<Button-1>", _click)
        frame.configure(cursor="hand2")

        # highlight currently selected
        if self.current_note and note.slug == self.current_note.slug:
            frame.configure(fg_color=("gray75", "gray30"))

    # ================================================================
    # Editor
    # ================================================================

    def _load_note_into_editor(self, note: Note) -> None:
        """Fill the editor fields with a note's content."""
        self.current_note = note
        self.title_entry.delete(0, "end")
        self.title_entry.insert(0, note.title)
        self.tags_entry.delete(0, "end")
        self.tags_entry.insert(0, ", ".join(note.tags))
        self.cat_combo.set(note.category)
        self.body_text.delete("0.0", "end")
        self.body_text.insert("0.0", note.body)

        # update combo with discovered categories
        discovered = sorted({n.category for n in self.all_notes})
        self.cat_combo.configure(values=list(CATEGORIES) + [c for c in discovered if c not in CATEGORIES])

    def _clear_editor(self) -> None:
        """Clear the editor (for new note creation)."""
        self.current_note = None
        self.title_entry.delete(0, "end")
        self.tags_entry.delete(0, "end")
        self.cat_combo.set("inbox")
        self.body_text.delete("0.0", "end")
        self._set_status("Editor cleared.")

    # ================================================================
    # Actions
    # ================================================================

    def _select_category(self, cat: str | None) -> None:
        """Filter the note list to one category (None = all)."""
        self.current_category = cat
        self.search_query = ""
        self._apply_filter()
        self._refresh_sidebar()
        self._refresh_note_list()

    def _apply_filter(self) -> None:
        if self.current_category:
            self.filtered_notes = [
                n for n in self.all_notes if n.category == self.current_category
            ]
        else:
            self.filtered_notes = list(self.all_notes)

    def _select_note(self, note: Note) -> None:
        """Load a note into the editor."""
        # Reload the note from disk to get latest content
        fresh = find_note(note.slug, self.vault)
        if fresh is None:
            self._set_status(f"Note '{note.title}' no longer exists on disk.")
            self._load_notes()
            self._refresh_note_list()
            return
        self._load_note_into_editor(fresh)
        self._refresh_note_list()  # re-highlight

    # ---- Search ----

    def _do_search(self) -> None:
        query = self.search_var.get().strip()
        if not query:
            return
        self.search_query = query
        try:
            results = read_top(query, top_k=50, vault=self.vault, index_path=self.index_path)
        except Exception as exc:
            self._set_status(f"Search error: {exc}")
            return

        searched_notes: list[Note] = []
        for r in results:
            if r.note:
                searched_notes.append(r.note)
        self.filtered_notes = searched_notes
        self.current_category = None
        self._refresh_note_list(self.filtered_notes)
        self._set_status(f"Found {len(searched_notes)} result(s) for '{query}'.")

    def _clear_search(self) -> None:
        self.search_query = ""
        self.search_var.set("")
        self._apply_filter()
        self._refresh_note_list()

    # ---- Save ----

    def _do_save(self) -> None:
        title = self.title_entry.get().strip()
        if not title:
            self._set_status("Cannot save: title is empty.")
            return

        old_note = self.current_note
        tags_str = self.tags_entry.get().strip()
        tag_list = [t.strip() for t in tags_str.split(",") if t.strip()]
        category = self.cat_combo.get().strip()
        body = self.body_text.get("0.0", "end").strip()

        if not body and not title:
            self._set_status("Cannot save: title and body are both empty.")
            return

        # Generate path for new slug
        new_path = note_path(title, category or "inbox", self.vault)
        new_slug = new_path.stem

        note = Note(
            path=new_path,
            title=title,
            body=body or "",
            tags=tag_list,
            category=category or "inbox",
        )

        try:
            # If title changed, orphan the old file
            if old_note and old_note.slug != new_slug:
                note.save()
                upsert_note(note, vault=self.vault)
                delete_note(old_note.slug, self.vault)
                remove_note(old_note.slug, vault=self.vault)
                self._set_status(
                    f"Renamed '{old_note.title}' → '{title}' (old file cleaned up)."
                )
            else:
                note.save()
                upsert_note(note, vault=self.vault)
                self._set_status(f"Saved '{title}'.")
        except Exception as exc:
            self._set_status(f"Save error: {exc}")
            return

        self._load_notes()
        self._apply_filter()
        self._refresh_sidebar()
        self._refresh_note_list()
        # re-select the saved note
        fresh = find_note(new_slug, self.vault)
        if fresh:
            self._load_note_into_editor(fresh)

    # ---- Delete ----

    def _do_delete(self) -> None:
        if not self.current_note:
            self._set_status("Nothing to delete.")
            return
        note = self.current_note
        # confirm
        confirm = tk.messagebox.askyesno(
            "Confirm Delete",
            f'Delete "{note.title}"?\n\nThis cannot be undone.',
            parent=self,
        )
        if not confirm:
            return

        try:
            delete_note(note.slug, self.vault)
            remove_note(note.slug, vault=self.vault)
            self._set_status(f"Deleted '{note.title}'.")
        except Exception as exc:
            self._set_status(f"Delete error: {exc}")
            return

        self.current_note = None
        self._clear_editor()
        self._load_notes()
        self._apply_filter()
        self._refresh_sidebar()
        self._refresh_note_list()

    # ---- New ----

    def _do_new(self) -> None:
        self._clear_editor()
        self._set_status("Enter a title and body, then click Save to create a new note.")

    # ---- Rebuild ----

    def _do_rebuild(self) -> None:
        self._set_status("Rebuilding index…")
        try:
            self.update_idletasks()
            count = rebuild_index(vault=self.vault)
            self._set_status(f"Index rebuilt — {count} notes indexed.")
        except Exception as exc:
            self._set_status(f"Rebuild error: {exc}")

    # ---- Stats ----

    def _show_stats(self) -> None:
        fs = self._fs
        scale = fs / 13.0
        win = ctk.CTkToplevel(self)
        win.title("Vault Statistics")
        win.geometry(f"{int(400 * scale)}x{int(500 * scale)}")
        win.minsize(int(360 * scale), int(300 * scale))
        win.transient(self)
        win.grab_set()

        counts: Counter[str] = Counter(n.category for n in self.all_notes)
        all_tags: Counter[str] = Counter()
        total_chars = 0
        for n in self.all_notes:
            for t in n.tags:
                all_tags[t] += 1
            total_chars += len(n.body)

        lines = [
            f"📂  Vault:      {self.vault}",
            f"📄  Notes:      {len(self.all_notes)}",
            f"🔤  Chars:      {total_chars:,}",
            f"📊  Index:      {'✓ exists' if self.index_path.exists() else '✗ missing'}",
            "",
            "── Categories ──",
        ]
        for cat, cnt in sorted(counts.items()):
            lines.append(f"   {cat}: {cnt}")

        if all_tags:
            lines.append("")
            lines.append("── Top Tags ──")
            for tag, cnt in all_tags.most_common(10):
                lines.append(f"   {tag}: {cnt}")

        text = "\n".join(lines)

        label = ctk.CTkLabel(
            win, text=text, justify="left",
            font=ctk.CTkFont(size=fs - 1, family="Consolas" if __import__("sys").platform == "win32" else "monospace"),
            anchor="nw",
        )
        label.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkButton(win, text="Close", command=win.destroy,
                      font=ctk.CTkFont(size=fs - 1)).pack(pady=(0, 12))

    # ---- Theme ----

    def _toggle_theme(self) -> None:
        self._theme_is_dark = not self._theme_is_dark
        mode = "dark" if self._theme_is_dark else "light"
        ctk.set_appearance_mode(mode)
        self.btn_theme.configure(text="🌙  Dark" if self._theme_is_dark else "☀️  Light")
        self._set_status(f"Theme switched to {mode}.")

    # ---- Status ----

    def _set_status(self, msg: str) -> None:
        self.status_var.set(msg)


# ===================================================================
# Entry point for `brain gui`
# ===================================================================

def run(vault: Path | None = None, font_size: int = 17) -> None:
    """Build and start the GUI.  Called from cli.py."""
    vault = vault or get_vault_dir()
    ensure_structure(vault)
    # ensure index exists
    ip = get_index_path(vault)
    if not ip.exists():
        rebuild_index(vault=vault, index_path=ip)

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme(THEME_COLOUR)
    app = BrainApp(vault=vault, font_size=font_size)
    app.mainloop()