# PyInstaller spec for the Limelight desktop app.
#
# Deliberately a one-directory bundle: --onefile would unpack ~300 MB of Qt,
# matplotlib and numpy into a temporary directory on every launch, which costs
# seconds of startup each time. One-dir starts in about the time the interpreter
# and its imports take. The installers hide the directory anyway.
#
# Build with:
#     pyinstaller packaging/limelight.spec --noconfirm

import os
import sys
from importlib.metadata import version as installed_version
from pathlib import Path

REPOSITORY_ROOT = Path(SPECPATH).resolve().parent

# The installers are named after the git tag; the workflow passes it in so the
# macOS bundle carries the same number. A local build takes what setuptools-scm
# derived when the package was installed.
VERSION = os.environ.get("LIMELIGHT_VERSION") or installed_version("limelight-app")
SOURCE_ROOT = REPOSITORY_ROOT / "src"

datas = [
    (str(SOURCE_ROOT / "limelight" / "assets"), "limelight/assets"),
    (str(SOURCE_ROOT / "limelight" / "language-reference"), "limelight/language-reference"),
]

hiddenimports = [
    # Reached only through matplotlib's runtime backend lookup.
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_qtagg",
]

# Qt ships far more than this app touches. Dropping the unused modules cuts both
# the download size and the number of shared objects loaded at startup.
excludes = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtQuick3D",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebSockets",
    "tkinter",
    "pytest",
]

block_cipher = None

analysis = Analysis(
    [str(REPOSITORY_ROOT / "packaging" / "limelight_entry.py")],
    pathex=[str(SOURCE_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

if sys.platform == "darwin":
    icon = str(REPOSITORY_ROOT / "packaging" / "macos" / "limelight.icns")
elif sys.platform == "win32":
    icon = str(REPOSITORY_ROOT / "packaging" / "windows" / "limelight.ico")
else:
    icon = None

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="limelight",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A windowed build on Windows and macOS, so no console flashes up. On Linux
    # the desktop entry controls that instead.
    console=sys.platform not in {"win32", "darwin"},
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon if icon and Path(icon).is_file() else None,
)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="limelight",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="Limelight.app",
        icon=icon if icon and Path(icon).is_file() else None,
        bundle_identifier="io.github.mikehulluk.limelight",
        info_plist={
            "CFBundleName": "Limelight",
            "CFBundleDisplayName": "Limelight",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Limelight Package",
                    "CFBundleTypeRole": "Viewer",
                    "LSItemContentTypes": ["public.data"],
                    "CFBundleTypeExtensions": ["limelight"],
                }
            ],
        },
    )
