"""Music.app integration: AppleScript bridge and XML library parser."""

from __future__ import annotations

import fnmatch
import plistlib
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generator

XML_PATH = Path.home() / "Music" / "Music" / "iTunes" / "iTunes Music Library.xml"

RELEVANT_GENRES = frozenset(["tango", "vals", "milonga", "cortina"])


@dataclass
class RawTrack:
    persistent_id: str
    title: str
    artist: str
    genre: str
    duration_seconds: int


@dataclass
class RawPlaylist:
    persistent_id: str
    name: str
    track_persistent_ids: list[str]  # ordered


def _is_relevant(genre: str) -> bool:
    g = genre.lower()
    return any(rg in g for rg in RELEVANT_GENRES)


# ---------------------------------------------------------------------------
# AppleScript: live track reader
# ---------------------------------------------------------------------------

_BATCH_SCRIPT = """\
tell application "Music"
    set allTracks to tracks of library playlist 1
    set sep to "|~|"
    set recSep to ASCII character 30
    set output to ""
    repeat with i from {start} to {end}
        try
            set t to item i of allTracks
            set g to genre of t
            if (g contains "ango" or g contains "als" or g contains "ilonga" or g contains "ortina") then
                set rec to (persistent ID of t) & sep & (name of t) & sep & (artist of t) & sep & g & sep & ((duration of t as integer) as text)
                if output is "" then
                    set output to rec
                else
                    set output to output & recSep & rec
                end if
            end if
        end try
    end repeat
    return output
end tell
"""


def _run_applescript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def get_track_count() -> int:
    out = _run_applescript(
        'tell application "Music" to return count of tracks of library playlist 1'
    )
    return int(out.strip())


def read_tracks_applescript(
    batch_size: int = 500,
) -> Generator[list[RawTrack], None, None]:
    """Yield batches of RawTrack from Music.app via AppleScript.

    Only yields tracks whose genre contains tango/vals/milonga/cortina.
    Yields one list[RawTrack] per batch.
    """
    total = get_track_count()
    start = 1
    while start <= total:
        end = min(start + batch_size - 1, total)
        script = _BATCH_SCRIPT.format(start=start, end=end)
        raw = _run_applescript(script)
        batch = _parse_applescript_track_list(raw)
        yield batch
        start = end + 1


def _parse_applescript_track_list(raw: str) -> list[RawTrack]:
    """Parse structured AppleScript output into RawTrack objects.

    Format: records separated by ASCII 30, fields within each record
    separated by '|~|'. Fields: persistent_id, title, artist, genre, duration.
    """
    if not raw:
        return []

    tracks: list[RawTrack] = []
    for record in raw.split("\x1e"):  # ASCII 30 = record separator
        record = record.strip()
        if not record:
            continue
        parts = record.split("|~|")
        if len(parts) != 5:
            continue
        pid, title, artist, genre, dur_str = parts
        try:
            duration = int(dur_str)
        except ValueError:
            continue
        if _is_relevant(genre):
            tracks.append(
                RawTrack(
                    persistent_id=pid,
                    title=title,
                    artist=artist,
                    genre=genre,
                    duration_seconds=duration,
                )
            )
    return tracks


# ---------------------------------------------------------------------------
# XML library: playlist structure
# ---------------------------------------------------------------------------


def parse_xml_library_for_playlists(
    name_filter: str | None = None,
    xml_path: Path = XML_PATH,
) -> tuple[dict[str, RawTrack], list[RawPlaylist], str]:
    """Parse the XML library, optionally filtering by playlist name (shell glob).

    Returns (tracks_by_pid, playlists, library_date_str).
    tracks_by_pid contains only tracks referenced by the returned playlists.
    If name_filter is None, all playlists are returned.
    """
    with open(xml_path, "rb") as f:
        lib = plistlib.load(f)

    date = str(lib.get("Date", "unknown"))

    # Build Track ID → raw track dict from XML
    xml_tracks: dict[int, dict] = {
        int(tid): t
        for tid, t in lib.get("Tracks", {}).items()
        if "Persistent ID" in t
    }
    tracks_by_id: dict[int, str] = {tid: t["Persistent ID"] for tid, t in xml_tracks.items()}

    _system_names = {
        "Library", "Music", "Podcasts", "TV Shows", "Audiobooks",
        "Voice Memos", "Downloaded", "iTunes U", "Genius",
    }

    matched_playlists: list[RawPlaylist] = []
    needed_pids: set[str] = set()

    for p in lib.get("Playlists", []):
        name = p.get("Name", "")
        if name in _system_names or p.get("Master") or p.get("Distinguished Kind"):
            continue
        pid = p.get("Playlist Persistent ID", "")
        if not pid:
            continue
        items = p.get("Playlist Items", [])
        if not items:
            continue

        if name_filter is not None:
            if not (
                name == name_filter
                or name.lower() == name_filter.lower()
                or fnmatch.fnmatch(name, name_filter)
                or fnmatch.fnmatch(name.lower(), name_filter.lower())
            ):
                continue

        track_pids: list[str] = []
        for item in items:
            track_id = item.get("Track ID")
            if track_id and track_id in tracks_by_id:
                track_pids.append(tracks_by_id[track_id])

        if track_pids:
            matched_playlists.append(RawPlaylist(persistent_id=pid, name=name, track_persistent_ids=track_pids))
            needed_pids.update(track_pids)

    # Build RawTrack objects only for referenced tracks with relevant genres
    pid_to_raw: dict[str, dict] = {t["Persistent ID"]: t for t in xml_tracks.values()}
    tracks_by_pid: dict[str, RawTrack] = {}
    for pid in needed_pids:
        t = pid_to_raw.get(pid)
        if t is None:
            continue
        genre = t.get("Genre", "")
        if not _is_relevant(genre):
            continue
        tracks_by_pid[pid] = RawTrack(
            persistent_id=pid,
            title=t.get("Name", ""),
            artist=t.get("Artist", ""),
            genre=genre,
            duration_seconds=int(t.get("Total Time", 0)) // 1000,
        )

    return tracks_by_pid, matched_playlists, date


def parse_xml_library(xml_path: Path = XML_PATH) -> tuple[list[RawPlaylist], str]:
    """Parse the Music.app XML export for playlist structure.

    Returns (playlists, library_date_str).
    Playlists reference tracks via Persistent ID (cross-referenced from the
    XML's track list).
    """
    with open(xml_path, "rb") as f:
        lib = plistlib.load(f)

    date = str(lib.get("Date", "unknown"))

    # Build Track ID → Persistent ID map
    tracks_by_id: dict[int, str] = {
        int(tid): t["Persistent ID"]
        for tid, t in lib.get("Tracks", {}).items()
        if "Persistent ID" in t
    }

    _system_names = {
        "Library", "Music", "Podcasts", "TV Shows", "Audiobooks",
        "Voice Memos", "Downloaded", "iTunes U", "Genius",
    }

    playlists: list[RawPlaylist] = []
    for p in lib.get("Playlists", []):
        name = p.get("Name", "")
        if name in _system_names:
            continue
        if p.get("Master"):
            continue
        if p.get("Distinguished Kind"):
            continue
        pid = p.get("Playlist Persistent ID", "")
        if not pid:
            continue
        items = p.get("Playlist Items", [])
        if not items:
            continue

        track_pids: list[str] = []
        for item in items:
            track_id = item.get("Track ID")
            if track_id and track_id in tracks_by_id:
                track_pids.append(tracks_by_id[track_id])

        if track_pids:
            playlists.append(
                RawPlaylist(
                    persistent_id=pid,
                    name=name,
                    track_persistent_ids=track_pids,
                )
            )

    return playlists, date


# ---------------------------------------------------------------------------
# AppleScript: live playlist reader
# ---------------------------------------------------------------------------

_SYSTEM_NAMES = frozenset({
    "Library", "Music", "Podcasts", "TV Shows", "Audiobooks",
    "Voice Memos", "Downloaded", "iTunes U", "Genius",
})

_ALL_PLAYLISTS_SCRIPT = """\
tell application "Music"
    set fieldSep to "|~|"
    set trackSep to ASCII character 30
    set plSep to ASCII character 29
    set output to ""
    repeat with pl in every user playlist
        set plPID to persistent ID of pl
        set plName to name of pl
        set pids to ""
        repeat with t in tracks of pl
            if pids is not "" then
                set pids to pids & trackSep
            end if
            set pids to pids & (persistent ID of t)
        end repeat
        if pids is not "" then
            if output is not "" then
                set output to output & plSep
            end if
            set output to output & plPID & fieldSep & plName & fieldSep & pids
        end if
    end repeat
    return output
end tell
"""


_PLAYLIST_SUMMARIES_SCRIPT = """\
tell application "Music"
    set sep to "|~|"
    set recSep to ASCII character 30
    set output to ""
    repeat with pl in every user playlist
        set plPID to persistent ID of pl
        set plName to name of pl
        set tc to count of tracks of pl
        if tc > 0 then
            if output is not "" then
                set output to output & recSep
            end if
            set output to output & plPID & sep & plName & sep & (tc as text)
        end if
    end repeat
    return output
end tell
"""


def read_playlist_summaries_applescript() -> list[tuple[str, str, int]]:
    """Return lightweight playlist summaries (persistent_id, name, track_count) from Music.app.

    Much faster than read_all_playlists_applescript() — uses `count of tracks`
    instead of iterating track IDs, so safe with large libraries (hundreds of playlists).
    """
    raw = _run_applescript(_PLAYLIST_SUMMARIES_SCRIPT)
    summaries: list[tuple[str, str, int]] = []
    if not raw:
        return summaries
    for rec in raw.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split("|~|", 2)
        if len(parts) != 3:
            continue
        pid, name, count_str = parts
        if name in _SYSTEM_NAMES:
            continue
        try:
            summaries.append((pid, name, int(count_str)))
        except ValueError:
            continue
    return summaries


def read_all_playlists_applescript() -> tuple[list[RawPlaylist], str]:
    """Return all user playlists (with track persistent IDs) direct from Music.app.

    Replaces parse_xml_library(). Returns (playlists, date_str).
    """
    raw = _run_applescript(_ALL_PLAYLISTS_SCRIPT)
    playlists: list[RawPlaylist] = []
    if not raw:
        return playlists, datetime.now().isoformat()

    for block in raw.split("\x1d"):
        block = block.strip()
        if not block:
            continue
        parts = block.split("|~|", 2)
        if len(parts) != 3:
            continue
        pid, name, pids_raw = parts
        if name in _SYSTEM_NAMES:
            continue
        track_pids = [p for p in pids_raw.split("\x1e") if p.strip()]
        if track_pids:
            playlists.append(RawPlaylist(persistent_id=pid, name=name, track_persistent_ids=track_pids))

    return playlists, datetime.now().isoformat()


def read_playlists_applescript(
    name_filter: str | None = None,
) -> tuple[dict[str, RawTrack], list[RawPlaylist], str]:
    """Return playlists (optionally filtered by name) with full track details from Music.app.

    Replaces parse_xml_library_for_playlists(). Returns (tracks_by_pid, playlists, date_str).

    Uses a two-phase approach: first fetches all playlist names cheaply, filters in Python,
    then fetches full track details only for matched playlists.
    """
    # Phase 1: get all playlist names + PIDs (light call)
    name_script = """\
tell application "Music"
    set sep to "|~|"
    set recSep to ASCII character 30
    set output to ""
    repeat with pl in every user playlist
        set plPID to persistent ID of pl
        set plName to name of pl
        if output is not "" then
            set output to output & recSep
        end if
        set output to output & plPID & sep & plName
    end repeat
    return output
end tell
"""
    raw_names = _run_applescript(name_script)
    all_pls: list[tuple[str, str]] = []  # (pid, name)
    for rec in raw_names.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split("|~|", 1)
        if len(parts) == 2:
            all_pls.append((parts[0], parts[1]))

    # Phase 2: filter by name_filter in Python
    if name_filter is not None:
        matched_pls = [
            (pid, name) for pid, name in all_pls
            if (
                name == name_filter
                or name.lower() == name_filter.lower()
                or fnmatch.fnmatch(name, name_filter)
                or fnmatch.fnmatch(name.lower(), name_filter.lower())
            )
        ]
    else:
        matched_pls = [(pid, name) for pid, name in all_pls if name not in _SYSTEM_NAMES]

    # Phase 3: for each matched playlist, fetch full track details
    tracks_by_pid: dict[str, RawTrack] = {}
    playlists: list[RawPlaylist] = []

    for pl_pid, pl_name in matched_pls:
        safe_name = pl_name.replace("\\", "\\\\").replace('"', '\\"')
        detail_script = f"""\
tell application "Music"
    set fieldSep to "|~|"
    set trackSep to ASCII character 30
    set output to ""
    try
        set pl to first user playlist whose name is "{safe_name}"
        repeat with t in tracks of pl
            set tPID to persistent ID of t
            set tTitle to name of t
            set tArtist to artist of t
            set tGenre to genre of t
            try
                set tDur to (duration of t as integer) as text
            on error
                set tDur to "0"
            end try
            if output is not "" then
                set output to output & trackSep
            end if
            set output to output & tPID & fieldSep & tTitle & fieldSep & tArtist & fieldSep & tGenre & fieldSep & tDur
        end repeat
    end try
    return output
end tell
"""
        raw_tracks = _run_applescript(detail_script)
        track_pids: list[str] = []
        for rec in raw_tracks.split("\x1e"):
            rec = rec.strip()
            if not rec:
                continue
            parts = rec.split("|~|")
            if len(parts) != 5:
                continue
            pid, title, artist, genre, dur_str = parts
            track_pids.append(pid)
            if _is_relevant(genre) and pid not in tracks_by_pid:
                try:
                    dur = int(dur_str)
                except ValueError:
                    dur = 0
                tracks_by_pid[pid] = RawTrack(
                    persistent_id=pid,
                    title=title,
                    artist=artist,
                    genre=genre,
                    duration_seconds=dur,
                )
        if track_pids:
            playlists.append(RawPlaylist(persistent_id=pl_pid, name=pl_name, track_persistent_ids=track_pids))

    return tracks_by_pid, playlists, datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Currently playing track
# ---------------------------------------------------------------------------


def get_current_track() -> RawTrack | None:
    """Return the currently playing track, or None if nothing is playing."""
    script = """\
tell application "Music"
    if player state is playing then
        set t to current track
        set sep to "|~|"
        return (persistent ID of t) & sep & (name of t) & sep & (artist of t) & sep & (genre of t) & sep & ((duration of t as integer) as text)
    else
        return ""
    end if
end tell
"""
    try:
        raw = _run_applescript(script)
    except RuntimeError:
        return None

    if not raw:
        return None

    tracks = _parse_applescript_track_list(raw)
    return tracks[0] if tracks else None
