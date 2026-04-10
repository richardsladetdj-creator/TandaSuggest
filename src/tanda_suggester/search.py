"""Fuzzy track matching using rapidfuzz."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from rapidfuzz import process, fuzz


@dataclass
class MatchResult:
    track_id: int
    title: str
    artist: str
    genre: str
    genre_family: str | None
    score: float


def fuzzy_match(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 10,
    score_threshold: float = 60.0,
) -> list[MatchResult]:
    """Fuzzy-match query against all tracks. Returns results sorted by score desc,
    then by number of tanda appearances (so well-used tracks surface first).
    """
    rows = conn.execute(
        """SELECT t.id, t.title, t.artist, t.genre, t.genre_family,
                  COUNT(tt.tanda_id) AS tanda_count
           FROM tracks t
           LEFT JOIN tanda_tracks tt ON tt.track_id = t.id
           GROUP BY t.id"""
    ).fetchall()

    if not rows:
        return []

    # Build search strings: "Title - Artist"
    choices = {r["id"]: f"{r['title']} - {r['artist']}" for r in rows}
    row_by_id = {r["id"]: r for r in rows}

    results = process.extract(
        query,
        choices,
        scorer=fuzz.WRatio,
        limit=limit * 5,  # over-fetch; we'll filter and trim
        score_cutoff=score_threshold,
    )

    matches: list[MatchResult] = []
    for _match_str, score, track_id in results:
        r = row_by_id[track_id]
        matches.append(
            MatchResult(
                track_id=track_id,
                title=r["title"],
                artist=r["artist"],
                genre=r["genre"],
                genre_family=r["genre_family"],
                score=score,
            )
        )

    # Sort by score DESC, then by tanda usage DESC (prefer tracks with history)
    matches.sort(
        key=lambda m: (m.score, row_by_id[m.track_id]["tanda_count"]),
        reverse=True,
    )
    return matches[:limit]
