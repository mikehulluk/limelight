"""A desktop entry for a Limelight installed with pip or uv.

The installers ship limelight.desktop, and a Linux desktop matches the app's
windows to it: that is where the taskbar icon comes from, and what makes the
app pinnable and lets it own the .limelight file type. A pip install has no
such entry, and GNOME then shows a placeholder icon for the running app -
the icon the window itself carries is not consulted. This writes the same
entry into the user's own applications directory, pointing at the installed
command and the icon inside the package.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ENTRY_NAME = "limelight.desktop"
MIME_NAME = "limelight.xml"

_ICON_PATH = Path(__file__).resolve().parent / "assets" / "limelight-icon.svg"

# The same entry the installers ship, with the command and icon resolved to
# where this install put them. StartupWMClass is the class Qt gives the
# windows, which is the application name.
_ENTRY = """[Desktop Entry]
Type=Application
Name=Limelight
GenericName=Limelight Package Viewer
Comment=Read and explore Limelight data packages
Exec={exec} %f
Icon={icon}
Terminal=false
Categories=Science;DataVisualization;Education;
MimeType=application/x-limelight-package;
StartupWMClass=Limelight
Keywords=data;plot;figure;dataset;report;
"""

_MIME = """<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-limelight-package">
    <comment>Limelight package</comment>
    <glob pattern="*.limelight"/>
    <glob pattern="*.ll"/>
  </mime-type>
</mime-info>
"""


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _gui_command() -> Path:
    """The limelight-gui this install provides, next to the running command."""

    candidate = Path(sys.argv[0]).resolve().parent / "limelight-gui"
    if candidate.is_file():
        return candidate
    found = shutil.which("limelight-gui")
    if found is None:
        raise FileNotFoundError("limelight-gui is not installed alongside this command or on PATH")
    return Path(found).resolve()


def _refresh(data_home: Path) -> None:
    """Tell the desktop about the change; harmless where the tools are absent."""

    for command in (
        ["update-desktop-database", str(data_home / "applications")],
        ["update-mime-database", str(data_home / "mime")],
    ):
        if shutil.which(command[0]):
            subprocess.run(command, check=False, capture_output=True)


def install(data_home: Path | None = None) -> list[Path]:
    """Write the entry and the file type; returns the files written."""

    if sys.platform != "linux":
        raise RuntimeError("Desktop entries are a Linux desktop convention; nothing to install here")
    data_home = data_home or _data_home()
    entry = data_home / "applications" / ENTRY_NAME
    mime = data_home / "mime" / "packages" / MIME_NAME
    entry.parent.mkdir(parents=True, exist_ok=True)
    mime.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(_ENTRY.format(exec=_gui_command(), icon=_ICON_PATH), encoding="utf-8")
    mime.write_text(_MIME, encoding="utf-8")
    _refresh(data_home)
    return [entry, mime]


def remove(data_home: Path | None = None) -> list[Path]:
    """Remove what install() wrote; returns the files removed."""

    data_home = data_home or _data_home()
    removed = []
    for path in (data_home / "applications" / ENTRY_NAME, data_home / "mime" / "packages" / MIME_NAME):
        if path.is_file():
            path.unlink()
            removed.append(path)
    _refresh(data_home)
    return removed
