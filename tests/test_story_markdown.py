from __future__ import annotations

import pytest

from limelight.story_markdown import (
    CrossReferenceError,
    ImageWidthError,
    StoryMarkdownRenderer,
    numbered_story_items,
    resolve_cross_references,
    rewrite_image_destinations,
    image_width_css,
    story_image_references,
    unparsed_attribute_text,
)


def test_an_image_alone_in_a_paragraph_is_a_figure() -> None:
    references = story_image_references("![Bench setup](rig.png)\n")

    assert len(references) == 1
    assert references[0].is_figure
    assert references[0].caption == "Bench setup"


def test_an_image_inside_a_sentence_is_not_a_figure() -> None:
    references = story_image_references("Prose with an ![icon](i.png) in it.\n")

    assert not references[0].is_figure


def test_a_title_becomes_the_caption_and_alt_stays_alt() -> None:
    references = story_image_references('![Alt for a reader](rig.png "Visible caption")\n')

    assert references[0].alt == "Alt for a reader"
    assert references[0].caption == "Visible caption"


def test_the_reference_form_resolves_to_the_same_thing() -> None:
    inline = story_image_references("![Bench](rig.png)\n")
    reference = story_image_references("![Bench][rig]\n\n[rig]: rig.png\n")

    assert reference[0].src == inline[0].src
    assert reference[0].caption == inline[0].caption
    assert reference[0].is_figure == inline[0].is_figure


def test_rewriting_covers_both_forms_and_leaves_links_alone() -> None:
    markdown = (
        "![Inline](images/rig.png)\n\n"
        '![Titled](images/rig.png "Caption")\n\n'
        "![Reference][rig]\n\n"
        "[rig]: images/other.png\n\n"
        "A [link](https://example.com) and a [ref link][doc].\n\n"
        "[doc]: https://example.com/doc\n"
    )

    rewritten = rewrite_image_destinations(
        markdown, lambda src: "assets/x.png" if src.startswith("images/") else None
    )

    assert "![Inline](assets/x.png)" in rewritten
    # The title survives, because only the destination is replaced.
    assert '![Titled](assets/x.png "Caption")' in rewritten
    assert "[rig]: assets/x.png" in rewritten
    assert "[link](https://example.com)" in rewritten
    assert "[doc]: https://example.com/doc" in rewritten


def test_figures_and_images_share_one_sequence() -> None:
    markdown = (
        "@figure(fig-a)\n\n"
        "![First photo](one.png)\n\n"
        "@figure(fig-b)\n\n"
        "Prose with an ![icon](i.png) that is not a figure.\n\n"
        "![Second photo](two.png)\n"
    )

    items = numbered_story_items(markdown)

    assert [(item.kind, item.key, item.number) for item in items] == [
        ("figureView", "fig-a", 1),
        ("image", "one.png", 2),
        ("figureView", "fig-b", 3),
        ("image", "two.png", 4),
    ]


def test_a_repeated_figure_reuses_its_number() -> None:
    markdown = "@figure(fig-a)\n\n@figure(fig-b)\n\n@figure(fig-a)\n"

    numbers = [item.number for item in numbered_story_items(markdown)]

    assert numbers == [1, 2, 1]


def test_a_figure_renders_with_its_caption_and_number() -> None:
    renderer = StoryMarkdownRenderer(
        resolve_image=lambda src: f"data:{src}",
        image_number=lambda src: 3,
    )

    rendered = renderer.render("![Bench setup](rig.png)\n")

    assert '<figure class="limelight-story-figure">' in rendered
    assert '<figcaption><span class=\"limelight-caption-label\">Figure 3.</span> Bench setup</figcaption>' in rendered


def test_an_unnumbered_image_renders_without_a_prefix() -> None:
    renderer = StoryMarkdownRenderer(
        resolve_image=lambda src: f"data:{src}",
        image_number=lambda src: None,
    )

    rendered = renderer.render("![Bench setup](rig.png)\n")

    assert "<figcaption>Bench setup</figcaption>" in rendered


def test_an_unresolved_image_becomes_a_visible_placeholder() -> None:
    renderer = StoryMarkdownRenderer(resolve_image=lambda src: None)

    rendered = renderer.render("![Bench setup](missing.png)\n")

    assert "limelight-missing-image" in rendered
    assert "missing.png" in rendered
    assert "<img" not in rendered


def test_raw_html_stays_disabled() -> None:
    renderer = StoryMarkdownRenderer(resolve_image=lambda src: f"data:{src}")

    rendered = renderer.render("<script>alert(1)</script>\n")

    assert "<script>" not in rendered


def test_a_cross_reference_gets_the_number_filled_in() -> None:
    resolved = resolve_cross_references(
        "Shown in [Figure](#fig-a).\n", {"fig-a": 3}.get
    )

    assert resolved == "Shown in [Figure 3](#fig-a).\n"


def test_the_authors_caption_word_is_kept() -> None:
    resolved = resolve_cross_references(
        "See [Fig.](#fig-a) and [table](#tab-b).\n", {"fig-a": 1, "tab-b": 2}.get
    )

    assert "[Fig. 1](#fig-a)" in resolved
    assert "[table 2](#tab-b)" in resolved


def test_the_reference_form_is_resolved_too() -> None:
    markdown = "See [Figure][ref].\n\n[ref]: #fig-a\n"

    resolved = resolve_cross_references(markdown, {"fig-a": 4}.get)

    assert "[Figure 4][ref]" in resolved


def test_ordinary_link_text_is_left_alone() -> None:
    markdown = "Read [the appendix](#appendix) and [this](https://example.com).\n"

    resolved = resolve_cross_references(markdown, {}.get)

    assert resolved == markdown


def test_a_cross_reference_to_nothing_fails_the_build() -> None:
    with pytest.raises(CrossReferenceError, match="not a figure or an image"):
        resolve_cross_references("See [Figure](#nope).\n", {}.get)


def test_anchors_are_rewritten_for_a_renderer_that_cannot_follow_them() -> None:
    renderer = StoryMarkdownRenderer(anchor_href=lambda anchor: f"https://host/{anchor}")

    rendered = renderer.render("See [Figure 3](#fig-a) and [out](https://example.com).\n")

    assert 'href="https://host/fig-a"' in rendered
    # An external link is left as written.
    assert 'href="https://example.com"' in rendered


def test_a_figure_image_carries_its_anchor() -> None:
    renderer = StoryMarkdownRenderer(
        resolve_image=lambda src: f"data:{src}",
        image_anchor=lambda src: "bench-rig",
    )

    rendered = renderer.render("![Bench](rig.png)\n")

    assert '<figure class="limelight-story-figure" id="bench-rig">' in rendered


class _story_runtime:
    """The slice of a runtime the story stylesheet reads."""

    def __init__(self) -> None:
        from limelight.app import StorySpacing
        from limelight.typography import Typography, resolve_fonts

        self.story_spacing = StorySpacing(3.0, 4.5, 5.0, 2.0)
        self.typography = Typography()
        self.fonts = resolve_fonts({"story": {}}, None)


def test_both_renderers_constrain_a_story_image_to_its_column() -> None:
    """The panel stylesheet was missing this, so images overflowed and clipped.

    The PDF document had the rule and the story panel did not, which is not a
    difference either renderer should have: an image is laid out to the text
    column in both.
    """

    from limelight.pdf_export import STORY_IMAGE_CSS
    from limelight.qt_app import _story_html

    assert "max-width: 100%" in STORY_IMAGE_CSS
    panel_document = _story_html("<p>body</p>", _story_runtime())
    assert "img.limelight-story-image" in panel_document
    assert "max-width: 100%" in panel_document


def test_the_story_body_does_not_let_child_margins_escape() -> None:
    """Without this the panel measures short and the block gets a scrollbar.

    A heading's top margin collapses out through `body` unless something stops
    it, so `body` reports a height that leaves the margin out while the page
    still renders it. The panel is sized from that measurement.
    """

    from limelight.app import StorySpacing
    from limelight.qt_app import _story_html

    assert "display: flow-root" in _story_html("<p>body</p>", _story_runtime())


def test_a_width_written_at_the_reference_is_applied() -> None:
    renderer = StoryMarkdownRenderer(resolve_image=lambda src: "data:x")

    assert 'style="width: 80mm"' in renderer.render("![B](r.png){width=80mm}\n")
    assert 'style="width: 60%"' in renderer.render('![B](r.png){width="60%"}\n')


def test_the_asset_width_is_used_when_the_reference_says_nothing() -> None:
    renderer = StoryMarkdownRenderer(
        resolve_image=lambda src: "data:x", image_width=lambda src: "40mm"
    )

    assert 'style="width: 40mm"' in renderer.render("![B](r.png)\n")


def test_a_width_at_the_reference_beats_the_asset_default() -> None:
    renderer = StoryMarkdownRenderer(
        resolve_image=lambda src: "data:x", image_width=lambda src: "40mm"
    )

    rendered = renderer.render("![B](r.png){width=90mm}\n")

    assert 'style="width: 90mm"' in rendered
    assert "40mm" not in rendered


def test_an_unsized_image_gets_no_width_style() -> None:
    renderer = StoryMarkdownRenderer(resolve_image=lambda src: "data:x")

    assert "style=" not in renderer.render("![B](r.png)\n")


def test_a_width_in_pixels_is_refused() -> None:
    # Pixels mean nothing on paper, and the story has a page.
    with pytest.raises(ImageWidthError, match="millimetres or a percentage"):
        image_width_css("300px")


def test_an_unquoted_percentage_is_detected_rather_than_printed() -> None:
    # markdown-it leaves this as literal text, which would otherwise appear in
    # the story with no indication that a width was meant.
    assert unparsed_attribute_text("![B](r.png){width=60%}\n") == ["{width=60%}"]
    assert unparsed_attribute_text('![B](r.png){width="60%"}\n') == []


def test_attributes_other_than_width_do_not_reach_the_page() -> None:
    renderer = StoryMarkdownRenderer(resolve_image=lambda src: "data:x")

    rendered = renderer.render('![B](r.png){width=20mm onerror="x"}\n')

    assert "onerror" not in rendered
    assert 'style="width: 20mm"' in rendered


def test_a_numbered_image_with_no_caption_still_shows_its_number() -> None:
    renderer = StoryMarkdownRenderer(
        resolve_image=lambda src: f"data:{src}",
        image_number=lambda src: 3,
    )

    rendered = renderer.render("![](rig.png)\n")

    assert '<figcaption><span class=\"limelight-caption-label\">Figure 3.</span></figcaption>' in rendered


def test_the_caption_markup_escapes_the_text_but_not_the_label() -> None:
    from limelight.story_markdown import figure_caption_markup

    assert figure_caption_markup(2, "a < b") == '<span class=\"limelight-caption-label\">Figure 2.</span> a &lt; b'
    assert figure_caption_markup(None, "plain") == "plain"
    assert figure_caption_markup(None, "") == ""
