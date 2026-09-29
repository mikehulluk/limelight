"""Story Markdown written out as Typst, for the PDF."""

from __future__ import annotations

from limelight.story_typst import (
    StoryTypstRenderer,
    TypstImage,
    TypstPage,
    TypstSpacing,
    tex_to_typst,
    typst_caption,
    typst_preamble,
    typst_string,
    typst_text,
)
from limelight.typography import Typography, resolve_fonts


def render(markdown: str, **kwargs) -> str:
    return StoryTypstRenderer(**kwargs).render(markdown)


def test_text_is_escaped_so_it_prints_as_written() -> None:
    assert typst_text("a #b $c *d* _e_ `f` <g> @h [i] ~j") == (
        r"a \#b \$c \*d\* \_e\_ \`f\` \<g\> \@h \[i\] \~j"
    )
    # `//` would open a comment, and `--` is a dash.
    assert typst_text("https://x.org -- 3 = 1+2") == r"https:\/\/x.org \-\- 3 \= 1\+2"
    # `1.` opens a numbered list at the start of a line.
    assert typst_text("1986. A year") == r"1986\. A year"


def test_a_string_literal_survives_quotes_and_backslashes() -> None:
    assert typst_string('say "hi" \\ bye') == '"say \\"hi\\" \\\\ bye"'


def test_headings_and_inline_markup() -> None:
    typst = render("## A *b* **c** ~~d~~ `e`")

    assert typst == "== A #emph[b] #strong[c] #strike[d] #raw(\"e\")"


def test_a_soft_break_is_a_space_so_no_line_opens_as_markup() -> None:
    # `= two` opening a line of Typst would be a heading.
    assert render("one\n= two") == "one \\= two"


def test_lists_nest_by_indenting_under_their_marker() -> None:
    typst = render("1. one\n2. two\n   - inner\n")

    assert typst == "1. one\n2. two\n   - inner"


def test_an_ordered_list_keeps_the_number_it_starts_at() -> None:
    assert render("5. five\n6. six") == "5. five\n6. six"


def test_a_loose_list_keeps_its_paragraphs_apart() -> None:
    assert render("- one\n\n- two") == "- one\n\n- two"


def test_maths_is_converted_from_tex() -> None:
    assert render("Let $x^2$ be.") == "Let $x^2$ be."
    assert render("$$\n\\frac{a+b}{c}\n$$") == "$ (a + b)/c $"


def test_a_math_fence_is_display_maths() -> None:
    assert render("```math\n\\alpha\n```") == "$ alpha $"


def test_tex_that_cannot_be_converted_is_shown_rather_than_failing_the_story() -> None:
    typst = render("$\\notacommand{x}$")

    assert typst == '#ll-notice[#raw("\\\\notacommand{x}")]'


def test_code_blocks_keep_their_language_and_their_backticks() -> None:
    typst = render("```python\nx = `1`\n```")

    assert typst == '#raw("x = `1`", block: true, lang: "python")'


def test_a_link_to_a_known_anchor_navigates_and_others_are_text() -> None:
    typst = render("[Figure 3](#fig-a) and [the appendix](#appendix)", anchors={"fig-a"})

    assert typst == '#link(label("fig-a"))[Figure 3] and the appendix'


def test_an_external_link_is_a_link() -> None:
    assert render("[site](https://x.org)") == '#link("https://x.org")[site]'


def test_markdown_tables_become_typst_tables() -> None:
    typst = render("| a | b |\n|:--|--:|\n| 1 | 2 |")

    assert typst == (
        "#table(\n  columns: 2,\n  align: (left, right,),\n"
        "  table.header([#strong[a]], [#strong[b]]),\n  [1],\n  [2],\n)"
    )


def _image(src: str) -> TypstImage | None:
    return TypstImage(f"images/{src.rsplit('/', 1)[-1]}", 40.0) if src != "missing.png" else None


def test_an_image_alone_in_its_paragraph_is_a_numbered_figure() -> None:
    typst = render(
        '![The rig](images/rig.jpg "Bench rig")',
        image=_image,
        image_number=lambda src: 3,
        image_anchor=lambda src: "rig",
    )

    assert typst == (
        '#ll-figure(image("images/rig.jpg", width: 100%), width: 40.00mm, '
        "caption: [#ll-caption[#ll-caption-label[Figure 3.] Bench rig]])"
        '#label("rig")'
    )


def test_a_figure_takes_the_width_written_at_it_over_the_assets() -> None:
    typst = render(
        '![Rig](images/rig.jpg){width="60%"}',
        image=_image,
        image_width=lambda src: "90mm",
    )

    assert "width: 60%, caption:" in typst


def test_a_figure_without_a_width_takes_the_assets() -> None:
    typst = render("![Rig](images/rig.jpg)", image=_image, image_width=lambda src: "90mm")

    assert "width: 90mm, caption:" in typst


def test_an_image_within_a_sentence_is_inline() -> None:
    typst = render("A ![](images/dot.png){width=8mm} dot.", image=_image)

    assert typst == 'A #box(image("images/dot.png", width: 8mm)) dot.'


def test_a_missing_image_is_a_visible_placeholder() -> None:
    assert render("![x](missing.png)", image=_image) == "#ll-notice[Missing image: missing.png]"


def test_an_image_shown_twice_is_labelled_once() -> None:
    renderer = StoryTypstRenderer(image=_image, image_anchor=lambda src: "rig")

    first = renderer.render("![Rig](images/rig.jpg)")
    second = renderer.render("![Rig](images/rig.jpg)")

    assert first.endswith('#label("rig")')
    assert "#label" not in second


def test_a_rule_breaks_the_page_or_rules_a_continuous_sheet() -> None:
    assert render("a\n\n---\n\nb") == "a\n\n#pagebreak(weak: true)\n\nb"
    assert "#line(length: 100%" in render("a\n\n---\n\nb", paged=False)


def test_a_caption_can_be_just_a_number_or_just_text() -> None:
    assert typst_caption(2, "") == "#ll-caption[#ll-caption-label[Figure 2.]]"
    assert typst_caption(None, "Plain") == "#ll-caption[Plain]"
    assert typst_caption(None, "") == ""


def test_the_preamble_sets_every_kind_of_text_from_the_typography() -> None:
    preamble = typst_preamble(
        title="Doc",
        page=TypstPage(210.0, 297.0, 15.0, 20.0),
        spacing=TypstSpacing(3.0, 4.5, 5.0, 2.0),
        typography=Typography(),
        fonts=resolve_fonts({"story": {}}, None),
    )

    assert '#set text(font: ("Ubuntu", "Noto Sans",), size: 10.5pt, weight: 400, style: "normal"' in preamble
    assert '#show heading.where(level: 1): set text(font: ("Ubuntu", "Noto Sans",), size: 17.85pt, weight: 700' in preamble
    assert '#show raw: set text(font: ("Ubuntu Mono", "DejaVu Sans Mono",)' in preamble
    # Line height 1.5 is half an em of leading between lines an em tall.
    assert "#set par(leading: 0.5em" in preamble


def test_tex_to_typst_writes_a_fraction_typst_stacks() -> None:
    assert tex_to_typst(r"\frac{dP}{dt}") == "(d P)/(d t)"
