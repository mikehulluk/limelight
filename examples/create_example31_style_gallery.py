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
    FormSpec,
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
    mpl_subplots,
)

REGIONS = ["North", "South", "East", "West"]
# The rows of the discrete y-axis, from the bottom up; "audit" never happens.
SCHEDULED_EVENTS = {"payment": range(0, 24), "interest": range(0, 24, 3), "review": [5, 17], "audit": []}
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

    # The figures are ordinary matplotlib calls, each bound by `dataset_id`
    # (and its columns' names) to the dataset declared above, so nothing is
    # copied. The categorical colorBy and the calendar-month axis below have
    # no matplotlib equivalent and are written out with the builders.
    time = oscillations["time"]
    fig, ax = mpl_subplots()
    ax.plot(time, oscillations["fast-decay"], color="crimson", linestyle="-", label="Fast decay", name="fast-decay", x_name="time")
    ax.plot(time, oscillations["medium-decay"], color="#1f77b4", linestyle="--", label="Medium decay", name="medium-decay", x_name="time")
    ax.plot(time, oscillations["slow-decay"], linestyle=":", label="Slow decay", name="slow-decay", x_name="time")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Amplitude")
    ax.legend()
    project.add_figure(
        fig.configure(
            id="explicit-styling",
            title="Explicit color and linestyle",
            caption=(
                "Three damped oscillations with the same frequency but different decay rates, styled "
                "with an explicit named color, an explicit hex color, and the default color cycle."
            ),
            dataset_id="damped-oscillations",
            forms=[
                FormSpec(
                    controls=[DropdownCtrlSpec(id="highlight-region-control", control_parameter="highlight-region")],
                    title="Controls",
                    caption="Not wired to the plot; demonstrates a titled control panel.",
                )
            ],
        )
    )

    fig, ax = mpl_subplots()
    temperature = measurements["temperature-c"]
    # Registered by name first, so the size column is the declared one.
    fig.dataset.register(measurements["sample-volume-ml"], name="sample-volume-ml")
    points = ax.scatter(
        measurements["depth-m"], temperature, c=temperature, s=measurements["sample-volume-ml"],
        label="Measurements", name="temperature-c", x_name="depth-m",
    )
    fig.colorbar(points, ax=ax, label="temperature-c")
    ax.set_xlabel("Depth (m)")
    ax.set_ylabel("Temperature (C)")
    ax.legend()
    project.add_figure(
        fig.configure(
            id="continuous-color-by",
            title="Continuous colorBy with size legend",
            caption=(
                "Each point's color encodes temperature on a continuous colormap (with a colorbar), "
                "and its size encodes sample volume (with a size legend)."
            ),
            dataset_id="depth-measurements",
        )
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

    fig, ax = mpl_subplots()
    ax.plot(region_names, region_temperatures, label="Mean temperature", name="mean-temperature-c", x_name="region")
    ax.set_xlabel("Region")
    ax.set_ylabel("Mean temperature (C)")
    ax.legend()
    project.add_figure(
        fig.configure(
            id="discrete-region-axis",
            title="Mean temperature by region",
            caption=(
                "A discrete x-axis plots one point per named category, in first-appearance order, "
                "instead of a numeric coordinate."
            ),
            dataset_id="region-mean-temperature",
        )
    )

    # Each kind of event gets a row, in the order declared here - "audit"
    # included, though nothing happens on it - as matplotlib's own categorical
    # axis holds them.
    fig, ax = mpl_subplots()
    ax.yaxis.update_units(list(SCHEDULED_EVENTS))
    for event, months in SCHEDULED_EVENTS.items():
        if months:
            ax.plot([float(month) for month in months], [event] * len(months), linestyle="none", marker="x", label=event)
    ax.set_xlabel("Month")
    ax.set_ylabel("Event")
    ax.legend()
    project.add_figure(
        fig.configure(
            id="discrete-event-axis",
            title="Scheduled events",
            caption=(
                "A discrete y-axis gives each event a row, in the order its categories are declared; "
                "a declared category nothing draws keeps its row. The lines draw their markers alone."
            ),
        )
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

## Discrete y-axis

A discrete y-axis gives each category a row. Its categories can be declared, which fixes the order
of the rows and refuses any other value; otherwise they follow first appearance across the panel's
artists. With `linestyle="none"`, a line artist draws its markers alone, as a raster of events.

{project.story_figure("discrete-event-axis")}

## Calendar month axis with UTC-time limits

A dataset can use a regular calendar Index (`Index.regular_calendar(unit="month", ...)`) instead
of an explicit x column, converting month-since-epoch offsets into real calendar dates. Combined
with a `timeSeries` axis declaring `calendar="CalendarDay"`, the ticks land on real month
boundaries, and `axes_action_set_x_utc_time_limits`/`axes_action_add_utc_time_window_decorator`
set limits and decorators with plain ISO date strings instead of numeric coordinates. The
`axes_action_add_rect_decorator` box takes its x bounds as dates on this axis and its y bounds
as revenue, and shades the region without changing what the axes show.

{project.story_figure("monthly-revenue-over-time", actions=revenue_actions)}
""",
        base_dir=Path(__file__).parent,
    )

    return project


def main() -> None:
    output = ROOT / "_build" / "examples" / "example31-style-gallery.limelight"
    build_project().write_folder(output, overwrite=True)
    print(output)


if __name__ == "__main__":
    main()
