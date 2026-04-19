# TangoSuggest

A macOS tool for Argentine tango DJs that learns from your own playlists and suggests tracks based on your personal DJ history.

TangoSuggest syncs with your Music.app library, detects the traditional tanda structure of your playlists, builds a co-occurrence index of which tracks you play together, and uses that to suggest what fits next — based on your taste, not a generic database.

---

## Features

- **Learns from your playlists** — Suggestions are derived from your actual DJ history, not external databases
- **Tanda-aware** — Understands tanda structure (cortinas as separators, genre families: tango, vals, milonga, cortina)
- **Live mode** — Auto-refreshes suggestions as the currently playing track changes in Music.app
- **Genre filtering** — Suggests within the same genre family by default
- **Fuzzy search** — Find tracks even with partial or imprecise names
- **GUI and CLI** — Interactive app for live use; CLI for scripting and batch operations
- **Selective imports** — Choose which playlists to learn from using include/exclude controls
- **Selective rebuild** — Re-import and rebuild individual playlists from the Library tab without touching the rest of the database
- **Noise diagnosis** — Right-click any suggestion to identify mixed-orchestra tandas and missing cortinas that may be causing unexpected results
- **Configurable genre settings** — Define which genres count as tango/vals/milonga and how cortinas are detected, via the Settings tab

---

## Requirements

- macOS (uses Apple Music.app via AppleScript)
- Python 3.11+
- Music.app with tango playlists (tracks should have genre tags: `tango`, `vals`, `milonga`, or `cortina`)

---

## Installation

### Using uv (recommended)

```bash
git clone https://github.com/richardsladetdj-creator/TandaSuggest.git
cd TangoSuggest
uv sync
```

This installs two commands:

| Command | Description |
|---|---|
| `tanda-suggester` | CLI interface |
| `tanda-suggester-gui` | GUI application |

### From a built .app bundle

Download or build `TangoSuggest.app` (see [Building from source](#building-from-source)), then:

1. Copy `TangoSuggest.app` to `/Applications`
2. On first launch, right-click → **Open** to bypass Gatekeeper (app is unsigned)

---

## Quick start

### 1 — Import your library

**GUI:** Open the **Import & Stats** tab and click **Import All from Music.app**.

**CLI:**
```bash
# Import all tracks from Music.app, then import playlists from the XML library
uv run tanda-suggester import
```

### 2 — Choose which playlists to learn from

**GUI:** Open the **Playlists** tab, select playlists, and use the Include/Exclude buttons.

**CLI:**
```bash
# List all playlists
uv run tanda-suggester playlists

# Include playlists whose names match a glob
uv run tanda-suggester include-all-matching "Milonga *"

# Exclude a specific playlist
uv run tanda-suggester exclude "Practice"
```

### 3 — Rebuild the suggestion index

After changing which playlists are included, rebuild:

**GUI:** **Import & Stats** tab → **Rebuild Tandas & Co-occurrence**.

**CLI:**
```bash
uv run tanda-suggester rebuild
```

### 4 — Get suggestions

**GUI:** Open the **Suggest** tab, type a track name, or click **Use Current Track** to suggest based on what is playing now. Enable **Live Mode** to have suggestions update automatically as the track changes.

**CLI:**
```bash
# Suggest tracks similar to a given track name
uv run tanda-suggester suggest "La Cumparsita"

# Suggest based on the track currently playing in Music.app
uv run tanda-suggester suggest-current

# Show more results, ignore genre filtering
uv run tanda-suggester suggest "La Cumparsita" --limit 20 --any-genre
```

---

## GUI overview

### Suggest tab

The main working view during a milonga. Shows the seed track and a ranked list of suggestions with genre badges and co-occurrence scores. The currently playing track can be grabbed with one click.

- **Live Mode** — polls Music.app every 5 seconds; updates automatically when the track changes
- **Same genre only** — restricts suggestions to the same genre family (tango/vals/milonga)
- Double-click a suggestion to use it as the new seed
- Right-click a suggestion → **Diagnose noise…** to inspect why a track may be producing unexpected suggestions (see [Noise diagnosis](#noise-diagnosis))

### Playlists tab

Manage which playlists feed the suggestion index.

- Left panel: playlists already in the database, with include/exclude controls
- Right panel: playlists available in Music.app that haven't been imported yet
- **Refresh from Music.app** — checks for new playlists without re-importing everything

### Library tab

Browse all imported tracks. Filter by genre or search by title/artist. Double-clicking a track opens it in the Suggest tab. The "Appearances" column shows how many times a track has appeared across your tandas.

Selecting a track shows the playlists it belongs to in a side panel. Select one or more playlists in that panel and click **Re-import & Rebuild ↻** to pull fresh track data from Music.app and re-detect tandas for just those playlists, without touching the rest of the database.

### Import & Stats tab

Shows database statistics (track count, playlist count, tanda count, co-occurrence pairs) and provides import and rebuild controls with progress bars.

### Settings tab

Configure how genres and cortinas are classified. Changes take effect immediately and trigger a tanda rebuild.

- **Dance genres** — Define which genre strings count as tango, vals, milonga, etc. Each rule can be a substring match (default) or an exact match.
- **Cortina detection** — Set which genre names identify cortina tracks. Enable **catch-all** mode to treat any non-dance track as a cortina.

Settings are stored in the database and applied during both import and tanda detection, so after saving the index is automatically rebuilt to reflect the new rules.

---

## CLI reference

```
tanda-suggester import                         Import all tracks and playlists from Music.app
tanda-suggester import-playlist PATTERN        Import playlists matching a glob pattern
tanda-suggester playlists                      List all playlists with status
tanda-suggester include PLAYLIST [PLAYLIST…]   Mark playlist(s) as included
tanda-suggester include-all-matching PATTERN   Include all playlists matching a glob
tanda-suggester exclude PLAYLIST [PLAYLIST…]   Mark playlist(s) as excluded
tanda-suggester exclude-all-matching PATTERN   Exclude all playlists matching a glob
tanda-suggester rebuild                        Rebuild tanda detection and co-occurrence index
tanda-suggester suggest QUERY                  Suggest tracks matching QUERY
tanda-suggester suggest-current                Suggest based on currently playing track
tanda-suggester clear-all [--yes]              Delete all data from the database
```

---

## Noise diagnosis

If a track appears in suggestions unexpectedly, it may be because it was grouped with a different orchestra's tracks inside a tanda — for example, two orchestras were played back-to-back without a cortina between them, causing the tanda detector to merge them.

Right-click any suggestion in the Suggest tab and choose **Diagnose noise…** to run a scan. The report shows:

- Every mixed-orchestra tanda the track appears in, with the full track listing and playlist positions
- Markers indicating where a cortina is missing between orchestra groups

Once you've identified the problematic playlists, select them in the Library tab and use **Re-import & Rebuild ↻** to fix the index after you've corrected the playlist in Music.app.

---

## How it works

### Track import

Tracks are read from Music.app via AppleScript in batches. Only tracks whose genre matches the configured dance genres or cortina names are stored. The genre filter is built dynamically from your Settings — by default it matches `tango`, `vals`, `milonga`, and `cortina` as substrings. Enable catch-all cortina mode to import every track regardless of genre.

### Tanda detection

Playlists are parsed as ordered sequences. Cortina tracks act as separators — consecutive non-cortina tracks form a tanda. Each tanda is assigned a genre family based on the dominant genre of its tracks.

### Co-occurrence index

For every tanda, each pair of tracks in that tanda increments a co-occurrence counter. Tracks that appear together frequently across many tandas receive a higher score. When you ask for suggestions for a seed track, TangoSuggest returns the tracks with the highest co-occurrence scores for that seed.

This means the suggestions reflect the actual combinations you have chosen as a DJ — they improve as you add more playlists.

---

## Building from source

Install dev dependencies:

```bash
uv sync --group dev
```

Generate the app icon:

```bash
uv run python scripts/make_icon.py
```

Build the `.app` bundle:

```bash
uv run pyinstaller TangoSuggest.spec --noconfirm
# Output: dist/TangoSuggest.app
```

Build a distributable `.dmg` (requires `brew install create-dmg`):

```bash
bash scripts/build_macos.sh --dmg
# Output: dist/TangoSuggest.dmg
```

---

## Data storage

The database is stored at `~/.local/share/tanda-suggester/db.sqlite`. It contains only data derived from your Music.app library — no audio files are copied or modified.

To reset everything:

```bash
uv run tanda-suggester clear-all --yes
```

---

## Development

Run the tests:

```bash
uv run pytest
```

Lint:

```bash
uv run ruff check src tests
```

---

## License

MIT — see [LICENSE](LICENSE).
