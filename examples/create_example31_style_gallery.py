from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from limelight import (
    AxisDataType,
    DropdownCtrlSpec,
    Index,
    LineArtist,
    LimelightProject,
    PageGeometry,
    ScatterArtist,
    array,
    axes_action_add_rect_decorator,
    axes_action_add_utc_time_window_decorator,
    axes_action_set_x_utc_time_limits,
    figure_view_action,
)

REGIONS = ["North", "South", "East", "West"]
MONTH_2024_ORDINAL = (2024 - 1) * 12  # months since the proleptic-Gregorian epoch, 0-based


def region_mean_temperature(measurements: dict[str, list[float] | list[str]]) -> tuple[list[str], list[float]]:
    totals: dict[str, list[float]] = {region: [] for region in REGIONS}
    for region, temperature in zip(measurements["region"], measurements["temperature-c"]):
        totals[region].append(temperature)
    return REGIONS, [sum(totals[region]) / len(totals[region]) for region in REGIONS]


def build_damped_oscillations() -> dict[str, list[float]]:
    rng = random.Random(7)
    sample_count = 220
    time = [index * 0.05 for index in range(sample_count)]
    signals: dict[str, list[float]] = {"time": time}
    for name, decay, frequency_hz, noise_sd in (
        ("fast-decay", 1.4, 1.0, 0.02),
        ("medium-decay", 0.6, 1.0, 0.02),
        ("slow-decay", 0.15, 1.0, 0.02),
    ):
        signals[name] = [
            math.exp(-decay * t) * math.sin(2.0 * math.pi * frequency_hz * t) + rng.gauss(0.0, noise_sd)
            for t in time
        ]
    return signals


def build_measurement_scatter() -> dict[str, list[float] | list[str]]:
    rng = random.Random(11)
    point_count = 60
    depth_m = [round(rng.uniform(0.0, 100.0), 2) for _ in range(point_count)]
    temperature_c = [round(22.0 - 0.15 * depth + rng.gauss(0.0, 0.6), 2) for depth in depth_m]
    sample_volume_ml = [round(rng.uniform(5.0, 45.0), 1) for _ in range(point_count)]
    region = [REGIONS[index % len(REGIONS)] for index in range(point_count)]
    return {
        "depth-m": depth_m,
        "temperature-c": temperature_c,
        "sample-volume-ml": sample_volume_ml,
        "region": region,
    }


def build_monthly_revenue() -> list[float]:
    rng = random.Random(3)
    base = 100.0
    return [round(base + 8.0 * month + rng.gauss(0.0, 4.0), 1) for month in range(12)]


def build_project() -> LimelightProject:
    oscillations = build_damped_oscillations()
    measurements = build_measurement_scatter()
    region_names, region_temperatures = region_mean_temperature(measurements)
    monthly_revenue = build_monthly_revenue()

    project = LimelightProject(
        title="Plot styling gallery",
        subtitle="Explicit colors, markers, linestyles, colorBy, and size legends",
        description=(
            "A small synthetic gallery demonstrating explicit artist styling: named/hex colors, "
            "line styles, markers, a continuous colorBy with a colorbar and size legend, and a "
            "categorical colorBy with per-category legend entries."
        ),
        authors=["Limelight examples"],
        page=PageGeometry.paged(margin_lr_mm=5.0),
        document_version="0.1",
    )

    project.add_csv_dataset(
        id="damped-oscillations",
        title="Damped oscillations",
        arrays={
            name: array(values, dtype="double")
            for name, values in oscillations.items()
        },
        index=Index.irregular_index_array(coordinate_array="time", unit="s"),
    )
    project.add_csv_dataset(
        id="depth-measurements",
        title="Depth measurements",
        arrays={
            "depth-m": array(measurements["depth-m"], dtype="double", label="Depth", unit="m"),
            "temperature-c": array(measurements["temperature-c"], dtype="double", label="Temperature", unit="C"),
            "sample-volume-ml": array(measurements["sample-volume-ml"], dtype="double", label="Sample volume", unit="mL"),
            "region": array(measurements["region"], dtype="category", label="Region"),
        },
        index=Index.no_index(),
    )
    project.add_csv_dataset(
        id="region-mean-temperature",
        title="Region mean temperature",
        arrays={
            "region": array(region_names, dtype="category", label="Region"),
            "mean-temperature-c": array(region_temperatures, dtype="double", label="Mean temperature", unit="C"),
        },
        index=Index.no_index(),
    )

    project.add_csv_dataset(
        id="monthly-revenue",
        title="Monthly revenue",
        arrays={
            "revenue": array(monthly_revenue, dtype="double", label="Revenue", unit="USD"),
        },
        index=Index.regular_calendar(
            calendar="prolepticGregorian",
            unit="month",
            start_ordinal=MONTH_2024_ORDINAL,
            step=1,
            render_anchor="periodStart",
        ),
    )

    project.add_discrete_control_parameter(
        id="highlight-region",
        label="Highlighted region",
        options=[(region, region) for region in REGIONS],
        default=REGIONS[0],
    )

    project.add_line_figure(
        id="explicit-styling",
        title="Explicit color and linestyle",
        data="damped-oscillations",
        x="time",
        y=[
            LineArtist("fast-decay", label="Fast decay", color="crimson", linestyle="-"),
            LineArtist("medium-decay", label="Medium decay", color="#1f77b4", linestyle="--"),
            LineArtist("slow-decay", label="Slow decay", linestyle=":"),
        ],
        caption=(
            "Three damped oscillations with the same frequency but different decay rates, styled "
            "with an explicit named color, an explicit hex color, and the default color cycle."
        ),
        x_axis=AxisDataType.continuous(label="Time", unit="s"),
        y_axis=AxisDataType.continuous(label="Amplitude"),
        controls=[DropdownCtrlSpec(id="highlight-region-control", control_parameter="highlight-region")],
        form_title="Controls",
        form_caption="Not wired to the plot; demonstrates a titled control panel.",
    )

    project.add_line_figure(
        id="continuous-color-by",
        title="Continuous colorBy with size legend",
        data="depth-measurements",
        x="depth-m",
        y=[],
        scatter=[
            ScatterArtist(
                array="temperature-c",
                label="Measurements",
                color_by="temperature-c",
                size_by="sample-volume-ml",
            )
        ],
        caption=(
            "Each point's color encodes temperature on a continuous colormap (with a colorbar), "
            "and its size encodes sample volume (with a size legend)."
        ),
        x_axis=AxisDataType.continuous(label="Depth", unit="m"),
        y_axis=AxisDataType.continuous(label="Temperature", unit="C"),
    )

    project.add_line_figure(
        id="categorical-color-by",
        title="Categorical colorBy with markers",
        data="depth-measurements",
        x="depth-m",
        y=[],
        scatter=[
            ScatterArtist(
                array="temperature-c",
                label="Measurements",
                color_by="region",
                marker="^",
            )
        ],
        caption=(
            "Each point's color encodes its region as a category, with one legend entry per region "
            "and a non-default triangle marker."
        ),
        x_axis=AxisDataType.continuous(label="Depth", unit="m"),
        y_axis=AxisDataType.continuous(label="Temperature", unit="C"),
    )

    project.add_line_figure(
        id="discrete-region-axis",
        title="Mean temperature by region",
        data="region-mean-temperature",
        x="region",
        y=[("mean-temperature-c", "Mean temperature")],
        caption=(
            "A discrete x-axis plots one point per named category, in first-appearance order, "
            "instead of a numeric coordinate."
        ),
        x_axis=AxisDataType.discrete(label="Region"),
        y_axis=AxisDataType.continuous(label="Mean temperature", unit="C"),
    )

    project.add_line_figure(
        id="monthly-revenue-over-time",
        title="Monthly revenue",
        data="monthly-revenue",
        x="",
        y=[("revenue", "Revenue")],
        caption=(
            "The x-axis comes from the dataset's regular calendar Index (one row per month of "
            "2024), not from an explicit x column."
        ),
        x_axis=AxisDataType.time_series(label="Month", calendar="CalendarDay"),
        y_axis=AxisDataType.continuous(label="Revenue", unit="USD"),
    )
    revenue_actions = [
        figure_view_action(
            "monthly-revenue-over-time",
            axes_action_set_x_utc_time_limits("2024-01-01", "2024-12-31"),
        ),
        figure_view_action(
            "monthly-revenue-over-time",
            axes_action_add_utc_time_window_decorator(start="2024-04-01", end="2024-06-30", label="Q2"),
        ),
        figure_view_action(
            "monthly-revenue-over-time",
            axes_action_add_rect_decorator(
                x_lower="2024-07-01",
                x_upper="2024-09-30",
                y_lower=150.0,
                y_upper=170.0,
                label="Q3 target",
                color="#2a9d8f",
                alpha=0.25,
            ),
        ),
    ]

    project.set_story_markdown(
        f"""
# Plot Styling Gallery

This example demonstrates the explicit styling controls available on plot artists: `color`,
`marker`, `linestyle`, `colorBy`, and the size legend for `sizeBy`.

## Explicit color and linestyle

Line artists can set an explicit `color` (a named matplotlib color or a hex string) and
`linestyle` (`"-"`, `"--"`, `"-."`, or `":"`). A line with neither falls back to the default
color cycle and a solid line.

{project.story_figure("explicit-styling")}

## Continuous colorBy

Scatter artists can set `colorBy` to a numeric column. Limelight detects the column is numeric and
maps it through a colormap, adding a colorbar. Combined with `sizeBy`, a size legend is also
shown.

{project.story_figure("continuous-color-by")}

## Categorical colorBy

When `colorBy` names a text/category column instead, Limelight draws one differently-colored group
per distinct value, each with its own legend entry. The `marker` field selects a non-default
marker shape.

{project.story_figure("categorical-color-by")}

## Discrete x-axis

A discrete x-axis places one point per named category (in first-appearance order) instead of a
numeric coordinate. The first figure above also shows a titled control panel above its plot.

{project.story_figure("discrete-region-axis")}

## Calendar month axis with UTC-time limits

A dataset can use a regular calendar Index (`Index.regular_calendar(unit="month", ...)`) instead
of an explicit x column, converting month-since-epoch offsets into real calendar dates. Combined
with a `timeSeries` axis declaring `calendar="CalendarDay"`, the ticks land on real month
boundaries, and `axes_action_set_x_utc_time_limits`/`axes_action_add_utc_time_window_decorator`
set limits and decorators with plain ISO date strings instead of numeric coordinates. The
`axes_action_add_rect_decorator` box takes its x bounds as dates on this axis and its y bounds
as revenue, and shades the region without changing what the axes show.

{project.story_figure("monthly-revenue-over-time", actions=revenue_actions)}
"""
    )

    return project


def main() -> None:
    output = ROOT / "_build" / "examples" / "example31-style-gallery.limelight"
    build_project().write_folder(output, overwrite=True)
    print(output)


if __name__ == "__main__":
    main()
