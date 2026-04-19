"""SQLite schema and connection management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "tanda-suggester" / "db.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id              INTEGER PRIMARY KEY,
    music_app_id    TEXT    UNIQUE NOT NULL,
    title           TEXT    NOT NULL,
    artist          TEXT    NOT NULL DEFAULT '',
    genre           TEXT    NOT NULL DEFAULT '',
    genre_family    TEXT,
    duration_seconds INTEGER
);

CREATE TABLE IF NOT EXISTS playlists (
    id              INTEGER PRIMARY KEY,
    music_app_id    TEXT    UNIQUE NOT NULL,
    name            TEXT    NOT NULL,
    included        BOOLEAN NOT NULL DEFAULT 0,
    excluded        BOOLEAN NOT NULL DEFAULT 0,
    track_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id     INTEGER NOT NULL REFERENCES playlists(id),
    position        INTEGER NOT NULL,
    track_id        INTEGER NOT NULL REFERENCES tracks(id),
    PRIMARY KEY (playlist_id, position)
);

CREATE TABLE IF NOT EXISTS tandas (
    id              INTEGER PRIMARY KEY,
    playlist_id     INTEGER NOT NULL REFERENCES playlists(id),
    position        INTEGER NOT NULL,
    genre           TEXT,
    genre_family    TEXT,
    track_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tanda_tracks (
    tanda_id        INTEGER NOT NULL REFERENCES tandas(id),
    position        INTEGER NOT NULL,
    track_id        INTEGER NOT NULL REFERENCES tracks(id),
    PRIMARY KEY (tanda_id, position)
);

CREATE TABLE IF NOT EXISTS co_occurrence (
    track_a_id      INTEGER NOT NULL REFERENCES tracks(id),
    track_b_id      INTEGER NOT NULL REFERENCES tracks(id),
    count           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (track_a_id, track_b_id)
);

CREATE INDEX IF NOT EXISTS idx_tracks_music_app_id ON tracks(music_app_id);
CREATE INDEX IF NOT EXISTS idx_co_occurrence_a ON co_occurrence(track_a_id);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    _apply_migrations(conn)
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations for existing databases."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(playlists)")}
    if "excluded" not in cols:
        conn.execute("ALTER TABLE playlists ADD COLUMN excluded BOOLEAN NOT NULL DEFAULT 0")
        conn.commit()

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "app_settings" not in tables:
        conn.execute(
            "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()


def genre_family(genre: str, conn: sqlite3.Connection | None = None) -> str | None:
    """Map a raw genre string to a genre family for filtering.

    When conn is provided, uses the user's configured genre rules from the DB.
    Falls back to default settings (Tango/Vals/Milonga/Cortina substring match).
    """
    from tanda_suggester.settings import classify_genre, load_settings, DEFAULT_SETTINGS
    if conn is not None:
        settings = load_settings(conn)
    else:
        settings = DEFAULT_SETTINGS
    return classify_genre(genre, settings)


def reprocess_genre_families(conn: sqlite3.Connection) -> int:
    """Re-classify genre_family for all tracks using current settings.

    Returns the number of tracks updated.
    """
    from tanda_suggester.settings import classify_genre, load_settings
    settings = load_settings(conn)
    rows = conn.execute("SELECT id, genre FROM tracks").fetchall()
    updates = [(classify_genre(row["genre"], settings), row["id"]) for row in rows]
    conn.executemany("UPDATE tracks SET genre_family = ? WHERE id = ?", updates)
    conn.commit()
    return len(updates)
