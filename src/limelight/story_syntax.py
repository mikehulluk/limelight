"""A story's labelled tokens, set in the document's syntax styles.

A tool labels a token with style names, general to particular (see
``typography.SyntaxStyle``), in one of two places:

- in maths, ``\\llrole{variable.state symbol.cash}{\\text{cash}}``;
- on inline code, ``\\`cash\\`{role="variable.state symbol.cash"}``.

The story's renderers look each label up (``LimelightRuntime.syntax_style``)
and set the token in its colour, weight and slant: MathJax through TeX's own
commands, here, and Typst through its maths functions (``story_typst``). A
renderer given no lookup sets the token as if it were unlabelled.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Callable

from .typography import ResolvedSyntaxStyle

StyleOf = Callable[[str], ResolvedSyntaxStyle]

ROLE_MACRO = "\\llrole"
# A weight from which a token is set bold; maths has only regular and bold.
BOLD_FROM = 600

_TEXT = re.compile(r"\\text\{(.*)\}", re.S)
_LETTER = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class Role:
    """One ``\\llrole{label}{inner}`` in a TeX string, from ``start`` to ``end``."""

    start: int
    end: int
    label: str
    inner: str


def _group(tex: str, at: int) -> tuple[str, int]:
    """The braced group opening at ``at`` (after any spaces), and where it ends."""

    while at < len(tex) and tex[at].isspace():
        at += 1
    if at >= len(tex) or tex[at] != "{":
        raise ValueError(f"{ROLE_MACRO} needs two braced arguments: {tex!r}")
    depth, i = 0, at
    while i < len(tex):
        if tex[i] == "\\":
            i += 2
            continue
        if tex[i] == "{":
            depth += 1
        elif tex[i] == "}":
            depth -= 1
            if depth == 0:
                return tex[at + 1:i], i + 1
        i += 1
    raise ValueError(f"Unbalanced braces after {ROLE_MACRO}: {tex!r}")


def find_roles(tex: str) -> list[Role]:
    """Every labelled token in ``tex``, in order; labels do not nest."""

    roles = []
    at = 0
    while (start := tex.find(ROLE_MACRO, at)) >= 0:
        after = start + len(ROLE_MACRO)
        if after < len(tex) and tex[after].isalpha():     # Some other macro, \llroles say.
            at = after
            continue
        label, after = _group(tex, after)
        inner, end = _group(tex, after)
        roles.append(Role(start, end, label.strip(), inner))
        at = end
    return roles


def replace_roles(tex: str, replacement: Callable[[Role], str]) -> str:
    out, at = [], 0
    for role in find_roles(tex):
        out += [tex[at:role.start], replacement(role)]
        at = role.end
    return "".join(out) + tex[at:]


def _bold(style: ResolvedSyntaxStyle) -> bool:
    return style.weight is not None and style.weight >= BOLD_FROM


def styled_tex(inner: str, style: ResolvedSyntaxStyle) -> str:
    """``inner`` in ``style``, as TeX MathJax sets.

    Upright text (``\\text{cash}``) is slanted with ``\\textit`` and made
    bold with ``\\textbf``; a single letter, italic in maths, is made
    upright with ``\\mathrm``; anything else is made bold with
    ``\\boldsymbol`` and keeps its slant.
    """

    body = inner.strip()
    text = _TEXT.fullmatch(body)
    if text is not None:
        words = text.group(1)
        if style.italic:
            words = f"\\textit{{{words}}}"
        if _bold(style):
            words = f"\\textbf{{{words}}}"
        if style.italic or _bold(style):
            body = words
    else:
        if style.italic is False and _LETTER.fullmatch(body):
            body = f"\\mathrm{{{body}}}"
        if _bold(style):
            body = f"\\boldsymbol{{{body}}}"
    if style.colour:
        body = f"\\textcolor{{{style.colour}}}{{{body}}}"
    return body


def tex_with_roles(tex: str, style_of: StyleOf | None) -> str:
    """``tex`` with each labelled token set in its style, or left plain with no lookup."""

    if ROLE_MACRO not in tex:
        return tex
    if style_of is None:
        return replace_roles(tex, lambda role: f"{{{role.inner}}}")
    return replace_roles(tex, lambda role: f"{{{styled_tex(role.inner, style_of(role.label))}}}")


def styled_typst(typst: str, inner_tex: str, style: ResolvedSyntaxStyle) -> str:
    """Typst maths ``typst`` (converted from ``inner_tex``) in ``style``."""

    if style.italic:
        typst = f"italic({typst})"
    elif style.italic is False and _LETTER.fullmatch(inner_tex.strip()):
        typst = f"upright({typst})"
    if _bold(style):
        typst = f"bold({typst})"
    if style.colour:
        typst = f'#text(fill: rgb("{style.colour}"))[${typst}$]'
    return typst


def code_css(style: ResolvedSyntaxStyle) -> str:
    """The declarations that set inline code in ``style``."""

    declarations = []
    if style.colour:
        declarations.append(f"color: {style.colour}")
    if style.weight is not None:
        declarations.append(f"font-weight: {style.weight}")
    if style.italic is not None:
        declarations.append(f"font-style: {'italic' if style.italic else 'normal'}")
    return "; ".join(declarations)


def code_html(code: str, label: str | None, style_of: StyleOf | None) -> str:
    """Inline code, in its label's style when it has one."""

    css = code_css(style_of(label)) if label and style_of is not None else ""
    style = f' style="{html.escape(css, quote=True)}"' if css else ""
    return f"<code{style}>{html.escape(code, quote=False)}</code>"


def typst_syntax_settings(style: ResolvedSyntaxStyle) -> str:
    """``style`` as the arguments of Typst's ``text``, or "" when it sets nothing."""

    settings = []
    if style.colour:
        settings.append(f'fill: rgb("{style.colour}")')
    if style.weight is not None:
        settings.append(f"weight: {style.weight}")
    if style.italic is not None:
        settings.append(f'style: "{"italic" if style.italic else "normal"}"')
    return ", ".join(settings)
