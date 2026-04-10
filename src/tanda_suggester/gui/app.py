"""TangoSuggest GUI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from tanda_suggester.db import DB_PATH, init_db
from tanda_suggester.gui.main_window import MainWindow
from tanda_suggester.gui.theme import apply_theme


def _set_macos_app_name(name: str) -> None:
    """Override the process name so the macOS Dock tooltip shows 'name' not 'Python X.Y'.

    Must be called before QApplication (i.e. NSApplication) is initialised,
    because that is when the Dock registers the process name.
    """
    try:
        from Foundation import NSProcessInfo  # pyobjc-framework-Foundation
        NSProcessInfo.processInfo().setProcessName_(name)
    except Exception:
        pass
    try:
        from AppKit import NSBundle  # pyobjc-framework-Cocoa
        NSBundle.mainBundle().infoDictionary()["CFBundleName"] = name
    except Exception:
        pass


def main() -> None:
    if sys.platform == "darwin":
        _set_macos_app_name("TangoSuggest")

    app = QApplication(sys.argv)
    app.setApplicationName("TangoSuggest")
    app.setOrganizationName("TangoSuggest")

    icon_path = Path(__file__).parent / "resources" / "TangoSuggest.icns"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    apply_theme(app)

    # Initialise DB (creates schema if first run; no-op if existing)
    init_db(DB_PATH)

    window = MainWindow(DB_PATH)
    window.show()
    sys.exit(app.exec())
