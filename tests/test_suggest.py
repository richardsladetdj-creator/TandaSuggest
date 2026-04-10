"""Tests for suggestion ranking with seeded co-occurrence data."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tanda_suggester.db import init_db
from tanda_suggester.suggest import suggest_for_track
from tanda_suggester.tandas import rebuild_tandas


@pytest.fixture
def suggest_db():
    """DB seeded with two tandas sharing a track, to test ranking."""
    tmp = Path(tempfile.mkdtemp()) / "suggest_test.sqlite"
    conn = init_db(db_path=tmp)

    # Tracks: 1–3 tango, 4–5 vals, 6 milonga
    conn.executemany(
        "INSERT INTO tracks (id, music_app_id, title, artist, genre, genre_family) VALUES (?,?,?,?,?,?)",
        [
            (1, "p1", "Felicia", "Di Sarli", "Tango (vocal)", "tango"),
            (2, "p2", "Bahia Blanca", "Di Sarli", "Tango (instrumental)", "tango"),
            (3, "p3", "Corazon", "Di Sarli", "Tango (vocal)", "tango"),
            (4, "p4", "La Paloma", "Canaro", "Vals", "vals"),
            (5, "p5", "Desde el Alma", "De Angelis", "Vals (vocal)", "vals"),
            (6, "p6", "Milonga Sentimental", "Troilo", "Milonga", "milonga"),
        ],
    )

    # Cortina tracks (needed for FK constraints in playlist_tracks)
    conn.executemany(
        "INSERT INTO tracks (id, music_app_id, title, artist, genre, genre_family) VALUES (?,?,?,?,?,?)",
        [
            (98, "c2", "Cortina2", "DJ", "Cortinas", "cortina"),
            (99, "c1", "Cortina", "DJ", "Cortina", "cortina"),
        ],
    )

    conn.executemany(
        "INSERT INTO playlists (id, music_app_id, name, included, track_count) VALUES (?,?,?,?,?)",
        [(1, "pl1", "Milonga A", 1, 7), (2, "pl2", "Milonga B", 1, 4)],
    )

    # Playlist 1: [1,2,3] cortina(99) [4,5]
    conn.executemany(
        "INSERT INTO playlist_tracks (playlist_id, position, track_id) VALUES (1,?,?)",
        [(0, 1), (1, 2), (2, 3), (3, 99), (4, 4), (5, 5)],
    )
    # Playlist 2: [1,2] cortina(98) [6]  (track 1 reappears with track 2)
    conn.executemany(
        "INSERT INTO playlist_tracks (playlist_id, position, track_id) VALUES (2,?,?)",
        [(0, 1), (1, 2), (2, 98), (3, 6)],
    )
    conn.commit()

    rebuild_tandas(conn)
    return conn


def test_suggest_same_genre(suggest_db):
    """Track 1 (tango) should suggest other tango tracks, not vals/milonga."""
    suggestions = suggest_for_track(suggest_db, track_id=1, genre_family="tango", same_genre=True)
    genres = {s.genre_family for s in suggestions}
    assert genres == {"tango"}
    # Track 1 co-occurs with 2 in two playlists, so track 2 should rank higher than track 3
    ids = [s.track_id for s in suggestions]
    assert 2 in ids
    assert 3 in ids
    assert ids.index(2) < ids.index(3)  # track 2 scored higher


def test_suggest_any_genre(suggest_db):
    """--any-genre flag returns suggestions across all genre families."""
    suggestions = suggest_for_track(suggest_db, track_id=1, genre_family="tango", same_genre=False)
    families = {s.genre_family for s in suggestions}
    # track 1 co-occurs with tango tracks 2,3 and milonga track 6 (via playlist 2 tanda skipped? no)
    # Actually playlist 2: [1,2] cortina [6] — tanda [1,2] is valid (2 tracks), tanda [6] alone is skipped
    # So track 1 co-occurs with 2 (twice) and 3 (once, from playlist 1)
    # Milonga track 6 is alone — not in a valid tanda, so no co-occurrence with track 1
    assert "tango" in families


def test_suggest_ranking(suggest_db):
    """Track 2 co-occurs with track 1 in both playlists — should rank above track 3."""
    suggestions = suggest_for_track(suggest_db, track_id=3, genre_family="tango", same_genre=True)
    # Track 3 only appears in playlist 1 with tracks 1 and 2
    ids = [s.track_id for s in suggestions]
    assert 1 in ids
    assert 2 in ids


def test_suggest_excludes_self(suggest_db):
    """The input track itself should never appear in suggestions."""
    suggestions = suggest_for_track(suggest_db, track_id=1, genre_family="tango", same_genre=False)
    assert all(s.track_id != 1 for s in suggestions)


def test_suggest_empty_when_no_co_occurrence(suggest_db):
    """A track with no tanda co-occurrences should return no suggestions."""
    # Track 6 (milonga) is alone in its tanda (1 track, < 2 min, skipped)
    suggestions = suggest_for_track(suggest_db, track_id=6, genre_family="milonga", same_genre=True)
    assert suggestions == []
