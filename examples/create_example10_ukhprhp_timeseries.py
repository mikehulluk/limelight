from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from limelight import (
    AxisDataType,
    Index,
    LineArtist,
    LimelightProject,
    PageGeometry,
    ScatterArtist,
    array,
)


SOURCE_CSV = (
    ROOT
    / "examples"
    / "example10_ukhprhp_data"
    / "gov.uk - Private rent and house prices"
    / "fig1_hp.csv"
)
BANK_RATE_CSV = (
    ROOT
    / "examples"
    / "example10_ukhprhp_data"
    / "bank of england interest rates"
    / "data.csv"
)
RPI_CSV = ROOT / "examples" / "example10_ukhprhp_data" / "rpi" / "series-110826.csv"
OUTPUT = ROOT / "_build" / "examples" / "example10-ukhprhp-timeseries.limelight"

MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


@dataclass(frozen=True)
class InflationRow:
    month: str
    date: str
    month_ordinal: int
    pipr: float | None
    uk_hpi: float | None


@dataclass(frozen=True)
class BankRateRow:
    date: str
    calendar_day: int
    rate: float


@dataclass(frozen=True)
class RpiRow:
    period: str
    period_kind: str
    date: str
    calendar_day: int
    index_value: float
    yoy_change: float | None


def parse_float(value: str) -> float | None:
    stripped = value.strip()
    return None if stripped == "" else float(stripped)


def parse_month(value: str) -> tuple[int, int]:
    month_text, year_text = value.strip().split()
    try:
        month = MONTHS[month_text]
    except KeyError as error:
        raise ValueError(f"Unsupported month label {value!r}") from error
    return int(year_text), month


def month_ordinal(year: int, month: int) -> int:
    return (year - 1970) * 12 + (month - 1)


def calendar_day(value: date) -> int:
    return value.toordinal()


def parse_calendar_day(value: str) -> date:
    stripped = value.strip()
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(stripped, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported CalendarDay label {value!r}")


def parse_rpi_period(value: str) -> tuple[str, str, date, tuple[object, ...]] | None:
    stripped = value.strip()
    parts = stripped.split()
    if len(parts) == 1 and parts[0].isdigit():
        year = int(parts[0])
        period_date = date(year, 1, 1)
        return (stripped, "year", period_date, ("year", year))

    if len(parts) == 2 and parts[0].isdigit():
        year = int(parts[0])
        try:
            month = MONTHS[parts[1].title()]
        except KeyError as error:
            raise ValueError(f"Unsupported RPI month label {value!r}") from error
        period_date = date(year, month, 1)
        return (stripped, "month", period_date, ("month", year, month))

    return None


def read_fig1_hp(path: Path = SOURCE_CSV) -> list[InflationRow]:
    if not path.exists():
        raise FileNotFoundError(path)

    rows: list[InflationRow] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        in_table = False
        for raw_row in reader:
            cells = [cell.strip() for cell in raw_row]
            if not cells or all(cell == "" for cell in cells):
                continue
            if cells[:3] == ["Date", "PIPR", "UK HPI"]:
                in_table = True
                continue
            if not in_table:
                continue
            if len(cells) < 3:
                continue

            year, month = parse_month(cells[0])
            rows.append(
                InflationRow(
                    month=cells[0],
                    date=f"{year:04d}-{month:02d}-01",
                    month_ordinal=month_ordinal(year, month),
                    pipr=parse_float(cells[1]),
                    uk_hpi=parse_float(cells[2]),
                )
            )

    if not rows:
        raise ValueError(f"No data rows found in {path}")
    return rows


def read_bank_rate_history(path: Path = BANK_RATE_CSV) -> list[BankRateRow]:
    if not path.exists():
        raise FileNotFoundError(path)

    rows: list[BankRateRow] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for raw_row in reader:
            date_text = (raw_row.get("date") or "").strip()
            rate_text = (raw_row.get("rate") or "").strip()
            if not date_text or not rate_text:
                continue
            parsed_date = parse_calendar_day(date_text)
            rows.append(
                BankRateRow(
                    date=parsed_date.isoformat(),
                    calendar_day=calendar_day(parsed_date),
                    rate=float(rate_text),
                )
            )

    if not rows:
        raise ValueError(f"No data rows found in {path}")
    return sorted(rows, key=lambda row: row.calendar_day)


def bank_rate_step_rows(rows: list[BankRateRow]) -> list[BankRateRow]:
    if not rows:
        return []

    stepped = [rows[0]]
    previous = rows[0]
    for row in rows[1:]:
        stepped.append(
            BankRateRow(
                date=row.date,
                calendar_day=row.calendar_day,
                rate=previous.rate,
            )
        )
        stepped.append(row)
        previous = row
    return stepped


def read_rpi_index(path: Path = RPI_CSV) -> list[RpiRow]:
    if not path.exists():
        raise FileNotFoundError(path)

    parsed_rows: list[tuple[str, str, date, tuple[object, ...], float]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        for raw_row in reader:
            cells = [cell.strip() for cell in raw_row]
            if len(cells) < 2:
                continue
            period = parse_rpi_period(cells[0])
            if period is None:
                continue
            index_value = parse_float(cells[1])
            if index_value is None:
                continue
            parsed_rows.append((*period, index_value))

    if not parsed_rows:
        raise ValueError(f"No RPI data rows found in {path}")

    parsed_rows.sort(key=lambda row: calendar_day(row[2]))
    index_by_period = {period_key: index_value for _, _, _, period_key, index_value in parsed_rows}
    normalized_rows = []
    for period, period_kind, period_date, period_key, index_value in parsed_rows:
        if period_kind == "year":
            _, year = period_key
            prior_key = ("year", int(year) - 1)
        else:
            _, year, month = period_key
            prior_key = ("month", int(year) - 1, int(month))

        prior_value = index_by_period.get(prior_key)
        yoy_change = None if prior_value in {None, 0.0} else ((index_value / prior_value) - 1.0) * 100.0
        normalized_rows.append(
            RpiRow(
                period=period,
                period_kind=period_kind,
                date=period_date.isoformat(),
                calendar_day=calendar_day(period_date),
                index_value=index_value,
                yoy_change=yoy_change,
            )
        )

    return normalized_rows


def build_project(
    rows: list[InflationRow],
    bank_rate_rows: list[BankRateRow],
    rpi_rows: list[RpiRow],
) -> LimelightProject:
    first = rows[0]
    last = rows[-1]
    first_bank_rate = bank_rate_rows[0]
    last_bank_rate = bank_rate_rows[-1]
    bank_rate_steps = bank_rate_step_rows(bank_rate_rows)
    rpi_yoy_rows = [row for row in rpi_rows if row.yoy_change is not None]
    if not rpi_yoy_rows:
        raise ValueError("No year-on-year RPI rows could be calculated")
    first_rpi = rpi_yoy_rows[0]
    last_rpi = rpi_yoy_rows[-1]
    project = LimelightProject(
        title="UK private rent, house prices, and interest rates",
        subtitle="Monthly rent and house price inflation with Bank Rate and RPI context",
        description=(
            "An example built from ONS private rent and house price data, plus Bank of England "
            "Bank Rate history and ONS RPI data. Bank Rate and RPI dates are normalized to "
            "CalendarDay coordinates during import."
        ),
        authors=["Limelight examples"],
        page=PageGeometry.paged(margin_lr_mm=10.0),
        document_version="0.1",
    )

    project.add_csv_dataset(
        id="uk-house-price-rent-inflation",
        title="Private rent and house price annual inflation",
        arrays={
            "month": array(
                [row.month for row in rows],
                dtype="text",
                label="Month",
                description="Original calendar-period label from the source CSV",
            ),
            "date": array(
                [row.date for row in rows],
                dtype="date",
                label="Month start",
                description="Month-start date used for display and future calendar-basis migration",
            ),
            "month-ordinal": array(
                [row.month_ordinal for row in rows],
                dtype="integer",
                label="Month ordinal",
                description="Months since January 1970; Jan 2016 is 552",
            ),
            "pipr": array(
                [row.pipr for row in rows],
                dtype="double",
                label="PIPR",
                unit="%",
                description="Private rent annual inflation",
                nullable=True,
            ),
            "uk-hpi": array(
                [row.uk_hpi for row in rows],
                dtype="double",
                label="UK HPI",
                unit="%",
                description="UK house price annual inflation",
                nullable=True,
            ),
        },
        index=Index.irregular_index_array(coordinate_array="month-ordinal"),
    )

    project.add_csv_dataset(
        id="bank-rate-history",
        title="Bank of England Bank Rate history",
        arrays={
            "date": array(
                [row.date for row in bank_rate_rows],
                dtype="date",
                label="Date",
                description="Normalized ISO date label for the Bank Rate CalendarDay",
            ),
            "calendar-day": array(
                [row.calendar_day for row in bank_rate_rows],
                dtype="natural",
                label="CalendarDay",
                description="Proleptic Gregorian CalendarDay ordinal from the normalized rate-change date",
            ),
            "rate": array(
                [row.rate for row in bank_rate_rows],
                dtype="double",
                label="Bank Rate",
                unit="%",
                description="Bank of England Bank Rate",
            ),
            "dot-size": array(
                [22.0 for _ in bank_rate_rows],
                dtype="double",
                label="Dot size",
                description="Marker area used by the example scatter artist",
            ),
        },
        index=Index.irregular_index_array(coordinate_array="calendar-day"),
    )

    project.add_csv_dataset(
        id="bank-rate-history-step",
        title="Bank of England Bank Rate history as steps",
        arrays={
            "date": array(
                [row.date for row in bank_rate_steps],
                dtype="date",
                label="Date",
                description="Normalized ISO date label for the Bank Rate CalendarDay",
            ),
            "calendar-day": array(
                [row.calendar_day for row in bank_rate_steps],
                dtype="natural",
                label="CalendarDay",
                description="Proleptic Gregorian CalendarDay ordinal used for step-line coordinates",
            ),
            "rate": array(
                [row.rate for row in bank_rate_steps],
                dtype="double",
                label="Bank Rate",
                unit="%",
                description="Bank of England Bank Rate, expanded into horizontal step segments",
            ),
        },
        index=Index.irregular_index_array(coordinate_array="calendar-day"),
    )

    project.add_csv_dataset(
        id="uk-rpi-index",
        title="UK Retail Prices Index year-on-year change",
        arrays={
            "period": array(
                [row.period for row in rpi_rows],
                dtype="text",
                label="Source period",
                description="Original RPI period label from the source CSV",
            ),
            "period-kind": array(
                [row.period_kind for row in rpi_rows],
                dtype="text",
                label="Period kind",
                description="Whether the source RPI value is annual or monthly",
            ),
            "date": array(
                [row.date for row in rpi_rows],
                dtype="date",
                label="Date",
                description="Normalized ISO period-start date for the RPI CalendarDay",
            ),
            "calendar-day": array(
                [row.calendar_day for row in rpi_rows],
                dtype="natural",
                label="CalendarDay",
                description="Proleptic Gregorian CalendarDay ordinal from the normalized RPI period",
            ),
            "rpi-index": array(
                [row.index_value for row in rpi_rows],
                dtype="double",
                label="RPI index",
                description="Retail Prices Index value from the source CSV",
            ),
            "yoy-change": array(
                [row.yoy_change for row in rpi_rows],
                dtype="double",
                label="RPI year-on-year change",
                unit="%",
                description="Year-on-year percentage change from the comparable prior period",
                nullable=True,
            ),
            "annual-yoy-change": array(
                [row.yoy_change if row.period_kind == "year" else None for row in rpi_rows],
                dtype="double",
                label="Annual RPI year-on-year change",
                unit="%",
                description="Annual RPI year-on-year percentage change",
                nullable=True,
            ),
            "monthly-yoy-change": array(
                [row.yoy_change if row.period_kind == "month" else None for row in rpi_rows],
                dtype="double",
                label="Monthly RPI year-on-year change",
                unit="%",
                description="Monthly RPI year-on-year percentage change against the same month one year earlier",
                nullable=True,
            ),
        },
        index=Index.irregular_index_array(coordinate_array="calendar-day"),
    )

    project.add_line_figure(
        id="inflation-timeseries",
        title="Private rent and house price annual inflation",
        data="uk-house-price-rent-inflation",
        x="month-ordinal",
        y=[
            LineArtist("pipr", label="PIPR"),
            LineArtist("uk-hpi", label="UK HPI"),
        ],
        caption=(
            "Monthly annual inflation rates from the ONS Figure 1 CSV. The final UK HPI "
            "observation is blank in the source and is stored as null."
        ),
        x_axis=AxisDataType.time_series(label="Month", calendar="monthOrdinal1970", share_group="calendar-month"),
        y_axis=AxisDataType.continuous(label="Annual inflation", unit="%"),
    )

    project.add_line_figure(
        id="bank-rate-history",
        title="Historic Bank of England interest rate",
        data="bank-rate-history-step",
        x="calendar-day",
        y=[LineArtist("rate", label="Bank Rate", id="bank-rate-step-line")],
        scatter=[
            ScatterArtist(
                "rate",
                id="bank-rate-change-points",
                data="bank-rate-history",
                size_by="dot-size",
            )
        ],
        caption=(
            "Bank of England Bank Rate changes from the source CSV. The line is expanded "
            "into steps and dots mark the recorded change dates."
        ),
        x_axis=AxisDataType.time_series(label="CalendarDay", calendar="CalendarDay", share_group="bank-rate-date"),
        y_axis=AxisDataType.continuous(label="Bank Rate", unit="%"),
    )

    project.add_line_figure(
        id="uk-rpi-yoy-change",
        title="UK RPI year-on-year change",
        data="uk-rpi-index",
        x="calendar-day",
        y=[
            LineArtist("annual-yoy-change", label="Annual RPI YoY", id="annual-rpi-yoy-line"),
            LineArtist("monthly-yoy-change", label="Monthly RPI YoY", id="monthly-rpi-yoy-line"),
        ],
        caption=(
            "Year-on-year percentage change calculated from the RPI index source. "
            "Annual values compare with the prior year; monthly values compare with the same month one year earlier."
        ),
        x_axis=AxisDataType.time_series(label="CalendarDay", calendar="CalendarDay", share_group="rpi-calendar-day"),
        y_axis=AxisDataType.continuous(label="Year-on-year change", unit="%"),
    )




    project.set_story_markdown(
        f"""
# UK Private Rent and House Prices

## 2026 July

This example reads `fig1_hp.csv`, an ONS-style CSV with a prose preamble followed by a monthly table.

The table runs from **{first.month}** to **{last.month}**. This generator keeps the original month labels and adds a numeric month ordinal, looked up via an irregular index array, that the plot formats as calendar months.

{project.story_figure("inflation-timeseries")}

# Bank of England Interest Rate

This figure reads `data.csv` from the Bank of England interest-rate source folder.

The import normalizes each source date to `CalendarDay`. The table runs from **{first_bank_rate.date}** to **{last_bank_rate.date}**. The plotted line is expanded into horizontal steps, with dots marking each recorded Bank Rate change.

{project.story_figure("bank-rate-history")}

# UK RPI Index

This figure reads `series-110826.csv` from the RPI source folder.

The import normalizes each source period to `CalendarDay` and calculates year-on-year percentage change. Annual rows compare with the prior year; monthly rows compare with the same month one year earlier. The calculated range runs from **{first_rpi.date}** to **{last_rpi.date}**.

{project.story_figure("uk-rpi-yoy-change")}
""",
        base_dir=Path(__file__).parent,
    )

    return project


def main() -> None:
    rows = read_fig1_hp()
    bank_rate_rows = read_bank_rate_history()
    rpi_rows = read_rpi_index()
    build_project(rows, bank_rate_rows, rpi_rows).write_folder(OUTPUT, overwrite=True)
    print(OUTPUT)


if __name__ == "__main__":
    main()
