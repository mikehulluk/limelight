"""Labelled tokens in a story's maths and inline code, set in the document's syntax styles."""

from __future__ import annotations

from pathlib import Path

import pytest

from limelight.story_markdown import StoryMarkdownRenderer
from limelight.story_syntax import code_html, find_roles, styled_tex, tex_with_roles
from limelight.story_typst import StoryTypstRenderer, typst_maths
from limelight.typography import ResolvedSyntaxStyle, SyntaxStyle, resolve_syntax_style

DOCUMENT = (
    SyntaxStyle("variable", colour="#24292f", italic=True),
    SyntaxStyle("variable.state", colour="#0550ae"),
    SyntaxStyle("variable.readonly", colour="#8250df", italic=False),
    SyntaxStyle("function", colour="#953800", italic=False),
    SyntaxStyle("keyword", colour="#cf222e", weight=600),
)


def style_of(label: str) -> ResolvedSyntaxStyle:
    return resolve_syntax_style(label, DOCUMENT)


CASH = r"\llrole{variable variable.state symbol.cash}{\text{cash}}"
RATE = r"\llrole{variable variable.readonly symbol.rate}{\text{rate}}"


def test_roles_are_found_with_their_braces_balanced() -> None:
    tex = rf"{CASH} = \frac{{{RATE}}}{{12}} + \llrole{{variable}}{{x_{{1}}}} + \llroles"
    roles = find_roles(tex)
    assert [(r.label, r.inner) for r in roles] == [
        ("variable variable.state symbol.cash", r"\text{cash}"),
        ("variable variable.readonly symbol.rate", r"\text{rate}"),
        ("variable", "x_{1}"),
    ]
    with pytest.raises(ValueError, match="two braced arguments"):
        find_roles(r"\llrole{variable} x")


def test_without_styles_a_labelled_token_is_set_plain() -> None:
    assert tex_with_roles(rf"{CASH} + 1", None) == r"{\text{cash}} + 1"
    assert tex_with_roles(r"x + 1", None) == r"x + 1"


def test_for_mathjax_text_is_slanted_and_coloured_with_tex_commands() -> None:
    assert tex_with_roles(rf"{CASH} = {RATE}", style_of) == (
        r"{\textcolor{#0550ae}{\textit{cash}}} = {\textcolor{#8250df}{\text{rate}}}"
    )
    # A single letter is italic already; upright takes \mathrm. Bold is \textbf on text, else \boldsymbol.
    assert styled_tex("x", style_of("variable.readonly")) == r"\textcolor{#8250df}{\mathrm{x}}"
    assert styled_tex(r"\text{IF}", style_of("keyword")) == r"\textcolor{#cf222e}{\textbf{IF}}"
    assert styled_tex(r"\operatorname{grow}", style_of("keyword")) == r"\textcolor{#cf222e}{\boldsymbol{\operatorname{grow}}}"


def test_for_typst_each_token_is_converted_then_styled_with_maths_functions() -> None:
    tex = rf"\begin{{aligned}} {CASH} &= \frac{{{RATE}}}{{12}} + \llrole{{function}}{{\operatorname{{grow}}}}\left( x \right) \end{{aligned}}"
    assert typst_maths(tex, style_of) == (
        '#text(fill: rgb("#0550ae"))[$italic("cash")$] &= #text(fill: rgb("#8250df"))[$"rate"$]/12'
        ' + #text(fill: rgb("#953800"))[$op("grow")$] (x)'
    )
    assert typst_maths(rf"{CASH} + 1", None) == '"cash" + 1'


def test_inline_code_takes_its_roles_style_on_screen_and_on_paper() -> None:
    markdown = 'Set `cash`{role="variable variable.state symbol.cash"} each `month`.'
    screen = StoryMarkdownRenderer(syntax_style=style_of).render(markdown)
    assert '<code style="color: #0550ae; font-style: italic">cash</code>' in screen
    assert "<code>month</code>" in screen
    assert 'role=' not in screen
    paper = StoryTypstRenderer(syntax_style=style_of).render(markdown)
    assert '#[#show raw: set text(fill: rgb("#0550ae"), style: "italic"); #raw("cash")]' in paper
    assert '#raw("month")' in paper
    # With no styles, a labelled token is plain code:
    assert code_html("cash", "variable.state", None) == "<code>cash</code>"


def test_labelled_maths_in_a_table_reaches_mathjax_styled() -> None:
    markdown = f"| Constant | Note |\n| --- | --- |\n| `rate`{{role=\"variable.readonly\"}} | ${RATE} = 0.05$ |\n"
    screen = StoryMarkdownRenderer(syntax_style=style_of).render(markdown)
    assert r"\({\textcolor{#8250df}{\text{rate}}} = 0.05\)" in screen
    assert '<code style="color: #8250df; font-style: normal">rate</code>' in screen


def test_a_styled_story_compiles_in_typst(tmp_path: Path) -> None:
    import typst

    from limelight.story_typst import TypstPage, TypstSpacing, typst_preamble
    from limelight.typography import Typography, resolve_fonts

    body = StoryTypstRenderer(syntax_style=style_of).render(
        f"The cash $${CASH} = {RATE} \\times 12$$ grows; `cash`{{role=\"variable.state\"}} is a state.\n"
    )
    preamble = typst_preamble(
        title="Doc", page=TypstPage(210.0, 297.0, 15.0, 20.0), spacing=TypstSpacing(3.0, 4.5, 5.0, 2.0),
        typography=Typography(maths_font="fira"), fonts=resolve_fonts({"story": {}}, None),
    )
    source = tmp_path / "story.typ"
    source.write_text(preamble + "\n" + body, encoding="utf-8")
    from limelight.typography import FONTS_DIR

    pdf = typst.compile(str(source), font_paths=[str(d) for d in FONTS_DIR.iterdir() if d.is_dir()])
    assert pdf[:4] == b"%PDF"
