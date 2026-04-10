"""Suggest tab: fuzzy track search + co-occurrence suggestions."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tanda_suggester.gui.workers import (
    LiveTrackWorker,
    SuggestByIdWorker,
    SuggestWorker,
)


SUGGESTION_COLS = ["Score", "Title", "Artist", "Genre"]
_GENRE_COLOUR = {
    "tango": "#e05252",
    "vals": "#5dade2",
    "milonga": "#52d98a",
    "cortina": "#95a5a6",
}
_SEED_ROW_COLOUR = "#1e4d2a"   # dark green background for seed track row


class SuggestTab(QWidget):
    show_in_library = Signal(int)   # emits track_id

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path
        self._live_worker: LiveTrackWorker | None = None
        self._suggest_worker: SuggestWorker | None = None
        self._by_id_worker: SuggestByIdWorker | None = None
        self._current_seed: dict | None = None   # {title, artist, genre, genre_family, track_id}

        self._build_ui()
        self._connect()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Top bar: search + current track button
        top = QHBoxLayout()
        from PySide6.QtWidgets import QLineEdit
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search track…  (type to search)")
        self._search.setClearButtonEnabled(True)
        top.addWidget(self._search)

        self._use_current_btn = QPushButton("▶  Use Current Track")
        self._use_current_btn.setFixedWidth(180)
        top.addWidget(self._use_current_btn)
        root.addLayout(top)

        # Live mode toggle
        self._live_chk = QCheckBox("Live Mode  (auto-refresh every 5 s when Music.app track changes)")
        root.addWidget(self._live_chk)

        # Seed label
        self._seed_label = QLabel("No track selected")
        self._seed_label.setStyleSheet("font-style: italic; color: #9a9a9a;")
        root.addWidget(self._seed_label)

        # Suggestions
        root.addWidget(QLabel("Suggestions"))

        self._table = QTableWidget(0, len(SUGGESTION_COLS))
        self._table.setHorizontalHeaderLabels(SUGGESTION_COLS)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 60)   # Score
        self._table.setColumnWidth(1, 260)  # Title
        self._table.setColumnWidth(2, 180)  # Artist
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        root.addWidget(self._table)

        opts = QHBoxLayout()
        self._same_genre_chk = QCheckBox("Same genre only")
        self._same_genre_chk.setChecked(True)
        opts.addWidget(self._same_genre_chk)
        opts.addStretch()
        opts.addWidget(QLabel("Limit:"))
        self._limit_spin = QSpinBox()
        self._limit_spin.setRange(5, 100)
        self._limit_spin.setValue(20)
        self._limit_spin.setFixedWidth(60)
        opts.addWidget(self._limit_spin)
        root.addLayout(opts)

        # Status label
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        root.addWidget(self._status_label)

        # Debounce timer
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)

    def _connect(self) -> None:
        self._search.textChanged.connect(self._debounce.start)
        self._debounce.timeout.connect(self._run_search)
        self._use_current_btn.clicked.connect(self._use_current_track)
        self._live_chk.toggled.connect(self._toggle_live_mode)
        self._same_genre_chk.toggled.connect(self._re_suggest)
        self._limit_spin.valueChanged.connect(self._re_suggest)
        self._table.doubleClicked.connect(
            lambda idx: self.set_seed_by_id(self._table.item(idx.row(), 0).data(Qt.UserRole))
        )
        self._table.customContextMenuRequested.connect(self._show_context_menu)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_seed_by_id(self, track_id: int) -> None:
        """Seed suggestions from a known track_id (called from Library tab)."""
        self._start_by_id_worker(track_id)

    def invalidate_cache(self) -> None:
        """Called after import/rebuild — re-run current suggest if a seed is active."""
        if self._current_seed:
            self._re_suggest()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _run_search(self) -> None:
        query = self._search.text().strip()
        if not query:
            return
        self._start_suggest(query)

    def _start_suggest(self, query: str) -> None:
        if self._suggest_worker and self._suggest_worker.isRunning():
            self._suggest_worker.results.disconnect()
            self._suggest_worker.no_match.disconnect()
            self._suggest_worker.error.disconnect()

        self._suggest_worker = SuggestWorker(
            query=query,
            same_genre=self._same_genre_chk.isChecked(),
            limit=self._limit_spin.value(),
            db_path=self.db_path,
        )
        self._suggest_worker.results.connect(self._on_suggest_results)
        self._suggest_worker.no_match.connect(self._on_no_match)
        self._suggest_worker.error.connect(self._on_error)
        self._suggest_worker.start()
        self._status_label.setText("Searching…")

    def _re_suggest(self) -> None:
        if self._current_seed:
            self._start_by_id_worker(self._current_seed["track_id"])

    def _start_by_id_worker(self, track_id: int) -> None:
        if self._by_id_worker and self._by_id_worker.isRunning():
            self._by_id_worker.results.disconnect()
            self._by_id_worker.error.disconnect()

        self._by_id_worker = SuggestByIdWorker(
            track_id=track_id,
            same_genre=self._same_genre_chk.isChecked(),
            limit=self._limit_spin.value(),
            db_path=self.db_path,
        )
        self._by_id_worker.results.connect(self._on_suggest_results)
        self._by_id_worker.error.connect(self._on_error)
        self._by_id_worker.start()

    # ------------------------------------------------------------------
    # Current track
    # ------------------------------------------------------------------

    def _use_current_track(self) -> None:
        from tanda_suggester.music_app import get_current_track
        try:
            track = get_current_track()
        except Exception as exc:
            self._status_label.setText(f"Music.app error: {exc}")
            return

        if not track:
            self._status_label.setText("Nothing is playing in Music.app")
            return

        self._search.blockSignals(True)
        self._search.setText(f"{track.title} - {track.artist}")
        self._search.blockSignals(False)
        self._start_suggest(f"{track.title} - {track.artist}")

    # ------------------------------------------------------------------
    # Live mode
    # ------------------------------------------------------------------

    def _toggle_live_mode(self, checked: bool) -> None:
        if checked:
            self._live_worker = LiveTrackWorker()
            self._live_worker.track_changed.connect(self._on_live_track_changed)
            self._live_worker.start()
            self._status_label.setText("Live mode active — watching Music.app…")
        else:
            if self._live_worker:
                self._live_worker.stop()
                self._live_worker.wait(2000)
                self._live_worker.deleteLater()
                self._live_worker = None
            self._status_label.setText("")

    def _on_live_track_changed(self, pid: str, title: str, artist: str, genre: str) -> None:
        query = f"{title} - {artist}"
        self._search.blockSignals(True)
        self._search.setText(query)
        self._search.blockSignals(False)
        self._start_suggest(query)

    # ------------------------------------------------------------------
    # Suggestion results
    # ------------------------------------------------------------------

    def _on_suggest_results(self, seed: object, suggestions: list) -> None:
        # seed may be a MatchResult (from SuggestWorker) or a dict (from SuggestByIdWorker)
        if hasattr(seed, "track_id"):
            self._current_seed = {
                "track_id": seed.track_id,
                "title": seed.title,
                "artist": seed.artist,
                "genre": seed.genre,
                "genre_family": seed.genre_family,
            }
        else:
            self._current_seed = seed

        self._seed_label.setText(
            f"Seed: {self._current_seed['title']}  —  {self._current_seed['artist']}"
            f"  [{self._current_seed.get('genre', '')}]"
        )

        self._table.setRowCount(0)

        # Insert seed track as top row, highlighted green
        seed = self._current_seed
        self._table.insertRow(0)
        seed_score_item = QTableWidgetItem("—")
        seed_score_item.setTextAlignment(Qt.AlignCenter)
        seed_score_item.setData(Qt.UserRole, seed["track_id"])
        seed_items = [
            seed_score_item,
            QTableWidgetItem(seed["title"]),
            QTableWidgetItem(seed["artist"]),
            QTableWidgetItem(seed.get("genre", "")),
        ]
        seed_bg = QBrush(QColor(_SEED_ROW_COLOUR))
        for col, item in enumerate(seed_items):
            item.setBackground(seed_bg)
            self._table.setItem(0, col, item)
        seed_genre_colour = _GENRE_COLOUR.get(seed.get("genre_family") or "", "")
        if seed_genre_colour:
            self._table.item(0, 3).setForeground(QColor(seed_genre_colour))

        for s in suggestions:
            row = self._table.rowCount()
            self._table.insertRow(row)
            score_item = QTableWidgetItem(str(s.score))
            score_item.setTextAlignment(Qt.AlignCenter)
            score_item.setData(Qt.UserRole, s.track_id)
            self._table.setItem(row, 0, score_item)
            self._table.setItem(row, 1, QTableWidgetItem(s.title))
            self._table.setItem(row, 2, QTableWidgetItem(s.artist))
            genre_item = QTableWidgetItem(s.genre)
            colour = _GENRE_COLOUR.get(s.genre_family or "", "")
            if colour:
                genre_item.setForeground(QColor(colour))
            self._table.setItem(row, 3, genre_item)

        count = len(suggestions)
        self._status_label.setText(
            f"{count} suggestion{'s' if count != 1 else ''}  "
            f"for  {self._current_seed['title']}"
        )

    def _on_no_match(self, msg: str) -> None:
        self._status_label.setText(msg)
        self._table.setRowCount(0)

    def _on_error(self, msg: str) -> None:
        self._status_label.setText(f"Error: {msg}")

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        reseed_action = menu.addAction("Re-seed from this track")
        show_playlists_action = menu.addAction("Show playlists")
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        track_id = self._table.item(row, 0).data(Qt.UserRole)
        if action == reseed_action:
            self.set_seed_by_id(track_id)
        elif action == show_playlists_action:
            self.show_in_library.emit(track_id)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Stop background threads. Must be called before the window closes."""
        if self._live_worker:
            self._live_worker.stop()
            self._live_worker.wait(2000)
            self._live_worker = None
        for worker in (self._suggest_worker, self._by_id_worker):
            if worker and worker.isRunning():
                worker.results.disconnect()
                worker.wait(2000)
