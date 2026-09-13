from __future__ import annotations

from pathlib import Path

import h5py
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from limelight.largeseries.cache import SourceSpec, build_or_get_cache, close_cache
from limelight.largeseries.mpl import ZoomSync, plot_envelope, update_envelope_artists
from limelight.largeseries.query import query_range


def _build_handle(tmp_path: Path, n: int = 4096, chunk_size: int = 8):
    source_path = tmp_path / "source.h5"
    values = np.sin(np.linspace(0, 20, n))
    with h5py.File(source_path, "w") as handle:
        handle.create_dataset("/y", data=values)

    spec = SourceSpec(hdf5_path=source_path, y_dataset="/y")
    cache_handle = build_or_get_cache(spec, cache_dir=tmp_path / "cache", chunk_size=chunk_size)
    return spec, cache_handle


def test_plot_envelope_creates_exactly_three_artists(tmp_path: Path) -> None:
    spec, handle = _build_handle(tmp_path)
    fig, ax = plt.subplots()
    try:
        result = query_range(handle, spec, 0, handle.source_length - 1, target_buckets=50)
        artists = plot_envelope(ax, result)
        assert len(artists) == 3
        line_min, line_max, fill = artists
        assert line_min in ax.lines
        assert line_max in ax.lines
        assert fill in ax.collections
    finally:
        plt.close(fig)
        close_cache(handle)


def test_update_envelope_artists_mutates_same_objects(tmp_path: Path) -> None:
    spec, handle = _build_handle(tmp_path)
    fig, ax = plt.subplots()
    try:
        result1 = query_range(handle, spec, 0, 1000, target_buckets=50)
        line_min, line_max, fill = plot_envelope(ax, result1)

        result2 = query_range(handle, spec, 1000, 2000, target_buckets=50)
        update_envelope_artists(line_min, line_max, fill, result2)

        assert line_min is ax.lines[0]
        np.testing.assert_array_equal(line_min.get_xdata(), result2.x)
        np.testing.assert_array_equal(line_min.get_ydata(), result2.y_min)
    finally:
        plt.close(fig)
        close_cache(handle)


def test_zoom_sync_creates_one_triple_and_updates_in_place(tmp_path: Path) -> None:
    spec, handle = _build_handle(tmp_path, n=8192, chunk_size=8)
    fig, ax = plt.subplots()
    try:
        sync = ZoomSync(ax=ax, handle=handle, spec=spec, target_buckets=50)
        assert len(ax.lines) == 2
        assert len(ax.collections) == 1

        ax.set_xlim(0, 500)
        ax.set_xlim(1000, 4000)
        ax.set_xlim(2000, 2100)

        assert len(ax.lines) == 2
        assert len(ax.collections) == 1
    finally:
        sync.disconnect()
        plt.close(fig)
        close_cache(handle)


def test_zoom_sync_disconnect_stops_updates(tmp_path: Path) -> None:
    spec, handle = _build_handle(tmp_path, n=8192, chunk_size=8)
    fig, ax = plt.subplots()
    try:
        sync = ZoomSync(ax=ax, handle=handle, spec=spec, target_buckets=50)
        sync.disconnect()

        before_x = np.array(sync._line_min.get_xdata())
        ax.set_xlim(2000, 2100)
        after_x = np.array(sync._line_min.get_xdata())

        np.testing.assert_array_equal(before_x, after_x)
    finally:
        plt.close(fig)
        close_cache(handle)


def test_plot_envelope_uses_one_opaque_colour_for_lines_and_fill(tmp_path: Path) -> None:
    spec, handle = _build_handle(tmp_path)
    fig, ax = plt.subplots()
    try:
        result = query_range(handle, spec, 0, handle.source_length - 1, target_buckets=50)
        line_min, line_max, fill = plot_envelope(ax, result)
        colour = matplotlib.colors.to_rgb(line_min.get_color())
        assert matplotlib.colors.to_rgb(line_max.get_color()) == colour
        assert tuple(fill.get_facecolor()[0]) == colour + (1.0,)
    finally:
        plt.close(fig)
        close_cache(handle)


def test_plot_envelope_fill_colour_and_alpha_are_configurable(tmp_path: Path) -> None:
    spec, handle = _build_handle(tmp_path)
    fig, ax = plt.subplots()
    try:
        result = query_range(handle, spec, 0, handle.source_length - 1, target_buckets=50)
        line_min, line_max, fill = plot_envelope(ax, result, color="red", fill_color="blue", fill_alpha=0.25)
        assert matplotlib.colors.to_rgb(line_min.get_color()) == (1.0, 0.0, 0.0)
        assert matplotlib.colors.to_rgb(line_max.get_color()) == (1.0, 0.0, 0.0)
        assert tuple(fill.get_facecolor()[0]) == (0.0, 0.0, 1.0, 0.25)

        sync = ZoomSync(ax=ax, handle=handle, spec=spec, target_buckets=50, color="green", fill_alpha=0.5)
        assert matplotlib.colors.to_rgb(sync._line_min.get_color()) == matplotlib.colors.to_rgb("green")
        assert tuple(sync._fill.get_facecolor()[0]) == matplotlib.colors.to_rgb("green") + (0.5,)
        sync.disconnect()
    finally:
        plt.close(fig)
        close_cache(handle)


def test_plot_envelope_shows_raw_line_only_when_fully_zoomed(tmp_path: Path) -> None:
    spec, handle = _build_handle(tmp_path)
    fig, ax = plt.subplots()
    try:
        # 20 samples into 50 buckets: every point is a raw sample.
        raw = query_range(handle, spec, 0, 19, target_buckets=50)
        assert raw.raw is True
        line_min, line_max, fill = plot_envelope(ax, raw)
        assert line_min.get_visible()
        assert not line_max.get_visible()
        assert not fill.get_visible()

        # Zooming back out restores the envelope on the same artists.
        wide = query_range(handle, spec, 0, handle.source_length - 1, target_buckets=50)
        assert wide.raw is False
        update_envelope_artists(line_min, line_max, fill, wide)
        assert line_max.get_visible()
        assert fill.get_visible()

        update_envelope_artists(line_min, line_max, fill, raw)
        assert not line_max.get_visible()
        assert not fill.get_visible()
    finally:
        plt.close(fig)
        close_cache(handle)


def test_zoom_sync_indicator_tracks_raw_vs_envelope(tmp_path: Path) -> None:
    spec, handle = _build_handle(tmp_path, n=8192, chunk_size=8)
    fig, ax = plt.subplots()
    try:
        sync = ZoomSync(ax=ax, handle=handle, spec=spec, target_buckets=50)
        assert sync._indicator is not None
        assert sync._indicator.get_text().startswith("min/max")

        ax.set_xlim(2000, 2020)
        assert sync._indicator.get_text().startswith("raw")

        ax.set_xlim(0, 8191)
        assert sync._indicator.get_text().startswith("min/max")
    finally:
        sync.disconnect()
        plt.close(fig)
        close_cache(handle)


def test_zoom_sync_indicator_can_be_disabled(tmp_path: Path) -> None:
    spec, handle = _build_handle(tmp_path, n=8192, chunk_size=8)
    fig, ax = plt.subplots()
    try:
        sync = ZoomSync(ax=ax, handle=handle, spec=spec, target_buckets=50, indicator=False)
        assert sync._indicator is None
        assert len(ax.texts) == 0
    finally:
        sync.disconnect()
        plt.close(fig)
        close_cache(handle)
