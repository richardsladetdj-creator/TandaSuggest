"""Tests for fuzzy track matching."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tanda_suggester.db import init_db
from tanda_suggester.search import fuzzy_match


@pytest.fixture
def search_db():
    tmp = Path(tempfile.mkdtemp()) / "search_test.sqlite"
    conn = init_db(db_path=tmp)
    conn.executemany(
        "INSERT INTO tracks (id, music_app_id, title, artist, genre, genre_family) VALUES (?,?,?,?,?,?)",
        [
            (1, "p1", "La Cumparsita", "Rodriguez", "Tango", "tango"),
            (2, "p2", "El Choclo", "Canaro", "Tango", "tango"),
            (3, "p3", "Desde el Alma", "Hector Varela", "Vals", "vals"),
            (4, "p4", "La Cumparsita", "Di Sarli", "Tango (instrumental)", "tango"),
        ],
    )
    conn.commit()
    return conn


def test_exact_title_match(search_db):
    results = fuzzy_match(search_db, "La Cumparsita")
    assert len(results) >= 1
    assert results[0].title == "La Cumparsita"


def test_fuzzy_typo(search_db):
    results = fuzzy_match(search_db, "cumparsita")
    assert len(results) >= 1
    assert results[0].title == "La Cumparsita"


def test_artist_included_in_match(search_db):
    results = fuzzy_match(search_db, "La Cumparsita Di Sarli")
    assert len(results) >= 1
    # Di Sarli version should rank highest
    assert results[0].artist == "Di Sarli"


def test_no_match_below_threshold(search_db):
    results = fuzzy_match(search_db, "zzzzzzzzz nonsense query", score_threshold=95.0)
    assert results == []


def test_empty_db():
    tmp = Path(tempfile.mkdtemp()) / "empty.sqlite"
    conn = init_db(db_path=tmp)
    results = fuzzy_match(conn, "anything")
    assert results == []


def test_multiple_matches_for_same_title(search_db):
    """Two tracks named 'La Cumparsita' by different artists should both appear."""
    results = fuzzy_match(search_db, "La Cumparsita", limit=10)
    titles = [r.title for r in results]
    assert titles.count("La Cumparsita") == 2
