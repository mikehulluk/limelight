"""The main window shows a tab only when the package has something for it.

These build the real window under Qt's offscreen platform, since which tabs
exist is decided while the window is built.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from limelight.reader import open_limelight
from limelight.writer import LimelightProject

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qt_app():
    # QtWebEngine has to be imported before the application exists, or the
    # first web view blocks forever; importing the app module does that.
    import limelight.qt_app  # noqa: F401
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _tabs(window) -> tuple[list[str], list[str]]:
    tabs = window.tabs
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    shown = [t for i, t in enumerate(titles) if tabs.isTabVisible(i)]
    return titles, shown


def _open_window(qt_app, folder: Path):
    from limelight.qt_app import LimelightWindow

    with open_limelight(folder) as package:
        return LimelightWindow(package, package.manifest_json())


def test_a_figures_only_package_shows_just_the_figures_tab(qt_app, tmp_path: Path) -> None:
    project = LimelightProject(title="Figures only", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0], "y": [1.0, 2.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=["y"])
    project.write_folder(tmp_path / "pkg")

    # No story was set, so none is written: the package is its figures.
    assert (tmp_path / "pkg" / "story" / "index.md").read_text() == ""

    window = _open_window(qt_app, tmp_path / "pkg")
    try:
        titles, shown = _tabs(window)
        assert titles == ["Story", "Figures", "Data", "Parameters"]
        assert shown == ["Figures", "Data"]
    finally:
        window.close()


def test_a_package_with_parameters_and_a_story_shows_every_tab(qt_app, tmp_path: Path) -> None:
    project = LimelightProject(title="Everything", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0], "y": [1.0, 2.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=["y"])
    project.add_float_control_parameter(id="gain", label="Gain", default=1.0, min=0.0, max=2.0)
    project.set_story_markdown("# A story\n\nWith a paragraph.\n")
    project.write_folder(tmp_path / "pkg")

    window = _open_window(qt_app, tmp_path / "pkg")
    try:
        _, shown = _tabs(window)
        assert shown == ["Story", "Figures", "Data", "Parameters"]
        assert window.help_button.text() == "Help"
    finally:
        window.close()
