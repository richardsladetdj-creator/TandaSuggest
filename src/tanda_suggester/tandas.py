"""Tanda detection and co-occurrence index building."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.status import Status

from tanda_suggester.db import genre_family, get_connection

console = Console()

DEFAULT_CORTINA_GENRES = frozenset(["cortina", "cortinas"])


@dataclass
class TrackRow:
    id: int
    title: str
    artist: str
    genre: str
    genre_family: str | None


@dataclass
class Tanda:
    playlist_id: int
    position: int  # 0-indexed within playlist
    tracks: list[TrackRow] = field(default_factory=list)

    @property
    def genre(self) -> str | None:
        genres = [t.genre for t in self.tracks if t.genre]
        if not genres:
            return None
        return Counter(genres).most_common(1)[0][0]

    @property
    def genre_family_value(self) -> str | None:
        families = [t.genre_family for t in self.tracks if t.genre_family]
        if not families:
            return None
        return Counter(families).most_common(1)[0][0]


def is_cortina(genre: str, cortina_genres: frozenset[str] = DEFAULT_CORTINA_GENRES) -> bool:
    return genre.lower() in cortina_genres


def detect_tandas_for_playlist(
    playlist_id: int,
    tracks: list[TrackRow],
    cortina_genres: frozenset[str] = DEFAULT_CORTINA_GENRES,
    min_tracks: int = 2,
) -> list[Tanda]:
    """Detect tandas within an ordered list of tracks for one playlist."""
    tandas: list[Tanda] = []
    buffer: list[TrackRow] = []
    tanda_position = 0

    def emit() -> None:
        nonlocal tanda_position
        # Filter out tracks with no genre family (non-tango noise)
        valid = [t for t in buffer if t.genre_family is not None and t.genre_family != "cortina"]
        if len(valid) >= min_tracks:
            t = Tanda(playlist_id=playlist_id, position=tanda_position, tracks=valid)
            tandas.append(t)
            tanda_position += 1

    for track in tracks:
        if not track.genre:
            continue  # skip genre-less tracks per spec
        if is_cortina(track.genre, cortina_genres):
            emit()
            buffer = []
        else:
            buffer.append(track)

    # Trailing tanda (no cortina at end of playlist)
    emit()

    return tandas


def rebuild_tandas(
    conn: sqlite3.Connection,
    cortina_genres: frozenset[str] = DEFAULT_CORTINA_GENRES,
) -> tuple[int, int]:
    """Detect tandas for all included playlists, rebuild co-occurrence.

    Returns (tanda_count, co_occurrence_pair_count).
    """
    # Clear existing tanda data
    conn.execute("DELETE FROM tanda_tracks")
    conn.execute("DELETE FROM tandas")
    conn.execute("DELETE FROM co_occurrence")
    conn.commit()

    included = conn.execute(
        "SELECT id FROM playlists WHERE included = 1 AND excluded = 0"
    ).fetchall()

    all_tandas: list[Tanda] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        detect_task = progress.add_task("Detecting tandas…", total=len(included))

        for (playlist_id,) in included:
            rows = conn.execute(
                """SELECT t.id, t.title, t.artist, t.genre, t.genre_family
                   FROM playlist_tracks pt
                   JOIN tracks t ON t.id = pt.track_id
                   WHERE pt.playlist_id = ?
                   ORDER BY pt.position""",
                (playlist_id,),
            ).fetchall()

            tracks = [
                TrackRow(
                    id=r["id"],
                    title=r["title"],
                    artist=r["artist"],
                    genre=r["genre"],
                    genre_family=r["genre_family"],
                )
                for r in rows
            ]

            tandas = detect_tandas_for_playlist(playlist_id, tracks, cortina_genres)
            all_tandas.extend(tandas)
            progress.advance(detect_task)

    # Write tandas and tanda_tracks
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        write_task = progress.add_task("Writing tandas…", total=len(all_tandas))

        for tanda in all_tandas:
            cur = conn.execute(
                """INSERT INTO tandas (playlist_id, position, genre, genre_family, track_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    tanda.playlist_id,
                    tanda.position,
                    tanda.genre,
                    tanda.genre_family_value,
                    len(tanda.tracks),
                ),
            )
            tanda_id = cur.lastrowid
            for pos, track in enumerate(tanda.tracks):
                conn.execute(
                    "INSERT INTO tanda_tracks (tanda_id, position, track_id) VALUES (?, ?, ?)",
                    (tanda_id, pos, track.id),
                )
            progress.advance(write_task)

    conn.commit()

    # Build co-occurrence from all tandas
    with Status("[blue]Building co-occurrence index…[/blue]", console=console):
        _rebuild_co_occurrence(conn)

    co_count = conn.execute("SELECT COUNT(*) FROM co_occurrence").fetchone()[0]
    return len(all_tandas), co_count


def _rebuild_co_occurrence(conn: sqlite3.Connection) -> None:
    """Rebuild co_occurrence table from tanda_tracks."""
    # Load all tanda memberships grouped by tanda
    rows = conn.execute(
        "SELECT tanda_id, track_id FROM tanda_tracks ORDER BY tanda_id, position"
    ).fetchall()

    # Group by tanda
    tanda_members: dict[int, list[int]] = {}
    for row in rows:
        tanda_members.setdefault(row["tanda_id"], []).append(row["track_id"])

    # Count co-occurrences (ordered pairs: a appeared in same tanda as b)
    counts: Counter[tuple[int, int]] = Counter()
    for track_ids in tanda_members.values():
        for i, a in enumerate(track_ids):
            for j, b in enumerate(track_ids):
                if a != b:
                    counts[(a, b)] += 1

    if not counts:
        return

    conn.executemany(
        "INSERT INTO co_occurrence (track_a_id, track_b_id, count) VALUES (?, ?, ?)",
        [(a, b, c) for (a, b), c in counts.items()],
    )
