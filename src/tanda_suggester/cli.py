"""CLI entry points for tanda-suggester."""

from __future__ import annotations

import fnmatch
import sys
from pathlib import Path

import click
from rapidfuzz import fuzz, process
from rich.console import Console
from rich.table import Table

from tanda_suggester.db import DB_PATH, init_db
from tanda_suggester.importer import import_named_playlists, run_import
from tanda_suggester.music_app import get_current_track, read_all_playlists_applescript
from tanda_suggester.search import fuzzy_match
from tanda_suggester.suggest import suggest_for_track
from tanda_suggester.tandas import rebuild_tandas

console = Console()


def _db(db_path: Path | None = None):
    return init_db(db_path)


def _find_playlist_by_name(conn, query: str):
    """Return a single playlist row matching query, or exit with a helpful error.

    Tries exact case-insensitive match first, then fuzzy match as fallback
    to handle special characters or minor name differences.
    """
    rows = conn.execute(
        "SELECT id, name FROM playlists WHERE lower(name) = lower(?)", (query,)
    ).fetchall()
    if rows:
        return rows[0]

    all_rows = conn.execute("SELECT id, name FROM playlists").fetchall()
    if not all_rows:
        console.print("[red]No playlists in database. Run 'import' first.[/red]")
        sys.exit(1)

    choices = {r["id"]: r["name"] for r in all_rows}
    top = process.extractOne(query, choices, scorer=fuzz.WRatio, score_cutoff=80)

    if top is None:
        console.print(f"[red]No playlist named '{query}'. Use 'playlists' to list all.[/red]")
        sys.exit(1)

    _match_name, top_score, matched_id = top

    all_results = process.extract(query, choices, scorer=fuzz.WRatio, score_cutoff=80, limit=5)
    close = [r for r in all_results if r[1] >= top_score - 5]

    if len(close) > 1:
        console.print(f"[yellow]Multiple playlists match '{query}':[/yellow]")
        for name, sc, pid in close:
            console.print(f"  [dim]{pid}[/dim]  {name}  [dim](score {sc:.0f})[/dim]")
        console.print("[yellow]Use the numeric ID to be precise, e.g.: tanda-suggester include <ID>[/yellow]")
        sys.exit(1)

    matched_row = next(r for r in all_rows if r["id"] == matched_id)
    console.print(f"[dim]Matched: {matched_row['name']}[/dim]")
    return matched_row


@click.group()
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=None,
              help="Path to the SQLite database (default: ~/.local/share/tanda-suggester/db.sqlite)")
@click.pass_context
def cli(ctx: click.Context, db_path: Path | None) -> None:
    """Tanda Suggester — suggest tango tracks based on your DJ history."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path


@cli.command("import")
@click.option("--batch-size", default=500, show_default=True, help="AppleScript batch size")
@click.pass_context
def import_cmd(ctx: click.Context, batch_size: int) -> None:
    """Import tracks from Music.app and playlists from the XML library file."""
    run_import(db_path=ctx.obj.get("db_path"), batch_size=batch_size)


def _suggest_close_names(pattern: str) -> None:
    """Print playlist names from Music.app that resemble pattern, as a diagnostic aid."""
    try:
        stripped = pattern.replace("*", "").replace("?", "").strip()
        if not stripped:
            return
        all_playlists, _ = read_all_playlists_applescript()
        matches = [pl.name for pl in all_playlists if stripped.lower() in pl.name.lower()]
        if matches:
            console.print("[dim]Similar playlists found in Music.app:[/dim]")
            for name in matches[:5]:
                console.print(f"  [dim]{name!r}[/dim]")
    except Exception:
        pass


@cli.command("import-playlist")
@click.argument("pattern")
@click.option("--include", "mark_included", is_flag=True, default=False,
              help="Automatically mark imported playlists as included")
@click.pass_context
def import_playlist_cmd(ctx: click.Context, pattern: str, mark_included: bool) -> None:
    """Import playlists from the XML library matching PATTERN (shell glob).

    Examples:
      tanda-suggester import-playlist "***** FRITKOT 2025"
      tanda-suggester import-playlist "***** *" --include
    """
    conn = _db(ctx.obj.get("db_path"))
    tracks_added, playlists_added, imported_names = import_named_playlists(conn, pattern)

    if playlists_added == 0:
        console.print(f"[yellow]No playlists found matching '{pattern}' in the XML library.[/yellow]")
        _suggest_close_names(pattern)
        return

    console.print(
        f"[green]✓ {playlists_added} playlist(s) imported, {tracks_added} track(s) upserted.[/green]"
    )

    if mark_included:
        for name in imported_names:
            conn.execute("UPDATE playlists SET included = 1 WHERE name = ?", (name,))
            console.print(f"  [green]✓ Included:[/green] {name}")
        conn.commit()

    included_count = conn.execute(
        "SELECT COUNT(*) FROM playlists WHERE included = 1 AND excluded = 0"
    ).fetchone()[0]
    if included_count == 0:
        console.print("[yellow]No included playlists — skipping rebuild. Use 'include' first.[/yellow]")
    else:
        excluded_count = conn.execute(
            "SELECT COUNT(*) FROM playlists WHERE excluded = 1"
        ).fetchone()[0]
        excluded_note = f", {excluded_count} excluded" if excluded_count else ""
        console.print(f"[blue]Rebuilding from {included_count} included playlist(s){excluded_note}…[/blue]")
        tanda_count, co_count = rebuild_tandas(conn)
        console.print(f"[green]✓ {tanda_count:,} tandas detected, {co_count:,} co-occurrence pairs built.[/green]")


@cli.command("playlists")
@click.pass_context
def playlists_cmd(ctx: click.Context) -> None:
    """List all playlists with their track counts and inclusion status."""
    conn = _db(ctx.obj.get("db_path"))
    rows = conn.execute(
        "SELECT id, name, track_count, included, excluded FROM playlists ORDER BY name"
    ).fetchall()

    if not rows:
        console.print("[yellow]No playlists found. Run 'import' first.[/yellow]")
        return

    table = Table(title=f"{len(rows)} playlists", show_lines=False)
    table.add_column("ID", style="dim", justify="right")
    table.add_column("Name")
    table.add_column("Tracks", justify="right")
    table.add_column("Status", justify="center")

    for r in rows:
        if r["excluded"]:
            status = "[red]✗ excluded[/red]"
        elif r["included"]:
            status = "[green]✓ included[/green]"
        else:
            status = ""
        table.add_row(str(r["id"]), r["name"], str(r["track_count"]), status)

    console.print(table)


@cli.command("include")
@click.argument("playlist", nargs=-1, required=True)
@click.pass_context
def include_cmd(ctx: click.Context, playlist: tuple[str, ...]) -> None:
    """Mark a playlist as included by name or ID. Accepts multiple arguments."""
    conn = _db(ctx.obj.get("db_path"))
    query = " ".join(playlist)

    # Try numeric ID first
    if query.isdigit():
        row = conn.execute("SELECT id, name FROM playlists WHERE id = ?", (int(query),)).fetchone()
        if row:
            conn.execute("UPDATE playlists SET included = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            console.print(f"[green]✓ Included playlist: {row['name']}[/green]")
            return
        console.print(f"[red]No playlist with ID {query}[/red]")
        sys.exit(1)

    row = _find_playlist_by_name(conn, query)
    conn.execute("UPDATE playlists SET included = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    console.print(f"[green]✓ Included playlist: {row['name']}[/green]")


@cli.command("include-all-matching")
@click.argument("pattern")
@click.pass_context
def include_all_matching_cmd(ctx: click.Context, pattern: str) -> None:
    """Mark all playlists matching a shell glob pattern as included."""
    conn = _db(ctx.obj.get("db_path"))
    rows = conn.execute("SELECT id, name FROM playlists").fetchall()

    matched = [r for r in rows if fnmatch.fnmatch(r["name"].lower(), pattern.lower())]

    if not matched:
        console.print(f"[yellow]No playlists match '{pattern}'.[/yellow]")
        return

    for row in matched:
        conn.execute("UPDATE playlists SET included = 1 WHERE id = ?", (row["id"],))
        console.print(f"  [green]✓[/green] {row['name']}")

    conn.commit()
    console.print(f"\n[green]{len(matched)} playlist(s) included.[/green]")


@cli.command("exclude")
@click.argument("playlist", nargs=-1, required=True)
@click.pass_context
def exclude_cmd(ctx: click.Context, playlist: tuple[str, ...]) -> None:
    """Mark a playlist as excluded by name or ID. Accepts multiple arguments."""
    conn = _db(ctx.obj.get("db_path"))
    query = " ".join(playlist)

    # Try numeric ID first
    if query.isdigit():
        row = conn.execute("SELECT id, name FROM playlists WHERE id = ?", (int(query),)).fetchone()
        if row:
            conn.execute("UPDATE playlists SET excluded = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            console.print(f"[red]✗ Excluded playlist: {row['name']}[/red]")
            return
        console.print(f"[red]No playlist with ID {query}[/red]")
        sys.exit(1)

    row = _find_playlist_by_name(conn, query)
    conn.execute("UPDATE playlists SET excluded = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    console.print(f"[red]✗ Excluded playlist: {row['name']}[/red]")


@cli.command("exclude-all-matching")
@click.argument("pattern")
@click.pass_context
def exclude_all_matching_cmd(ctx: click.Context, pattern: str) -> None:
    """Mark all playlists matching a shell glob pattern as excluded."""
    conn = _db(ctx.obj.get("db_path"))
    rows = conn.execute("SELECT id, name FROM playlists").fetchall()

    matched = [r for r in rows if fnmatch.fnmatch(r["name"].lower(), pattern.lower())]

    if not matched:
        console.print(f"[yellow]No playlists match '{pattern}'.[/yellow]")
        return

    for row in matched:
        conn.execute("UPDATE playlists SET excluded = 1 WHERE id = ?", (row["id"],))
        console.print(f"  [red]✗[/red] {row['name']}")

    conn.commit()
    console.print(f"\n[red]{len(matched)} playlist(s) excluded.[/red]")


@cli.command("rebuild")
@click.pass_context
def rebuild_cmd(ctx: click.Context) -> None:
    """Re-detect tandas and rebuild the co-occurrence index from included playlists."""
    conn = _db(ctx.obj.get("db_path"))
    included_count = conn.execute(
        "SELECT COUNT(*) FROM playlists WHERE included = 1 AND excluded = 0"
    ).fetchone()[0]
    excluded_count = conn.execute(
        "SELECT COUNT(*) FROM playlists WHERE excluded = 1"
    ).fetchone()[0]

    if included_count == 0:
        console.print("[yellow]No included playlists. Use 'include' or 'include-all-matching' first.[/yellow]")
        return

    excluded_note = f", {excluded_count} excluded" if excluded_count else ""
    console.print(f"[blue]Rebuilding from {included_count} included playlist(s){excluded_note}…[/blue]")
    tanda_count, co_count = rebuild_tandas(conn)
    console.print(f"[green]✓ {tanda_count:,} tandas detected, {co_count:,} co-occurrence pairs built.[/green]")


@cli.command("clear-all")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt")
@click.pass_context
def clear_all_cmd(ctx: click.Context, yes: bool) -> None:
    """Delete all tracks, playlists, tandas and co-occurrence data from the database."""
    conn = _db(ctx.obj.get("db_path"))

    track_count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    playlist_count = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
    tanda_count = conn.execute("SELECT COUNT(*) FROM tandas").fetchone()[0]

    if track_count == 0 and playlist_count == 0:
        console.print("[yellow]Database is already empty.[/yellow]")
        return

    console.print(
        f"[yellow]This will delete {track_count:,} tracks, {playlist_count:,} playlists, "
        f"and {tanda_count:,} tandas.[/yellow]"
    )

    if not yes:
        click.confirm("Continue?", abort=True)

    conn.execute("DELETE FROM co_occurrence")
    conn.execute("DELETE FROM tanda_tracks")
    conn.execute("DELETE FROM tandas")
    conn.execute("DELETE FROM playlist_tracks")
    conn.execute("DELETE FROM playlists")
    conn.execute("DELETE FROM tracks")
    conn.commit()

    console.print("[green]✓ Database cleared. Run 'import' to start fresh.[/green]")


@cli.command("suggest")
@click.argument("query")
@click.option("--any-genre", is_flag=True, default=False,
              help="Include suggestions from any genre, not just the matched track's genre family")
@click.option("--limit", default=20, show_default=True, help="Number of suggestions to show")
@click.pass_context
def suggest_cmd(ctx: click.Context, query: str, any_genre: bool, limit: int) -> None:
    """Suggest tracks that go well with QUERY (fuzzy track name match)."""
    conn = _db(ctx.obj.get("db_path"))
    _run_suggest(conn, query, same_genre=not any_genre, limit=limit)


@cli.command("suggest-current")
@click.option("--any-genre", is_flag=True, default=False)
@click.option("--limit", default=20, show_default=True)
@click.pass_context
def suggest_current_cmd(ctx: click.Context, any_genre: bool, limit: int) -> None:
    """Suggest tracks based on what's currently playing in Music.app."""
    current = get_current_track()
    if current is None:
        console.print("[yellow]Nothing is currently playing in Music.app.[/yellow]")
        sys.exit(1)

    console.print(f"[blue]Currently playing:[/blue] {current.title} — {current.artist} [{current.genre}]")

    conn = _db(ctx.obj.get("db_path"))
    row = conn.execute(
        "SELECT id, genre_family FROM tracks WHERE music_app_id = ?", (current.persistent_id,)
    ).fetchone()

    if row is None:
        console.print(
            f"[yellow]Track '{current.title}' is not in the database. Run 'import' to update.[/yellow]"
        )
        sys.exit(1)

    suggestions = suggest_for_track(
        conn,
        track_id=row["id"],
        genre_family=row["genre_family"],
        same_genre=not any_genre,
        limit=limit,
    )
    _print_suggestions(suggestions, current.title, current.artist)


def _run_suggest(
    conn,
    query: str,
    same_genre: bool = True,
    limit: int = 20,
) -> None:
    matches = fuzzy_match(conn, query, limit=10, score_threshold=60.0)

    if not matches:
        console.print(f"[red]No tracks found matching '{query}'.[/red]")
        sys.exit(1)

    # If top match is clear (score >= 90 and well ahead of second), use it directly
    top = matches[0]
    strong = [m for m in matches if m.score >= 90]

    if len(strong) == 1 or (len(strong) > 1 and strong[0].score - strong[1].score >= 5):
        chosen = top
    elif len(strong) > 1:
        # Ambiguous — ask user to pick
        console.print(f"\n[yellow]Multiple strong matches for '{query}':[/yellow]\n")
        for i, m in enumerate(strong, 1):
            console.print(f"  [bold]{i}.[/bold] {m.title} — {m.artist} [{m.genre}] (score: {m.score:.0f})")
        console.print()
        idx_str = click.prompt("Pick a track (number)", type=str)
        try:
            idx = int(idx_str) - 1
            if not 0 <= idx < len(strong):
                raise ValueError
            chosen = strong[idx]
        except (ValueError, IndexError):
            console.print("[red]Invalid selection.[/red]")
            sys.exit(1)
    else:
        chosen = top

    console.print(
        f"\n[blue]Suggestions for:[/blue] {chosen.title} — {chosen.artist} [{chosen.genre}]\n"
    )

    suggestions = suggest_for_track(
        conn,
        track_id=chosen.track_id,
        genre_family=chosen.genre_family,
        same_genre=same_genre,
        limit=limit,
    )
    _print_suggestions(suggestions, chosen.title, chosen.artist)


def _print_suggestions(suggestions, source_title: str, source_artist: str) -> None:
    if not suggestions:
        console.print(
            "[yellow]No suggestions found. Have you run 'rebuild' after including playlists?[/yellow]"
        )
        return

    table = Table(show_header=True, show_lines=False)
    table.add_column("#", style="dim", justify="right", width=3)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Title")
    table.add_column("Artist")
    table.add_column("Genre", style="dim")

    for s in suggestions:
        table.add_row(str(s.rank), str(s.score), s.title, s.artist, s.genre)

    console.print(table)
