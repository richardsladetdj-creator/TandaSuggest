"""Library tab: browse all imported tracks with search and filtering."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSortFilterProxyModel, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QAbstractItemView,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from tanda_suggester.db import get_connection
from tanda_suggester.gui.workers import SelectiveRebuildWorker

GENRES = ["All", "Tango", "Vals", "Milonga", "Cortina"]
COLS = ["Title", "Artist", "Genre", "Duration", "Appearances"]
_GENRE_FAMILY_MAP = {
    "All": None,
    "Tango": "tango",
    "Vals": "vals",
    "Milonga": "milonga",
    "Cortina": "cortina",
}


def _fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


class TrackTableModel(QAbstractTableModel):
    """Simple table model backed by a list of row dicts."""

    def __init__(self, rows: list[dict]) -> None:
        super().__init__()
        self._rows = rows

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLS)

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return row["title"]
            if col == 1:
                return row["artist"]
            if col == 2:
                return row["genre"]
            if col == 3:
                return _fmt_duration(row.get("duration_seconds"))
            if col == 4:
                return str(row.get("appearances", 0))
        if role == Qt.UserRole:
            return row["id"]
        if role == Qt.TextAlignmentRole and col in (3, 4):
            return int(Qt.AlignCenter)
        return None

    def track_id_at(self, source_row: int) -> int:
        return self._rows[source_row]["id"]


class LibraryTab(QWidget):
    track_selected = Signal(int)   # emits track_id
    rebuild_finished = Signal()

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path
        self._all_rows: list[dict] = []
        self._current_playlists: list[dict] = []   # id, name for selected track
        self._rebuild_worker: SelectiveRebuildWorker | None = None

        self._build_ui()
        self._connect()
        self.refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Controls row
        controls = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search title or artist…")
        self._search.setClearButtonEnabled(True)
        controls.addWidget(self._search)

        controls.addWidget(QLabel("Genre:"))
        self._genre_combo = QComboBox()
        self._genre_combo.addItems(GENRES)
        self._genre_combo.setFixedWidth(100)
        controls.addWidget(self._genre_combo)

        controls.addWidget(QLabel("Sort:"))
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["Appearances ↓", "Title A-Z", "Artist A-Z"])
        self._sort_combo.setFixedWidth(140)
        controls.addWidget(self._sort_combo)
        root.addLayout(controls)

        # Table
        self._model = TrackTableModel([])
        self._proxy = QSortFilterProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)  # search all columns

        self._view = QTableView()
        self._view.setModel(self._proxy)
        self._view.setEditTriggers(QTableView.NoEditTriggers)
        self._view.setSelectionBehavior(QTableView.SelectRows)
        self._view.setSelectionMode(QTableView.SingleSelection)
        self._view.setAlternatingRowColors(True)
        self._view.setSortingEnabled(True)
        self._view.horizontalHeader().setStretchLastSection(False)
        self._view.setColumnWidth(0, 280)
        self._view.setColumnWidth(1, 200)
        self._view.setColumnWidth(2, 120)
        self._view.setColumnWidth(3, 70)
        self._view.setColumnWidth(4, 90)

        # Right panel — playlists containing the selected track
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(4)

        playlists_header = QLabel("Playlists")
        playlists_header.setStyleSheet("color: #6a9fd8; font-weight: bold;")
        right_layout.addWidget(playlists_header)

        self._playlist_list = QListWidget()
        self._playlist_list.setEditTriggers(QListWidget.NoEditTriggers)
        self._playlist_list.setSelectionMode(QListWidget.ExtendedSelection)
        self._playlist_list.setAlternatingRowColors(True)
        right_layout.addWidget(self._playlist_list)

        self._rebuild_btn = QPushButton("Re-import & Rebuild ↻")
        self._rebuild_btn.setEnabled(False)
        right_layout.addWidget(self._rebuild_btn)

        self._rebuild_status = QLabel("")
        self._rebuild_status.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        self._rebuild_status.setWordWrap(True)
        right_layout.addWidget(self._rebuild_status)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._view)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 65)
        splitter.setStretchFactor(1, 35)
        root.addWidget(splitter, stretch=1)

        self._show_playlist_placeholder()

        self._count_label = QLabel("0 tracks")
        self._count_label.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        root.addWidget(self._count_label)

    def _connect(self) -> None:
        self._search.textChanged.connect(self._apply_filter)
        self._genre_combo.currentIndexChanged.connect(self._apply_filter)
        self._sort_combo.currentIndexChanged.connect(self._apply_sort)
        self._view.doubleClicked.connect(self._on_row_double_clicked)
        self._view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._playlist_list.itemSelectionChanged.connect(self._on_playlist_selection_changed)
        self._rebuild_btn.clicked.connect(self._start_selective_rebuild)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        conn = get_connection(self.db_path)
        rows = conn.execute(
            """SELECT t.id, t.title, t.artist, t.genre, t.genre_family, t.duration_seconds,
                      COUNT(tt.tanda_id) AS appearances
               FROM tracks t
               LEFT JOIN tanda_tracks tt ON tt.track_id = t.id
               GROUP BY t.id
               ORDER BY appearances DESC"""
        ).fetchall()
        conn.close()
        self._all_rows = [dict(r) for r in rows]
        self._apply_filter()

    # ------------------------------------------------------------------
    # Filtering & sorting
    # ------------------------------------------------------------------

    def _apply_filter(self) -> None:
        text = self._search.text().strip()
        genre_label = self._genre_combo.currentText()
        genre_family = _GENRE_FAMILY_MAP.get(genre_label)

        if genre_family:
            filtered = [r for r in self._all_rows if r.get("genre_family") == genre_family]
        else:
            filtered = list(self._all_rows)

        if text:
            tl = text.lower()
            filtered = [
                r for r in filtered
                if tl in r["title"].lower() or tl in r["artist"].lower()
            ]

        self._model = TrackTableModel(filtered)
        self._proxy.setSourceModel(self._model)
        self._apply_sort()
        self._count_label.setText(f"{len(filtered):,} tracks")

    def _apply_sort(self) -> None:
        sort_idx = self._sort_combo.currentIndex()
        if sort_idx == 0:   # Appearances ↓
            self._proxy.sort(4, Qt.DescendingOrder)
        elif sort_idx == 1: # Title A-Z
            self._proxy.sort(0, Qt.AscendingOrder)
        elif sort_idx == 2: # Artist A-Z
            self._proxy.sort(1, Qt.AscendingOrder)

    # ------------------------------------------------------------------
    # Row selection → Suggest tab
    # ------------------------------------------------------------------

    def _on_row_double_clicked(self, proxy_index: QModelIndex) -> None:
        source_index = self._proxy.mapToSource(proxy_index)
        track_id = self._model.track_id_at(source_index.row())
        self.track_selected.emit(track_id)

    def _on_selection_changed(self) -> None:
        indexes = self._view.selectionModel().selectedRows()
        if not indexes:
            self._show_playlist_placeholder()
            return
        source_index = self._proxy.mapToSource(indexes[0])
        track_id = self._model.track_id_at(source_index.row())
        self._load_playlists_for_track(track_id)

    def _load_playlists_for_track(self, track_id: int) -> None:
        conn = get_connection(self.db_path)
        rows = conn.execute(
            """SELECT p.id, p.name, p.included, p.excluded
               FROM playlist_tracks pt
               JOIN playlists p ON p.id = pt.playlist_id
               WHERE pt.track_id = ?
               ORDER BY p.name""",
            (track_id,),
        ).fetchall()
        conn.close()

        self._current_playlists = [dict(r) for r in rows]
        self._rebuild_status.setText("")
        self._rebuild_btn.setEnabled(False)
        self._playlist_list.clear()
        if rows:
            for row in rows:
                self._playlist_list.addItem(row["name"])
        else:
            item = QListWidgetItem("Not in any playlist")
            item.setForeground(QColor("#6a6a6a"))
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
            item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self._playlist_list.addItem(item)

    def _on_playlist_selection_changed(self) -> None:
        selected = self._playlist_list.selectedItems()
        if self._rebuild_worker is not None and self._rebuild_worker.isRunning():
            self._rebuild_btn.setEnabled(False)
            return
        self._rebuild_btn.setEnabled(bool(selected))

    def _start_selective_rebuild(self) -> None:
        if self._rebuild_worker is not None and self._rebuild_worker.isRunning():
            return

        selected_rows = [self._playlist_list.row(i) for i in self._playlist_list.selectedItems()]
        playlist_ids = [self._current_playlists[r]["id"] for r in selected_rows]
        if not playlist_ids:
            return

        self._rebuild_btn.setEnabled(False)
        self._rebuild_status.setText("Rebuilding…")

        self._rebuild_worker = SelectiveRebuildWorker(self.db_path, playlist_ids)
        self._rebuild_worker.progress.connect(self._on_rebuild_progress)
        self._rebuild_worker.finished.connect(self._on_rebuild_done)
        self._rebuild_worker.error.connect(self._on_rebuild_error)
        self._rebuild_worker.start()

    def _on_rebuild_progress(self, current: int, total: int, label: str) -> None:
        self._rebuild_status.setText(f"{label}  ({current}/{total})")

    def _on_rebuild_done(self, tanda_count: int, co_count: int) -> None:
        self._rebuild_status.setText(
            f"Done — {tanda_count:,} tandas, {co_count:,} pairs"
        )
        self._rebuild_btn.setEnabled(bool(self._playlist_list.selectedItems()))
        self.refresh()
        self.rebuild_finished.emit()

    def _on_rebuild_error(self, msg: str) -> None:
        self._rebuild_status.setText(f"Error: {msg}")
        self._rebuild_btn.setEnabled(bool(self._playlist_list.selectedItems()))

    def _show_playlist_placeholder(self) -> None:
        self._current_playlists = []
        self._rebuild_btn.setEnabled(False)
        self._rebuild_status.setText("")
        self._playlist_list.clear()
        item = QListWidgetItem("Select a track…")
        item.setForeground(QColor("#6a6a6a"))
        font = item.font()
        font.setItalic(True)
        item.setFont(font)
        item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
        self._playlist_list.addItem(item)

    def select_track_by_id(self, track_id: int) -> None:
        """Clear filters, select the given track, and show its playlists."""
        self._search.setText("")
        self._genre_combo.setCurrentIndex(0)

        for proxy_row in range(self._proxy.rowCount()):
            source_row = self._proxy.mapToSource(self._proxy.index(proxy_row, 0)).row()
            if self._model.track_id_at(source_row) == track_id:
                index = self._proxy.index(proxy_row, 0)
                self._view.setCurrentIndex(index)
                self._view.scrollTo(index, QAbstractItemView.PositionAtCenter)
                return
