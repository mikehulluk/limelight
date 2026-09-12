from __future__ import annotations

import random
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from limelight import (
    AxisDataType,
    CellStyleRule,
    ColumnFormat,
    Index,
    LineArtist,
    LimelightProject,
    PageGeometry,
    PlotArrow,
    SourceProvenance,
    TableViewSpec,
    array,
    axes_action_add_annotation,
    axes_action_add_axis_window_decorator,
    axes_action_add_rect_decorator,
    axes_action_set_x_float_limits,
    axes_action_set_y_float_limits,
    figure_view_action,
    summary_statistics,
)

Derivative = Callable[[float, float], tuple[float, float]]


def rk4_step(
    derivative: Derivative,
    prey: float,
    predator: float,
    dt: float,
) -> tuple[float, float]:
    k1x, k1y = derivative(prey, predator)
    k2x, k2y = derivative(prey + 0.5 * dt * k1x, predator + 0.5 * dt * k1y)
    k3x, k3y = derivative(prey + 0.5 * dt * k2x, predator + 0.5 * dt * k2y)
    k4x, k4y = derivative(prey + dt * k3x, predator + dt * k3y)

    next_prey = prey + (dt / 6.0) * (k1x + 2.0 * k2x + 2.0 * k3x + k4x)
    next_predator = predator + (dt / 6.0) * (k1y + 2.0 * k2y + 2.0 * k3y + k4y)
    return next_prey, next_predator


def lotka_volterra(
    *,
    alpha: float,
    beta: float,
    gamma: float,
    delta: float,
    prey0: float,
    predator0: float,
    dt: float,
    steps: int,
) -> tuple[list[float], list[float], list[float]]:
    def derivative(prey: float, predator: float) -> tuple[float, float]:
        d_prey = alpha * prey - beta * prey * predator
        d_predator = delta * prey * predator - gamma * predator
        return d_prey, d_predator

    times = [0.0]
    prey = [prey0]
    predator = [predator0]

    for index in range(steps):
        x = prey[-1]
        y = predator[-1]

        next_prey, next_predator = rk4_step(derivative, x, y, dt)

        times.append((index + 1) * dt)
        prey.append(next_prey)
        predator.append(next_predator)

    return times, prey, predator


def lotka_volterra_with_prey_pulse(
    *,
    alpha: float,
    beta: float,
    gamma: float,
    delta: float,
    prey0: float,
    predator0: float,
    dt: float,
    steps: int,
    pulse_time: float,
    prey_pulse: float,
) -> tuple[list[float], list[float], list[float]]:
    def derivative(prey: float, predator: float) -> tuple[float, float]:
        d_prey = alpha * prey - beta * prey * predator
        d_predator = delta * prey * predator - gamma * predator
        return d_prey, d_predator

    times = [0.0]
    prey = [prey0]
    predator = [predator0]
    pulse_applied = False

    for index in range(steps):
        current_time = times[-1]
        next_time = (index + 1) * dt
        next_prey, next_predator = rk4_step(derivative, prey[-1], predator[-1], dt)

        if not pulse_applied and current_time < pulse_time <= next_time:
            next_prey += prey_pulse
            pulse_applied = True

        times.append(next_time)
        prey.append(next_prey)
        predator.append(next_predator)

    return times, prey, predator


def noisy_density_limited_lotka_volterra(
    *,
    alpha: float,
    beta: float,
    gamma: float,
    delta: float,
    carrying_capacity: float,
    prey0: float,
    predator0: float,
    dt: float,
    steps: int,
    population_noise_sd: float,
    seed: int,
) -> tuple[list[float], list[float], list[float]]:
    rng = random.Random(seed)

    def derivative(prey: float, predator: float) -> tuple[float, float]:
        density_limit = 1.0 - prey / carrying_capacity
        d_prey = alpha * prey * density_limit - beta * prey * predator
        d_predator = delta * prey * predator - gamma * predator
        return d_prey, d_predator

    times = [0.0]
    prey = [prey0]
    predator = [predator0]

    for index in range(steps):
        next_prey, next_predator = rk4_step(derivative, prey[-1], predator[-1], dt)

        next_prey += rng.gauss(0.0, population_noise_sd)
        next_predator += rng.gauss(0.0, population_noise_sd)

        times.append((index + 1) * dt)
        prey.append(max(0.001, next_prey))
        predator.append(max(0.001, next_predator))

    return times, prey, predator


def sample_at(
    times: list[float],
    prey: list[float],
    predator: list[float],
    target_time: float,
) -> tuple[float, float, float]:
    index = min(range(len(times)), key=lambda candidate: abs(times[candidate] - target_time))
    return times[index], prey[index], predator[index]


def time_point(
    times: list[float],
    prey: list[float],
    predator: list[float],
    target_time: float,
    series: str,
) -> tuple[float, float]:
    time, prey_value, predator_value = sample_at(times, prey, predator, target_time)
    if series == "prey":
        return (time, prey_value)
    if series == "predator":
        return (time, predator_value)
    raise ValueError(f"Unknown series {series!r}")


def phase_point(
    times: list[float],
    prey: list[float],
    predator: list[float],
    target_time: float,
) -> tuple[float, float]:
    _, prey_value, predator_value = sample_at(times, prey, predator, target_time)
    return (prey_value, predator_value)


def phase_point_at_index(
    prey: list[float],
    predator: list[float],
    index: int,
) -> tuple[float, float]:
    return (prey[index], predator[index])


def cyclic_lag(
    leading_time: float,
    following_time: float,
    cycle_duration: float,
) -> float:
    lag = following_time - leading_time
    if lag < 0.0:
        lag += cycle_duration
    return lag


def local_minima(values: list[float]) -> list[int]:
    return [
        index
        for index in range(1, len(values) - 1)
        if values[index - 1] > values[index] <= values[index + 1]
    ]


def extrema_in_window(
    times: list[float],
    values: list[float],
    window: tuple[float, float],
) -> tuple[int, int]:
    lower, upper = window
    indices = [
        index
        for index, time in enumerate(times)
        if lower <= time <= upper
    ]
    if not indices:
        raise ValueError(f"No samples inside window {window!r}")

    minimum_index = min(indices, key=lambda index: values[index])
    maximum_index = max(indices, key=lambda index: values[index])
    return minimum_index, maximum_index


def build_project() -> LimelightProject:
    dt = 0.05
    alpha = 1.1
    beta = 0.4
    gamma = 0.4
    delta = 0.1
    prey0 = 10.0
    predator0 = 5.0
    times, prey, predator = lotka_volterra(
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        delta=delta,
        prey0=prey0,
        predator0=predator0,
        dt=dt,
        steps=2000,
    )
    noisy_carrying_capacity = 30.0
    noisy_times, noisy_prey, noisy_predator = noisy_density_limited_lotka_volterra(
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        delta=delta,
        carrying_capacity=noisy_carrying_capacity,
        prey0=prey0,
        predator0=predator0,
        dt=dt,
        steps=800,
        population_noise_sd=0.02,
        seed=42,
    )
    constant_prey = gamma / delta
    constant_predator = alpha / beta
    pulse_time = 12.0
    prey_pulse = 12.0
    pulse_times, pulse_prey, pulse_predator = lotka_volterra_with_prey_pulse(
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        delta=delta,
        prey0=constant_prey,
        predator0=constant_predator,
        dt=dt,
        steps=1200,
        pulse_time=pulse_time,
        prey_pulse=prey_pulse,
    )
    large_prey_pulse = 2.0 * prey_pulse
    large_pulse_times, large_pulse_prey, large_pulse_predator = lotka_volterra_with_prey_pulse(
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        delta=delta,
        prey0=constant_prey,
        predator0=constant_predator,
        dt=dt,
        steps=1200,
        pulse_time=pulse_time,
        prey_pulse=large_prey_pulse,
    )
    attractor_prey = gamma / delta
    attractor_predator = (alpha / beta) * (
        1.0 - attractor_prey / noisy_carrying_capacity
    )
    prey_minima = local_minima(prey)
    season_5_start_index = prey_minima[4]
    season_5_end_index = prey_minima[5]
    season_4_window = (
        times[prey_minima[3]],
        times[season_5_start_index],
    )
    season_5_window = (
        times[season_5_start_index],
        times[season_5_end_index],
    )
    season_8_window = (
        times[prey_minima[7]],
        times[prey_minima[8]],
    )
    phase_analysis_window = (
        season_4_window[0],
        season_8_window[1],
    )
    season_5_cycle_duration = season_5_window[1] - season_5_window[0]
    _, prey_max_index = extrema_in_window(times, prey, season_5_window)
    prey_min_index = season_5_start_index
    predator_min_index, predator_max_index = extrema_in_window(
        times,
        predator,
        season_5_window,
    )
    peak_lag = cyclic_lag(
        times[prey_max_index],
        times[predator_max_index],
        season_5_cycle_duration,
    )
    trough_lag = cyclic_lag(
        times[prey_min_index],
        times[predator_min_index],
        season_5_cycle_duration,
    )
    peak_lag_angle = 360.0 * peak_lag / season_5_cycle_duration
    trough_lag_angle = 360.0 * trough_lag / season_5_cycle_duration
    season_1_actions = [
        figure_view_action("populations-over-time", axes_action_set_x_float_limits(0.0, season_4_window[0])),
    ]
    multi_season_actions = [
        figure_view_action("populations-over-time", axes_action_set_x_float_limits(season_4_window[0], season_8_window[1])),
        figure_view_action("populations-over-time", axes_action_add_axis_window_decorator(lower=season_4_window[0], upper=season_5_window[0], label="Season 4")),
        figure_view_action("populations-over-time", axes_action_add_axis_window_decorator(lower=season_5_window[0], upper=season_5_window[1], label="Season 5")),
    ]
    phase_cycle_actions = [
        figure_view_action("phase-plot", axes_action_set_x_float_limits(0.0, max(prey) * 1.05)),
        figure_view_action("phase-plot", axes_action_set_y_float_limits(0.0, max(predator) * 1.05)),
    ]
    season_5_phase_actions = [
        figure_view_action(
            "phase-plot",
            axes_action_add_rect_decorator(
                x_lower=prey[prey_min_index],
                x_upper=prey[prey_max_index],
                y_lower=predator[predator_min_index],
                y_upper=predator[predator_max_index],
                label="Season 5 extrema",
            ),
        ),
    ]
    prey_peak_point = (times[prey_max_index], prey[prey_max_index])
    predator_peak_point = (times[predator_max_index], predator[predator_max_index])
    season_5_analysis_actions = [
        figure_view_action("populations-over-time", axes_action_set_x_float_limits(phase_analysis_window[0], phase_analysis_window[1])),
        figure_view_action("populations-over-time", axes_action_add_axis_window_decorator(lower=season_5_window[0], upper=season_5_window[1], label="Season 5")),
        figure_view_action(
            "populations-over-time",
            axes_action_add_annotation(
                PlotArrow(start=prey_peak_point, end=prey_peak_point),
                label="Prey peak",
                color="seagreen",
                label_offset=(8.0, 10.0),
            ),
        ),
        figure_view_action(
            "populations-over-time",
            axes_action_add_annotation(
                PlotArrow(start=predator_peak_point, end=predator_peak_point),
                label="Predator peak",
                color="firebrick",
                label_offset=(8.0, -14.0),
            ),
        ),
    ]
    noisy_time_actions = [
        figure_view_action("noisy-populations-over-time", axes_action_set_x_float_limits(0.0, noisy_times[-1])),
    ]
    pulse_window_actions = [
        figure_view_action("pulse-populations-over-time", axes_action_set_x_float_limits(0.0, pulse_times[-1])),
        figure_view_action("pulse-populations-over-time", axes_action_add_axis_window_decorator(lower=pulse_time, upper=pulse_time, label="Pulse")),
    ]
    large_pulse_window_actions = [
        figure_view_action("large-pulse-populations-over-time", axes_action_set_x_float_limits(0.0, large_pulse_times[-1])),
        figure_view_action("large-pulse-populations-over-time", axes_action_add_axis_window_decorator(lower=pulse_time, upper=pulse_time, label="Pulse")),
    ]

    project = LimelightProject(
        title="Lotka-Volterra predator-prey walkthrough",
        subtitle="Small generated example",
        description=(
            "A simple generated Limelight package showing coupled predator-prey "
            "population cycles."
        ),
        authors=["Limelight examples"],
        page=PageGeometry.paged(margin_lr_mm=10.0),
        created="2026-08-05",
        document_version="0.1",
    )

    project.add_csv_dataset(
        id="population-simulation",
        title="Population simulation",
        index=Index.irregular_index_array(coordinate_array="time", unit="cycles"),
        arrays={
            "time": array(times, dtype="double", label="Time", unit="cycles"),
            "prey": array(prey, dtype="double", label="Prey population"),
            "predator": array(predator, dtype="double", label="Predator population"),
        },
        provenance=SourceProvenance(
            origin="Synthetic data generated by a Runge-Kutta 4 integration of the Lotka-Volterra equations.",
            release_date="2026-08-05",
            url="https://en.wikipedia.org/wiki/Lotka%E2%80%93Volterra_equations",
        ),
    )
    prey_stats = summary_statistics(prey)
    prey_stat_names = ["min", "max", "mean", "sd"]
    project.add_csv_dataset(
        id="prey-summary-stats",
        title="Prey population summary statistics",
        index=Index.irregular_index_array(coordinate_array="statistic"),
        arrays={
            "statistic": array(prey_stat_names, dtype="category", label="Statistic"),
            "prey": array(
                [prey_stats[name] for name in prey_stat_names],
                dtype="double",
                label="Prey population",
            ),
        },
    )
    project.add_csv_dataset(
        id="noisy-population-simulation",
        title="Noisy population simulation",
        index=Index.irregular_index_array(coordinate_array="time", unit="cycles"),
        arrays={
            "time": array(noisy_times, dtype="double", label="Time", unit="cycles"),
            "prey": array(noisy_prey, dtype="double", label="Noisy prey population"),
            "predator": array(
                noisy_predator,
                dtype="double",
                label="Noisy predator population",
            ),
        },
    )
    project.add_csv_dataset(
        id="pulse-input-constant-state",
        title="Pulse input from constant state",
        index=Index.irregular_index_array(coordinate_array="time", unit="cycles"),
        arrays={
            "time": array(pulse_times, dtype="double", label="Time", unit="cycles"),
            "prey": array(pulse_prey, dtype="double", label="Prey population"),
            "predator": array(pulse_predator, dtype="double", label="Predator population"),
        },
    )
    project.add_csv_dataset(
        id="large-pulse-input-constant-state",
        title="Large pulse input from constant state",
        index=Index.irregular_index_array(coordinate_array="time", unit="cycles"),
        arrays={
            "time": array(large_pulse_times, dtype="double", label="Time", unit="cycles"),
            "prey": array(large_pulse_prey, dtype="double", label="Prey population"),
            "predator": array(large_pulse_predator, dtype="double", label="Predator population"),
        },
    )

    project.add_line_figure(
        id="populations-over-time",
        title="Populations over time",
        caption="The predator population lags the prey population over each cycle.",
        data="population-simulation",
        x="time",
        y=[("prey", "Prey"), ("predator", "Predator")],
        x_axis=AxisDataType.continuous(label="Time", unit="cycles", share_group="simulation-time"),
        y_axis=AxisDataType.continuous(label="Population"),
    )
    project.add_line_figure(
        id="phase-plot",
        title="Phase plot",
        caption="The phase plot shows the cyclic relationship between prey and predator populations.",
        data="population-simulation",
        x="prey",
        y=[LineArtist(array="predator", label="Trajectory", x="prey")],
        x_axis=AxisDataType.continuous(label="Prey population"),
        y_axis=AxisDataType.continuous(label="Predator population"),
    )
    project.add_line_figure(
        id="prey-summary-table",
        title="Prey population summary statistics",
        caption="Min/max/mean/sd of the prey population, computed at example-build time.",
        data="prey-summary-stats",
        x="statistic",
        y=[],
        x_axis=AxisDataType.continuous(label="Statistic"),
        y_axis=AxisDataType.continuous(label="Prey population"),
        table_views=[
            TableViewSpec(
                id="prey-summary-table-view",
                data="prey-summary-stats",
                column_formats=[ColumnFormat(column="prey", format=".2f")],
                header_style=["Bold"],
                cell_styles=[CellStyleRule(styles=["Italic"], column=0)],
            )
        ],
    )
    project.add_line_figure(
        id="pulse-populations-over-time",
        title="Pulse input from constant state",
        caption=(
            "The system starts at the coexistence fixed point. A prey pulse at Season 2 "
            "moves it onto a closed deterministic orbit."
        ),
        data="pulse-input-constant-state",
        x="time",
        y=[("prey", "Prey"), ("predator", "Predator")],
        x_axis=AxisDataType.continuous(label="Time", unit="cycles", share_group="pulse-simulation-time"),
        y_axis=AxisDataType.continuous(label="Population"),
    )
    project.add_line_figure(
        id="pulse-phase-plot",
        title="Pulse phase plot",
        caption=(
            "A one-time prey injection from the fixed point creates a closed orbit around "
            "the coexistence state."
        ),
        data="pulse-input-constant-state",
        x="prey",
        y=[LineArtist(array="predator", label="Trajectory", x="prey")],
        x_axis=AxisDataType.continuous(label="Prey population"),
        y_axis=AxisDataType.continuous(label="Predator population"),
    )
    project.add_line_figure(
        id="large-pulse-populations-over-time",
        title="Large pulse input from constant state",
        caption=(
            "A larger one-time prey pulse at Season 2 moves the system onto a wider "
            "closed orbit around the coexistence fixed point."
        ),
        data="large-pulse-input-constant-state",
        x="time",
        y=[("prey", "Prey"), ("predator", "Predator")],
        x_axis=AxisDataType.continuous(label="Time", unit="cycles", share_group="pulse-simulation-time"),
        y_axis=AxisDataType.continuous(label="Population"),
    )
    project.add_line_figure(
        id="input-phase-comparison",
        title="Pulse-size phase comparison",
        caption=(
            "Both one-time prey pulses create closed orbits; the larger pulse lands on "
            "a wider trajectory."
        ),
        data="pulse-input-constant-state",
        x="prey",
        y=[
            LineArtist(array="predator", label="Pulse +12", x="prey"),
            LineArtist(
                array="predator",
                label="Pulse +24",
                id="large-pulse-phase-line",
                data="large-pulse-input-constant-state",
                x="prey",
            ),
        ],
        x_axis=AxisDataType.continuous(label="Prey population"),
        y_axis=AxisDataType.continuous(label="Predator population"),
    )
    project.add_line_figure(
        id="noisy-populations-over-time",
        title="Noisy populations over time",
        caption=(
            "Small additive Gaussian noise perturbs both populations while density-limited dynamics "
            "pull the system back toward stable coexistence."
        ),
        data="noisy-population-simulation",
        x="time",
        y=[("prey", "Prey"), ("predator", "Predator")],
        x_axis=AxisDataType.continuous(label="Time", unit="cycles", share_group="noisy-simulation-time"),
        y_axis=AxisDataType.continuous(label="Population"),
    )
    project.add_line_figure(
        id="noisy-phase-plot",
        title="Noisy phase plot",
        caption=(
            "The noisy trajectory spirals toward the coexistence attractor and then wanders "
            "around it under continuing population shocks."
        ),
        data="noisy-population-simulation",
        x="prey",
        y=[
            LineArtist(array="predator", label="Noisy trajectory", x="prey"),
            LineArtist(
                array="predator",
                label="Deterministic trajectory",
                id="deterministic-trajectory-line",
                data="population-simulation",
                alpha=0.3,
                x="prey",
            ),
        ],
        x_axis=AxisDataType.continuous(label="Prey population"),
        y_axis=AxisDataType.continuous(label="Predator population"),
    )
    project.set_story_markdown(
        base_dir=Path(__file__).resolve().parent / "example01_lotka_volterra",
        markdown=f"""
# Simple Predator-Prey interactions with Lotka-Volterra

A compact walkthrough of predator-prey dynamics: first as a deterministic Lotka-Volterra cycle, then as a noisy population model with a stable coexistence attractor.

## The animals

The prey are rabbits and the predators are foxes. Neither is simulated as an
individual: the whole state of the model is two numbers, a prey population and
a predator population.

![A rabbit, standing in for the prey population](images/rabbit.png){{width="30%"}}

![A fox, standing in for the predator population](images/fox.png){{width="30%"}}

Those two illustrations are numbered in the same sequence as the plots, because
a reader working through the story has no reason to care which of them is a
photograph and which is a rendered figure.

## Overview

The backdrop is a coupled predator-prey system. Let $x(t)$ be the prey population and $y(t)$ be the predator population.

The deterministic Lotka-Volterra equations are:

$$
\\frac{{dx}}{{dt}} = \\alpha x - \\beta xy
$$

$$
\\frac{{dy}}{{dt}} = \\delta xy - \\gamma y
$$

The $\\alpha$ term grows prey, $\\beta$ removes prey through predation, $\\gamma$ removes predators through mortality, and $\\delta$ converts predation into predator growth.

## Season 1

In the first cycle, prey recover first. Predator growth follows after prey become abundant.

{project.story_figure("populations-over-time", actions=season_1_actions)}

## Season 2 and onwards

From Season 2 onward, the same lagged pattern repeats. The five-cycle window shows the recurrence, with alternate Season bands labeled Season 1, Season 3, and Season 5.

{project.story_figure("populations-over-time", actions=multi_season_actions)}

If we plot the sizes of the populations, against each other, this gives us a phase-plot.

{project.story_figure("phase-plot", id="phase-cycle", actions=phase_cycle_actions)}

## Phase Plot

The phase plot removes time from the x-axis and shows the cycle as a trajectory through prey-predator state space. The closed loop in [Figure](#phase-cycle) is the same data as the time series above it, and the rabbits of [Figure](#rabbit) are its horizontal axis.

{project.story_figure("phase-plot")}

## Phase Analysis

This view spans Seasons 4-8 of the deterministic time-series. Season 5 runs from one prey minimum to the next, lasting about {season_5_cycle_duration:.1f} time units. The dotted vertical markers show the minimum and maximum of each population within Season 5.

{project.story_figure("populations-over-time", actions=season_5_analysis_actions)}

Peak phase-lag: the predator peak follows the prey peak by {peak_lag:.2f} time units, or {peak_lag_angle:.0f} degrees of the Season 5 cycle. Trough phase-lag: the predator trough follows the prey trough by {trough_lag:.2f} time units, or {trough_lag_angle:.0f} degrees of the cycle.

Phase analysis treats each point as a full system state rather than a timestamp. The deterministic model returns to the same closed trajectory, so equal prey levels can imply different predator trends depending on where the state sits on the loop. The shaded box is bounded by the Season 5 extrema of each population: the loop touches every side of it and never leaves it.

{project.story_figure("phase-plot", actions=season_5_phase_actions)}

## Small Additive Gaussian Noise

The noisy example adds a seeded Gaussian value with mean 0.0 to each population after every integration step. A weak carrying-capacity term makes the coexistence point a stable attractor, so the trajectory spirals inward and then fluctuates around that state.

{project.story_figure("noisy-populations-over-time", actions=noisy_time_actions)}

{project.story_figure("noisy-phase-plot")}

## Pulse Input from 'constant' state

For the deterministic Lotka-Volterra equations, fixed points satisfy:

$$
x(\\alpha - \\beta y) = 0
$$

$$
y(\\delta x - \\gamma) = 0
$$

So the fixed points are the collapsed state $(0, 0)$ and the coexistence state $(\\gamma / \\delta, \\alpha / \\beta) = ({constant_prey:.2f}, {constant_predator:.2f})$.

The collapsed state remains collapsed only if both populations are exactly zero. It is not stable to a prey perturbation: if $y=0$, predators cannot appear, so a pulse into $x$ alone does not create predator-prey dynamics. The experiments below therefore start from the nonzero constant coexistence state. The pulse experiment injects {prey_pulse:.1f} prey once at the start of Season 2, $t={pulse_time:.1f}$.

{project.story_figure("pulse-populations-over-time", actions=pulse_window_actions)}

Before the pulse, both populations sit at the coexistence fixed point. The prey input moves the system away from that point; predators then rise in response, prey falls, and the deterministic system settles onto a closed orbit rather than damping back to the fixed point.

The second experiment uses another one-time prey pulse at the same time, but with size {large_prey_pulse:.1f}, twice the first pulse. The larger displacement creates a wider closed orbit around the same coexistence fixed point.

{project.story_figure("large-pulse-populations-over-time", actions=large_pulse_window_actions)}

The phase comparison makes the difference clearer: both inputs are one-time pulses, and the pulse size controls how far the state is displaced onto the surrounding orbit.

{project.story_figure("input-phase-comparison")}

{project.story_figure("pulse-phase-plot")}

## Summary statistics

The prey population time series above can be summarized with a few descriptive statistics, computed once at example-build time and shown here as a read-only table:

{project.story_figure("prey-summary-table")}
"""
    )

    return project


def main() -> None:
    output = ROOT / "_build" / "examples" / "example01-lotka-volterra.limelight"
    build_project().write_folder(output, overwrite=True)
    print(output)


if __name__ == "__main__":
    main()
