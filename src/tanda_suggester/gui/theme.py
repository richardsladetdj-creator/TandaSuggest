"""Dark theme setup for TangoSuggest."""

from __future__ import annotations

import qdarktheme
from PySide6.QtWidgets import QApplication

_CUSTOM_QSS = """
QPushButton {
    padding: 6px 16px;
    border-radius: 4px;
    font-size: 13px;
}

QTabBar::tab {
    padding: 8px 20px;
    font-size: 13px;
}

QTabBar::tab:selected {
    font-weight: bold;
}

QHeaderView::section {
    padding: 6px 8px;
    font-weight: 600;
    font-size: 12px;
}

QLineEdit {
    padding: 5px 8px;
    border-radius: 4px;
    font-size: 13px;
}

QComboBox {
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 13px;
}

QProgressBar {
    border-radius: 4px;
    text-align: center;
    font-size: 11px;
}

QGroupBox {
    font-size: 13px;
    font-weight: bold;
    padding-top: 14px;
    margin-top: 6px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
}

QStatusBar {
    font-size: 12px;
}

QSpinBox {
    padding: 4px 6px;
    border-radius: 4px;
    font-size: 13px;
}

QCheckBox {
    font-size: 13px;
    spacing: 6px;
}

QCheckBox:focus {
    outline: none;
    border: none;
}

QCheckBox:hover {
    background: transparent;
    border: none;
}

QTableView, QTableWidget {
    font-size: 13px;
}
"""


def apply_theme(app: QApplication) -> None:
    """Apply dark theme to the application."""
    qdarktheme.setup_theme("dark")
    app.setStyleSheet(app.styleSheet() + _CUSTOM_QSS)
