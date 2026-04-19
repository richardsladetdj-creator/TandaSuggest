"""Settings tab: configure genre rules and cortina behaviour."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tanda_suggester.db import get_connection, reprocess_genre_families
from tanda_suggester.settings import (
    CortinaConfig,
    GenreRule,
    GenreSettings,
    load_settings,
    save_settings,
)


class _GenreRow(QWidget):
    """A single row in the genre list: [name field] [Partial Match ✓] [× delete]"""

    delete_requested = Signal(object)  # emits self

    def __init__(self, name: str, partial_match: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("Genre name")
        self.name_edit.setFixedWidth(200)

        self.partial_check = QCheckBox("Partial match")
        self.partial_check.setChecked(partial_match)
        self.partial_check.setToolTip(
            "When checked, any track whose genre tag contains this name is matched.\n"
            "E.g. 'Tango Argentino' matches 'Tango'.\n"
            "When unchecked, the track's genre must match exactly."
        )

        self.delete_btn = QPushButton("✕")
        self.delete_btn.setFixedWidth(28)
        self.delete_btn.setToolTip("Remove this genre")
        self.delete_btn.setStyleSheet(
            "QPushButton { color: #e74c3c; border: 1px solid #c0392b; border-radius: 3px; }"
            "QPushButton:hover { background: #c0392b; color: white; }"
            "QPushButton:disabled { color: #555; border-color: #444; }"
        )
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self))

        layout.addWidget(self.name_edit)
        layout.addWidget(self.partial_check)
        layout.addWidget(self.delete_btn)
        layout.addStretch()

    def to_rule(self) -> GenreRule:
        return GenreRule(name=self.name_edit.text().strip(), partial_match=self.partial_check.isChecked())

    def set_enabled(self, enabled: bool) -> None:
        self.name_edit.setEnabled(enabled)
        self.partial_check.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)


class SettingsTab(QWidget):
    """Settings tab for configuring genre rules and cortina behaviour."""

    settings_changed = Signal()

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path
        self._dance_rows: list[_GenreRow] = []
        self._cortina_rows: list[_GenreRow] = []

        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setSpacing(12)

        # ── Dance Genres ──────────────────────────────────────────────
        self._dance_group = QGroupBox("Dance Genres")
        dance_layout = QVBoxLayout(self._dance_group)

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("<b>Name</b>"))
        header_row.addStretch()
        dance_layout.addLayout(header_row)

        self._dance_list_layout = QVBoxLayout()
        self._dance_list_layout.setSpacing(2)
        dance_layout.addLayout(self._dance_list_layout)

        add_dance_btn = QPushButton("+ Add Genre")
        add_dance_btn.setFixedWidth(120)
        add_dance_btn.clicked.connect(self._add_dance_genre)
        dance_layout.addWidget(add_dance_btn)

        layout.addWidget(self._dance_group)

        # ── Cortina ───────────────────────────────────────────────────
        cortina_group = QGroupBox("Cortina")
        cortina_layout = QVBoxLayout(cortina_group)

        self._catch_all_check = QCheckBox(
            "Treat all non-dance tracks as cortinas"
        )
        self._catch_all_check.setToolTip(
            "When checked, any track that does not match a dance genre is treated as a cortina.\n"
            "The cortina name fields below are ignored.\n"
            "All tracks in included playlists will also be imported (not just dance tracks)."
        )
        self._catch_all_check.stateChanged.connect(self._on_catch_all_changed)
        cortina_layout.addWidget(self._catch_all_check)

        self._cortina_names_widget = QWidget()
        cortina_names_layout = QVBoxLayout(self._cortina_names_widget)
        cortina_names_layout.setContentsMargins(0, 4, 0, 0)
        cortina_names_layout.setSpacing(2)

        self._cortina_list_layout = QVBoxLayout()
        self._cortina_list_layout.setSpacing(2)
        cortina_names_layout.addLayout(self._cortina_list_layout)

        add_cortina_btn = QPushButton("+ Add Name")
        add_cortina_btn.setFixedWidth(120)
        add_cortina_btn.clicked.connect(self._add_cortina_name)
        cortina_names_layout.addWidget(add_cortina_btn)

        cortina_layout.addWidget(self._cortina_names_widget)
        layout.addWidget(cortina_group)

        # ── Save button ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save Settings")
        save_btn.setFixedWidth(140)
        save_btn.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #5dade2;")
        layout.addWidget(self._status_label)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Load / populate
    # ------------------------------------------------------------------

    def _load(self) -> None:
        conn = get_connection(self.db_path)
        try:
            settings = load_settings(conn)
        finally:
            conn.close()

        # Clear existing rows
        for row in list(self._dance_rows):
            self._remove_dance_row(row)
        for row in list(self._cortina_rows):
            self._remove_cortina_row(row)

        for rule in settings.dance_genres:
            self._add_dance_genre(rule.name, rule.partial_match)

        self._catch_all_check.setChecked(settings.cortina.catch_all)

        for name in settings.cortina.names:
            self._add_cortina_name(name, settings.cortina.partial_match)

        self._on_catch_all_changed(self._catch_all_check.checkState())

    # ------------------------------------------------------------------
    # Row management: dance genres
    # ------------------------------------------------------------------

    def _add_dance_genre(self, name: str = "", partial_match: bool = True) -> None:
        row = _GenreRow(name, partial_match)
        row.delete_requested.connect(self._remove_dance_row)
        self._dance_list_layout.addWidget(row)
        self._dance_rows.append(row)

    def _remove_dance_row(self, row: _GenreRow) -> None:
        if row in self._dance_rows:
            self._dance_rows.remove(row)
        self._dance_list_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    # ------------------------------------------------------------------
    # Row management: cortina names
    # ------------------------------------------------------------------

    def _add_cortina_name(self, name: str = "", partial_match: bool = True) -> None:
        row = _GenreRow(name, partial_match)
        row.delete_requested.connect(self._remove_cortina_row)
        self._cortina_list_layout.addWidget(row)
        self._cortina_rows.append(row)
        row.set_enabled(not self._catch_all_check.isChecked())

    def _remove_cortina_row(self, row: _GenreRow) -> None:
        if row in self._cortina_rows:
            self._cortina_rows.remove(row)
        self._cortina_list_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    # ------------------------------------------------------------------
    # Catch-all toggle
    # ------------------------------------------------------------------

    def _on_catch_all_changed(self, state) -> None:
        checked = bool(state)
        for row in self._cortina_rows:
            row.set_enabled(not checked)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save(self) -> None:
        # Collect and validate dance genres
        dance_rules: list[GenreRule] = []
        seen_names: set[str] = set()
        for row in self._dance_rows:
            rule = row.to_rule()
            if not rule.name:
                QMessageBox.warning(self, "Invalid Settings", "Genre names cannot be blank.")
                return
            lower = rule.name.lower()
            if lower in seen_names:
                QMessageBox.warning(
                    self, "Invalid Settings", f"Duplicate genre name: '{rule.name}'"
                )
                return
            seen_names.add(lower)
            dance_rules.append(rule)

        # Collect and validate cortina names
        catch_all = self._catch_all_check.isChecked()
        cortina_names: list[str] = []
        cortina_partial = True

        if not catch_all:
            for row in self._cortina_rows:
                rule = row.to_rule()
                if not rule.name:
                    QMessageBox.warning(self, "Invalid Settings", "Cortina names cannot be blank.")
                    return
                cortina_names.append(rule.name)
                cortina_partial = rule.partial_match  # use last row's setting as shared value
            if not cortina_names:
                cortina_names = ["Cortina"]
        else:
            # Preserve existing cortina names even when disabled
            for row in self._cortina_rows:
                name = row.name_edit.text().strip()
                if name:
                    cortina_names.append(name)
                    cortina_partial = row.partial_check.isChecked()
            if not cortina_names:
                cortina_names = ["Cortina"]

        settings = GenreSettings(
            dance_genres=dance_rules,
            cortina=CortinaConfig(
                names=cortina_names,
                partial_match=cortina_partial,
                catch_all=catch_all,
            ),
        )

        conn = get_connection(self.db_path)
        try:
            save_settings(conn, settings)
            count = reprocess_genre_families(conn)
        finally:
            conn.close()

        self._status_label.setText(f"Settings saved — {count} tracks reclassified.")
        self.settings_changed.emit()
