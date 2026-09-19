from __future__ import annotations

import sys
from pathlib import Path

import h5py
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from limelight import AxisDataType, Index, LimelightProject, PageGeometry, SourceProvenance, TimeSeriesArtist, hdf_array


POINT_COUNT = 50_000_000
BLOCK_SIZE = 1_000_000
DX = 0.001


def write_hdf5_source(path: Path) -> None:
    with h5py.File(path, "w") as handle:
        dataset = handle.create_dataset("/y", shape=(POINT_COUNT,), dtype=np.float64)

        rng = np.random.default_rng(seed=30)
        for start in range(0, POINT_COUNT, BLOCK_SIZE):
            end = min(start + BLOCK_SIZE, POINT_COUNT)
            index = np.arange(start, end, dtype=np.float64)
            t = index * DX
            carrier = np.sin(t * 0.05) + 0.3 * np.sin(t * 0.9 + 1.7)
            drift = 0.0000005 * index
            noise = rng.normal(scale=0.15, size=end - start)
            dataset[start:end] = carrier + drift + noise


def build_project(h5_path: Path) -> LimelightProject:
    project = LimelightProject(
        title="Large dataset envelope rendering",
        subtitle="50,000,000-point HDF5 series rendered via the largeseries LOD cache",
        description=(
            "Generated fake data for exercising the largeseries min/max LOD pyramid cache "
            "end-to-end: authoring an hdf source, a timeSeries artist, and live pan/zoom "
            "re-leveling in the Qt app."
        ),
        authors=["Limelight examples"],
        page=PageGeometry.paged(margin_lr_mm=5.0),
        document_version="0.1",
    )

    project.add_hdf_dataset(
        id="bigseries",
        hdf5_path=h5_path,
        y_arrays=[hdf_array("y", "/y")],
        index=Index.regular_time(step_nom=1, step_denom=1000, step_unit="s"),
        provenance=SourceProvenance(
            origin="Synthetic 50M-point series generated locally for testing the largeseries LOD cache.",
            release_date="2026-08-05",
        ),
    )

    f = project.add_line_figure(
        id="largedataset-figure",
        title="50M-point synthetic series",
        data="bigseries",
        x="time",
        y=[],
        time_series=[TimeSeriesArtist(data="bigseries", array="y", id="largedataset-envelope")],
        # The index is relative (elapsed seconds), so this is a number line,
        # not a calendar axis.
        x_axis=AxisDataType.continuous(label="Time", unit="s"),
        caption=(
            f"A {POINT_COUNT:,}-point synthetic series backed by an HDF5 file. "
            "Rendered as a min/max envelope; pan and zoom to see it re-level."
        ),
    )

    fig_view0 = project.story_figure(ref="largedataset-figure", id="largedataset-figure-view0", actions=[])

    md = f"""
# Some Markdown

Blah Blah

{fig_view0}
    """

    project.set_story_markdown(md, base_dir=Path(__file__).parent)

    return project


def main() -> None:
    build_dir = ROOT / "_build" / "examples"
    build_dir.mkdir(parents=True, exist_ok=True)

    h5_path = build_dir / "example30-largedataset-source.h5"
    # 50M float64 samples is a 400 MB file, so it is generated once and reused
    # on later runs rather than rebuilt every time.
    if not h5_path.exists():
        print(f"Generating {POINT_COUNT:,}-point HDF5 source at {h5_path} ...")
        write_hdf5_source(h5_path)

    output = build_dir / "example30-largedataset.limelight"
    build_project(h5_path).write_folder(output, overwrite=True)
    print(output)


if __name__ == "__main__":
    main()
