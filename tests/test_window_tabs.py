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


def test_cache_progress_indicator_shows_builds_and_hides_when_done(qt_app) -> None:
    from limelight.qt_app import CacheProgressIndicator

    indicator = CacheProgressIndicator()
    indicator.LINGER_MS = 0
    assert indicator.isHidden()

    indicator.report("big['y']", 250, 1000)
    assert not indicator.isHidden()
    assert "big['y']" in indicator.label.text() and "25%" in indicator.label.text()

    indicator.report("other['z']", 0, 1000)
    assert "+1 more" in indicator.label.text()

    indicator.report("big['y']", 1000, 1000)
    assert "other['z']" in indicator.label.text() and "+1 more" not in indicator.label.text()

    indicator.report("other['z']", 1000, 1000)
    assert "ready" in indicator.label.text()
    indicator._hide_if_idle()
    assert indicator.isHidden()


def test_window_routes_cache_progress_to_its_status_bar(qt_app, tmp_path: Path) -> None:
    project = LimelightProject(title="Progress", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [1.0, 2.0], "y": [3.0, 4.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=["y"])
    folder = tmp_path / "pkg.limelight"
    project.write_folder(folder)

    window = _open_window(qt_app, folder)
    try:
        assert window.runtime.cache_progress is not None
        window.runtime.cache_progress("big", "y", 1, 4)
        qt_app.processEvents()
        assert "big['y']" in window._cache_progress.label.text()
        assert "25%" in window._cache_progress.label.text()
    finally:
        window.close()


def test_ctrl_wheel_over_a_figure_zooms_the_story(qt_app, tmp_path: Path) -> None:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    from limelight.qt_app import STORY_ZOOM_STEPS, StoryFigureViewPanel

    project = LimelightProject(title="Zoom", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [1.0, 2.0], "y": [3.0, 4.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=["y"])
    view = project.story_figure(ref="fig", id="fig-view", actions=[])
    project.set_story_markdown(f"# Story\n\n{view}\n")
    folder = tmp_path / "pkg.limelight"
    project.write_folder(folder)

    window = _open_window(qt_app, folder)
    try:
        story = window.story_blocks
        assert story is not None
        qt_app.processEvents()
        figures = [
            story.layout.itemAt(i).widget()
            for i in range(story.layout.count())
            if isinstance(story.layout.itemAt(i).widget(), StoryFigureViewPanel)
        ]
        assert figures, "the story should hold the figure block"
        assert story._zoom == 1.0

        def wheel(widget, steps: int, ctrl: bool) -> bool:
            modifiers = Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
            event = QWheelEvent(
                QPointF(5, 5), widget.mapToGlobal(QPoint(5, 5)), QPoint(0, 0), QPoint(0, 120 * steps),
                Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
            )
            qt_app.sendEvent(widget, event)
            return event.isAccepted()

        # A static figure is an image label that ignores the wheel, which the
        # window system then hands up to the story's viewport; sendEvent does
        # no such climbing, so the test delivers where the climb ends.
        assert not wheel(figures[0].image_label, +1, ctrl=True)
        wheel(story.viewport(), +1, ctrl=True)
        assert story._zoom == STORY_ZOOM_STEPS[STORY_ZOOM_STEPS.index(1.0) + 1]
        wheel(story.viewport(), -1, ctrl=True)
        assert story._zoom == 1.0
        # A plain wheel scrolls rather than zooms.
        wheel(story.viewport(), +1, ctrl=False)
        assert story._zoom == 1.0

        # In Explore mode the matplotlib canvas keeps every wheel event, so a
        # Ctrl+wheel over the plot leaves the story's zoom alone.
        figures[0]._explore_inline()
        assert wheel(figures[0].plot_panel.canvas, +1, ctrl=True)
        assert story._zoom == 1.0
    finally:
        window.close()
