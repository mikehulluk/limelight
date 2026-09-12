from __future__ import annotations

import math
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
    TextControlParameterMatch,
    array,
)


FIGURE_COUNT = 30
POINT_COUNT = 10_000
SCENARIOS = [
    ("baseline", "Baseline"),
    ("shock", "Shock event"),
    ("drift", "Long drift"),
    ("cycle", "Nested cycle"),
]
CHAPTER_SIZE = 6


def deterministic_jitter(point_index: int, figure_index: int, scenario_index: int) -> float:
    raw = math.sin(
        point_index * 12.9898
        + figure_index * 78.233
        + scenario_index * 37.719
    ) * 43758.5453
    return raw - math.floor(raw) - 0.5


def scenario_offset(
    *,
    x_value: float,
    figure_index: int,
    scenario_index: int,
) -> float:
    normalized_x = x_value / float(POINT_COUNT - 1)
    if scenario_index == 0:
        return 0.0

    if scenario_index == 1:
        center = 0.32 + 0.012 * (figure_index % 9)
        rise = 1.0 / (1.0 + math.exp(-(normalized_x - center) * 85.0))
        fall = 1.0 / (1.0 + math.exp(-(normalized_x - center - 0.12) * 85.0))
        return 1.45 * (rise - fall)

    if scenario_index == 2:
        drift = (normalized_x - 0.5) * (1.4 + 0.025 * figure_index)
        wobble = 0.28 * math.sin(normalized_x * math.tau * (2.0 + figure_index % 4))
        return drift + wobble

    nested_frequency = 1.5 + (figure_index % 6) * 0.4
    return 0.68 * math.sin(normalized_x * math.tau * nested_frequency + normalized_x * normalized_x * 8.0)


def rolling_mean(values: list[float], window: int) -> list[float]:
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)

    smoothed = []
    for index in range(len(values)):
        lower = max(0, index + 1 - window)
        sample_count = index + 1 - lower
        smoothed.append((prefix[index + 1] - prefix[lower]) / sample_count)
    return smoothed


def build_series(figure_index: int) -> dict[str, list[float]]:
    x_values = [float(index) for index in range(POINT_COUNT)]
    point_sizes = [
        7.0 + 9.0 * (0.5 + 0.5 * math.sin(index * 0.007 + figure_index * 0.41))
        for index in range(POINT_COUNT)
    ]
    arrays: dict[str, list[float]] = {
        "sample": x_values,
        "point-size": point_sizes,
    }

    amplitude = 1.0 + figure_index * 0.035
    slow_frequency = 0.0048 + (figure_index % 7) * 0.00055
    fast_frequency = 0.019 + (figure_index % 5) * 0.0017
    phase = figure_index * 0.23

    for scenario_index, (scenario_id, _) in enumerate(SCENARIOS):
        values = []
        for point_index, x_value in enumerate(x_values):
            carrier = amplitude * math.sin(x_value * slow_frequency + phase)
            harmonic = 0.42 * math.cos(x_value * fast_frequency + phase * 0.5)
            modulation = 0.18 * math.sin(x_value * 0.0009 * (figure_index % 8 + 1))
            jitter = 0.055 * deterministic_jitter(point_index, figure_index, scenario_index)
            values.append(
                carrier
                + harmonic
                + modulation
                + scenario_offset(
                    x_value=x_value,
                    figure_index=figure_index,
                    scenario_index=scenario_index,
                )
                + jitter
            )

        arrays[f"value-{scenario_id}"] = values
        arrays[f"rolling-{scenario_id}"] = rolling_mean(values, 75 + (figure_index % 5) * 10)

    return arrays


def add_parameters(project: LimelightProject) -> DropdownCtrlSpec:
    project.add_discrete_control_parameter(
        id="perf-scenario",
        label="Scenario",
        options=[(scenario_id, label) for scenario_id, label in SCENARIOS],
        default=SCENARIOS[0][0],
    )
    return DropdownCtrlSpec(
        id="perf-scenario-control",
        control_parameter="perf-scenario",
        label="Scenario",
    )


def add_sources_and_figures(project: LimelightProject, scenario_control: DropdownCtrlSpec) -> list[str]:
    figure_ids = []
    for figure_number in range(1, FIGURE_COUNT + 1):
        figure_index = figure_number - 1
        source_id = f"perf-series-{figure_number:02d}"
        figure_id = f"performance-figure-{figure_number:02d}"
        generated = build_series(figure_index)

        project.add_csv_dataset(
            id=source_id,
            title=f"Performance series {figure_number:02d}",
            arrays={
                name: array(
                    values,
                    dtype="double",
                    label=name.replace("-", " ").title(),
                )
                for name, values in generated.items()
            },
            index=Index.no_index(),
        )

        lines = []
        scatters = []
        for scenario_id, scenario_label in SCENARIOS:
            match = TextControlParameterMatch(control_parameter="perf-scenario", value=scenario_id)
            lines.append(
                LineArtist(
                    f"value-{scenario_id}",
                    label=f"{scenario_label} raw",
                    id=f"{figure_id}-{scenario_id}-raw",
                    alpha=0.55,
                    visible_when=match,
                )
            )
            lines.append(
                LineArtist(
                    f"rolling-{scenario_id}",
                    label=f"{scenario_label} rolling mean",
                    id=f"{figure_id}-{scenario_id}-rolling",
                    alpha=0.95,
                    visible_when=match,
                )
            )
            scatters.append(
                ScatterArtist(
                    f"value-{scenario_id}",
                    label=f"{scenario_label} markers",
                    id=f"{figure_id}-{scenario_id}-markers",
                    size_by="point-size",
                    visible_when=match,
                )
            )

        project.add_line_figure(
            id=figure_id,
            title=f"Performance figure {figure_number:02d}",
            data=source_id,
            x="sample",
            y=lines,
            scatter=scatters,
            caption=(
                f"Fake 10,000-point time-series for performance figure {figure_number:02d}. "
                "The shared Scenario control is intentionally reused by every figure."
            ),
            x_axis=AxisDataType.continuous(
                label="Sample",
                share_group="performance-sample",
            ),
            y_axis=AxisDataType.continuous(
                label=f"Signal {figure_number:02d}",
                unit="a.u.",
            ),
            controls=[scenario_control],
        )
        figure_ids.append(figure_id)

    return figure_ids


def chapter_markdown(chapter_number: int, figure_ids: list[str], scenario_label: str) -> str:
    first = figure_ids[0].replace("performance-figure-", "")
    last = figure_ids[-1].replace("performance-figure-", "")
    return f"""
This chapter renders figures {first}-{last} together, each backed by a separate 10,000-row CSV source.

All plots share the same `perf-scenario` dropdown. Selecting a scenario in one figure changes the visible artists everywhere because the control writes to one shared Limelight parameter.

The active story preset starts this chapter in the **{scenario_label}** scenario. The synthetic signal combines:

$$
y_i(t) = A_i\\sin(\\omega_i t + \\phi_i) + B_i\\cos(\\nu_i t) + s(t) + \\epsilon_i(t)
$$

where $s(t)$ is the selected fake scenario and $\\epsilon_i(t)$ is deterministic jitter.
""".strip()


def figure_markdown(figure_id: str, figure_number: int) -> str:
    return f"""
`{figure_id}` is a single-figure stress page. It redraws one 10,000-point source with two visible line artists and one visible scatter artist for the selected scenario.

Use this page to compare against the chapter pages, which render six figures at once. With `limelight --debug-timing`, the log records CSV loading, point extraction, Matplotlib artist construction, markdown rendering, and story-step rendering times.
""".strip()


def add_story(project: LimelightProject, figure_ids: list[str]) -> None:
    sections = [
        f"""
# Performance Test Overview

This package is intentionally synthetic and heavy. It contains **{FIGURE_COUNT} figures**, each with **{POINT_COUNT:,} samples**, controlled by a shared Scenario dropdown.

The first screen renders three representative figures. Chapter pages render six figures at a time, and detail pages render one figure at a time. This makes it useful for comparing full story transitions, individual figure redraws, markdown rendering, and cached CSV reads.

Run with:

```text
limelight --debug-timing _build/examples/example90-performance-test.limelight
```
""".strip(),
    ]

    for chapter_index in range(FIGURE_COUNT // CHAPTER_SIZE):
        start = chapter_index * CHAPTER_SIZE
        chapter_figure_ids = figure_ids[start:start + CHAPTER_SIZE]
        _, scenario_label = SCENARIOS[chapter_index % len(SCENARIOS)]
        sections.append(
            "\n\n".join(
                [
                    f"## Chapter {chapter_index + 1}: figures {start + 1:02d}-{start + CHAPTER_SIZE:02d}",
                    chapter_markdown(chapter_index + 1, chapter_figure_ids, scenario_label),
                    *[
                        project.story_figure(figure_id)
                        for figure_id in chapter_figure_ids
                    ],
                ]
            )
        )

        for offset, figure_id in enumerate(chapter_figure_ids, start=1):
            figure_number = start + offset
            sections.append(
                "\n\n".join(
                    [
                        f"### Detail: figure {figure_number:02d}",
                        figure_markdown(figure_id, figure_number),
                        project.story_figure(figure_id),
                        (
                            "Change the Scenario dropdown here, then navigate to another figure or chapter. "
                            "The visible scenario remains linked through the shared parameter."
                        ),
                    ]
                )
            )

    project.set_story_markdown("\n\n".join(sections))


def build_project() -> LimelightProject:
    project = LimelightProject(
        title="Performance test with linked 10k-point figures",
        subtitle="Synthetic stress package for Limelight rendering",
        description=(
            "Generated fake data for exercising story navigation, markdown rendering, "
            "plot rendering, CSV loading, and shared controls."
        ),
        authors=["Limelight examples"],
        page=PageGeometry.paged(margin_lr_mm=5.0),
        document_version="0.1",
    )

    scenario_control = add_parameters(project)
    figure_ids = add_sources_and_figures(project, scenario_control)
    add_story(project, figure_ids)
    return project


def main() -> None:
    output = ROOT / "_build" / "examples" / "example90-performance-test.limelight"
    build_project().write_folder(output, overwrite=True)
    print(output)


if __name__ == "__main__":
    main()
