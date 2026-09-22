from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from limelight import (
    AxisDataType,
    DropdownCtrlSpec,
    FigureViewAction,
    FormSpec,
    Index,
    LineArtist,
    LimelightProject,
    PageGeometry,
    Panel,
    SliderCtrlSpec,
    StemArtist,
    TextControlParameterMatch,
    array,
    axes_action_add_axis_window_decorator,
    figure_view_action,
)

SAMPLE_RATE_HZ = 400.0
DURATION_S = 3.0
BODE_MAX_FREQUENCY_HZ = 120.0
COMPONENT_FREQUENCIES_HZ = [2.0, 13.0, 45.0]


@dataclass(frozen=True)
class FilterConfig:
    id: str
    label: str
    family: str
    cutoff_hz: float


FILTERS = [
    FilterConfig("butterworth-8hz", "Butterworth, 2nd order, 8 Hz cutoff", "butterworth", 8.0),
    FilterConfig("butterworth-20hz", "Butterworth, 2nd order, 20 Hz cutoff", "butterworth", 20.0),
    FilterConfig("bessel-8hz", "Bessel, 2nd order, 8 Hz cutoff", "bessel", 8.0),
    FilterConfig("bessel-20hz", "Bessel, 2nd order, 20 Hz cutoff", "bessel", 20.0),
    FilterConfig("critically-damped-20hz", "Critically damped, 2nd order, 20 Hz cutoff", "critical", 20.0),
]


def lowpass_response(config: FilterConfig, frequencies_hz: np.ndarray) -> np.ndarray:
    ratio = frequencies_hz / config.cutoff_hz
    s = 1j * ratio
    if config.family == "butterworth":
        return 1.0 / (s**2 + np.sqrt(2.0) * s + 1.0)
    if config.family == "bessel":
        return 3.0 / (s**2 + 3.0 * s + 3.0)
    if config.family == "critical":
        return 1.0 / (1.0 + s) ** 2
    raise ValueError(f"Unknown filter family {config.family!r}")


def build_signal(
    sample_rate_hz: float = SAMPLE_RATE_HZ,
    duration_s: float = DURATION_S,
) -> dict[str, np.ndarray]:
    sample_count = int(sample_rate_hz * duration_s)
    time = np.arange(sample_count) / sample_rate_hz
    low_hz, mid_hz, high_hz = COMPONENT_FREQUENCIES_HZ
    low = 1.0 * np.sin(2.0 * np.pi * low_hz * time)
    mid = 0.55 * np.sin(2.0 * np.pi * mid_hz * time + 0.35)
    high = 0.28 * np.sin(2.0 * np.pi * high_hz * time + 0.8)
    signal = low + mid + high

    columns: dict[str, np.ndarray] = {
        "time": time,
        "low-component": low,
        "mid-component": mid,
        "high-component": high,
        "input-signal": signal,
    }
    fft_frequencies = np.fft.rfftfreq(sample_count, d=1.0 / sample_rate_hz)
    signal_spectrum = np.fft.rfft(signal)
    for config in FILTERS:
        response = lowpass_response(config, fft_frequencies)
        columns[f"output-{config.id}"] = np.fft.irfft(signal_spectrum * response, n=sample_count)
    return columns


def build_input_bode(signal: dict[str, np.ndarray], sample_rate_hz: float = SAMPLE_RATE_HZ) -> dict[str, np.ndarray]:
    samples = signal["input-signal"]
    spectrum = np.fft.rfft(samples)
    frequencies_hz = np.fft.rfftfreq(len(samples), d=1.0 / sample_rate_hz)

    single_sided_amplitude = 2.0 * np.abs(spectrum) / len(samples)
    single_sided_amplitude[0] = np.abs(spectrum[0]) / len(samples)
    frequency_mask = (frequencies_hz > 0.0) & (frequencies_hz <= BODE_MAX_FREQUENCY_HZ)

    bin_frequency_hz = frequencies_hz[frequency_mask]
    bin_spectrum = spectrum[frequency_mask]
    bin_amplitude = np.maximum(single_sided_amplitude[frequency_mask], 1e-12)
    return {
        "frequency-hz": bin_frequency_hz,
        "magnitude-db": 20.0 * np.log10(bin_amplitude),
        "phase-deg": np.angle(bin_spectrum) * 180.0 / np.pi,
    }


def build_bode() -> dict[str, np.ndarray]:
    frequencies_hz = np.logspace(np.log10(0.5), np.log10(BODE_MAX_FREQUENCY_HZ), 650)
    columns: dict[str, np.ndarray] = {
        "frequency-hz": frequencies_hz,
    }
    for config in FILTERS:
        response = lowpass_response(config, frequencies_hz)
        magnitude = np.maximum(np.abs(response), 1e-12)
        columns[f"magnitude-db-{config.id}"] = 20.0 * np.log10(magnitude)
        columns[f"phase-deg-{config.id}"] = np.unwrap(np.angle(response)) * 180.0 / np.pi
    return columns


def visible_for(config: FilterConfig) -> TextControlParameterMatch:
    return TextControlParameterMatch(control_parameter="filter-design", value=config.id)


def filtered_lines() -> list[LineArtist]:
    lines = [
        LineArtist("input-signal", label="Input signal", id="input-reference-line", alpha=0.3),
    ]
    for config in FILTERS:
        lines.append(
            LineArtist(
                f"output-{config.id}",
                label=config.label,
                id=f"output-{config.id}-line",
                visible_when=visible_for(config),
            )
        )
    return lines


def bode_lines(prefix: str, label_suffix: str) -> list[LineArtist]:
    return [
        LineArtist(
            f"{prefix}-{config.id}",
            label=f"{config.label} ({label_suffix})",
            id=f"{prefix}-{config.id}-line",
            visible_when=visible_for(config),
        )
        for config in FILTERS
    ]


def build_project() -> LimelightProject:
    signal = build_signal()
    input_bode = build_input_bode(signal)
    bode = build_bode()

    project = LimelightProject(
        title="Signal filtering walkthrough",
        subtitle="Dropdown-controlled low-pass filter comparisons",
        description=(
            "A composite signal made from three sinusoids, filtered with several low-pass designs "
            "and cutoff frequencies."
        ),
        authors=["Limelight examples"],
        page=PageGeometry.paged(margin_lr_mm=10.0),
        document_version="0.1",
    )

    project.add_discrete_control_parameter(
        id="filter-design",
        label="Filter",
        options=[(config.id, config.label) for config in FILTERS],
        default=FILTERS[0].id,
    )
    filter_dropdown = DropdownCtrlSpec(
        id="filter-design-control",
        control_parameter="filter-design",
        label="Filter",
    )

    project.add_float_control_parameter(
        id="reference-line-alpha",
        label="Reference line opacity",
        default=0.3,
        min=0.0,
        max=1.0,
    )
    reference_alpha_slider = SliderCtrlSpec(
        id="reference-line-alpha-control",
        control_parameter="reference-line-alpha",
        label="Reference line opacity",
    )

    project.add_csv_dataset(
        id="composite-signal",
        title="Composite sinusoidal signal",
        arrays={
            name: array(values.tolist(), dtype="double")
            for name, values in signal.items()
        },
        index=Index.regular_time(step_nom=2500, step_denom=1, step_unit="us"),
    )
    project.add_csv_dataset(
        id="filter-bode",
        title="Filter frequency responses",
        arrays={
            name: array(values.tolist(), dtype="double")
            for name, values in bode.items()
        },
        index=Index.no_index(),
    )
    project.add_csv_dataset(
        id="input-signal-bode",
        title="Original signal Bode-style spectrum",
        arrays={
            name: array(values.tolist(), dtype="double")
            for name, values in input_bode.items()
        },
        index=Index.no_index(),
    )

    project.add_line_figure(
        id="signal-components",
        title="Three sinusoidal components",
        data="composite-signal",
        x="time",
        y=[
            LineArtist("input-signal", label="Composite signal", id="composite-line", alpha=0.9),
            ("low-component", "2 Hz component"),
            ("mid-component", "13 Hz component"),
            ("high-component", "45 Hz component"),
        ],
        caption="The input is the sum of low-, mid-, and high-frequency sinusoids.",
        x_axis=AxisDataType.continuous(label="Time", unit="s", share_group="signal-time"),
        y_axis=AxisDataType.continuous(label="Amplitude"),
        controls=[reference_alpha_slider],
    )
    project.add_line_figure(
        id="filtered-output",
        title="Selected low-pass output",
        data="composite-signal",
        x="time",
        y=filtered_lines(),
        caption="Use the dropdown to compare the selected filter output against the unfiltered signal.",
        x_axis=AxisDataType.continuous(label="Time", unit="s", share_group="signal-time"),
        y_axis=AxisDataType.continuous(label="Amplitude"),
        controls=[filter_dropdown],
    )
    bode_frequency_axis = AxisDataType.continuous(
        label="Frequency", unit="Hz", scale="Log", share_group="bode-frequency"
    )
    project.add_figure(
        id="input-bode",
        title="Original signal Bode plot",
        panels=[
            Panel(
                stems=[StemArtist(array="magnitude-db", label="Magnitude", baseline=-245.0)],
                data="input-signal-bode",
                x="frequency-hz",
                x_axis=bode_frequency_axis,
                y_axis=AxisDataType.continuous(label="Magnitude", unit="dB"),
            ),
            Panel(
                lines=[("phase-deg", "Phase")],
                data="input-signal-bode",
                x="frequency-hz",
                x_axis=bode_frequency_axis,
                y_axis=AxisDataType.continuous(label="Phase", unit="deg"),
            ),
        ],
        caption=(
            "Full FFT magnitude and phase spectrum of the unfiltered input, plotted over every frequency "
            "bin rather than just the three component peaks. The 2, 13, and 45 Hz components each complete "
            "a whole number of cycles in the 3 second analysis window, so there is essentially no spectral "
            "leakage: magnitude between peaks sits at the numerical noise floor, and phase there is "
            "meaningless noise rather than filter phase delay."
        ),
    )
    project.add_figure(
        id="bode-response",
        title="Bode magnitude and phase response",
        panels=[
            # The magnitude panel carries the story, so it gets twice the
            # height of the phase panel beneath it.
            Panel(
                lines=bode_lines("magnitude-db", "Magnitude"),
                data="filter-bode",
                x="frequency-hz",
                x_axis=bode_frequency_axis,
                y_axis=AxisDataType.continuous(label="Magnitude", unit="dB"),
                height=2.0,
            ),
            Panel(
                lines=bode_lines("phase-deg", "Phase"),
                data="filter-bode",
                x="frequency-hz",
                x_axis=bode_frequency_axis,
                y_axis=AxisDataType.continuous(label="Phase", unit="deg"),
                height=1.0,
            ),
        ],
        caption=(
            "Magnitude and phase response of the selected low-pass filter, plotted against a "
            "logarithmically-spaced frequency axis."
        ),
        forms=[FormSpec(controls=[filter_dropdown])],
    )

    def component_frequency_markers(figure_id: str) -> list[FigureViewAction]:
        # The same marker down both panels of a Bode plot: the magnitude
        # above and the phase below, so a component lines up across them.
        markers = []
        for frequency_hz in COMPONENT_FREQUENCIES_HZ:
            decorator = axes_action_add_axis_window_decorator(
                lower=frequency_hz,
                upper=frequency_hz,
                label=f"{frequency_hz:g} Hz",
            )
            markers.extend(
                figure_view_action(figure_id, decorator, panel=panel) for panel in (1, 2)
            )
        return markers

    project.set_story_markdown(
        f"""
# Signal Filtering

The input signal is a sum of three sinusoids:

$$
x(t) = \\sin(2\\pi 2t) + 0.55\\sin(2\\pi 13t + 0.35) + 0.28\\sin(2\\pi 45t + 0.8)
$$

The sampled signal uses an exact relative time basis: 400 Hz is represented as a 2500 microsecond step from `t = 0`, without inventing a wall-clock epoch.

The dropdown control selects a low-pass filter design and cutoff frequency. The selected value is a shared Limelight parameter, so the time-domain output and Bode plots stay synchronized.

## Input Signal

The composite input contains one low-frequency component, one mid-band component, and one high-frequency component. The slider control adjusts the opacity of the composite reference line (a `SliderCtrlSpec` bound to a float control parameter).

{project.story_figure("signal-components")}

## Filtered Output

A lower cutoff removes more of the 13 Hz and 45 Hz components. Bessel and critically damped filters trade sharper attenuation for smoother phase behavior.

{project.story_figure("filtered-output")}

## Bode Plots

The original signal has a discrete spectrum: energy at the three sinusoidal component frequencies. The filter Bode plots then show how the selected filter weights those frequencies in magnitude and phase.

{project.story_figure("input-bode", actions=component_frequency_markers("input-bode"))}

{project.story_figure("bode-response", actions=component_frequency_markers("bode-response"))}
""",
        base_dir=Path(__file__).parent,
    )

    return project


def main() -> None:
    output = ROOT / "_build" / "examples" / "example02-signal-filters.limelight"
    build_project().write_folder(output, overwrite=True)
    print(output)


if __name__ == "__main__":
    main()
