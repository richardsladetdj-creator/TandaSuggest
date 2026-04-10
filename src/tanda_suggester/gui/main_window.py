"""Main application window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QStatusBar, QTabWidget
from PySide6.QtGui import QCloseEvent

from tanda_suggester.gui.tabs.import_tab import ImportTab
from tanda_suggester.gui.tabs.library_tab import LibraryTab
from tanda_suggester.gui.tabs.playlists_tab import PlaylistsTab
from tanda_suggester.gui.tabs.suggest_tab import SuggestTab


class MainWindow(QMainWindow):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path
        self.setWindowTitle("TangoSuggest")
        self.resize(1200, 800)

        self._tabs = QTabWidget()
        self._tabs.tabBar().setElideMode(Qt.ElideNone)

        self.suggest_tab = SuggestTab(db_path)
        self.playlists_tab = PlaylistsTab(db_path)
        self.library_tab = LibraryTab(db_path)
        self.import_tab = ImportTab(db_path)

        self._tabs.addTab(self.suggest_tab, "Suggest")
        self._tabs.addTab(self.playlists_tab, "Playlists")
        self._tabs.addTab(self.library_tab, "Library")
        self._tabs.addTab(self.import_tab, "Import & Stats")

        self.setCentralWidget(self._tabs)
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready")

        # Cross-tab wiring
        self.import_tab.data_changed.connect(self._on_data_changed)
        self.import_tab.status_message.connect(self._status.showMessage)
        self.playlists_tab.rebuild_finished.connect(self._on_rebuild_finished)
        self.playlists_tab.status_message.connect(self._status.showMessage)
        self.library_tab.track_selected.connect(self.suggest_tab.set_seed_by_id)
        self.suggest_tab.show_in_library.connect(self._on_show_in_library)
        self._tabs.currentChanged.connect(self._on_tab_changed)

    def _on_data_changed(self) -> None:
        self.library_tab.refresh()
        self.playlists_tab.refresh_db_panel()
        self.suggest_tab.invalidate_cache()

    def _on_rebuild_finished(self) -> None:
        self.suggest_tab.invalidate_cache()
        self.import_tab.refresh_stats()
        self._status.showMessage("Rebuild complete")

    def _on_show_in_library(self, track_id: int) -> None:
        self._tabs.setCurrentIndex(2)
        self.library_tab.select_track_by_id(track_id)

    def _on_tab_changed(self, index: int) -> None:
        widget = self._tabs.widget(index)
        if widget is self.import_tab:
            self.import_tab.refresh_stats()
        elif widget is self.library_tab:
            self.library_tab.refresh()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.suggest_tab.cleanup()
        super().closeEvent(event)
