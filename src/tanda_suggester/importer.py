"""Orchestrates the import of tracks (AppleScript) and playlists (XML)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.status import Status

from tanda_suggester.db import genre_family, init_db
from tanda_suggester.music_app import (
    RawPlaylist,
    RawTrack,
    get_track_count,
    read_all_playlists_applescript,
    read_playlists_applescript,
    read_tracks_applescript,
)

console = Console()


def import_tracks(conn: sqlite3.Connection, batch_size: int = 500) -> int:
    """Read all relevant tracks from Music.app via AppleScript and upsert into DB.

    Returns number of tracks upserted.
    """
    total = get_track_count()
    console.print(f"[blue]Reading {total:,} tracks from Music.app (batches of {batch_size})…[/blue]")
    console.print("[dim]Only tracks with tango/vals/milonga/cortina genres will be stored.[/dim]")

    upserted = 0
    batches = (total + batch_size - 1) // batch_size

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Importing tracks…", total=batches)

        for batch in read_tracks_applescript(batch_size=batch_size):
            _upsert_tracks(conn, batch)
            upserted += len(batch)
            progress.advance(task)

    conn.commit()
    console.print(f"[green]✓ {upserted:,} tracks imported.[/green]")
    return upserted


def _upsert_tracks(conn: sqlite3.Connection, tracks: list[RawTrack]) -> None:
    conn.executemany(
        """INSERT INTO tracks (music_app_id, title, artist, genre, genre_family, duration_seconds)
           VALUES (:pid, :title, :artist, :genre, :gf, :dur)
           ON CONFLICT(music_app_id) DO UPDATE SET
               title = excluded.title,
               artist = excluded.artist,
               genre = excluded.genre,
               genre_family = excluded.genre_family,
               duration_seconds = excluded.duration_seconds""",
        [
            {
                "pid": t.persistent_id,
                "title": t.title,
                "artist": t.artist,
                "genre": t.genre,
                "gf": genre_family(t.genre),
                "dur": t.duration_seconds,
            }
            for t in tracks
        ],
    )


def import_playlists(conn: sqlite3.Connection) -> int:
    """Read playlist structure from Music.app via AppleScript and write to DB.

    Returns number of playlists imported.
    """
    with Status("[blue]Reading playlists from Music.app…[/blue]", console=console):
        playlists, date = read_all_playlists_applescript()

    console.print(f"[blue]Found {len(playlists):,} playlists.[/blue]")

    # Build persistent_id → db_id lookup for tracks already in DB
    rows = conn.execute("SELECT id, music_app_id FROM tracks").fetchall()
    pid_to_id: dict[str, int] = {r["music_app_id"]: r["id"] for r in rows}

    imported = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Importing playlists…", total=len(playlists))

        for pl in playlists:
            track_ids = [pid_to_id[pid] for pid in pl.track_persistent_ids if pid in pid_to_id]
            progress.advance(task)
            if not track_ids:
                continue

            # Upsert playlist row (preserve included status if already set)
            conn.execute(
                """INSERT INTO playlists (music_app_id, name, track_count)
                   VALUES (?, ?, ?)
                   ON CONFLICT(music_app_id) DO UPDATE SET
                       name = excluded.name,
                       track_count = excluded.track_count""",
                (pl.persistent_id, pl.name, len(track_ids)),
            )
            pl_id = conn.execute(
                "SELECT id FROM playlists WHERE music_app_id = ?", (pl.persistent_id,)
            ).fetchone()["id"]

            # Replace playlist_tracks for this playlist
            conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (pl_id,))
            conn.executemany(
                "INSERT INTO playlist_tracks (playlist_id, position, track_id) VALUES (?, ?, ?)",
                [(pl_id, pos, tid) for pos, tid in enumerate(track_ids)],
            )
            imported += 1

    conn.commit()
    console.print(f"[green]✓ {imported:,} playlists imported.[/green]")
    return imported


def import_named_playlists(
    conn: sqlite3.Connection,
    name_filter: str,
) -> tuple[int, int, list[str]]:
    """Import tracks and playlists from Music.app for playlists matching name_filter (shell glob).

    Returns (tracks_upserted, playlists_imported, imported_names).
    """
    with Status("[blue]Fetching playlist details from Music.app…[/blue]", console=console):
        tracks_by_pid, playlists, date = read_playlists_applescript(name_filter)

    if not playlists:
        return 0, 0, []

    console.print(
        f"[blue]Found {len(playlists)} playlist(s) matching '{name_filter}' "
        f"(library date: {date}).[/blue]"
    )

    _upsert_tracks(conn, list(tracks_by_pid.values()))
    conn.commit()

    # Build pid → db id map for the tracks we just upserted
    pid_to_id: dict[str, int] = {
        r["music_app_id"]: r["id"]
        for r in conn.execute("SELECT id, music_app_id FROM tracks").fetchall()
    }

    imported = 0
    imported_names: list[str] = []
    for pl in playlists:
        track_ids = [pid_to_id[pid] for pid in pl.track_persistent_ids if pid in pid_to_id]
        if not track_ids:
            console.print(f"  [yellow]⚠ Skipped '{pl.name}' — no tango tracks.[/yellow]")
            continue

        conn.execute(
            """INSERT INTO playlists (music_app_id, name, track_count)
               VALUES (?, ?, ?)
               ON CONFLICT(music_app_id) DO UPDATE SET
                   name = excluded.name,
                   track_count = excluded.track_count""",
            (pl.persistent_id, pl.name, len(track_ids)),
        )
        pl_id = conn.execute(
            "SELECT id FROM playlists WHERE music_app_id = ?", (pl.persistent_id,)
        ).fetchone()["id"]

        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (pl_id,))
        conn.executemany(
            "INSERT INTO playlist_tracks (playlist_id, position, track_id) VALUES (?, ?, ?)",
            [(pl_id, pos, tid) for pos, tid in enumerate(track_ids)],
        )
        console.print(f"  [green]✓[/green] {pl.name} ({len(track_ids)} tracks)")
        imported += 1
        imported_names.append(pl.name)

    conn.commit()
    return len(tracks_by_pid), imported, imported_names


def run_import(db_path: Path | None = None, batch_size: int = 500) -> None:
    """Full import: tracks via AppleScript, playlists via XML."""
    conn = init_db(db_path)
    import_tracks(conn, batch_size=batch_size)
    import_playlists(conn)
    conn.close()
