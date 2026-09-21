"""Debug mode is one flag on the runtime that every view reads, toggled from
View > Debug Mode or a figure toolbar; the story's status bar counts the
figures of a render burst."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from limelight.reader import open_limelight
from limelight.writer import LimelightProject


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import limelight.qt_app  # noqa: F401  (QtWebEngine before the application)
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _package(tmp_path: Path) -> Path:
    project = LimelightProject(title="Debug", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [1.0, 2.0, 3.0], "y": [1.0, 4.0, 9.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=["y"])
    view = project.story_figure(ref="fig")
    project.set_story_markdown(f"# Story\n\n{view}\n", base_dir=tmp_path)
    return project.write_folder(tmp_path / "pkg")


def test_debug_mode_is_one_flag_shared_by_menu_and_figure_toolbar(qt_app, tmp_path: Path) -> None:
    from limelight.qt_app import LimelightWindow, _parameter_signature

    with open_limelight(_package(tmp_path)) as package:
        window = LimelightWindow(package, package.manifest_json())
        try:
            assert window.runtime.debug_ui is False
            plain = _parameter_signature(window.runtime)

            window.debug_ui_action.trigger()
            assert window.runtime.debug_ui is True
            assert window.figure_view_panel.debug_action.isChecked()
            # A cached static render is only good for the mode it was made in.
            assert _parameter_signature(window.runtime) != plain

            window.figure_view_panel.debug_action.trigger()
            assert window.runtime.debug_ui is False
            assert not window.debug_ui_action.isChecked()
        finally:
            window.close()


def test_render_progress_counts_a_burst(qt_app) -> None:
    from limelight.qt_app import RenderProgressIndicator

    indicator = RenderProgressIndicator()
    indicator.report(0, 3)
    assert indicator.label.text() == "Rendering figures: 1 of 3"
    indicator.report(2, 3)
    assert indicator.label.text() == "Rendering figures: 3 of 3"
    indicator.report(3, 3)
    assert indicator.label.text().startswith("Rendered 3 figures in")
    # Idle reports after the burst change nothing.
    indicator.report(3, 3)
    assert indicator.label.text().startswith("Rendered 3 figures in")
