"""Recents are offered on the welcome screen, and only while they are on disk."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qt_app():
    import limelight.qt_app  # noqa: F401
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    QApplication.setOrganizationName("Limelight")
    QApplication.setApplicationName("LimelightTests")
    return app


@pytest.fixture
def settings(qt_app):
    from PySide6.QtCore import QSettings

    stored = QSettings()
    stored.clear()
    stored.sync()
    yield stored
    stored.clear()
    stored.sync()


def _recent_items(dialog) -> list[str]:
    from PySide6.QtCore import Qt

    if dialog.recent_list is None:
        return []
    return [
        str(dialog.recent_list.item(row).data(Qt.ItemDataRole.UserRole))
        for row in range(dialog.recent_list.count())
    ]


def test_a_package_that_is_gone_is_dropped_from_the_recents(settings, tmp_path: Path) -> None:
    from limelight.qt_app import RECENT_PACKAGES_KEY, add_recent_package, load_recent_packages

    here = tmp_path / "here.limelight"
    here.mkdir()
    gone = tmp_path / "gone.limelight"
    gone.mkdir()
    add_recent_package(gone)
    add_recent_package(here)

    assert load_recent_packages() == [str(here), str(gone)]

    gone.rmdir()
    assert load_recent_packages() == [str(here)]
    # The pruning is written back, not just filtered on the way out.
    assert list(settings.value(RECENT_PACKAGES_KEY)) == [str(here)]


def test_the_most_recent_comes_first_and_is_never_listed_twice(settings, tmp_path: Path) -> None:
    from limelight.qt_app import MAX_RECENT_PACKAGES, add_recent_package

    packages = []
    for index in range(MAX_RECENT_PACKAGES + 3):
        package = tmp_path / f"p{index}.limelight"
        package.mkdir()
        packages.append(package)
        add_recent_package(package)

    recents = add_recent_package(packages[0])
    assert recents[0] == str(packages[0])
    assert len(recents) == MAX_RECENT_PACKAGES
    assert len(set(recents)) == len(recents)


def test_the_welcome_screen_offers_the_recents_and_opens_one(settings, tmp_path: Path) -> None:
    from limelight.qt_app import StartupPackageDialog, add_recent_package

    package = tmp_path / "recent.limelight"
    package.mkdir()
    add_recent_package(package)

    dialog = StartupPackageDialog()
    try:
        assert _recent_items(dialog) == [str(package)]
        dialog._open_selected_recent()
        assert dialog.selected_path == str(package)
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()


def test_the_welcome_screen_has_no_recents_section_when_there_are_none(settings, tmp_path: Path) -> None:
    from limelight.qt_app import StartupPackageDialog, add_recent_package

    gone = tmp_path / "gone.limelight"
    gone.mkdir()
    add_recent_package(gone)
    gone.rmdir()

    dialog = StartupPackageDialog()
    try:
        assert dialog.recent_list is None
    finally:
        dialog.deleteLater()
