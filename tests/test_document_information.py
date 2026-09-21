"""The Document Information dialog's typography and spacing tabs say what the document is set in."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from limelight.reader import open_limelight
from limelight.writer import LimelightProject

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qt_app():
    import limelight.qt_app  # noqa: F401
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _table_rows(table) -> list[list[str]]:
    return [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]


def test_the_typography_tab_lists_the_fonts_and_the_face_each_style_really_gets(qt_app, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QTableWidget

    from limelight.qt_app import LimelightWindow, _typography_page

    project = LimelightProject(title="Type", authors=["Test"])
    project.set_story_markdown("# Heading\n\nBody text.\n", base_dir=Path("."))
    project.write_folder(tmp_path / "pkg")

    with open_limelight(tmp_path / "pkg") as package:
        window = LimelightWindow(package, package.manifest_json())
        try:
            page = _typography_page(window.runtime)
            fonts, styles = page.findChildren(QTableWidget)
            font_rows = _table_rows(fonts)
            assert [row[0] for row in font_rows] == ["ubuntu", "noto-sans", "ubuntu-mono", "dejavu-sans-mono"]
            assert all(row[3] == "Available" for row in font_rows)
            assert font_rows[0][4] == "Ubuntu-Regular.ttf"

            by_name = {row[0]: row for row in _table_rows(styles)}
            assert by_name["body"][1:] == ["ubuntu, noto-sans", "10.5pt (14px)", "400", "Ubuntu", "Ubuntu (Ubuntu-Regular.ttf)"]
            assert by_name["heading1"][3] == "700" and by_name["heading1"][5] == "Ubuntu (Ubuntu-Bold.ttf)"
            assert by_name["code"][4] == "Ubuntu Mono"
        finally:
            window.close()


def test_the_spacing_tab_shows_the_page_and_the_rhythm_in_millimetres(qt_app, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QLabel

    from limelight.qt_app import LimelightWindow, _spacing_page

    project = LimelightProject(title="Space", authors=["Test"])
    project.set_story_markdown("Body text.\n", base_dir=Path("."))
    project.write_folder(tmp_path / "pkg")

    with open_limelight(tmp_path / "pkg") as package:
        window = LimelightWindow(package, package.manifest_json())
        try:
            page = _spacing_page(window.runtime)
            texts = [label.text() for label in page.findChildren(QLabel)]
            geometry = window.runtime.declared_page_geometry
            spacing = window.runtime.story_spacing
            assert f"{geometry.width_mm:g} mm" in texts and f"{geometry.content_width_mm:g} mm" in texts
            assert f"{spacing.block_gap_mm:g} mm" in texts and f"{spacing.heading_gap_before_mm:g} mm" in texts
        finally:
            window.close()
