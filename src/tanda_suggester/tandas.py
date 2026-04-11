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
class OrchestraGroup:
    artist: str
    tracks: list[tuple[int, str, str]]  # (playlist_pos, title, artist)


@dataclass
class NoiseReport:
    playlist_name: str
    tanda_position: int
    orchestra_groups: list[OrchestraGroup]
    missing_cortina_positions: list[int]  # playlist positions after which a cortina is missing


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


def diagnose_noise_track(conn: sqlite3.Connection, track_id: int) -> list[NoiseReport]:
    """Find all mixed-orchestra tandas containing track_id and identify missing cortinas."""
    tanda_rows = conn.execute(
        """SELECT t.id, t.playlist_id, p.name, t.position
           FROM tandas t
           JOIN playlists p ON p.id = t.playlist_id
           WHERE t.id IN (SELECT tanda_id FROM tanda_tracks WHERE track_id = ?)
           ORDER BY p.name, t.position""",
        (track_id,),
    ).fetchall()

    reports: list[NoiseReport] = []

    for row in tanda_rows:
        tanda_id = row["id"]
        playlist_id = row["playlist_id"]
        playlist_name = row["name"]
        tanda_position = row["position"]

        track_rows = conn.execute(
            """SELECT tt.position AS tanda_pos, t.title, t.artist, pt.position AS pl_pos
               FROM tanda_tracks tt
               JOIN tracks t ON t.id = tt.track_id
               JOIN playlist_tracks pt ON pt.track_id = t.id AND pt.playlist_id = ?
               WHERE tt.tanda_id = ?
               ORDER BY tt.position""",
            (playlist_id, tanda_id),
        ).fetchall()

        if not track_rows:
            continue

        # Group consecutive tracks by artist
        groups: list[OrchestraGroup] = []
        current_artist: str | None = None
        current_tracks: list[tuple[int, str, str]] = []

        for tr in track_rows:
            artist = tr["artist"]
            if artist != current_artist:
                if current_tracks and current_artist is not None:
                    groups.append(OrchestraGroup(artist=current_artist, tracks=current_tracks))
                current_artist = artist
                current_tracks = [(tr["pl_pos"], tr["title"], tr["artist"])]
            else:
                current_tracks.append((tr["pl_pos"], tr["title"], tr["artist"]))

        if current_tracks and current_artist is not None:
            groups.append(OrchestraGroup(artist=current_artist, tracks=current_tracks))

        # Skip homogeneous tandas (single orchestra)
        if len(groups) <= 1:
            continue

        # Find positions where a cortina is missing (consecutive playlist positions, different artist)
        missing: list[int] = []
        for i in range(len(groups) - 1):
            last_pl_pos = groups[i].tracks[-1][0]
            first_pl_pos = groups[i + 1].tracks[0][0]
            if first_pl_pos == last_pl_pos + 1:
                missing.append(last_pl_pos)

        reports.append(NoiseReport(
            playlist_name=playlist_name,
            tanda_position=tanda_position,
            orchestra_groups=groups,
            missing_cortina_positions=missing,
        ))

    return reports
