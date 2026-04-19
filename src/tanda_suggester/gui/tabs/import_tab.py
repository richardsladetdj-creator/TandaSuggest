"""Import & Stats tab: run imports, rebuild index, view database statistics."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tanda_suggester.db import get_connection
from tanda_suggester.gui.workers import (
    ImportWorker,
    RebuildWorker,
)


class ImportTab(QWidget):
    data_changed = Signal()         # emitted after a successful import
    rebuild_done = Signal()         # emitted after a successful rebuild
    status_message = Signal(str)    # for the main window status bar

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path
        self._import_worker: ImportWorker | None = None
        self._rebuild_worker: RebuildWorker | None = None

        self._build_ui()
        self._connect()
        self.refresh_stats()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ── Stats ──
        stats_box = QGroupBox("Database Statistics")
        stats_layout = QHBoxLayout(stats_box)
        stats_layout.setSpacing(32)

        def _stat_widget(title: str) -> tuple[QLabel, QWidget]:
            col = QWidget()
            cl = QVBoxLayout(col)
            cl.setSpacing(2)
            val = QLabel("—")
            val.setStyleSheet("font-size: 22px; font-weight: bold;")
            lbl = QLabel(title)
            lbl.setStyleSheet("color: #9a9a9a; font-size: 11px;")
            cl.addWidget(val)
            cl.addWidget(lbl)
            return val, col

        self._tracks_val, tw = _stat_widget("Tracks")
        self._playlists_val, pw = _stat_widget("Playlists")
        self._tandas_val, tdw = _stat_widget("Tandas")
        self._co_val, cw = _stat_widget("Co-occurrence pairs")

        for w in (tw, pw, tdw, cw):
            stats_layout.addWidget(w)
        stats_layout.addStretch()
        root.addWidget(stats_box)

        # DB path
        self._db_path_lbl = QLabel(f"DB: {self.db_path}")
        self._db_path_lbl.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        root.addWidget(self._db_path_lbl)

        # ── Import ──
        import_box = QGroupBox("Import from Music.app")
        import_layout = QVBoxLayout(import_box)
        import_layout.setSpacing(8)

        import_desc = QLabel(
            "Reads all tracks and playlists from Music.app via AppleScript.\n"
            "Existing data is updated; inclusion status is preserved."
        )
        import_desc.setWordWrap(True)
        import_desc.setStyleSheet("color: #9a9a9a;")
        import_layout.addWidget(import_desc)

        self._import_btn = QPushButton("Import All from Music.app")
        self._import_btn.setFixedWidth(260)
        import_layout.addWidget(self._import_btn)

        self._import_progress = QProgressBar()
        self._import_progress.setTextVisible(True)
        self._import_progress.setFormat("%v / %m  ·  %p%")
        self._import_progress.hide()
        import_layout.addWidget(self._import_progress)

        self._import_status = QLabel("")
        self._import_status.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        import_layout.addWidget(self._import_status)
        root.addWidget(import_box)

        # ── Rebuild ──
        rebuild_box = QGroupBox("Rebuild Suggestion Index")
        rebuild_layout = QVBoxLayout(rebuild_box)
        rebuild_layout.setSpacing(8)

        rebuild_desc = QLabel(
            "Re-detects tandas in all included playlists and rebuilds the co-occurrence index.\n"
            "Run this after changing which playlists are included."
        )
        rebuild_desc.setWordWrap(True)
        rebuild_desc.setStyleSheet("color: #9a9a9a;")
        rebuild_layout.addWidget(rebuild_desc)

        self._rebuild_btn = QPushButton("Rebuild Suggestion Index")
        self._rebuild_btn.setFixedWidth(260)
        rebuild_layout.addWidget(self._rebuild_btn)

        self._rebuild_progress = QProgressBar()
        self._rebuild_progress.setTextVisible(True)
        self._rebuild_progress.setFormat("%v / %m  ·  %p%")
        self._rebuild_progress.hide()
        rebuild_layout.addWidget(self._rebuild_progress)

        self._rebuild_status = QLabel("")
        self._rebuild_status.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        rebuild_layout.addWidget(self._rebuild_status)
        root.addWidget(rebuild_box)

        root.addStretch()

    def _connect(self) -> None:
        self._import_btn.clicked.connect(self._start_import)
        self._rebuild_btn.clicked.connect(self._start_rebuild)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def refresh_stats(self) -> None:
        try:
            conn = get_connection(self.db_path)
            tracks = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            playlists = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
            tandas = conn.execute("SELECT COUNT(*) FROM tandas").fetchone()[0]
            co = conn.execute("SELECT COUNT(*) FROM co_occurrence").fetchone()[0]
            conn.close()
        except Exception:
            return

        self._tracks_val.setText(f"{tracks:,}")
        self._playlists_val.setText(f"{playlists:,}")
        self._tandas_val.setText(f"{tandas:,}")
        self._co_val.setText(f"{co:,}")

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _start_import(self) -> None:
        self._import_btn.setEnabled(False)
        self._import_progress.setValue(0)
        self._import_progress.show()
        self._import_status.setText("Starting…")

        self._import_worker = ImportWorker(self.db_path)
        self._import_worker.progress.connect(self._on_import_progress)
        self._import_worker.finished.connect(self._on_import_done)
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def _on_import_progress(self, current: int, total: int, phase: str) -> None:
        self._import_progress.setMaximum(total)
        self._import_progress.setValue(current)
        self._import_status.setText(f"{phase}  ({current}/{total})")

    def _on_import_done(self, tracks: int, playlists: int) -> None:
        self._import_progress.hide()
        self._import_btn.setEnabled(True)
        self._import_status.setText(
            f"Done — {tracks:,} tracks, {playlists:,} playlists imported"
        )
        self.status_message.emit(f"Import complete: {tracks:,} tracks, {playlists:,} playlists")
        self.refresh_stats()
        self.data_changed.emit()

    def _on_import_error(self, msg: str) -> None:
        self._import_progress.hide()
        self._import_btn.setEnabled(True)
        self._import_status.setText(f"Error: {msg}")
        self.status_message.emit(f"Import failed: {msg}")

    # ------------------------------------------------------------------
    # Rebuild
    # ------------------------------------------------------------------

    def start_rebuild(self) -> None:
        """Public entry point to trigger a tanda rebuild (e.g. from the Settings tab)."""
        self._start_rebuild()

    def _start_rebuild(self) -> None:
        if self._rebuild_worker is not None and self._rebuild_worker.isRunning():
            return
        self._rebuild_btn.setEnabled(False)
        self._rebuild_progress.setValue(0)
        self._rebuild_progress.show()
        self._rebuild_status.setText("Starting…")

        self._rebuild_worker = RebuildWorker(self.db_path)
        self._rebuild_worker.progress.connect(self._on_rebuild_progress)
        self._rebuild_worker.finished.connect(self._on_rebuild_done)
        self._rebuild_worker.error.connect(self._on_rebuild_error)
        self._rebuild_worker.start()

    def _on_rebuild_progress(self, current: int, total: int, phase: str) -> None:
        self._rebuild_progress.setMaximum(max(total, 1))
        self._rebuild_progress.setValue(current)
        self._rebuild_status.setText(f"{phase}  ({current}/{total})")

    def _on_rebuild_done(self, tandas: int, co: int) -> None:
        self._rebuild_progress.hide()
        self._rebuild_btn.setEnabled(True)
        self._rebuild_status.setText(
            f"Done — {tandas:,} tandas, {co:,} co-occurrence pairs"
        )
        self.status_message.emit(f"Rebuild complete: {tandas:,} tandas")
        self.refresh_stats()
        self.rebuild_done.emit()

    def _on_rebuild_error(self, msg: str) -> None:
        self._rebuild_progress.hide()
        self._rebuild_btn.setEnabled(True)
        self._rebuild_status.setText(f"Error: {msg}")
        self.status_message.emit(f"Rebuild failed: {msg}")

