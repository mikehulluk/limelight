"""The story view sets maths with the MathJax Limelight ships, never one from a network.

The last test renders a story in Qt WebEngine under the offscreen platform,
with every request that is not for a local file refused, and checks MathJax
set each equation in the document's maths font.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path

import pytest

from limelight.app import LimelightRuntime
from limelight.reader import open_limelight
from limelight.typography import MATHS_FONTS
from limelight.writer import LimelightProject

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

EQUATIONS = [
    r"\text{net\_worth} = \text{value} - \text{debt} + \text{cash}",
    r"\operatorname{band}\left( x \right) = \begin{cases} 0 & x < 100 \\ 2 & \text{otherwise} \end{cases}",
    # \mathbb's glyphs and \boldsymbol's extension are both loaded on demand:
    r"\frac{\text{cash} \times \text{rate}}{12} \geq \sqrt{x^2} \sum_{i=1}^{n} \int_0^1 \mathbb{R} \boldsymbol{\alpha}",
    # Syntax styles' TeX (story_syntax), and a label no lookup has styled, which MathJax sets plain:
    r"\textcolor{#0550ae}{\textit{cash}} = \textcolor{#cf222e}{\textbf{IF}} + \textcolor{#8250df}{\mathrm{x}}",
    r"\llrole{variable.state symbol.cash}{\text{cash}} + 1",
]


@pytest.fixture(scope="module")
def qt_app():
    import limelight.qt_app  # noqa: F401
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _runtime(tmp_path: Path, maths_font: str) -> LimelightRuntime:
    project = LimelightProject(title="Maths", authors=["Test"])
    project.set_story_markdown("# Maths\n\nBody text.\n", base_dir=Path("."))
    with open_limelight(project.write_folder(tmp_path / "maths.limelight")) as package:
        runtime = LimelightRuntime(package, package.manifest_json())
    runtime.typography = replace(runtime.typography, maths_font=maths_font)
    return runtime


def _local_path(uri: str) -> Path:
    from PySide6.QtCore import QUrl

    return Path(QUrl(uri).toLocalFile())


@pytest.mark.parametrize("maths_font", list(MATHS_FONTS))
def test_the_story_page_loads_mathjax_and_its_font_from_limelight_itself(tmp_path: Path, maths_font: str) -> None:
    from limelight.qt_app import MATHJAX_FONTS, MATHJAX_SCRIPT, _story_html

    page = _story_html("<p>\\(x\\)</p>", _runtime(tmp_path, maths_font))
    assert not re.search(r"https?://", page)
    assert _local_path(MATHJAX_SCRIPT).is_file()
    font = MATHS_FONTS[maths_font].mathjax
    assert f"font: '{font}'" in page
    assert (_local_path(MATHJAX_FONTS) / f"{font}-font" / "svg.js").is_file()


@pytest.mark.parametrize("maths_font", list(MATHS_FONTS))
def test_maths_is_set_with_no_network(qt_app, tmp_path: Path, maths_font: str) -> None:
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor

    from limelight.qt_app import _story_html

    refused: list[str] = []

    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info) -> None:  # noqa: N802 - Qt's name
            url = info.requestUrl().toString()
            if not url.startswith(("file:", "data:")):
                refused.append(url)
                info.block(True)

    body = "".join(f"<p>\\[{tex}\\]</p>" for tex in EQUATIONS)
    story = tmp_path / "story.html"
    story.write_text(_story_html(body, _runtime(tmp_path, maths_font)), encoding="utf-8")

    profile = QWebEngineProfile()
    interceptor = Offline()
    profile.setUrlRequestInterceptor(interceptor)
    page = QWebEnginePage(profile)
    loop = QEventLoop()
    page.loadFinished.connect(lambda _ok: loop.quit())
    page.load(QUrl.fromLocalFile(str(story)))
    loop.exec()

    # MathJax typesets after the page has loaded; ask until it has finished.
    result: dict = {}
    script = """JSON.stringify(window.MathJax && MathJax.startup && MathJax.startup.promise ? {
        containers: document.querySelectorAll('mjx-container svg').length,
        errors: document.querySelectorAll('mjx-merror, [data-mjx-error]').length,
        font: MathJax.startup.output ? MathJax.startup.output.font.constructor.NAME : ''} : null)"""
    for _ in range(100):
        page.runJavaScript(script, 0, lambda value: (result.update(value=value), loop.quit()))
        loop.exec()
        value = json.loads(result["value"] or "null")
        if value and value["containers"] == len(EQUATIONS):
            break
        QTimer.singleShot(100, loop.quit)
        loop.exec()

    assert refused == []
    assert value == {
        "containers": len(EQUATIONS),
        "errors": 0,
        "font": {"fira": "MathJaxFira", "new-computer-modern": "MathJaxNewcm"}[maths_font],
    }
