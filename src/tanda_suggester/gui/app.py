"""TangoSuggest GUI entry point."""

from __future__ import annotations

import sys

from tanda_suggester import APP_NAME

# ── macOS: set bundle/process name BEFORE NSApplication is initialised ──────
# Three-layer approach to maximise the chance of the Dock tooltip showing the
# app name instead of "python3.11":
#
#   1. ctypes setprogname  – sets the OS-level program name that NSProcessInfo
#      reads on first access.
#   2. pyobjc NSProcessInfo.setProcessName_  – overrides the ObjC-level name.
#   3. pyobjc NSBundle.mainBundle infoDictionary mutations  – updates CFBundleName
#      and CFBundleDisplayName in the in-memory bundle info dict.
#   4. NSApplication.sharedApplication()  – forces Dock registration NOW, while
#      our mutations are in effect; Qt reuses the existing NSApplication instance.
#
# All of this must happen before any PySide6 symbol is imported, because Qt may
# initialise NSApplication during the first PySide6 import on some versions.
if sys.platform == "darwin":
    try:
        import ctypes
        import ctypes.util
        _libc = ctypes.CDLL(ctypes.util.find_library("c"))
        _libc.setprogname.argtypes = [ctypes.c_char_p]
        _libc.setprogname(APP_NAME.encode())
    except Exception:
        pass

    try:
        from Foundation import NSBundle, NSProcessInfo  # pyobjc-framework-Cocoa
        from AppKit import NSApplication

        NSProcessInfo.processInfo().setProcessName_(APP_NAME)

        _info = NSBundle.mainBundle().infoDictionary()
        if _info is not None:
            _info.setObject_forKey_(APP_NAME, "CFBundleName")
            _info.setObject_forKey_(APP_NAME, "CFBundleDisplayName")

        # Register with the Dock using our name before Qt gets a chance to.
        NSApplication.sharedApplication()
    except Exception:
        pass

from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from tanda_suggester.db import DB_PATH, init_db
from tanda_suggester.gui.main_window import MainWindow
from tanda_suggester.gui.theme import apply_theme


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)

    icon_path = Path(__file__).parent / "resources" / "TangoSuggest.icns"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    apply_theme(app)

    # Initialise DB (creates schema if first run; no-op if existing)
    init_db(DB_PATH)

    window = MainWindow(DB_PATH)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
