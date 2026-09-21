"""Help > Check for Updates: the newest release, and the way to it for this install."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from limelight import updates

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


RELEASE_PAYLOAD = {
    "tag_name": "v0.0.9",
    "html_url": "https://github.com/mikehulluk/limelight/releases/tag/v0.0.9",
    "body": "## [0.0.9]\n\n### Fixed\n\n- A thing.",
    "assets": [
        {"name": "limelight-0.0.9-windows-x64.exe", "browser_download_url": "https://x/limelight-0.0.9-windows-x64.exe"},
        {"name": "Limelight-0.0.9.dmg", "browser_download_url": "https://x/Limelight-0.0.9.dmg"},
        {"name": "Limelight-0.0.9-x86_64.AppImage", "browser_download_url": "https://x/Limelight-0.0.9-x86_64.AppImage"},
        {"name": "limelight_0.0.9_amd64.deb", "browser_download_url": "https://x/limelight_0.0.9_amd64.deb"},
        {"name": "SHA256SUMS", "browser_download_url": "https://x/SHA256SUMS"},
    ],
}


def test_release_is_read_from_the_github_payload() -> None:
    release = updates.release_from_payload(RELEASE_PAYLOAD)
    assert release.version == "0.0.9" and release.tag == "v0.0.9"
    assert release.page_url.endswith("/v0.0.9")
    assert "A thing." in release.notes
    assert set(release.assets) == {
        "limelight-0.0.9-windows-x64.exe", "Limelight-0.0.9.dmg", "Limelight-0.0.9-x86_64.AppImage",
        "limelight_0.0.9_amd64.deb", "SHA256SUMS",
    }


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("0.0.9", "0.0.8", True),
        ("0.0.10", "0.0.9", True),  # numeric, not lexical
        ("0.0.8", "0.0.8", False),
        ("0.0.7", "0.0.8", False),
        ("0.0.8", "0.0.8.dev3+gabc", True),  # a checkout heading for 0.0.8 does not have it yet
        ("not-a-version", "0.0.8", False),
    ],
)
def test_is_newer(candidate: str, current: str, newer: bool) -> None:
    assert updates.is_newer(candidate, current) is newer


@pytest.mark.parametrize(
    ("frozen", "platform", "environ", "prefix", "kind"),
    [
        (True, "win32", {}, "C:/Program Files/Limelight", updates.InstallKind.WINDOWS_INSTALLER),
        (True, "darwin", {}, "/Applications/Limelight.app", updates.InstallKind.MACOS_BUNDLE),
        (True, "linux", {"APPIMAGE": "/home/me/Limelight.AppImage"}, "/tmp/_MEI", updates.InstallKind.LINUX_APPIMAGE),
        (True, "linux", {}, "/opt/limelight", updates.InstallKind.LINUX_PACKAGE),
        (False, "linux", {}, "<uv tool env>", updates.InstallKind.PYTHON_UV_TOOL),
        (False, "linux", {}, "/home/me/dev/limelight/.venv", updates.InstallKind.PYTHON_PACKAGE),
        # A path that merely looks like uv's is not a uv tool.
        (False, "linux", {}, "/home/uv/tools/limelight/.venv", updates.InstallKind.PYTHON_PACKAGE),
    ],
)
def test_install_kind_is_told_from_how_the_app_runs(monkeypatch, tmp_path, frozen, platform, environ, prefix, kind) -> None:
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    # A uv tool environment is known by the receipt uv leaves at its root.
    if kind is updates.InstallKind.PYTHON_UV_TOOL:
        prefix = tmp_path / "uv-tool-env"
        prefix.mkdir()
        (prefix / "uv-receipt.toml").write_text("[tool]\n")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.delenv("APPIMAGE", raising=False)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    assert updates.install_kind() is kind


def test_installer_asset_matches_the_install_kind() -> None:
    release = updates.release_from_payload(RELEASE_PAYLOAD)
    picks = {kind: updates.installer_asset(release, kind) for kind in updates.InstallKind}
    assert picks[updates.InstallKind.WINDOWS_INSTALLER][0] == "limelight-0.0.9-windows-x64.exe"
    assert picks[updates.InstallKind.MACOS_BUNDLE][0] == "Limelight-0.0.9.dmg"
    assert picks[updates.InstallKind.LINUX_APPIMAGE][0] == "Limelight-0.0.9-x86_64.AppImage"
    assert picks[updates.InstallKind.LINUX_PACKAGE][0] == "limelight_0.0.9_amd64.deb"
    assert picks[updates.InstallKind.PYTHON_PACKAGE] is None
    assert picks[updates.InstallKind.PYTHON_UV_TOOL] is None


def test_python_upgrade_commands_keep_the_gui_extra() -> None:
    # The app is the `gui` extra; an upgrade that named the bare distribution
    # would come back without Qt and the app would not start.
    assert updates.python_upgrade_command(updates.InstallKind.PYTHON_UV_TOOL, "0.0.9") == [
        "uv", "tool", "install", "--force", "limelight-app[gui]==0.0.9",
    ]
    assert updates.python_upgrade_command(updates.InstallKind.PYTHON_UV_TOOL) == [
        "uv", "tool", "install", "--force", "limelight-app[gui]",
    ]
    assert updates.python_upgrade_command(updates.InstallKind.PYTHON_PACKAGE, "0.0.9") == [
        sys.executable, "-m", "pip", "install", "--upgrade", "limelight-app[gui]==0.0.9",
    ]
    assert updates.python_upgrade_command(updates.InstallKind.PYTHON_PACKAGE) == [
        sys.executable, "-m", "pip", "install", "--upgrade", "limelight-app[gui]",
    ]


def test_windows_installer_runs_silently_then_relaunches() -> None:
    command = updates.windows_installer_command(Path("C:/tmp/limelight-0.0.9-windows-x64.exe"), Path("C:/Program Files/Limelight/limelight-gui.exe"))
    assert command[:2] == ["cmd", "/c"]
    assert "/SILENT" in command[2] and "/CLOSEAPPLICATIONS" in command[2] and "/NORESTART" in command[2]
    assert command[2].endswith('&& start "" "C:\\Program Files\\Limelight\\limelight-gui.exe"') or command[2].endswith(
        '&& start "" "C:/Program Files/Limelight/limelight-gui.exe"'
    )


def test_sha256sums_are_parsed_and_files_hashed(tmp_path: Path) -> None:
    exe_digest, dmg_digest = "a" * 64, "B" * 64
    text = f"{exe_digest}  limelight-0.0.9-windows-x64.exe\n{dmg_digest} *Limelight-0.0.9.dmg\nbad line here\n"
    assert updates.parse_sha256sums(text) == {
        "limelight-0.0.9-windows-x64.exe": exe_digest,
        "Limelight-0.0.9.dmg": dmg_digest.lower(),
    }
    import hashlib

    payload = tmp_path / "blob"
    payload.write_bytes(b"limelight")
    assert updates.sha256_of(payload) == hashlib.sha256(b"limelight").hexdigest()


@pytest.fixture(scope="module")
def qt_app():
    import limelight.qt_app  # noqa: F401 - QtWebEngine must be imported before the application exists
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_update_dialog_offers_what_fits_the_install(qt_app) -> None:
    from limelight.qt_app import UpdateDialog

    release = updates.release_from_payload(RELEASE_PAYLOAD)
    labels = {}
    for kind in updates.InstallKind:
        dialog = UpdateDialog(None, release=release, current="0.0.8", kind=kind)
        labels[kind] = dialog.update_button.text()
        assert "A thing." in dialog.notes.toPlainText()
        dialog.deleteLater()
    assert labels[updates.InstallKind.WINDOWS_INSTALLER] == "Update Now"
    assert labels[updates.InstallKind.PYTHON_PACKAGE] == "Upgrade Now"
    assert labels[updates.InstallKind.PYTHON_UV_TOOL] == "Upgrade Now"
    assert labels[updates.InstallKind.MACOS_BUNDLE] == "Open Download"
    assert labels[updates.InstallKind.LINUX_PACKAGE] == "Open Download"


def test_window_has_the_help_action_and_reports_up_to_date(qt_app, tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from limelight.qt_app import LimelightWindow
    from limelight.reader import open_limelight
    from limelight.writer import LimelightProject

    project = LimelightProject(title="Up to date", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [1.0], "y": [2.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=["y"])
    folder = tmp_path / "pkg.limelight"
    project.write_folder(folder)

    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: shown.append(text))
    monkeypatch.setattr(updates, "current_version", lambda: "0.0.9")

    with open_limelight(folder) as package:
        window = LimelightWindow(package, package.manifest_json())
        try:
            [action] = [a for a in window.help_menu.actions() if a.objectName() == "check-for-updates"]
            assert action.text() == "Check for &Updates..."
            window._show_update_check_result(updates.release_from_payload(RELEASE_PAYLOAD))
        finally:
            window.close()
    assert shown == ["Limelight 0.0.9 is the latest version."]
