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
        ' + #text(fill: rgb("#953800"))[$upright(op("grow"))$] (x)'
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


# --- Labelled code blocks ---------------------------------------------------

CODE = "STATE cash = 0.0\nEVENT pay() -> [ SET cash += 1 ]\n"
CODE_TOKENS = [
    (0, 0, 5, "keyword"), (0, 6, 4, "variable variable.state symbol.cash"),
    (1, 0, 5, "keyword"), (1, 21, 4, "variable variable.state symbol.cash"),
]


def _package_with_code(tmp_path: Path) -> Path:
    from limelight.typography import Typography
    from limelight.writer import LimelightProject

    project = LimelightProject(title="Code", authors=["Test"], typography=Typography(syntax=DOCUMENT))
    project.add_code_tokens("source", CODE_TOKENS)
    project.set_story_markdown(f"# Code\n\n```forecast {{tokens=source}}\n{CODE}```\n", base_dir=Path("."))
    return project.write_folder(tmp_path / "code.limelight")


def test_a_fence_names_its_language_and_tokens() -> None:
    from limelight.story_syntax import fence_info

    assert fence_info("forecast {tokens=source}") == ("forecast", "source")
    assert fence_info('forecast { tokens="events-1" }') == ("forecast", "events-1")
    assert fence_info("python") == ("python", None)
    assert fence_info("") == ("", None)


@pytest.mark.parametrize("document, message", [
    (b"[1, 2]", 'an object with a "tokens" list'),
    (b'{"tokens": [[0, 0, 5]]}', "not \\[line, start, length, label\\]"),
    (b'{"tokens": [[0, -1, 5, "keyword"]]}', "negative position"),
    (b'{"tokens": [[0, 0, 5, "key word!"]]}', "not syntax style names"),
    (b"not json", "is not JSON"),
])
def test_a_code_tokens_file_is_checked(document: bytes, message: str) -> None:
    from limelight.story_syntax import parse_code_tokens

    with pytest.raises(ValueError, match=message):
        parse_code_tokens(document, "tokens")


def test_code_tokens_are_written_declared_and_verified(tmp_path: Path) -> None:
    import json

    from limelight.reader import LimelightError, open_limelight
    from limelight.semantic import validate_manifest_semantics
    from limelight.story_syntax import verify_code_tokens

    package = _package_with_code(tmp_path)
    assert json.loads((package / "story" / "code" / "source.json").read_text()) == {"tokens": [list(t) for t in CODE_TOKENS]}
    with open_limelight(package) as opened:
        manifest = opened.manifest_json()
        assert manifest["codeTokens"] == [{"id": "source", "path": "story/code/source.json"}]
        validate_manifest_semantics(manifest)
        verify_code_tokens(opened, manifest)
    (package / "story" / "code" / "source.json").write_text('{"tokens": [[0, 0]]}')
    with open_limelight(package) as opened, pytest.raises(LimelightError, match="not \\[line, start, length, label\\]"):
        verify_code_tokens(opened, opened.manifest_json())
    (package / "story" / "code" / "source.json").unlink()
    with open_limelight(package) as opened, pytest.raises(LimelightError, match="is missing"):
        verify_code_tokens(opened, opened.manifest_json())


def test_the_writer_refuses_a_bad_id_a_repeat_or_a_bad_token() -> None:
    from limelight.writer import LimelightProject

    project = LimelightProject(title="Code", authors=["Test"])
    project.add_code_tokens("source", CODE_TOKENS)
    with pytest.raises(ValueError, match="already declared"):
        project.add_code_tokens("source", [])
    with pytest.raises(ValueError, match="must be a letter"):
        project.add_code_tokens("1st", [])
    with pytest.raises(ValueError, match="no length"):
        project.add_code_tokens("other", [(0, 0, 0, "keyword")])


def test_a_labelled_code_block_is_set_in_its_styles_on_screen(tmp_path: Path) -> None:
    from limelight.app import LimelightRuntime
    from limelight.reader import open_limelight

    with open_limelight(_package_with_code(tmp_path)) as opened:
        runtime = LimelightRuntime(opened, opened.manifest_json())
    renderer = StoryMarkdownRenderer(syntax_style=style_of, code_tokens=runtime.code_tokens)
    screen = renderer.render(f"```forecast {{tokens=source}}\n{CODE}```\n")
    assert screen == (
        '<pre><code class="language-forecast">'
        '<span style="color: #cf222e; font-weight: 600">STATE</span> '
        '<span style="color: #0550ae; font-style: italic">cash</span> = 0.0\n'
        '<span style="color: #cf222e; font-weight: 600">EVENT</span> pay() -&gt; [ SET '
        '<span style="color: #0550ae; font-style: italic">cash</span> += 1 ]\n</code></pre>\n'
    )
    # A fence naming no tokens is markdown-it's own, and one naming unknown tokens is set plain:
    assert renderer.render("```forecast\nx\n```\n") == '<pre><code class="language-forecast">x\n</code></pre>\n'
    assert renderer.render("```forecast {tokens=missing}\nx\n```\n") == '<pre><code class="language-forecast">x\n</code></pre>\n'


def test_a_labelled_code_block_is_set_in_its_styles_in_the_pdf(tmp_path: Path) -> None:
    import typst

    from limelight.app import LimelightRuntime
    from limelight.reader import open_limelight
    from limelight.story_typst import TypstPage, TypstSpacing, typst_preamble
    from limelight.typography import FONTS_DIR, Typography, resolve_fonts

    with open_limelight(_package_with_code(tmp_path)) as opened:
        runtime = LimelightRuntime(opened, opened.manifest_json())
    body = StoryTypstRenderer(syntax_style=style_of, code_tokens=runtime.code_tokens).render(
        f"```forecast {{tokens=source}}\n{CODE}```\n"
    )
    assert body.startswith('#ll-code-block[#text(fill: rgb("#cf222e"), weight: 600, "STATE")#" "')
    assert '#text(fill: rgb("#0550ae"), style: "italic", "cash")' in body
    preamble = typst_preamble(
        title="Doc", page=TypstPage(210.0, 297.0, 15.0, 20.0), spacing=TypstSpacing(3.0, 4.5, 5.0, 2.0),
        typography=Typography(), fonts=resolve_fonts({"story": {}}, None),
    )
    source = tmp_path / "story.typ"
    source.write_text(preamble + "\n" + body, encoding="utf-8")
    pdf = typst.compile(str(source), font_paths=[str(d) for d in FONTS_DIR.iterdir() if d.is_dir()])
    assert pdf[:4] == b"%PDF"


def test_tokens_that_do_not_fit_the_code_are_dropped() -> None:
    from limelight.story_syntax import CodeToken, code_block_html

    tokens = [CodeToken(0, 0, 50, "keyword"), CodeToken(0, 0, 1, "keyword"), CodeToken(0, 0, 2, "keyword"), CodeToken(5, 0, 1, "keyword")]
    assert code_block_html("ab\n", "", tokens, style_of) == (
        '<pre><code><span style="color: #cf222e; font-weight: 600">a</span>b\n</code></pre>\n'
    )


# --- Labels from other tools ------------------------------------------------
#
# Nothing above is particular to one tool's names; these use a physics
# paper's maths and a Python listing labelled by any lexer.

PHYSICS = DOCUMENT + (SyntaxStyle("symbol.velocity", colour="#1a7f37"),)


def test_a_later_name_in_a_label_wins_over_an_earlier_one_whatever_its_depth() -> None:
    styles = (SyntaxStyle("variable.readonly", colour="#8250df"), SyntaxStyle("symbol", colour="#1a7f37"))
    assert resolve_syntax_style("variable.readonly symbol.rate", styles).colour == "#1a7f37"
    assert resolve_syntax_style("symbol.rate variable.readonly", styles).colour == "#8250df"
    # A dotted name wins over its own prefixes:
    assert resolve_syntax_style("variable.readonly", DOCUMENT).colour == "#8250df"


def test_a_physics_symbol_is_styled_on_screen_and_on_paper() -> None:
    def physics_style(label: str) -> ResolvedSyntaxStyle:
        return resolve_syntax_style(label, PHYSICS)

    tex = r"\llrole{variable symbol.velocity}{\vec{v}} = \frac{d \llrole{variable}{x}}{dt}"
    assert tex_with_roles(tex, physics_style).startswith(r"{\textcolor{#1a7f37}{\vec{v}}}")
    paper = typst_maths(tex, physics_style)
    assert paper.startswith('#text(fill: rgb("#1a7f37"))[$')
    assert "arrow(v)" in paper


def test_a_python_block_labelled_in_code_points_is_set_in_its_styles() -> None:
    from limelight.story_syntax import CodeToken, code_block_html

    styles = DOCUMENT + (SyntaxStyle("string", colour="#0a3069"), SyntaxStyle("comment", colour="#6e7781"))
    code = 'name = "café"  # π\nprint(name)\n'
    tokens = [
        CodeToken(0, 0, 4, "variable"), CodeToken(0, 7, 6, "string"), CodeToken(0, 15, 3, "comment"),
        CodeToken(1, 0, 5, "function"), CodeToken(1, 6, 4, "variable"),
    ]
    screen = code_block_html(code, "python", tokens, lambda label: resolve_syntax_style(label, styles))
    assert screen.startswith('<pre><code class="language-python">')
    assert '<span style="color: #0a3069">"café"</span>' in screen
    assert '<span style="color: #6e7781"># π</span>' in screen
    assert '<span style="color: #953800; font-style: normal">print</span>' in screen


@pytest.mark.parametrize("inner, italic, screen, paper", [
    # Upright: the whole token, lower-case Greek as Unicode, which MathJax's \mathrm sets upright.
    (r"\alpha", False, r"\mathrm{α}", "upright(alpha)"),
    (r"\hat{\theta}", False, r"\mathrm{\hat{θ}}", "upright(hat(theta))"),
    (r"\vec{v}", False, r"\mathrm{\vec{v}}", "upright(arrow(v))"),
    # Italic: what is not maths italic already; a letter keeps its maths italic.
    (r"\Gamma", True, r"\mathit{\Gamma}", "italic(Gamma)"),
    (r"\alpha", True, r"\alpha", "italic(alpha)"),
    (r"\vec{v}", True, r"\vec{v}", "italic(arrow(v))"),
    # Text is upright already:
    (r"\text{rate}", False, r"\text{rate}", '"rate"'),
])
def test_a_slant_applies_to_the_whole_token_alike_on_screen_and_on_paper(inner, italic, screen, paper) -> None:
    style = ResolvedSyntaxStyle(italic=italic)
    assert styled_tex(inner, style) == screen
    assert typst_maths(rf"\llrole{{x}}{{{inner}}}", lambda label: style) == paper
