"""Background QThread workers for long-running operations."""

from __future__ import annotations

import threading
import time
from math import ceil
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from tanda_suggester.db import DB_PATH, get_connection
from tanda_suggester.settings import classify_genre, load_settings
from tanda_suggester.music_app import (
    get_current_track,
    get_track_count,
    read_all_playlists_applescript,
    read_playlist_summaries_applescript,
    read_playlists_applescript,
    read_tracks_applescript,
)
from tanda_suggester.search import fuzzy_match
from tanda_suggester.suggest import suggest_for_track
from tanda_suggester.tandas import TrackRow, detect_tandas_for_playlist, _rebuild_co_occurrence


_rebuild_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Import worker: tracks + playlists from Music.app
# ---------------------------------------------------------------------------

class ImportWorker(QThread):
    """Import all tracks and playlists from Music.app."""

    progress = Signal(int, int, str)   # current, total, phase label
    finished = Signal(int, int)        # tracks_upserted, playlists_imported
    error = Signal(str)

    def __init__(self, db_path: Path | None = None, batch_size: int = 500) -> None:
        super().__init__()
        self.db_path = db_path or DB_PATH
        self.batch_size = batch_size

    def run(self) -> None:
        try:
            conn = get_connection(self.db_path)
            settings = load_settings(conn)

            # --- Phase 1: tracks ---
            total_tracks = get_track_count()
            total_batches = max(1, ceil(total_tracks / self.batch_size))
            tracks_upserted = 0

            for batch_num, batch in enumerate(read_tracks_applescript(self.batch_size, settings=settings), 1):
                _upsert_tracks(conn, batch, settings=settings)
                tracks_upserted += len(batch)
                self.progress.emit(batch_num, total_batches, "Importing tracks")

            conn.commit()

            # --- Phase 2: playlists ---
            playlists, _ = read_all_playlists_applescript()
            pid_to_id: dict[str, int] = {
                r["music_app_id"]: r["id"]
                for r in conn.execute("SELECT id, music_app_id FROM tracks").fetchall()
            }

            playlists_imported = 0
            for i, pl in enumerate(playlists, 1):
                self.progress.emit(i, len(playlists), "Importing playlists")
                track_ids = [pid_to_id[p] for p in pl.track_persistent_ids if p in pid_to_id]
                if not track_ids:
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
                playlists_imported += 1

            conn.commit()
            conn.close()
            self.finished.emit(tracks_upserted, playlists_imported)

        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Rebuild worker: tanda detection + co-occurrence index
# ---------------------------------------------------------------------------

class RebuildWorker(QThread):
    """Rebuild tanda detection and co-occurrence index."""

    progress = Signal(int, int, str)
    finished = Signal(int, int)   # tanda_count, co_occurrence_count
    error = Signal(str)

    def __init__(self, db_path: Path | None = None) -> None:
        super().__init__()
        self.db_path = db_path or DB_PATH

    def run(self) -> None:
        if not _rebuild_lock.acquire(blocking=False):
            self.error.emit("A rebuild is already in progress.")
            return

        conn = None
        emit_result: tuple[int, int] | None = None
        emit_error: str | None = None

        try:
            conn = get_connection(self.db_path)
            settings = load_settings(conn)
            conn.execute("SAVEPOINT rebuild")

            conn.execute("DELETE FROM tanda_tracks")
            conn.execute("DELETE FROM tandas")
            conn.execute("DELETE FROM co_occurrence")

            included = conn.execute(
                "SELECT id FROM playlists WHERE included = 1 AND excluded = 0"
            ).fetchall()

            all_tandas = []
            for i, row in enumerate(included, 1):
                playlist_id = row["id"]
                self.progress.emit(i, len(included), "Detecting tandas")

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
                all_tandas.extend(detect_tandas_for_playlist(playlist_id, tracks, settings))

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

            _rebuild_co_occurrence(conn)
            co_count = conn.execute("SELECT COUNT(*) FROM co_occurrence").fetchone()[0]
            conn.execute("RELEASE rebuild")
            emit_result = (len(all_tandas), co_count)

        except Exception as exc:
            if conn is not None:
                try:
                    conn.execute("ROLLBACK TO rebuild")
                    conn.execute("RELEASE rebuild")
                except Exception:
                    pass
            emit_error = str(exc)

        finally:
            if conn is not None:
                conn.close()
            _rebuild_lock.release()

        # Emit signals only after conn is fully closed and the lock is released.
        # This prevents QThread destruction while conn.close() is still running
        # (which causes SIGABRT on macOS via QThread::~QThread() fatal check).
        if emit_error is not None:
            self.error.emit(emit_error)
        elif emit_result is not None:
            self.finished.emit(*emit_result)


# ---------------------------------------------------------------------------
# Selective re-import + rebuild worker: refresh specific playlists from Music.app
# ---------------------------------------------------------------------------

class SelectiveRebuildWorker(QThread):
    """Re-import specific playlists from Music.app then rebuild their tanda/co-occurrence index."""

    progress = Signal(int, int, str)
    finished = Signal(int, int)   # tanda_count, co_occurrence_count
    error = Signal(str)

    def __init__(self, db_path: Path, playlist_ids: list[int]) -> None:
        super().__init__()
        self.db_path = db_path
        self.playlist_ids = playlist_ids

    def run(self) -> None:
        if not _rebuild_lock.acquire(blocking=False):
            self.error.emit("A rebuild is already in progress.")
            return

        conn = None
        emit_result: tuple[int, int] | None = None
        emit_error: str | None = None

        try:
            conn = get_connection(self.db_path)
            settings = load_settings(conn)
            conn.execute("SAVEPOINT selective_rebuild")

            placeholders = ",".join("?" * len(self.playlist_ids))

            # Look up name + music_app_id for each selected playlist
            playlist_rows = conn.execute(
                f"SELECT id, name, music_app_id FROM playlists WHERE id IN ({placeholders})",
                self.playlist_ids,
            ).fetchall()

            # Phase 1: Re-import each playlist from Music.app
            for i, pl_row in enumerate(playlist_rows, 1):
                self.progress.emit(i, len(playlist_rows), f"Importing '{pl_row['name']}'")
                tracks_by_pid, raw_playlists, _ = read_playlists_applescript(pl_row["name"], settings=settings)

                _upsert_tracks(conn, list(tracks_by_pid.values()), settings=settings)

                pid_to_id: dict[str, int] = {
                    r["music_app_id"]: r["id"]
                    for r in conn.execute("SELECT id, music_app_id FROM tracks").fetchall()
                }

                for raw_pl in raw_playlists:
                    if raw_pl.persistent_id != pl_row["music_app_id"]:
                        continue  # name matched a different playlist — skip
                    track_ids = [pid_to_id[p] for p in raw_pl.track_persistent_ids if p in pid_to_id]
                    conn.execute(
                        "UPDATE playlists SET track_count = ? WHERE id = ?",
                        (len(track_ids), pl_row["id"]),
                    )
                    conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (pl_row["id"],))
                    conn.executemany(
                        "INSERT INTO playlist_tracks (playlist_id, position, track_id) VALUES (?, ?, ?)",
                        [(pl_row["id"], pos, tid) for pos, tid in enumerate(track_ids)],
                    )

            # Phase 2: Delete existing tanda data for selected playlists
            tanda_ids = conn.execute(
                f"SELECT id FROM tandas WHERE playlist_id IN ({placeholders})",
                self.playlist_ids,
            ).fetchall()
            if tanda_ids:
                tanda_id_list = [r["id"] for r in tanda_ids]
                td_placeholders = ",".join("?" * len(tanda_id_list))
                conn.execute(
                    f"DELETE FROM tanda_tracks WHERE tanda_id IN ({td_placeholders})",
                    tanda_id_list,
                )
            conn.execute(
                f"DELETE FROM tandas WHERE playlist_id IN ({placeholders})",
                self.playlist_ids,
            )

            # Phase 3: Re-detect tandas for included playlists
            included = conn.execute(
                f"SELECT id FROM playlists WHERE id IN ({placeholders}) AND included = 1 AND excluded = 0",
                self.playlist_ids,
            ).fetchall()

            new_tandas = []
            for i, row in enumerate(included, 1):
                playlist_id = row["id"]
                self.progress.emit(i, len(included), "Detecting tandas")

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
                new_tandas.extend(detect_tandas_for_playlist(playlist_id, tracks, settings))

            for tanda in new_tandas:
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

            # Phase 4: Rebuild co_occurrence from ALL tanda_tracks (global table)
            conn.execute("DELETE FROM co_occurrence")
            _rebuild_co_occurrence(conn)
            co_count = conn.execute("SELECT COUNT(*) FROM co_occurrence").fetchone()[0]
            conn.execute("RELEASE selective_rebuild")
            emit_result = (len(new_tandas), co_count)

        except Exception as exc:
            if conn is not None:
                try:
                    conn.execute("ROLLBACK TO selective_rebuild")
                    conn.execute("RELEASE selective_rebuild")
                except Exception:
                    pass
            emit_error = str(exc)

        finally:
            if conn is not None:
                conn.close()
            _rebuild_lock.release()

        if emit_error is not None:
            self.error.emit(emit_error)
        elif emit_result is not None:
            self.finished.emit(*emit_result)


# ---------------------------------------------------------------------------
# Live track worker: polls Music.app for currently playing track
# ---------------------------------------------------------------------------

class LiveTrackWorker(QThread):
    """Poll Music.app every 5 seconds and emit when the track changes."""

    track_changed = Signal(str, str, str, str)   # persistent_id, title, artist, genre
    stopped = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._last_pid: str | None = None

    def run(self) -> None:
        self._running = True
        while self._running:
            try:
                track = get_current_track()
                if track and track.persistent_id != self._last_pid:
                    self._last_pid = track.persistent_id
                    self.track_changed.emit(
                        track.persistent_id, track.title, track.artist, track.genre
                    )
            except Exception:
                pass
            # Sleep in small increments so stop() is responsive
            for _ in range(50):
                if not self._running:
                    break
                time.sleep(0.1)
        self.stopped.emit()

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Refresh playlists worker: fetch unimported playlists from Music.app
# ---------------------------------------------------------------------------

class RefreshPlaylistsWorker(QThread):
    """Fetch all Music.app playlists; optionally filter to those not yet in the DB."""

    finished = Signal(list)   # list of (name, track_count, persistent_id)
    error = Signal(str)

    def __init__(self, db_path: Path | None = None, only_unimported: bool = True) -> None:
        super().__init__()
        self.db_path = db_path or DB_PATH
        self.only_unimported = only_unimported

    def run(self) -> None:
        try:
            summaries = read_playlist_summaries_applescript()
            # summaries: list of (persistent_id, name, track_count)

            conn = get_connection(self.db_path)
            db_ids = {
                r["music_app_id"]
                for r in conn.execute("SELECT music_app_id FROM playlists").fetchall()
            }
            conn.close()

            if self.only_unimported:
                result = [
                    (name, track_count, pid, False)
                    for pid, name, track_count in summaries
                    if pid not in db_ids
                ]
            else:
                result = [
                    (name, track_count, pid, pid in db_ids)
                    for pid, name, track_count in summaries
                ]

            self.finished.emit(result)

        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Import named playlists worker
# ---------------------------------------------------------------------------

class ImportNamedPlaylistsWorker(QThread):
    """Import specific playlists by exact name from Music.app."""

    progress = Signal(int, int, str)
    finished = Signal(int)   # playlists imported
    error = Signal(str)

    def __init__(self, names: list[str], db_path: Path | None = None) -> None:
        super().__init__()
        self.names = names
        self.db_path = db_path or DB_PATH

    def run(self) -> None:
        try:
            conn = get_connection(self.db_path)
            settings = load_settings(conn)
            total_imported = 0

            for i, name in enumerate(self.names, 1):
                self.progress.emit(i, len(self.names), f"Importing '{name}'")
                tracks_by_pid, playlists, _ = read_playlists_applescript(name, settings=settings)

                if not playlists:
                    continue

                _upsert_tracks(conn, list(tracks_by_pid.values()), settings=settings)
                conn.commit()

                pid_to_id: dict[str, int] = {
                    r["music_app_id"]: r["id"]
                    for r in conn.execute("SELECT id, music_app_id FROM tracks").fetchall()
                }

                for pl in playlists:
                    track_ids = [pid_to_id[p] for p in pl.track_persistent_ids if p in pid_to_id]
                    if not track_ids:
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
                    total_imported += 1

            conn.commit()
            conn.close()
            self.finished.emit(total_imported)

        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Suggest worker: fuzzy match + co-occurrence query
# ---------------------------------------------------------------------------

class SuggestWorker(QThread):
    """Fuzzy-match a query and return co-occurrence suggestions."""

    results = Signal(object, list)   # seed MatchResult, list[Suggestion]
    no_match = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        query: str,
        same_genre: bool,
        limit: int,
        db_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.query = query
        self.same_genre = same_genre
        self.limit = limit
        self.db_path = db_path or DB_PATH

    def run(self) -> None:
        try:
            conn = get_connection(self.db_path)
            matches = fuzzy_match(conn, self.query, limit=5)
            if not matches:
                self.no_match.emit(f"No track found matching '{self.query}'")
                conn.close()
                return
            seed = matches[0]
            suggestions = suggest_for_track(
                conn, seed.track_id, seed.genre_family,
                same_genre=self.same_genre, limit=self.limit
            )
            conn.close()
            self.results.emit(seed, suggestions)

        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Suggest-by-ID worker: skip fuzzy match, go straight to co-occurrence
# ---------------------------------------------------------------------------

class SuggestByIdWorker(QThread):
    """Look up suggestions for a known track_id directly."""

    results = Signal(object, list)   # seed dict, list[Suggestion]
    error = Signal(str)

    def __init__(
        self,
        track_id: int,
        same_genre: bool,
        limit: int,
        db_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.track_id = track_id
        self.same_genre = same_genre
        self.limit = limit
        self.db_path = db_path or DB_PATH

    def run(self) -> None:
        try:
            conn = get_connection(self.db_path)
            row = conn.execute(
                "SELECT id, title, artist, genre, genre_family FROM tracks WHERE id = ?",
                (self.track_id,),
            ).fetchone()
            if not row:
                self.error.emit(f"Track id {self.track_id} not found")
                conn.close()
                return
            seed = {
                "track_id": row["id"],
                "title": row["title"],
                "artist": row["artist"],
                "genre": row["genre"],
                "genre_family": row["genre_family"],
            }
            suggestions = suggest_for_track(
                conn, self.track_id, row["genre_family"],
                same_genre=self.same_genre, limit=self.limit
            )
            conn.close()
            self.results.emit(seed, suggestions)

        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Suggest-by-music-app-id worker: direct lookup via persistent_id
# ---------------------------------------------------------------------------

class SuggestByMusicAppIdWorker(QThread):
    """Look up a track by Music.app persistent_id; emit not_in_library if absent."""

    results = Signal(object, list)          # seed dict, list[Suggestion]
    not_in_library = Signal(str, str, str)  # title, artist, genre
    error = Signal(str)

    def __init__(
        self,
        pid: str,
        title: str,
        artist: str,
        genre: str,
        same_genre: bool,
        limit: int,
        db_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.pid = pid
        self.title = title
        self.artist = artist
        self.genre = genre
        self.same_genre = same_genre
        self.limit = limit
        self.db_path = db_path or DB_PATH

    def run(self) -> None:
        try:
            conn = get_connection(self.db_path)
            row = conn.execute(
                "SELECT id, title, artist, genre, genre_family FROM tracks WHERE music_app_id = ?",
                (self.pid,),
            ).fetchone()
            if not row:
                conn.close()
                self.not_in_library.emit(self.title, self.artist, self.genre)
                return
            seed = {
                "track_id": row["id"],
                "title": row["title"],
                "artist": row["artist"],
                "genre": row["genre"],
                "genre_family": row["genre_family"],
            }
            suggestions = suggest_for_track(
                conn, row["id"], row["genre_family"],
                same_genre=self.same_genre, limit=self.limit,
            )
            conn.close()
            self.results.emit(seed, suggestions)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Noise diagnosis worker: find mixed-orchestra tandas for a track
# ---------------------------------------------------------------------------

class NoiseDiagnosisWorker(QThread):
    """Diagnose a noise track: find mixed-orchestra tandas and missing cortinas."""

    results = Signal(list)   # list[NoiseReport]
    error = Signal(str)

    def __init__(self, track_id: int, db_path: Path | None = None) -> None:
        super().__init__()
        self.track_id = track_id
        self.db_path = db_path or DB_PATH

    def run(self) -> None:
        try:
            from tanda_suggester.tandas import diagnose_noise_track
            conn = get_connection(self.db_path)
            reports = diagnose_noise_track(conn, self.track_id)
            conn.close()
            self.results.emit(reports)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Helpers (mirrors of private importer functions, avoiding rich output)
# ---------------------------------------------------------------------------

def _upsert_tracks(conn, tracks, settings=None) -> None:
    if settings is None:
        from tanda_suggester.settings import DEFAULT_SETTINGS
        settings = DEFAULT_SETTINGS
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
                "gf": classify_genre(t.genre, settings),
                "dur": t.duration_seconds,
            }
            for t in tracks
        ],
    )
