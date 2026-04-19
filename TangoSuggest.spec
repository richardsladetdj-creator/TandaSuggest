# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for TangoSuggest macOS .app bundle
# Build with:  uv run pyinstaller TangoSuggest.spec --noconfirm

block_cipher = None

a = Analysis(
    ["src/tanda_suggester/gui/app.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        (
            "src/tanda_suggester/gui/resources/TangoSuggest.icns",
            "tanda_suggester/gui/resources",
        ),
    ],
    hiddenimports=[
        "tanda_suggester.db",
        "tanda_suggester.settings",
        "tanda_suggester.suggest",
        "tanda_suggester.search",
        "tanda_suggester.tandas",
        "tanda_suggester.importer",
        "tanda_suggester.music_app",
        "tanda_suggester.cli",
        "PySide6.QtSvg",
        "PySide6.QtPrintSupport",
        "PySide6.QtNetwork",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TangoSuggest",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    codesign_identity=None,
    entitlements_file=None,
    icon="src/tanda_suggester/gui/resources/TangoSuggest.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TangoSuggest",
)

app = BUNDLE(
    coll,
    name="TangoSuggest.app",
    icon="src/tanda_suggester/gui/resources/TangoSuggest.icns",
    bundle_identifier="com.tangosuggest.app",
    info_plist={
        "CFBundleName": "TangoSuggest",
        "CFBundleDisplayName": "TangoSuggest",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        # Suppress the Python runtime name in Dock/Activity Monitor
        "LSApplicationCategoryType": "public.app-category.music",
    },
)
