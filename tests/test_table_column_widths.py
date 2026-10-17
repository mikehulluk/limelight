"""Table column widths: auto, fixed (mm) and a share of what is left.

One ColumnWidth drives both outputs: Typst's track sizes in the PDF, and the
app's column sizer, which is built here under Qt's offscreen platform.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from limelight.app import LimelightRuntime, table_view_column_formats, table_view_column_width, table_view_row_limit
from limelight.reader import LimelightError, open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.writer import ColumnFormat, ColumnWidth, LimelightProject, TableViewSpec, array

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qt_app():
    import limelight.qt_app  # noqa: F401
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _package(tmp_path: Path, *column_formats: ColumnFormat) -> Path:
    project = LimelightProject(title="Widths", authors=["Test"])
    project.add_csv_dataset(
        id="events",
        arrays={
            "date": array(["2026-04-06", "2027-04-06"], dtype="date", label="Date"),
            "event": array(["evt_year", "evt_year_post2029"], dtype="text", label="Event"),
            "changes": array(["cash: +1.00 → 1.00\ndebt: -2.00 → 8.00", "cash: +1.00 → 2.00"], dtype="text"),
        },
    )
    project.add_figure(
        id="table",
        title="Events",
        table_views=[TableViewSpec(id="events-table", data="events", columns=["date", "event", "changes"], column_formats=list(column_formats))],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    return folder


def _manifest_and_spec(folder: Path):
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        runtime = LimelightRuntime(package, manifest)
        [table_view_spec] = runtime.figure_specs["table"]["tableViewSpecs"]
    return manifest, table_view_spec


def _table_widget(folder: Path, width: int):
    from PySide6.QtWidgets import QTableWidget

    from limelight.qt_app import _populate_table_widget

    with open_limelight(folder) as package:
        runtime = LimelightRuntime(package, package.manifest_json())
        [table_view_spec] = runtime.figure_specs["table"]["tableViewSpecs"]
        preview = runtime.table_preview(
            table_view_spec["data"],
            table_view_spec.get("columns"),
            limit=table_view_row_limit(table_view_spec),
            column_formats=table_view_column_formats(table_view_spec),
        )
        table = QTableWidget()
        _populate_table_widget(table, preview, table_view_spec, runtime=runtime)
    table.resize(width, 300)
    table.show()
    table._ll_column_sizer.apply()
    return table


def _column_widths(table) -> list[int]:
    return [table.columnWidth(index) for index in range(table.columnCount())]


def test_each_kind_of_width_round_trips_and_validates(tmp_path: Path) -> None:
    manifest, table_view_spec = _manifest_and_spec(
        _package(
            tmp_path,
            ColumnFormat(column="date", width=ColumnWidth.auto()),
            ColumnFormat(column="event", width=ColumnWidth.mm(30.0)),
            ColumnFormat(column="changes", width=ColumnWidth.share(2.0)),
        )
    )

    validate_manifest_semantics(manifest)
    assert table_view_column_width(table_view_spec, "date") == ("auto", None)
    assert table_view_column_width(table_view_spec, "event") == ("mm", 30.0)
    assert table_view_column_width(table_view_spec, "changes") == ("share", 2.0)


def test_a_column_with_no_width_is_auto(tmp_path: Path) -> None:
    _, table_view_spec = _manifest_and_spec(_package(tmp_path, ColumnFormat(column="date", alignment="Center")))

    assert table_view_column_width(table_view_spec, "date") == ("auto", None)
    assert table_view_column_width(table_view_spec, "event") == ("auto", None)


@pytest.mark.parametrize("make", [lambda: ColumnWidth.mm(0.0), lambda: ColumnWidth.share(-1.0)])
def test_a_non_positive_width_is_refused_by_the_writer(make) -> None:
    with pytest.raises(ValueError, match="positive"):
        make()


def test_a_non_positive_width_in_a_manifest_is_rejected(tmp_path: Path) -> None:
    manifest, table_view_spec = _manifest_and_spec(_package(tmp_path, ColumnFormat(column="event", width=ColumnWidth.mm(30.0))))
    table_view_spec["columnFormats"][0]["width"] = {"widthMm": 0.0}
    manifest["figures"][0]["tableViewSpecs"][0] = table_view_spec

    with pytest.raises(LimelightError, match="must be positive"):
        validate_manifest_semantics(manifest)


def test_the_pdf_table_takes_its_widths_as_typst_tracks() -> None:
    from limelight.pdf_export import _column_width_typst

    assert _column_width_typst("auto", None) == "auto"
    assert _column_width_typst("mm", 30.0) == "30mm"
    assert _column_width_typst("share", 2.0) == "2fr"


def test_auto_widths_stay_natural_when_they_fit() -> None:
    from limelight.qt_app import _fit_auto_widths

    assert _fit_auto_widths([100, 50, 200], 400) == [100, 50, 200]


def test_when_auto_widths_do_not_fit_only_the_widest_are_cut() -> None:
    from limelight.qt_app import _fit_auto_widths

    assert _fit_auto_widths([100, 50, 400], 300) == [100, 50, 150]
    assert _fit_auto_widths([300, 50, 400], 350) == [150, 50, 150]


def test_in_the_app_auto_columns_fit_their_content(qt_app, tmp_path: Path) -> None:
    table = _table_widget(_package(tmp_path), width=1200)

    widths = _column_widths(table)
    # Each at its natural width, and the table shrinks around them rather than
    # leaving the rest of the 1200 px empty.
    assert widths == table._ll_column_sizer.natural
    assert table.width() < 1200
    assert widths[1] > widths[0]  # "evt_year_post2029" is wider than a date


def test_in_the_app_a_share_column_takes_what_the_others_leave(qt_app, tmp_path: Path) -> None:
    table = _table_widget(_package(tmp_path, ColumnFormat(column="changes", width=ColumnWidth.share())), width=1200)

    assert sum(_column_widths(table)) == pytest.approx(table.viewport().width(), abs=2)


def test_in_the_app_a_fixed_column_is_its_width_in_millimetres(qt_app, tmp_path: Path) -> None:
    table = _table_widget(_package(tmp_path, ColumnFormat(column="event", width=ColumnWidth.mm(40.0))), width=1200)

    expected = round(40.0 * table.logicalDpiX() / 25.4)
    assert table.columnWidth(1) == expected


def test_in_the_app_columns_are_resized_with_the_view(qt_app, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    table = _table_widget(_package(tmp_path, ColumnFormat(column="changes", width=ColumnWidth.share())), width=1200)
    wide = table.columnWidth(2)
    table.resize(700, 300)
    QApplication.processEvents()

    assert table.columnWidth(2) < wide
    assert sum(_column_widths(table)) == pytest.approx(table.viewport().width(), abs=2)


def test_a_document_table_has_no_row_numbers(qt_app, tmp_path: Path) -> None:
    table = _table_widget(_package(tmp_path), width=800)

    assert not table.verticalHeader().isVisible()


def test_a_table_keeps_page_colours_under_a_dark_theme(qt_app, tmp_path: Path) -> None:
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    original = QApplication.palette()
    dark = QPalette(original)
    for role, colour in ((QPalette.ColorRole.Base, "#2a2a2a"), (QPalette.ColorRole.Window, "#2a2a2a"), (QPalette.ColorRole.Text, "#ffffff")):
        dark.setColor(role, QColor(colour))
    QApplication.setPalette(dark)
    try:
        table = _table_widget(_package(tmp_path), width=800)

        assert table.palette().color(QPalette.ColorRole.Base).name() == "#ffffff"
        assert table.palette().color(QPalette.ColorRole.Text).name() == "#202124"
        # And it is drawn that way: a cell away from its text is white.
        image = table.viewport().grab().toImage()
        assert image.pixelColor(table.columnWidth(0) - 3, 3).name() == "#ffffff"
    finally:
        QApplication.setPalette(original)


def test_a_short_narrow_table_is_no_bigger_than_its_content(qt_app, tmp_path: Path) -> None:
    table = _table_widget(_package(tmp_path), width=1200)

    # Two rows and three auto columns: far smaller than the 1200 px it was offered.
    assert table.maximumWidth() < 1200
    assert table.maximumWidth() == pytest.approx(sum(table._ll_column_sizer.natural) + 2 * table.frameWidth(), abs=2)
    rows = sum(table.rowHeight(row) for row in range(table.rowCount()))
    assert table.maximumHeight() == pytest.approx(rows + table.horizontalHeader().height() + 2 * table.frameWidth(), abs=2)


def test_a_table_with_a_share_column_may_take_the_whole_width(qt_app, tmp_path: Path) -> None:
    table = _table_widget(_package(tmp_path, ColumnFormat(column="changes", width=ColumnWidth.share())), width=1200)

    assert table.maximumWidth() > 1200


def test_in_the_tables_tab_the_caption_sits_under_a_short_table(qt_app, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from limelight.qt_app import LimelightWindow

    with open_limelight(_package(tmp_path)) as package:
        window = LimelightWindow(package, package.manifest_json())
    try:
        window.resize(1100, 800)
        window.show()
        tabs = window.tabs
        tabs.setCurrentIndex([tabs.tabText(i) for i in range(tabs.count())].index("Tables"))
        for _ in range(10):
            QApplication.processEvents()

        table, caption = window.table_widget, window.table_caption
        assert table.height() < 200
        # Directly under the table, not pushed to the bottom of the tab.
        assert 0 <= caption.y() - (table.y() + table.height()) < 30
    finally:
        window.close()
