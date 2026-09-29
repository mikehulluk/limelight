"""Story Markdown as Typst markup, for the PDF.

A story is parsed with the one Markdown configuration in story_markdown, so
the page and the screen agree about what a document means, and the parsed
tree is written out as Typst rather than HTML. Typst then lays the story out
and writes the PDF itself: real pages, real fonts, and maths set as maths,
with nothing fetched from a network while it does.

The markup written here leans on a handful of functions the document's
preamble defines (see ``typst_preamble``) - ``ll-figure``, ``ll-caption``,
``ll-caption-label`` and the rest - so a figure, a caption or a table is set
the same way wherever it comes from, and the typography is said once.

Like story_markdown, this module imports nothing from Qt.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Container, Mapping

from markdown_it.tree import SyntaxTreeNode

from .story_markdown import image_width_css, story_markdown_parser
from .typography import ResolvedFont, TextStyle, Typography

logger = logging.getLogger(__name__)

# Every ASCII character Typst markup gives a meaning to, or could at the
# start of a line: markup, shorthands (`--`, `-?`, `~`), comments (`//`),
# labels, references and list markers. A backslash before any of them writes
# the character itself.
_MARKUP_SPECIAL = re.compile(r"""([\\#$*_`<>@\[\]~/=+\-])""")
# `1.` opens a numbered list at the start of a line; a digit's full stop is
# escaped wherever it falls, which costs nothing.
_NUMBERED_FULL_STOP = re.compile(r"(?<=\d)\.")

# A story's text colour, and the quieter one its captions are set in, as
# the story's stylesheet has them.
TEXT_COLOUR = "#202124"
CAPTION_COLOUR = "#3c4043"
NOTE_COLOUR = "#5f6368"
CODE_BACKGROUND = "#f3f4f6"
RULE_COLOUR = "#d0d3d6"
LINK_COLOUR = "#1a5fb4"


def typst_text(text: str) -> str:
    """Text for Typst markup, written exactly as it reads."""

    escaped = _MARKUP_SPECIAL.sub(r"\\\1", text)
    return _NUMBERED_FULL_STOP.sub(r"\\.", escaped)


def typst_string(text: str) -> str:
    """A Typst string literal holding ``text``."""

    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{escaped}"'


def typst_label(anchor: str) -> str:
    """The label a cross-reference link navigates to.

    Written with ``label()`` rather than ``<anchor>`` so an id with
    characters the angle-bracket form does not allow still works.
    """

    return f"#label({typst_string(anchor)})"


def typst_caption(number: int | None, text: str) -> str:
    """A figure's caption: its number in the caption label's style, then its text.

    The same shape as story_markdown.figure_caption_markup, so a caption
    reads the same on screen and on paper. Empty when both are missing.
    """

    parts = []
    if number is not None:
        parts.append(f"#ll-caption-label[Figure {number}.]")
    if text:
        parts.append(typst_text(text))
    if not parts:
        return ""
    return f"#ll-caption[{' '.join(parts)}]"


def tex_to_typst(tex: str) -> str:
    """TeX maths as Typst maths, which is how a story's `$...$` reaches the page."""

    from tex2typst import tex2typst

    # Strict, so a command it does not know is an error here rather than a
    # name Typst then fails the whole document over. A fraction comes out as
    # `(a + b)/c`, which Typst sets as a stacked fraction, dropping the
    # brackets; tex2typst's strict mode will not write `frac(...)`.
    converted = tex2typst(tex.strip(), non_strict=False)
    if not isinstance(converted, str):
        raise TypeError("tex2typst returned several values for one expression")
    return converted


class TypstImageError(Exception):
    """Raised when a story image cannot be placed on the page."""


@dataclass(frozen=True)
class TypstImage:
    """A story image as the Typst document reaches it.

    ``path`` is relative to the document's root. ``natural_width_mm`` is
    the width the image draws at when nothing says otherwise: its pixels
    at CSS resolution, as the story panel draws it.
    """

    path: str
    natural_width_mm: float


class StoryTypstRenderer:
    """Renders a story block to Typst markup.

    The callables mirror StoryMarkdownRenderer's. ``image`` places a story
    image's file where the document can reach it and returns it, or
    ``None`` to write a visible placeholder instead. ``anchors`` are the
    labels the document carries, so a link to one of them navigates and a
    link to anything else is written as its text: Typst will not compile a
    link to a label that is not there.
    """

    def __init__(
        self,
        image: Callable[[str], TypstImage | None] | None = None,
        image_number: Callable[[str], int | None] | None = None,
        image_anchor: Callable[[str], str | None] | None = None,
        image_width: Callable[[str], str | None] | None = None,
        anchors: Container[str] = (),
        *,
        paged: bool = True,
    ) -> None:
        self._parser = story_markdown_parser()
        self._image = image
        self._image_number = image_number
        self._image_anchor = image_anchor
        self._image_width = image_width
        self._anchors = anchors
        self._paged = paged
        self._labelled: set[str] = set()

    def render(self, markdown: str) -> str:
        tree = SyntaxTreeNode(self._parser.parse(markdown))
        return self._blocks(tree.children)

    def label(self, anchor: str | None) -> str:
        """The label for ``anchor``'s first appearance, and nothing for any later one.

        A figure shown twice is one figure, and a cross-reference goes to
        where the reader first met it; Typst refuses a label used twice.
        """

        if not anchor or anchor in self._labelled:
            return ""
        self._labelled.add(anchor)
        return typst_label(anchor)

    # --- Blocks ------------------------------------------------------------

    def _blocks(self, nodes: list[SyntaxTreeNode], *, tight: bool = False) -> str:
        rendered = [self._block(node) for node in nodes]
        return ("\n" if tight else "\n\n").join(part for part in rendered if part)

    def _block(self, node: SyntaxTreeNode) -> str:
        kind = node.type
        if kind == "paragraph":
            return self._paragraph(node)
        if kind == "heading":
            level = int(node.tag[1:])
            return f"{'=' * level} {self._inline_of(node)}"
        if kind == "bullet_list":
            return self._list(node, ordered=False)
        if kind == "ordered_list":
            return self._list(node, ordered=True)
        if kind == "blockquote":
            return f"#quote(block: true)[\n{self._blocks(node.children)}\n]"
        if kind in ("fence", "code_block"):
            return self._code_block(node)
        if kind in ("math_block", "math_block_label"):
            return self._display_math(node.content)
        if kind == "table":
            return self._table(node)
        if kind == "hr":
            # A rule is where an author breaks the story; on a page that is a
            # new page, and on a sheet with no pages, a rule across it.
            return "#pagebreak(weak: true)" if self._paged else "#line(length: 100%, stroke: 0.5pt + ll-rule-colour)"
        if kind in ("html_block", "footnote_block"):
            return ""
        logger.warning("Story block %r has no Typst form; writing its text", kind)
        return typst_text(node.content)

    def _paragraph(self, node: SyntaxTreeNode) -> str:
        image = _figure_image(node)
        if image is not None:
            return self._image_figure(image)
        return self._inline_of(node)

    def _list(self, node: SyntaxTreeNode, *, ordered: bool) -> str:
        start = int(node.attrs.get("start", 1)) if ordered else 1
        tight = all(_is_tight_item(item) for item in node.children)
        items = []
        for offset, item in enumerate(node.children):
            # An explicit number keeps a list that starts part way, `5.`,
            # numbered as written.
            marker = f"{start + offset}." if ordered else "-"
            body = self._blocks(item.children, tight=tight)
            indent = " " * (len(marker) + 1)
            lines = body.split("\n") if body else [""]
            continued = [f"{indent}{line}" if line else "" for line in lines[1:]]
            items.append("\n".join([f"{marker} {lines[0]}", *continued]))
        return ("\n" if tight else "\n\n").join(items)

    def _code_block(self, node: SyntaxTreeNode) -> str:
        info = (node.info or "").strip()
        language = info.split()[0] if info else ""
        if language == "math":
            return self._display_math(node.content)
        content = node.content.rstrip("\n")
        lang = f", lang: {typst_string(language)}" if language else ""
        return f"#raw({typst_string(content)}, block: true{lang})"

    def _display_math(self, tex: str) -> str:
        try:
            return f"$ {tex_to_typst(tex)} $"
        except Exception as error:
            logger.warning("Could not convert display maths %r: %s", tex, error)
            return f"#ll-notice[#raw({typst_string(tex.strip())}, block: true)]"

    def _table(self, node: SyntaxTreeNode) -> str:
        header_rows: list[list[SyntaxTreeNode]] = []
        body_rows: list[list[SyntaxTreeNode]] = []
        for section in node.children:
            rows = [row.children for row in section.children]
            (header_rows if section.type == "thead" else body_rows).extend(rows)

        first_row = (header_rows or body_rows or [[]])[0]
        alignments = ", ".join(_cell_alignment(cell) for cell in first_row)
        cells: list[str] = []
        if header_rows:
            header = ", ".join(
                f"[#strong[{self._inline_of(cell)}]]" for row in header_rows for cell in row
            )
            cells.append(f"table.header({header})")
        cells.extend(f"[{self._inline_of(cell)}]" for row in body_rows for cell in row)
        return (
            f"#table(\n  columns: {len(first_row)},\n  align: ({alignments},),\n  "
            + ",\n  ".join(cells)
            + ",\n)"
        )

    def _image_figure(self, image: SyntaxTreeNode) -> str:
        """An image alone in its paragraph: a figure, numbered and captioned."""

        source = str(image.attrs.get("src") or "")
        placed = self._image(source) if self._image is not None else None
        if placed is None:
            return f"#ll-notice[Missing image: {typst_text(source)}]"

        title = image.attrs.get("title")
        caption_text = str(title) if title else _plain_text(image)
        number = self._image_number(source) if self._image_number is not None else None
        caption = typst_caption(number, caption_text)
        width = self._width(image.attrs.get("width"), source) or f"{placed.natural_width_mm:.2f}mm"
        caption_argument = f", caption: [{caption}]" if caption else ""
        figure = (
            f"#ll-figure(image({typst_string(placed.path)}, width: 100%), "
            f"width: {width}{caption_argument})"
        )
        anchor = self._image_anchor(source) if self._image_anchor is not None else None
        return figure + self.label(anchor)

    def _width(self, authored: Any, source: str) -> str | None:
        # A width written at the reference wins over the asset's own, as it
        # does on screen. `80mm` and `60%` mean the same in Typst as in CSS.
        if authored:
            return image_width_css(str(authored))
        return self._image_width(source) if self._image_width is not None else None

    # --- Inline ------------------------------------------------------------

    def _inline_of(self, node: SyntaxTreeNode) -> str:
        return "".join(self._inline(child) for child in node.children)

    def _inline(self, node: SyntaxTreeNode) -> str:
        kind = node.type
        if kind == "inline":
            return self._inline_of(node)
        if kind == "text":
            return typst_text(node.content)
        if kind == "softbreak":
            # A space, not a newline: a line of markup opening with a marker
            # is a list or a heading, and a soft break means nothing more.
            return " "
        if kind == "hardbreak":
            return "\\\n"
        if kind == "code_inline":
            return f"#raw({typst_string(node.content)})"
        if kind == "strong":
            return f"#strong[{self._inline_of(node)}]"
        if kind == "em":
            return f"#emph[{self._inline_of(node)}]"
        if kind == "s":
            return f"#strike[{self._inline_of(node)}]"
        if kind == "link":
            return self._link(node)
        if kind == "image":
            return self._inline_image(node)
        if kind == "math_inline":
            return self._inline_math(node.content)
        if kind == "math_inline_double":
            return self._display_math(node.content)
        if kind == "html_inline":
            return ""
        logger.warning("Inline story markup %r has no Typst form; writing its text", kind)
        return typst_text(node.content)

    def _link(self, node: SyntaxTreeNode) -> str:
        href = str(node.attrs.get("href") or "")
        text = self._inline_of(node)
        if href.startswith("#"):
            anchor = href[1:]
            if anchor in self._anchors:
                return f"#link(label({typst_string(anchor)}))[{text}]"
            return text
        return f"#link({typst_string(href)})[{text}]"

    def _inline_image(self, node: SyntaxTreeNode) -> str:
        source = str(node.attrs.get("src") or "")
        placed = self._image(source) if self._image is not None else None
        if placed is None:
            return f"#ll-notice[Missing image: {typst_text(source)}]"
        width = self._width(node.attrs.get("width"), source) or f"{placed.natural_width_mm:.2f}mm"
        return f"#box(image({typst_string(placed.path)}, width: {width}))"

    def _inline_math(self, tex: str) -> str:
        try:
            return f"${tex_to_typst(tex)}$"
        except Exception as error:
            logger.warning("Could not convert inline maths %r: %s", tex, error)
            return f"#ll-notice[#raw({typst_string(tex)})]"


def _figure_image(paragraph: SyntaxTreeNode) -> SyntaxTreeNode | None:
    """The image a paragraph holds, when it holds only that: story_markdown's figure rule."""

    if not paragraph.children:
        return None
    content = [
        child
        for child in paragraph.children[0].children
        if child.type != "text" or child.content.strip()
    ]
    if len(content) == 1 and content[0].type == "image":
        return content[0]
    return None


def _is_tight_item(item: SyntaxTreeNode) -> bool:
    return all(child.type != "paragraph" or child.hidden for child in item.children)


def _plain_text(node: SyntaxTreeNode) -> str:
    """An image's alt text with its inline markup flattened, as a caption uses it."""

    if node.type in ("text", "code_inline", "math_inline"):
        return node.content
    return "".join(_plain_text(child) for child in node.children)


def _cell_alignment(cell: SyntaxTreeNode) -> str:
    style = str(cell.attrs.get("style") or "")
    for alignment in ("left", "center", "right"):
        if f"text-align:{alignment}" in style:
            return alignment
    return "left"


# --- The document ----------------------------------------------------------


@dataclass(frozen=True)
class TypstPage:
    """The page a story prints on. A ``height_mm`` of None runs as long as the story."""

    width_mm: float
    height_mm: float | None
    margin_lr_mm: float
    margin_tb_mm: float


@dataclass(frozen=True)
class TypstSpacing:
    """The story's vertical rhythm, in millimetres (see app.StorySpacing)."""

    block_gap_mm: float
    figure_gap_mm: float
    heading_gap_before_mm: float
    heading_gap_after_mm: float


def typst_text_settings(style: TextStyle, fonts: Mapping[str, ResolvedFont]) -> str:
    """The `text` arguments that set one of the typography's styles."""

    families = ", ".join(typst_string(family) for family in style.families(fonts))
    return (
        f"font: ({families},), size: {style.size_pt:g}pt, weight: {style.weight}, "
        f"style: \"{'italic' if style.italic else 'normal'}\""
    )


def typst_preamble(
    *,
    title: str,
    page: TypstPage,
    spacing: TypstSpacing,
    typography: Typography,
    fonts: Mapping[str, ResolvedFont],
) -> str:
    """The rules and functions every story document opens with.

    Each kind of text is set from the document's typography, every face by
    name and every size in points, as the story's stylesheet does.

    Line height is the one that needs care. CSS measures it baseline to
    baseline; Typst measures leading between one line's bottom edge and the
    next one's top edge. With those edges an em apart, the leading is
    whatever the line height has left over, and the two agree.
    """

    def text(style: TextStyle) -> str:
        return typst_text_settings(style, fonts)

    height = "auto" if page.height_mm is None else f"{page.height_mm:g}mm"
    headings = [
        f"#show heading.where(level: {level}): set text({text(style)})"
        for level, style in (
            (1, typography.heading1),
            (2, typography.heading2),
            (3, typography.heading3),
            (4, typography.heading3),
            (5, typography.heading3),
            (6, typography.heading3),
        )
    ]
    return "\n".join(
        [
            f"#set document(title: {typst_string(title)})",
            f"#set page(width: {page.width_mm:g}mm, height: {height}, "
            f"margin: (x: {page.margin_lr_mm:g}mm, y: {page.margin_tb_mm:g}mm))",
            f"#set text({text(typography.body)}, fill: rgb(\"{TEXT_COLOUR}\"), "
            "top-edge: 0.8em, bottom-edge: -0.2em)",
            f"#set par(leading: {max(0.0, typography.line_height - 1.0):g}em, "
            f"spacing: {spacing.block_gap_mm:g}mm)",
            f"#set block(spacing: {spacing.block_gap_mm:g}mm)",
            f"#show heading: set block(above: {spacing.heading_gap_before_mm:g}mm, "
            f"below: {spacing.heading_gap_after_mm:g}mm)",
            *headings,
            f"#show raw: set text({text(typography.code)})",
            f"#show raw.where(block: true): set block(fill: rgb(\"{CODE_BACKGROUND}\"), "
            "inset: 0.7em, radius: 6pt, width: 100%, breakable: false)",
            f"#show raw.where(block: false): box.with(fill: rgb(\"{CODE_BACKGROUND}\"), "
            "inset: (x: 0.25em), outset: (y: 0.2em), radius: 4pt)",
            f"#show link: set text(fill: rgb(\"{LINK_COLOUR}\"))",
            f"#let ll-rule-colour = rgb(\"{RULE_COLOUR}\")",
            "#set table(stroke: 0.5pt + ll-rule-colour, inset: (x: 7pt, y: 3pt))",
            "#show quote.where(block: true): it => block(stroke: (left: 2pt + ll-rule-colour), "
            "inset: (left: 0.8em, y: 0.2em), it.body)",
            f"#let ll-caption(body) = text({text(typography.caption)}, fill: rgb(\"{CAPTION_COLOUR}\"), body)",
            f"#let ll-caption-label(body) = text({text(typography.caption_label)}, body)",
            f"#let ll-table-heading(body) = align(center, text({text(typography.table_heading)}, body))",
            f"#let ll-table-header(body) = text({text(typography.table_header)}, body)",
            f"#let ll-table-cell(body) = text({text(typography.table_cell)}, body)",
            f"#let ll-table-note(body) = text({text(typography.table_note)}, fill: rgb(\"{NOTE_COLOUR}\"), body)",
            f"#let ll-notice(body) = text(fill: rgb(\"{NOTE_COLOUR}\"), body)",
            # A figure is a box centred in the column, never wider than it,
            # with its caption hanging from its left edge no wider than it
            # is. `width` is a length, or a share of the column.
            "#let ll-figure(body, width: 100%, caption: none) = block(",
            f"  above: {spacing.figure_gap_mm:g}mm, below: {spacing.figure_gap_mm:g}mm,",
            "  breakable: false, width: 100%,",
            "  layout(size => {",
            "    let w = if type(width) == ratio { width * size.width }",
            "      else if type(width) == relative { width.ratio * size.width + width.length.to-absolute() }",
            "      else { width.to-absolute() }",
            "    align(center, block(width: calc.min(w, size.width), {",
            "      body",
            "      if caption != none { block(above: 0.4em, align(left, caption)) }",
            "    }))",
            "  }),",
            ")",
        ]
    )
