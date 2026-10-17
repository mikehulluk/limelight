"""Table views: what a table shows, cell by cell and header by header.

CSV cells are read as text, so a table types them by their array's dtype
before formatting and aligning them; headers come from the arrays' labels
and units; and a table view can say how many rows it shows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from limelight.app import LimelightRuntime, table_view_column_alignment, table_view_column_formats, table_view_row_limit
from limelight.reader import LimelightError, open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.writer import ColumnFormat, LimelightProject, TableViewSpec, array


def _package(tmp_path: Path, rows: int = 5, **table_view: object) -> Path:
    project = LimelightProject(title="Tables", authors=["Test"])
    project.add_csv_dataset(
        id="income",
        arrays={
            "date": array([f"2026-04-{6 + day:02d}" for day in range(rows)], dtype="date", label="Date"),
            "amount": array([1234.5 * day for day in range(rows)], dtype="double", label="Income", unit="GBP"),
            "count": array(list(range(rows)), dtype="natural"),
        },
    )
    spec = {"id": "income-table", "data": "income"}
    spec.update(table_view)
    project.add_figure(id="table", title="Income", table_views=[TableViewSpec(**spec)])
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    return folder


def _preview(folder: Path):
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        runtime = LimelightRuntime(package, manifest)
        [table_view_spec] = runtime.figure_specs["table"]["tableViewSpecs"]
        preview = runtime.table_preview(
            table_view_spec["data"],
            table_view_spec.get("columns"),
            limit=table_view_row_limit(table_view_spec),
            column_formats=table_view_column_formats(table_view_spec),
        )
    return manifest, table_view_spec, preview


def test_a_table_only_figure_needs_no_panels_and_validates(tmp_path: Path) -> None:
    manifest, _, _ = _preview(_package(tmp_path))

    validate_manifest_semantics(manifest)


def test_a_format_spec_applies_to_a_numeric_csv_column(tmp_path: Path) -> None:
    _, _, preview = _preview(_package(tmp_path, columns=["date", "amount"], column_formats=[ColumnFormat(column="amount", format=",.2f")]))

    assert [row[1] for row in preview.rows[:3]] == ["0.00", "1,234.50", "2,469.00"]


def test_numeric_columns_are_known_by_dtype_and_aligned_right(tmp_path: Path) -> None:
    _, table_view_spec, preview = _preview(_package(tmp_path, columns=["date", "amount", "count"]))

    assert preview.column_kinds == ["text", "numeric", "numeric"]
    alignments = [table_view_column_alignment(table_view_spec, preview, index) for index in range(3)]
    assert alignments == ["Left", "Right", "Right"]
    assert preview.rows[2] == ["2026-04-08", "2469.0", "2"]


def test_headers_carry_the_label_and_unit_and_columns_keep_the_names(tmp_path: Path) -> None:
    _, _, preview = _preview(_package(tmp_path, columns=["date", "amount", "count"]))

    assert preview.columns == ["date", "amount", "count"]
    assert preview.headers == ["Date", "Income (GBP)", "count"]


def test_without_a_column_list_the_row_number_column_is_headed_by_name(tmp_path: Path) -> None:
    _, _, preview = _preview(_package(tmp_path))

    assert preview.columns[0] == "index"
    assert preview.headers[0] == "index"


def test_row_limit_round_trips_and_caps_the_rows(tmp_path: Path) -> None:
    manifest, table_view_spec, preview = _preview(_package(tmp_path, rows=10, row_limit=4))

    assert table_view_spec["rowLimit"] == 4
    assert len(preview.rows) == 4
    assert preview.total_rows == 10
    assert preview.truncated


def test_without_a_row_limit_the_default_applies(tmp_path: Path) -> None:
    _, table_view_spec, _ = _preview(_package(tmp_path))

    assert table_view_row_limit(table_view_spec) == 2000


def test_a_row_limit_above_the_default_shows_every_row(tmp_path: Path) -> None:
    _, _, preview = _preview(_package(tmp_path, rows=2500, row_limit=2500))

    assert len(preview.rows) == 2500
    assert not preview.truncated


def test_a_zero_row_limit_is_rejected(tmp_path: Path) -> None:
    manifest, _, _ = _preview(_package(tmp_path, row_limit=0))

    with pytest.raises(LimelightError, match="rowLimit"):
        validate_manifest_semantics(manifest)


def test_a_multi_line_cell_is_set_on_several_lines_in_the_pdf() -> None:
    from limelight.pdf_export import _cell_typst

    assert _cell_typst("cash: 1.00\ndebt: 2.00") == "cash: 1\\.00 \\ debt: 2\\.00"
    assert _cell_typst("one line") == "one line"
