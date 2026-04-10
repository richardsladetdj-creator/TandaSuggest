"""Playlists tab: manage included/excluded playlists and import new ones."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tanda_suggester.db import get_connection
from tanda_suggester.gui.workers import (
    ImportNamedPlaylistsWorker,
    RebuildWorker,
    RefreshPlaylistsWorker,
)

_STATUS_LABELS = {(True, False): "Included", (False, True): "Excluded", (False, False): "—"}

_STATUS_COLOURS = {
    "Included": "#52d98a",
    "Excluded": "#e05252",
    "—": "#aaa",
}

DB_COLS = ["Name", "Tracks", "Status"]
MUSIC_APP_COLS = ["Name", "Tracks", "Status"]


class PlaylistsTab(QWidget):
    rebuild_finished = Signal()
    status_message = Signal(str)

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path
        self._unimported: list[tuple[str, int, str, bool]] = []   # (name, count, pid, is_imported)
        self._import_worker: ImportNamedPlaylistsWorker | None = None
        self._rebuild_worker: RebuildWorker | None = None
        self._refresh_worker: RefreshPlaylistsWorker | None = None
        self._pending_refresh: bool = False

        self._build_ui()
        self._connect()
        self.refresh_db_panel()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Top bar
        top = QHBoxLayout()
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter playlists…")
        self._filter_edit.setClearButtonEnabled(True)
        top.addWidget(self._filter_edit)
        top.addStretch()
        self._refresh_btn = QPushButton("Refresh from Music.app")
        top.addWidget(self._refresh_btn)
        root.addLayout(top)

        # Splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # --- Left: in DB ---
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        self._db_label = QLabel("In Database (0)")
        ll.addWidget(self._db_label)

        self._db_table = QTableWidget(0, len(DB_COLS))
        self._db_table.setHorizontalHeaderLabels(DB_COLS)
        self._db_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._db_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._db_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._db_table.setAlternatingRowColors(True)
        self._db_table.horizontalHeader().setStretchLastSection(False)
        self._db_table.setColumnWidth(0, 300)
        self._db_table.setColumnWidth(1, 60)
        self._db_table.setColumnWidth(2, 80)
        self._db_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        ll.addWidget(self._db_table)
        splitter.addWidget(left)

        # --- Right: all Music.app playlists ---
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        self._unimported_label = QLabel("Music.app Playlists (—)")
        rl.addWidget(self._unimported_label)

        self._unimported_table = QTableWidget(0, len(MUSIC_APP_COLS))
        self._unimported_table.setHorizontalHeaderLabels(MUSIC_APP_COLS)
        self._unimported_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._unimported_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._unimported_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._unimported_table.setAlternatingRowColors(True)
        self._unimported_table.horizontalHeader().setStretchLastSection(False)
        self._unimported_table.setColumnWidth(0, 220)
        self._unimported_table.setColumnWidth(1, 55)
        self._unimported_table.setColumnWidth(2, 60)
        self._unimported_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        rl.addWidget(self._unimported_table)

        self._import_selected_btn = QPushButton("Import Selected")
        rl.addWidget(self._import_selected_btn)
        splitter.addWidget(right)
        splitter.setSizes([600, 600])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # Action buttons row
        actions = QHBoxLayout()
        self._include_btn = QPushButton("Include Selected")
        self._exclude_btn = QPushButton("Exclude Selected")
        self._clear_status_btn = QPushButton("Clear Status")
        actions.addWidget(self._include_btn)
        actions.addWidget(self._exclude_btn)
        actions.addWidget(self._clear_status_btn)
        actions.addStretch()
        root.addLayout(actions)

        # Progress bar + rebuild
        prog_row = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m  %p%")
        self._progress.hide()
        prog_row.addWidget(self._progress)
        self._rebuild_btn = QPushButton("Rebuild Index ↻")
        self._rebuild_btn.setFixedWidth(160)
        prog_row.addWidget(self._rebuild_btn)
        root.addLayout(prog_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        root.addWidget(self._status_lbl)

    def _connect(self) -> None:
        self._filter_edit.textChanged.connect(self._apply_filter)
        self._refresh_btn.clicked.connect(self._start_refresh)
        self._include_btn.clicked.connect(lambda: self._set_status(included=True, excluded=False))
        self._exclude_btn.clicked.connect(lambda: self._set_status(included=False, excluded=True))
        self._clear_status_btn.clicked.connect(lambda: self._set_status(included=False, excluded=False))
        self._import_selected_btn.clicked.connect(self._import_selected)
        self._rebuild_btn.clicked.connect(self._start_rebuild)

    # ------------------------------------------------------------------
    # DB panel
    # ------------------------------------------------------------------

    def refresh_db_panel(self) -> None:
        conn = get_connection(self.db_path)
        rows = conn.execute(
            "SELECT id, name, track_count, included, excluded FROM playlists ORDER BY name"
        ).fetchall()
        conn.close()
        self._db_rows = [dict(r) for r in rows]
        self._apply_filter()

    def refresh(self) -> None:
        self.refresh_db_panel()

    def _apply_filter(self) -> None:
        query = self._filter_edit.text().strip().lower()
        filtered = [r for r in self._db_rows if query in r["name"].lower()] if query else self._db_rows
        self._populate_db_table(filtered)

    def _populate_db_table(self, rows: list[dict]) -> None:
        self._db_table.setRowCount(0)
        for r in rows:
            included = bool(r["included"])
            excluded = bool(r["excluded"])
            status = _STATUS_LABELS.get((included, excluded), "—")

            row = self._db_table.rowCount()
            self._db_table.insertRow(row)

            name_item = QTableWidgetItem(r["name"])
            name_item.setData(Qt.UserRole, r["id"])
            self._db_table.setItem(row, 0, name_item)

            count_item = QTableWidgetItem(str(r["track_count"]))
            count_item.setTextAlignment(Qt.AlignCenter)
            self._db_table.setItem(row, 1, count_item)

            status_item = QTableWidgetItem(status)
            status_item.setTextAlignment(Qt.AlignCenter)
            from PySide6.QtGui import QColor
            status_item.setForeground(QColor(_STATUS_COLOURS.get(status, "#000")))
            self._db_table.setItem(row, 2, status_item)

        self._db_label.setText(f"In Database ({len(rows)})")

    def _selected_db_ids(self) -> list[int]:
        rows = {idx.row() for idx in self._db_table.selectedIndexes()}
        return [self._db_table.item(r, 0).data(Qt.UserRole) for r in sorted(rows)]

    def _set_status(self, included: bool, excluded: bool) -> None:
        ids = self._selected_db_ids()
        if not ids:
            return
        conn = get_connection(self.db_path)
        conn.executemany(
            "UPDATE playlists SET included = ?, excluded = ? WHERE id = ?",
            [(int(included), int(excluded), pid) for pid in ids],
        )
        conn.commit()
        conn.close()
        self.refresh_db_panel()

    # ------------------------------------------------------------------
    # Refresh unimported from Music.app
    # ------------------------------------------------------------------

    def _start_refresh(self) -> None:
        if self._refresh_worker is not None and self._refresh_worker.isRunning():
            self._pending_refresh = True   # re-run after current one finishes
            return
        self._pending_refresh = False
        self._refresh_btn.setEnabled(False)
        self._unimported_label.setText("Music.app Playlists (fetching…)")
        self._refresh_worker = RefreshPlaylistsWorker(self.db_path, only_unimported=False)
        self._refresh_worker.finished.connect(self._on_refresh_done)
        self._refresh_worker.error.connect(self._on_refresh_error)
        self._refresh_worker.start()

    def _on_refresh_done(self, playlists: list) -> None:
        self._unimported = playlists
        self._populate_unimported_table(playlists)
        self._refresh_btn.setEnabled(True)
        if self._pending_refresh:
            self._start_refresh()

    def _on_refresh_error(self, msg: str) -> None:
        self._unimported_label.setText("Music.app Playlists (error)")
        self._status_lbl.setText(f"Refresh error: {msg}")
        self._refresh_btn.setEnabled(True)
        if self._pending_refresh:
            self._start_refresh()

    def _populate_unimported_table(self, rows: list[tuple]) -> None:
        from PySide6.QtGui import QColor
        self._unimported_table.setRowCount(0)
        for name, count, pid, is_imported in rows:
            row = self._unimported_table.rowCount()
            self._unimported_table.insertRow(row)

            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, name)  # store name for import
            self._unimported_table.setItem(row, 0, name_item)

            count_item = QTableWidgetItem(str(count))
            count_item.setTextAlignment(Qt.AlignCenter)
            self._unimported_table.setItem(row, 1, count_item)

            if is_imported:
                status_item = QTableWidgetItem("in DB")
                status_item.setForeground(QColor("#52d98a"))
            else:
                status_item = QTableWidgetItem("—")
                status_item.setForeground(QColor("#666"))
            status_item.setTextAlignment(Qt.AlignCenter)
            self._unimported_table.setItem(row, 2, status_item)

        self._unimported_label.setText(f"Music.app Playlists ({len(rows)})")

    # ------------------------------------------------------------------
    # Import selected unimported playlists
    # ------------------------------------------------------------------

    def _import_selected(self) -> None:
        rows = {idx.row() for idx in self._unimported_table.selectedIndexes()}
        names = [self._unimported_table.item(r, 0).data(Qt.UserRole) for r in sorted(rows)]
        if not names:
            return

        self._import_selected_btn.setEnabled(False)
        self._progress.setMaximum(len(names))
        self._progress.setValue(0)
        self._progress.show()

        self._import_worker = ImportNamedPlaylistsWorker(names, self.db_path)
        self._import_worker.progress.connect(self._on_import_progress)
        self._import_worker.finished.connect(self._on_import_done)
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def _on_import_progress(self, current: int, total: int, label: str) -> None:
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._status_lbl.setText(label)

    def _on_import_done(self, count: int) -> None:
        self._progress.hide()
        self._import_selected_btn.setEnabled(True)
        self._status_lbl.setText(f"Imported {count} playlist(s)")
        self.status_message.emit(f"Imported {count} playlist(s) from Music.app")
        self.refresh_db_panel()
        # Remove imported from unimported list
        self._start_refresh()

    def _on_import_error(self, msg: str) -> None:
        self._progress.hide()
        self._import_selected_btn.setEnabled(True)
        self._status_lbl.setText(f"Import error: {msg}")

    # ------------------------------------------------------------------
    # Rebuild
    # ------------------------------------------------------------------

    def _start_rebuild(self) -> None:
        included = sum(1 for r in self._db_rows if r["included"] and not r["excluded"])
        if included == 0:
            QMessageBox.warning(
                self, "No Playlists Included",
                "No playlists are marked as Included.\n"
                "Select playlists and click 'Include Selected' first."
            )
            return

        if self._rebuild_worker is not None and self._rebuild_worker.isRunning():
            return

        self._rebuild_btn.setEnabled(False)
        self._progress.setMaximum(included)
        self._progress.setValue(0)
        self._progress.show()
        self._status_lbl.setText("Rebuilding…")

        self._rebuild_worker = RebuildWorker(self.db_path)
        self._rebuild_worker.progress.connect(self._on_rebuild_progress)
        self._rebuild_worker.finished.connect(self._on_rebuild_done)
        self._rebuild_worker.error.connect(self._on_rebuild_error)
        self._rebuild_worker.start()

    def _on_rebuild_progress(self, current: int, total: int, label: str) -> None:
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._status_lbl.setText(f"{label}  ({current}/{total})")

    def _on_rebuild_done(self, tanda_count: int, co_count: int) -> None:
        self._progress.hide()
        self._rebuild_btn.setEnabled(True)
        self._status_lbl.setText(
            f"Rebuild complete — {tanda_count:,} tandas, {co_count:,} co-occurrence pairs"
        )
        self.rebuild_finished.emit()

    def _on_rebuild_error(self, msg: str) -> None:
        self._progress.hide()
        self._rebuild_btn.setEnabled(True)
        self._status_lbl.setText(f"Rebuild error: {msg}")
