"""Story Markdown parsing and rendering.

This module owns the one Markdown configuration a Limelight story is written in,
so the builder, the story panel and the PDF exporter all agree about what a
story document means. It deliberately imports nothing from Qt: the builder
needs to find and rewrite image references without a display, and both
renderers need the same parser that produced them.

Limelight stories are GFM-like Markdown with raw HTML disabled and TeX-style maths
rendered by the application. On top of that they use two conventions:

* An image alone in its own paragraph is a figure, and its caption comes from
  its title text if it has one, otherwise from its alt text. An image inline
  within a sentence stays an ordinary image with no caption. This is pandoc's
  ``implicit_figures`` rule, chosen because it is an existing convention and
  because a story using it still previews correctly in an editor that knows
  nothing about Limelight.
* A link to a ``#fragment`` naming a figure or image is a cross-reference. The
  builder resolves those into literal text, so nothing here has to.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.attrs import attrs_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin

# Caption words a cross-reference may use as its link text. The builder
# replaces the text of such a link with the number of whatever it points at.
REFERENCE_LABELS = ("Figure", "figure", "Fig.", "fig.", "Table", "table", "Image", "image")

# A story figure block. Kept in step with app.FIGURE_DIRECTIVE_PATTERN, which
# splits the same documents into blocks at read time.
FIGURE_DIRECTIVE_PATTERN = re.compile(r"^\s*@figure\(([^)]+)\)\s*$")

# `![alt](dest "title")`, capturing the destination so it can be rewritten in
# place. Only the destination is touched, so alt text and titles survive
# whatever they contain.
INLINE_IMAGE_PATTERN = re.compile(
    r"(!\[(?:[^\]\\]|\\.)*\]\(\s*)(<[^>]*>|[^)\s]+)(\s*(?:\"[^\"]*\"|'[^']*'|\([^)]*\))?\s*\))"
)

# `[label]: dest "title"` at the head of a line - the reference form's half of
# the same job.
LINK_DEFINITION_PATTERN = re.compile(
    r"(?m)^(\s{0,3}\[(?:[^\]\\]|\\.)+\]:\s*)(<[^>]*>|\S+)(\s*(?:\"[^\"]*\"|'[^']*'|\([^)]*\))?\s*)$"
)


@dataclass(frozen=True)
class StoryImageReference:
    """One image reference found in a story document."""

    src: str
    alt: str
    title: str | None
    is_figure: bool

    @property
    def caption(self) -> str:
        """What a reader sees under the image.

        The title slot wins when both are present, so alt text can stay a
        description for a screen reader while the caption reads as prose.
        """

        return self.title if self.title else self.alt


def story_markdown_parser() -> MarkdownIt:
    """The Markdown configuration every Limelight story is parsed with."""

    parser = MarkdownIt("gfm-like", {"html": False}).use(
        dollarmath_plugin,
        renderer=_render_math,
    )
    # `![alt](rig.png){width=80mm}`. Only a width is honoured; anything else an
    # author attaches is parsed and then ignored rather than reaching the page,
    # which keeps this from becoming a way to write arbitrary HTML attributes.
    return parser.use(attrs_plugin, allowed=["width"])


def _render_math(content: str, options: dict[str, Any]) -> str:
    escaped = html.escape(content, quote=False)
    if options.get("display_mode"):
        return f"\\[{escaped}\\]"
    return f"\\({escaped}\\)"


def _is_figure_paragraph(tokens: list[Token], inline_index: int) -> bool:
    """Whether an inline token is a paragraph containing only one image."""

    if inline_index == 0 or tokens[inline_index - 1].type != "paragraph_open":
        return False

    children = tokens[inline_index].children or []
    content = [
        child
        for child in children
        if child.type != "text" or child.content.strip()
    ]
    return len(content) == 1 and content[0].type == "image"


def _image_tokens(tokens: list[Token]) -> Iterator[tuple[Token, bool]]:
    """Every image token in a parsed document, with whether it is a figure."""

    for index, token in enumerate(tokens):
        if token.type != "inline":
            continue
        is_figure_paragraph = _is_figure_paragraph(tokens, index)
        for child in token.children or []:
            if child.type == "image":
                yield child, is_figure_paragraph


def _image_reference(token: Token) -> StoryImageReference:
    title = token.attrGet("title")
    return StoryImageReference(
        src=str(token.attrGet("src") or ""),
        # `content` is the alt text with any inline markup already flattened.
        alt=token.content,
        title=str(title) if title else None,
        is_figure=False,
    )


def story_image_references(markdown: str) -> list[StoryImageReference]:
    """Every image a story document references, in document order.

    markdown-it resolves reference-style images during inline parsing, so an
    image written as ``![alt][label]`` arrives here with its destination
    already resolved and is indistinguishable from the inline form.
    """

    parser = story_markdown_parser()
    tokens = parser.parse(markdown)
    references: list[StoryImageReference] = []
    for token, is_figure in _image_tokens(tokens):
        reference = _image_reference(token)
        references.append(
            StoryImageReference(
                src=reference.src,
                alt=reference.alt,
                title=reference.title,
                is_figure=is_figure,
            )
        )
    return references


@dataclass(frozen=True)
class NumberedStoryItem:
    """One numbered thing in a story: a figure view, or a figure image."""

    kind: str
    key: str
    number: int


def numbered_story_items(markdown: str) -> list[NumberedStoryItem]:
    """Figure views and figure images in story order, numbered from one.

    Images and figure views share one sequence. A reader moving through a
    document has no reason to care which kind a given figure is, and two
    sequences under one "Figure N." label would produce duplicate numbers.

    A figure view referenced more than once keeps the number of its first
    appearance rather than consuming another: that is a reader seeing the same
    figure again, not a new figure.
    """

    found: list[tuple[int, int, str, str]] = []

    for line_number, line in enumerate(markdown.splitlines()):
        match = FIGURE_DIRECTIVE_PATTERN.match(line)
        if match is not None:
            found.append((line_number, 0, "figureView", match.group(1).strip()))

    parser = story_markdown_parser()
    tokens = parser.parse(markdown)
    sequence = 0
    for index, token in enumerate(tokens):
        if token.type != "inline" or not _is_figure_paragraph(tokens, index):
            continue
        # A paragraph token carries the line span its content came from, which
        # is what orders images against the figure directives found above.
        block_map = tokens[index - 1].map
        line_number = block_map[0] if block_map is not None else 0
        image = next(child for child in token.children or [] if child.type == "image")
        sequence += 1
        found.append((line_number, sequence, "image", str(image.attrGet("src") or "")))

    items: list[NumberedStoryItem] = []
    numbers: dict[tuple[str, str], int] = {}
    for _, _, kind, key in sorted(found, key=lambda entry: (entry[0], entry[1])):
        number = numbers.get((kind, key))
        if number is None:
            number = len(numbers) + 1
            numbers[(kind, key)] = number
        items.append(NumberedStoryItem(kind=kind, key=key, number=number))
    return items


def link_definition_labels(markdown: str) -> dict[str, str]:
    """Link definition labels mapped to their destinations.

    markdown-it normalises labels to upper case while collecting them, and
    those are the keys used here.
    """

    parser = story_markdown_parser()
    env: dict[str, Any] = {}
    parser.parse(markdown, env)
    references = env.get("references") or {}
    return {label: str(payload["href"]) for label, payload in references.items()}


class ImageWidthError(Exception):
    """Raised when an image width is not a width."""


# `80mm` or `60%`. Both mean something on paper and on screen, because the
# story has a page; a pixel count would not.
IMAGE_WIDTH_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mm|%)\s*$")


def image_width_css(value: str) -> str:
    """Turn an authored width into a CSS length.

    CSS understands `mm` directly and resolves it correctly for both the screen
    and the printed page, so the authored units survive into the document
    rather than being converted to pixels against a guessed resolution.
    """

    match = IMAGE_WIDTH_PATTERN.match(value)
    if match is None:
        raise ImageWidthError(
            f"Image width {value!r} is not a width. Use millimetres or a percentage of the "
            'column, as in {width=80mm} or {width="60%"}.'
        )
    return f"{match.group(1)}{match.group(2)}"


def unparsed_attribute_text(markdown: str) -> list[str]:
    """Attribute blocks that did not attach to anything.

    `{width=60%}` is not valid attribute syntax - the percent has to be quoted -
    and markdown-it leaves it as literal text rather than complaining, so it
    would print in the story. Finding it here turns that into a build error.
    """

    parser = story_markdown_parser()
    found: list[str] = []
    for token in parser.parse(markdown):
        if token.type != "inline":
            continue
        for child in token.children or []:
            if child.type == "text" and child.content.lstrip().startswith("{"):
                found.append(child.content.strip().split("}")[0] + "}")
    return found


class CrossReferenceError(Exception):
    """Raised when a cross-reference does not name anything."""


# `[Figure](#fig-lotka)`. The lookbehind keeps images out of it: `![alt](...)`
# is an image, not a link.
INLINE_LINK_PATTERN = re.compile(r"(?<!!)\[((?:[^\]\\]|\\.)*)\]\(\s*(#[^)\s]*)\s*\)")

# `[Figure][rig]`, whose destination lives in a link definition.
REFERENCE_LINK_PATTERN = re.compile(r"(?<!!)\[((?:[^\]\\]|\\.)*)\]\[((?:[^\]\\]|\\.)*)\]")


def _reference_label(text: str) -> str | None:
    """The caption word a cross-reference used, if its text is only that."""

    stripped = text.strip()
    return stripped if stripped in REFERENCE_LABELS else None


def resolve_cross_references(
    markdown: str,
    number_for_anchor: Callable[[str], int | None],
) -> str:
    """Fill in the numbers in cross-reference links.

    A link whose text is a bare caption word - "Figure", "Fig.", "Table" - and
    whose destination is a fragment gets that word followed by the number of
    whatever the fragment names. The author never types the digit, so it cannot
    go stale.

    Any other link text is left exactly as written, and only such a bare
    caption word makes an unknown anchor an error: a link reading "see the
    appendix" pointing at a heading is not a cross-reference, but one reading
    "Figure" plainly meant to be one.
    """

    definitions = link_definition_labels(markdown)

    def resolved_text(text: str, anchor: str) -> str:
        label = _reference_label(text)
        if label is None:
            return text

        number = number_for_anchor(anchor.lstrip("#"))
        if number is None:
            raise CrossReferenceError(
                f"Cross-reference {text.strip()!r} points at {anchor!r}, which is not a figure "
                "or an image in this story"
            )
        # The author's own caption word is kept, so "Fig." stays "Fig. 3".
        return f"{label} {number}"

    def replace_inline(match: re.Match[str]) -> str:
        text = resolved_text(match.group(1), match.group(2))
        return f"[{text}]({match.group(2)})"

    def replace_reference(match: re.Match[str]) -> str:
        label = match.group(2).strip()
        # A collapsed reference, `[Figure][]`, takes its label from its text.
        destination = definitions.get((label or match.group(1)).strip().upper())
        if destination is None or not destination.startswith("#"):
            return match.group(0)
        text = resolved_text(match.group(1), destination)
        return f"[{text}][{match.group(2)}]"

    rewritten = INLINE_LINK_PATTERN.sub(replace_inline, markdown)
    return REFERENCE_LINK_PATTERN.sub(replace_reference, rewritten)


def _strip_angle_brackets(destination: str) -> str:
    if destination.startswith("<") and destination.endswith(">"):
        return destination[1:-1]
    return destination


def rewrite_image_destinations(markdown: str, resolve: Callable[[str], str | None]) -> str:
    """Rewrite every image destination through ``resolve``.

    ``resolve`` is given a destination as written and returns its replacement,
    or ``None`` to leave it alone. Both the inline form and the reference
    form's link definitions are rewritten, because the two are the same feature
    with the destination in a different place.

    Only the destination substring is replaced, so alt text, titles and
    surrounding prose are untouched by construction.
    """

    def replace_inline(match: re.Match[str]) -> str:
        destination = _strip_angle_brackets(match.group(2))
        replacement = resolve(destination)
        if replacement is None:
            return match.group(0)
        return f"{match.group(1)}{replacement}{match.group(3)}"

    def replace_definition(match: re.Match[str]) -> str:
        destination = _strip_angle_brackets(match.group(2))
        replacement = resolve(destination)
        if replacement is None:
            return match.group(0)
        return f"{match.group(1)}{replacement}{match.group(3)}"

    rewritten = INLINE_IMAGE_PATTERN.sub(replace_inline, markdown)
    return LINK_DEFINITION_PATTERN.sub(replace_definition, rewritten)


class StoryMarkdownRenderer:
    """Renders a story block to HTML.

    ``resolve_image`` turns an image destination into something a renderer can
    load - in practice a ``data:`` URI, because that is the only form that
    works unchanged in the story panel, the WebEngine PDF path and the rich
    text PDF fallback. Returning ``None`` renders a visible placeholder rather
    than a broken image.
    """

    def __init__(
        self,
        resolve_image: Callable[[str], str | None] | None = None,
        image_number: Callable[[str], int | None] | None = None,
        image_anchor: Callable[[str], str | None] | None = None,
        anchor_href: Callable[[str], str] | None = None,
        image_width: Callable[[str], str | None] | None = None,
    ) -> None:
        self.markdown = story_markdown_parser()
        self._resolve_image = resolve_image
        self._image_number = image_number
        self._image_anchor = image_anchor
        self._anchor_href = anchor_href
        self._image_width = image_width
        self.markdown.core.ruler.after("inline", "limelight_figures", _mark_figure_paragraphs)
        self.markdown.renderer.rules["image"] = self._render_image
        self.markdown.renderer.rules["paragraph_open"] = self._render_paragraph_open
        self.markdown.renderer.rules["paragraph_close"] = self._render_paragraph_close
        self.markdown.renderer.rules["link_open"] = self._render_link_open

    def render(self, markdown: str) -> str:
        return self.markdown.render(markdown)

    def _render_link_open(self, tokens: list[Token], index: int, options: Any, env: Any) -> str:
        """Rewrite in-page anchors for renderers that cannot follow them.

        A story is rendered one run of prose per widget, so a fragment link has
        no single document to navigate within, and the panel has to intercept
        the click and scroll the right widget into view instead. It can only
        intercept http(s): a fragment is resolved inside the page by the
        browser engine, and a custom scheme is dropped before the delegate sees
        it, both verified against this Qt build. Hence a sentinel host - the
        request is always cancelled, so it never reaches a network.
        """

        token = tokens[index]
        href = str(token.attrGet("href") or "")
        if self._anchor_href is not None and href.startswith("#"):
            token.attrSet("href", self._anchor_href(href[1:]))
        return self.markdown.renderer.renderToken(tokens, index, options, env)

    def _render_image(self, tokens: list[Token], index: int, options: Any, env: Any) -> str:
        token = tokens[index]
        source = str(token.attrGet("src") or "")
        resolved = self._resolve_image(source) if self._resolve_image is not None else source
        if resolved is None:
            return f'<span class="limelight-missing-image">Missing image: {html.escape(source)}</span>'

        alt = html.escape(token.content, quote=True)
        # A figure's width goes on the figure, so the caption can be as wide
        # as the image; the image then fills it. Only an inline image is
        # sized by itself.
        width = None if token.meta.get("limelight_figure") else self._width_css(token.attrGet("width"), source)
        style = f' style="width: {width}"' if width else ""
        return (
            f'<img class="limelight-story-image" src="{html.escape(resolved, quote=True)}" '
            f'alt="{alt}"{style}>'
        )

    def _width_css(self, authored: Any, source: str) -> str | None:
        # A width written at the reference wins over the asset's own, since it
        # was written about this appearance of the image.
        if authored:
            return image_width_css(str(authored))
        return self._image_width(source) if self._image_width is not None else None

    def _render_paragraph_open(self, tokens: list[Token], index: int, options: Any, env: Any) -> str:
        meta = tokens[index].meta
        if meta.get("limelight_caption") is None:
            return "<p>"

        # The anchor is what a cross-reference link navigates to.
        anchor = self._image_anchor(meta["limelight_src"]) if self._image_anchor is not None else None
        anchor_attribute = f' id="{html.escape(anchor, quote=True)}"' if anchor else ""
        width = self._width_css(meta.get("limelight_width"), meta["limelight_src"])
        if width is None:
            return f'<figure class="limelight-story-figure"{anchor_attribute}>'
        return (
            f'<figure class="limelight-story-figure limelight-sized-figure"{anchor_attribute} '
            f'style="width: {width}">'
        )

    def _render_paragraph_close(self, tokens: list[Token], index: int, options: Any, env: Any) -> str:
        meta = tokens[index].meta
        caption = meta.get("limelight_caption")
        if caption is None:
            return "</p>"

        # Images and figure views are numbered in one sequence, and captioned
        # the same way.
        number = self._image_number(meta["limelight_src"]) if self._image_number is not None else None
        return figcaption_html(number, caption) + "</figure>"


def figure_caption_markup(number: int | None, text: str) -> str:
    """What is written under a figure: ``<b>Figure 3.</b> text``.

    The number is bold and the text plain, whichever kind of figure it is -
    a plot, a table, or an image in the story - so a reader can find "Figure
    3" by eye and read on. Either part can be missing; the result is empty
    when both are.
    """

    label = f"<b>Figure {number}.</b>" if number is not None else ""
    return " ".join(part for part in (label, html.escape(text)) if part)


def figcaption_html(number: int | None, text: str) -> str:
    markup = figure_caption_markup(number, text)
    return f"<figcaption>{markup}</figcaption>" if markup else ""


def _mark_figure_paragraphs(state: Any) -> None:
    """Tag paragraphs holding a single image so they render as figures.

    The image rule cannot see its enclosing paragraph, so the decision is made
    here and recorded on the paragraph tokens either side of the inline token.
    """

    tokens = state.tokens
    for index, token in enumerate(tokens):
        if token.type != "inline" or not _is_figure_paragraph(tokens, index):
            continue

        image = next(child for child in token.children or [] if child.type == "image")
        image.meta["limelight_figure"] = True
        reference = _image_reference(image)
        # Both paragraph tokens carry the caption and the source: the opening
        # one writes the anchor and the width, the closing one writes the
        # caption and needs the source to look the number up by.
        for paragraph in (tokens[index - 1], tokens[index + 1]):
            paragraph.meta["limelight_caption"] = reference.caption
            paragraph.meta["limelight_src"] = reference.src
            paragraph.meta["limelight_width"] = image.attrGet("width")
