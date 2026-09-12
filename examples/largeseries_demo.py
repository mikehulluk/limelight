from __future__ import annotations

import sys
from pathlib import Path

import h5py
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from limelight.largeseries import SourceSpec, ZoomSync, build_or_get_cache, close_cache

POINT_COUNT = 20_000_000
CACHE_DIR = ROOT / "_build" / "examples" / "largeseries-cache"
SOURCE_PATH = ROOT / "_build" / "examples" / "largeseries-demo-source.h5"


def build_synthetic_source() -> None:
    SOURCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOURCE_PATH.exists():
        return

    print(f"Generating {POINT_COUNT:,} synthetic samples at {SOURCE_PATH}")
    with h5py.File(SOURCE_PATH, "w") as handle:
        dataset = handle.create_dataset("/y", shape=(POINT_COUNT,), dtype=np.float64)
        block = 1_000_000
        rng = np.random.default_rng(0)
        for start in range(0, POINT_COUNT, block):
            end = min(start + block, POINT_COUNT)
            x = np.arange(start, end, dtype=np.float64)
            values = (
                np.sin(x * 0.0003)
                + 0.3 * np.sin(x * 0.011)
                + 0.02 * rng.standard_normal(end - start)
            )
            if start <= 3_500_000 < end:
                spike_index = 3_500_000 - start
                values[spike_index] += 6.0
            dataset[start:end] = values


def main() -> None:
    build_synthetic_source()

    spec = SourceSpec(hdf5_path=SOURCE_PATH, y_dataset="/y")

    print("Building or reusing cache...")
    handle = build_or_get_cache(spec, cache_dir=CACHE_DIR)
    print(f"content_hash={handle.content_hash} n_levels={handle.n_levels}")

    fig, ax = plt.subplots(figsize=(10, 4))
    sync = ZoomSync(ax=ax, handle=handle, spec=spec)
    ax.set_title("limelight.largeseries demo: min/max envelope with live re-leveling")

    print("Simulating pan/zoom...")
    for x_start, x_end in [
        (0, POINT_COUNT - 1),
        (3_000_000, 4_000_000),
        (3_450_000, 3_550_000),
    ]:
        ax.set_xlim(x_start, x_end)
        fig.canvas.draw()
        print(f"  xlim=({x_start}, {x_end}) -> level={sync._line_min.get_xdata().shape[0]} buckets")

    output_path = ROOT / "_build" / "examples" / "largeseries-demo.png"
    fig.savefig(output_path, dpi=150)
    print(f"Saved {output_path}")

    sync.disconnect()
    plt.close(fig)
    close_cache(handle)


if __name__ == "__main__":
    main()
