"""mpl_compare: one plotting script, drawn by matplotlib and by Limelight from
its capture, compared pixel for pixel."""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.image as mimage
import matplotlib.pyplot as plt
import numpy as np
import pytest

from limelight import mpl_compare
from limelight.cli import main as cli_main


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LIMELIGHT_CACHE_DIR", str(tmp_path / "cache"))
    yield
    plt.close("all")


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "plots.py"
    path.write_text("import numpy as np\nimport matplotlib.pyplot as plt\n" + textwrap.dedent(body))
    return path


def test_each_figure_is_drawn_both_ways_at_the_same_size(tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        """
        fig, ax = plt.subplots(figsize=(4, 3))
        ax.plot(np.linspace(0, 1, 50), np.random.normal(size=50), "k.", ms=2)
        fig2, (a, b) = plt.subplots(2, 1, figsize=(5, 4))
        a.plot([0, 1], [0, 1]); b.scatter([0, 1], [1, 0])
        plt.show()
        """,
    )
    results = mpl_compare.compare(script, tmp_path / "out", dpi=50)

    assert [result.figure_id for result in results] == ["figure-1", "figure-2"]
    for result, size in zip(results, [(150, 200), (200, 250)]):
        for path in (result.matplotlib_png, result.limelight_png, result.preview_png, result.difference_png, result.side_by_side_png):
            assert path.exists()
        assert mimage.imread(result.matplotlib_png).shape[:2] == size
        assert mimage.imread(result.limelight_png).shape[:2] == size
        assert 0.0 <= result.mismatch < 0.5
    # "." is a package marker; the marker size is not, and the report says so.
    assert not any("marker=" in message for message in results[0].warnings)
    assert any("markersize=2" in message for message in results[0].warnings)
    report = (tmp_path / "out" / "report.md").read_text()
    assert "figure-1" in report and "markersize=2" in report


def test_a_figure_drawn_out_of_capture_s_sight_is_a_clear_error(tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        """
        fig = plt.figure()
        fig.add_subplot().plot([0, 1], [0, 1])
        """,
    )
    with pytest.raises(ValueError, match="capture\\(\\) saw 0"):
        mpl_compare.compare(script, tmp_path / "out")


def test_a_function_can_be_compared_and_the_backend_is_restored(tmp_path: Path) -> None:
    before = matplotlib.get_backend()

    def draw() -> None:
        _, ax = plt.subplots()
        ax.plot([0.0, 1.0, 2.0], [2.0, 1.0, 3.0])

    [result] = mpl_compare.compare(draw, tmp_path / "out", dpi=40)
    assert result.mismatch < 0.5
    assert matplotlib.get_backend().lower() == before.lower()


def test_the_cli_writes_the_comparison(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = _script(tmp_path, "fig, ax = plt.subplots()\nax.plot([0, 1], [1, 0])\n")
    assert cli_main(["mpl-compare", str(script), "--out", str(tmp_path / "cmp"), "--dpi", "40"]) == 0
    out = capsys.readouterr().out
    assert "figure-1:" in out and "of pixels differ" in out
    assert (tmp_path / "cmp" / "figure-1" / "side-by-side.png").exists()
