"""`LL desktop-entry` writes a user-level desktop entry and file type."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from limelight import desktop_entry

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="desktop entries are a Linux convention")


def test_install_writes_an_entry_pointing_at_this_install(tmp_path: Path, monkeypatch) -> None:
    gui = tmp_path / "bin" / "limelight-gui"
    gui.parent.mkdir()
    gui.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "bin" / "LL")])
    monkeypatch.setattr(desktop_entry, "_refresh", lambda data_home: None)

    written = desktop_entry.install(tmp_path / "share")

    entry = (tmp_path / "share" / "applications" / "limelight.desktop").read_text()
    assert f"Exec={gui} %f" in entry
    assert "Icon=" in entry and "limelight-icon.svg" in entry
    assert "StartupWMClass=Limelight" in entry
    assert "MimeType=application/x-limelight-package;" in entry
    mime = (tmp_path / "share" / "mime" / "packages" / "limelight.xml").read_text()
    assert '<glob pattern="*.limelight"/>' in mime
    assert '<glob pattern="*.ll"/>' in mime
    assert [p.name for p in written] == ["limelight.desktop", "limelight.xml"]


def test_remove_takes_both_away_and_is_quiet_when_nothing_is_there(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(desktop_entry, "_refresh", lambda data_home: None)
    monkeypatch.setattr(desktop_entry, "_gui_command", lambda: Path("/opt/limelight-gui"))
    desktop_entry.install(tmp_path / "share")

    removed = desktop_entry.remove(tmp_path / "share")

    assert [p.name for p in removed] == ["limelight.desktop", "limelight.xml"]
    assert desktop_entry.remove(tmp_path / "share") == []


def test_ll_routes_the_subcommand_to_the_cli() -> None:
    from limelight import cli

    assert "desktop-entry" in cli.COMMANDS
