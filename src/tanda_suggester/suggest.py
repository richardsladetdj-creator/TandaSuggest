"""Co-occurrence based track suggestion logic."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class Suggestion:
    rank: int
    score: int
    track_id: int
    title: str
    artist: str
    genre: str
    genre_family: str | None


def suggest_for_track(
    conn: sqlite3.Connection,
    track_id: int,
    genre_family: str | None,
    same_genre: bool = True,
    limit: int = 20,
) -> list[Suggestion]:
    """Return ranked suggestions for a given track based on co-occurrence."""
    if same_genre and genre_family:
        rows = conn.execute(
            """SELECT t.id, t.title, t.artist, t.genre, t.genre_family,
                      SUM(co.count) AS score
               FROM co_occurrence co
               JOIN tracks t ON t.id = co.track_b_id
               WHERE co.track_a_id = ?
                 AND t.genre_family = ?
                 AND co.track_b_id != ?
               GROUP BY co.track_b_id
               ORDER BY score DESC
               LIMIT ?""",
            (track_id, genre_family, track_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT t.id, t.title, t.artist, t.genre, t.genre_family,
                      SUM(co.count) AS score
               FROM co_occurrence co
               JOIN tracks t ON t.id = co.track_b_id
               WHERE co.track_a_id = ?
                 AND co.track_b_id != ?
               GROUP BY co.track_b_id
               ORDER BY score DESC
               LIMIT ?""",
            (track_id, track_id, limit),
        ).fetchall()

    return [
        Suggestion(
            rank=i + 1,
            score=r["score"],
            track_id=r["id"],
            title=r["title"],
            artist=r["artist"],
            genre=r["genre"],
            genre_family=r["genre_family"],
        )
        for i, r in enumerate(rows)
    ]
