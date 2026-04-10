"""Tests for tanda detection logic using synthetic data."""

from __future__ import annotations

import sqlite3

import pytest

from tanda_suggester.db import genre_family, init_db
from tanda_suggester.tandas import (
    TrackRow,
    detect_tandas_for_playlist,
    is_cortina,
    rebuild_tandas,
)


def make_track(id: int, title: str, genre: str, artist: str = "Artist") -> TrackRow:
    return TrackRow(id=id, title=title, artist=artist, genre=genre, genre_family=genre_family(genre))


# --- Unit: is_cortina ---


def test_is_cortina_exact():
    assert is_cortina("Cortina")
    assert is_cortina("cortina")
    assert is_cortina("CORTINA")
    assert is_cortina("Cortinas")
    assert is_cortina("cortinas")


def test_is_cortina_false():
    assert not is_cortina("Tango")
    assert not is_cortina("Vals")
    assert not is_cortina("")


# --- Unit: detect_tandas_for_playlist ---


def test_basic_tanda_detection():
    """Three tangos, cortina, two vals → two tandas."""
    tracks = [
        make_track(1, "T1", "Tango"),
        make_track(2, "T2", "Tango (vocal)"),
        make_track(3, "T3", "Tango (instrumental)"),
        make_track(4, "C1", "Cortina"),
        make_track(5, "V1", "Vals"),
        make_track(6, "V2", "Vals (vocal)"),
    ]
    tandas = detect_tandas_for_playlist(playlist_id=1, tracks=tracks)
    assert len(tandas) == 2
    assert len(tandas[0].tracks) == 3
    assert len(tandas[1].tracks) == 2
    assert tandas[0].position == 0
    assert tandas[1].position == 1


def test_tanda_genre_majority():
    """Mixed genres → dominant genre wins."""
    tracks = [
        make_track(1, "T1", "Tango (vocal)"),
        make_track(2, "T2", "Tango (vocal)"),
        make_track(3, "T3", "Tango (instrumental)"),
    ]
    tandas = detect_tandas_for_playlist(playlist_id=1, tracks=tracks)
    assert len(tandas) == 1
    assert tandas[0].genre == "Tango (vocal)"
    assert tandas[0].genre_family_value == "tango"


def test_short_tanda_skipped():
    """All short tandas skipped, one valid tanda at end."""
    tracks = [
        make_track(1, "T1", "Tango"),
        make_track(2, "C1", "Cortina"),
        make_track(3, "T2", "Tango"),
        make_track(4, "C2", "cortinas"),
        make_track(5, "T3", "Tango"),
        make_track(6, "T4", "Tango"),
    ]
    tandas = detect_tandas_for_playlist(playlist_id=1, tracks=tracks)
    # T1 alone → skipped; T2 alone → skipped; T3+T4 → valid
    assert len(tandas) == 1
    assert {t.id for t in tandas[0].tracks} == {5, 6}


def test_trailing_tanda_emitted():
    """Playlist ending without a cortina still emits the last tanda."""
    tracks = [
        make_track(1, "T1", "Tango"),
        make_track(2, "T2", "Tango"),
        make_track(3, "C1", "Cortina"),
        make_track(4, "V1", "Vals"),
        make_track(5, "V2", "Vals"),
        make_track(6, "V3", "Vals"),
        # no trailing cortina
    ]
    tandas = detect_tandas_for_playlist(playlist_id=1, tracks=tracks)
    assert len(tandas) == 2


def test_empty_genre_tracks_skipped():
    """Tracks with empty genre are skipped entirely (not counted as cortinas)."""
    tracks = [
        make_track(1, "T1", "Tango"),
        make_track(2, "??", ""),  # empty genre — skip, don't break tanda
        make_track(3, "T3", "Tango"),
        make_track(4, "C1", "Cortina"),
        make_track(5, "T4", "Tango"),
        make_track(6, "T5", "Tango"),
    ]
    tandas = detect_tandas_for_playlist(playlist_id=1, tracks=tracks)
    assert len(tandas) == 2
    # First tanda has T1 and T3 (empty-genre track skipped)
    assert len(tandas[0].tracks) == 2
    assert {t.id for t in tandas[0].tracks} == {1, 3}


def test_cortinas_variant():
    """'Cortinas' (plural) works as boundary."""
    tracks = [
        make_track(1, "T1", "Tango"),
        make_track(2, "T2", "Tango"),
        make_track(3, "C", "Cortinas"),
        make_track(4, "V1", "Vals"),
        make_track(5, "V2", "Vals"),
    ]
    tandas = detect_tandas_for_playlist(playlist_id=1, tracks=tracks)
    assert len(tandas) == 2


# --- Integration: rebuild_tandas with DB ---


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    conn = init_db(db_path=None)
    # Use in-memory DB
    conn2 = sqlite3.connect(":memory:")
    conn2.row_factory = sqlite3.Row
    conn2.execute("PRAGMA foreign_keys=ON")
    conn2.executescript(
        open(__file__.replace("test_tandas.py", ""), "r").read()  # dummy
    )
    return conn


@pytest.fixture
def seeded_db():
    """In-memory DB with one included playlist of 5 tracks + cortina."""
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp()) / "test.sqlite"
    conn = init_db(db_path=tmp)

    # Insert tracks
    conn.executemany(
        "INSERT INTO tracks (id, music_app_id, title, artist, genre, genre_family) VALUES (?,?,?,?,?,?)",
        [
            (1, "pid1", "Felicia", "Di Sarli", "Tango (vocal)", "tango"),
            (2, "pid2", "Bahia Blanca", "Di Sarli", "Tango (instrumental)", "tango"),
            (3, "pid3", "Corazon", "Di Sarli", "Tango (vocal)", "tango"),
            (4, "pid4", "My Cortina", "DJ", "Cortina", "cortina"),
            (5, "pid5", "La Cumparsita", "Rodriguez", "Tango (instrumental)", "tango"),
            (6, "pid6", "Adios Muchachos", "Rodriguez", "Tango (vocal)", "tango"),
        ],
    )

    # Insert playlist
    conn.execute(
        "INSERT INTO playlists (id, music_app_id, name, included, track_count) VALUES (1,'pl1','Test Milonga',1,6)"
    )

    # Insert playlist_tracks: tanda [1,2,3], cortina [4], tanda [5,6]
    conn.executemany(
        "INSERT INTO playlist_tracks (playlist_id, position, track_id) VALUES (1,?,?)",
        [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6)],
    )
    conn.commit()
    return conn


def test_rebuild_tandas_count(seeded_db):
    tanda_count, co_count = rebuild_tandas(seeded_db)
    assert tanda_count == 2


def test_rebuild_co_occurrence(seeded_db):
    rebuild_tandas(seeded_db)
    # Tracks 1,2,3 were in tanda 1 → all pairs should be present
    rows = seeded_db.execute(
        "SELECT track_a_id, track_b_id, count FROM co_occurrence ORDER BY track_a_id, track_b_id"
    ).fetchall()
    pair_map = {(r["track_a_id"], r["track_b_id"]): r["count"] for r in rows}

    # Tanda 1: tracks 1,2,3 → 6 ordered pairs
    assert pair_map[(1, 2)] == 1
    assert pair_map[(1, 3)] == 1
    assert pair_map[(2, 1)] == 1
    assert pair_map[(3, 1)] == 1

    # Tracks 5,6 in tanda 2
    assert pair_map[(5, 6)] == 1
    assert pair_map[(6, 5)] == 1

    # Cross-tanda pairs should NOT exist
    assert (1, 5) not in pair_map
    assert (5, 1) not in pair_map


def test_rebuild_is_idempotent(seeded_db):
    rebuild_tandas(seeded_db)
    rebuild_tandas(seeded_db)
    count = seeded_db.execute("SELECT COUNT(*) FROM tandas").fetchone()[0]
    assert count == 2  # not doubled
