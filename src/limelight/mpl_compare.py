"""Compare a matplotlib script's own figures with what Limelight draws from them.

The same plotting code is run twice:

    A. matplotlib calls  ->  E. plain matplotlib figures  ->  F. PNG
    A. matplotlib calls  ->  B. CapturingFigure/Axes  ->  C. package (dhall)
                         ->  matplotlib figures, drawn by the app's own static
                             renderer (what a story image or `LL pdf` uses)  ->  D. PNG

Both PNGs are the same pixel size at the same point scale, so they line up
pixel for pixel, and each figure gets a difference image, a side-by-side,
and the share of its pixels that differ. The `MplCaptureWarning`s the
captured run raises are reported with them: they say what the interactive
figure cannot show, and so where to expect a difference.

    from limelight import mpl_compare
    for result in mpl_compare.compare("plots.py", "compare-out"):
        print(result.figure_id, f"{result.mismatch:.1%}")

or `limelight-cli mpl-compare plots.py --out compare-out`.

The script must draw the same thing both times: both runs start from the
same `numpy.random`/`random` seed, but data from an unseeded
`numpy.random.default_rng()`, the clock or the network will differ.
"""

from __future__ import annotations

import contextlib
import os
import random
import runpy
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

# A pixel differs when any of its channels differs by more than this (0..1).
DEFAULT_THRESHOLD = 0.1


@dataclass
class FigureComparison:
    number: int
    figure_id: str
    matplotlib_png: Path
    limelight_png: Path
    preview_png: Path
    difference_png: Path
    side_by_side_png: Path
    # The share of pixels that differ by more than the threshold.
    mismatch: float
    # What capturing the script warned about (for the whole run; matplotlib
    # does not say which figure a call was for).
    warnings: list[str] = field(default_factory=list)


@contextlib.contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _run(source: Callable[[], Any] | str | Path, seed: int) -> None:
    np.random.seed(seed)
    random.seed(seed)
    with warnings.catch_warnings():
        # plt.show() under Agg says it cannot show anything; nothing is lost.
        warnings.filterwarnings("ignore", message=".*non-interactive.*")
        if callable(source):
            source()
            return
        script = Path(source).resolve()
        with _working_directory(script.parent):
            runpy.run_path(str(script), run_name="__main__")


def _new_figures(before: set[int]) -> list[Any]:
    import matplotlib.pyplot as plt

    return [plt.figure(number) for number in sorted(set(plt.get_fignums()) - before)]


def _read_rgb(path: Path) -> np.ndarray:
    import matplotlib.image as mimage

    image = mimage.imread(path)
    return image[..., :3].astype(float)


def _padded(image: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    out = np.ones((*shape, 3))
    out[: image.shape[0], : image.shape[1]] = image
    return out


def _difference(matplotlib_png: Path, limelight_png: Path, difference_png: Path, side_by_side_png: Path, threshold: float) -> float:
    import matplotlib.image as mimage
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    plain, drawn = _read_rgb(matplotlib_png), _read_rgb(limelight_png)
    shape = (max(plain.shape[0], drawn.shape[0]), max(plain.shape[1], drawn.shape[1]))
    plain, drawn = _padded(plain, shape), _padded(drawn, shape)
    delta = np.abs(plain - drawn).max(axis=2)
    mismatch = float((delta > threshold).mean())
    # White where the two agree, darker the more they differ.
    mimage.imsave(difference_png, 1.0 - np.clip(delta * 3.0, 0.0, 1.0), cmap="gray", vmin=0.0, vmax=1.0)

    height, width = shape
    figure = Figure(figsize=(3 * width / 100, height / 100 + 0.4), dpi=100)
    FigureCanvasAgg(figure)
    for index, (image, title) in enumerate(
        [(plain, "matplotlib"), (drawn, "Limelight"), (1.0 - np.clip(delta * 3.0, 0.0, 1.0), f"difference: {mismatch:.1%} of pixels")]
    ):
        axes = figure.add_axes((index / 3, 0.0, 1 / 3, height / (height + 40)))
        axes.imshow(image, cmap="gray" if image.ndim == 2 else None, vmin=0.0, vmax=1.0)
        axes.set_title(title, fontsize=10)
        axes.set_axis_off()
    figure.savefig(side_by_side_png)
    return mismatch


def compare(
    source: Callable[[], Any] | str | Path,
    out_dir: str | Path,
    *,
    dpi: float = 100.0,
    threshold: float = DEFAULT_THRESHOLD,
    seed: int = 0,
) -> list[FigureComparison]:
    """Runs `source` - a function, or the path of a script - plainly and
    under `mpl_capture.capture()`, and compares each figure it makes as
    matplotlib draws it with the same figure as Limelight draws it from the
    captured package. Writes each figure's PNGs to `out_dir/figure-<n>/`, and
    `out_dir/report.md`. pyplot is switched to Agg while it runs (closing
    any figures already open) and back afterwards."""

    import matplotlib
    import matplotlib.pyplot as plt

    from . import mpl_capture
    from .writer import FigureSize, LimelightProject

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    previous_backend = matplotlib.get_backend()
    plt.switch_backend("Agg")
    try:
        # E -> F: the script as it is.
        before = set(plt.get_fignums())
        _run(source, seed)
        plain = _new_figures(before)
        sizes: list[tuple[int, int]] = []
        for number, figure in enumerate(plain, start=1):
            folder = out_dir / f"figure-{number}"
            folder.mkdir(exist_ok=True)
            figure.savefig(folder / "matplotlib.png", dpi=dpi)
            width, height = figure.get_size_inches()
            sizes.append((round(width * dpi), round(height * dpi)))
        plt.close("all")

        # B -> C: the same script, captured into a package.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", mpl_capture.MplCaptureWarning)
            with mpl_capture.capture() as captured:
                _run(source, seed)
        messages = [str(warning.message) for warning in caught if issubclass(warning.category, mpl_capture.MplCaptureWarning)]
        if len(captured) != len(plain):
            raise ValueError(
                f"the script made {len(plain)} figure(s) but capture() saw {len(captured)}: figures made "
                "with plt.figure() and drawn on through fig.add_subplot()/fig.add_axes() are not captured"
            )
        project = LimelightProject(title="matplotlib comparison", authors=["limelight.mpl_compare"])
        for number, figure in enumerate(captured, start=1):
            figure.savefig(out_dir / f"figure-{number}" / "preview.png", dpi=dpi)
            suptitle = getattr(figure.mpl_figure, "_suptitle", None)
            width, height = figure.mpl_figure.get_size_inches()
            figure.configure(
                id=figure.id or f"figure-{number}",
                # matplotlib draws no title but a suptitle; neither does the comparison.
                title=figure.title or (suptitle.get_text() if suptitle is not None else ""),
            )
            if figure.size is None:
                figure.configure(size=FigureSize(aspect=float(height / width)))
            figure.add_to(project)
        package_dir = Path(tempfile.mkdtemp(prefix="limelight-mpl-compare-")) / "comparison.limelight"
        project.write_folder(package_dir)
        plt.close("all")

        # C -> D: the package, drawn as the app draws a figure to an image.
        from . import qt_app
        from .app import LimelightRuntime
        from .reader import open_limelight
        from .semantic import validate_manifest_semantics

        results: list[FigureComparison] = []
        with open_limelight(package_dir) as package:
            manifest = package.manifest_json()
            validate_manifest_semantics(manifest)
            runtime = LimelightRuntime(package, manifest)
            try:
                for number, (figure, (width, height)) in enumerate(zip(captured, sizes), start=1):
                    folder = out_dir / f"figure-{number}"
                    png = qt_app._render_static_figure_view_png_bytes(
                        runtime, figure.id, width=width, height=height, layout_dpi=dpi, dpi=dpi
                    )
                    (folder / "limelight.png").write_bytes(png)
                    mismatch = _difference(
                        folder / "matplotlib.png", folder / "limelight.png", folder / "difference.png",
                        folder / "side-by-side.png", threshold,
                    )
                    results.append(
                        FigureComparison(
                            number=number,
                            figure_id=figure.id,
                            matplotlib_png=folder / "matplotlib.png",
                            limelight_png=folder / "limelight.png",
                            preview_png=folder / "preview.png",
                            difference_png=folder / "difference.png",
                            side_by_side_png=folder / "side-by-side.png",
                            mismatch=mismatch,
                            warnings=list(messages),
                        )
                    )
            finally:
                runtime.close()
    finally:
        plt.close("all")
        plt.switch_backend(previous_backend)

    _write_report(out_dir / "report.md", source, results, messages, threshold)
    return results


def _write_report(path: Path, source: Any, results: list[FigureComparison], messages: list[str], threshold: float) -> None:
    lines = [
        f"# matplotlib vs Limelight: {source if not callable(source) else getattr(source, '__name__', 'function')}",
        "",
        f"A pixel differs when a channel differs by more than {threshold:g}.",
        "",
        "| figure | pixels differing | side by side |",
        "|---|---|---|",
    ]
    for result in results:
        lines.append(f"| {result.figure_id} | {result.mismatch:.1%} | {result.side_by_side_png.relative_to(path.parent)} |")
    lines += ["", "## What the capture warned about", ""]
    lines += [f"- {message}" for message in messages] or ["None: everything drawn was captured."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
