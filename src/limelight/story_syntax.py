"""A story's labelled tokens, set in the document's syntax styles.

A tool labels a token with style names, general to particular (see
``typography.SyntaxStyle``), in one of two places:

- in maths, ``\\llrole{variable symbol.velocity}{v}``;
- on inline code, ``\\`velocity\\`{role="variable symbol.velocity"}``.

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

# TeX's lower-case Greek, which stays italic under \mathrm in MathJax as in
# TeX; the Unicode letter takes the upright variant.
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ",
    "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
}
_GREEK_MACRO = re.compile(r"\\(" + "|".join(sorted(_GREEK, key=len, reverse=True)) + r")(?![A-Za-z])")
# Maths italic already: a Latin or lower-case Greek letter, perhaps under an accent.
_ITALIC_LETTER = r"(?:[A-Za-z]|" + _GREEK_MACRO.pattern + ")"
_ITALIC = re.compile(
    _ITALIC_LETTER + r"|\\(?:vec|hat|bar|tilde|dot|ddot|check|breve|acute|grave)\s*\{\s*" + _ITALIC_LETTER + r"\s*\}"
)


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


def _text_words(body: str) -> str | None:
    """The words of ``body`` when it is one ``\\text{...}`` and nothing else."""

    if not body.startswith("\\text") or body[5:6].isalpha():
        return None
    try:
        words, end = _group(body, 5)
    except ValueError:
        return None
    return words if end == len(body) else None


def styled_tex(inner: str, style: ResolvedSyntaxStyle) -> str:
    """``inner`` in ``style``, as TeX MathJax sets.

    The slant applies to the whole token, as Typst's ``italic`` and
    ``upright`` do (``styled_typst``), so screen and paper agree. Text
    (``\\text{rate}``), upright already, is slanted with ``\\textit`` and
    made bold with ``\\textbf``. Other maths is slanted with ``\\mathit``,
    which slants capital Greek too, unless it is maths italic already (a
    letter, perhaps accented, whose maths italic ``\\mathit`` would swap
    for text italic), and made upright with ``\\mathrm``, its
    lower-case Greek written as Unicode letters, since ``\\mathrm{\\alpha}``
    stays italic; it is made bold with ``\\boldsymbol``.
    """

    body = inner.strip()
    words = _text_words(body)
    if words is not None:
        if style.italic:
            words = f"\\textit{{{words}}}"
        if _bold(style):
            words = f"\\textbf{{{words}}}"
        if style.italic or _bold(style):
            body = words
    else:
        if style.italic and not _ITALIC.fullmatch(body):
            body = f"\\mathit{{{body}}}"
        elif style.italic is False:
            body = "\\mathrm{" + _GREEK_MACRO.sub(lambda m: _GREEK[m.group(1)], body) + "}"
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
    """Typst maths ``typst`` (converted from ``inner_tex``) in ``style``; see ``styled_tex``."""

    if style.italic:
        typst = f"italic({typst})"
    elif style.italic is False and _text_words(inner_tex.strip()) is None:
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


# --- Labelled code blocks ---------------------------------------------------
#
# A code block's tokens travel in a file of their own (a CodeTokens asset,
# story/code/<id>.json), so the code stays as written; the fence names it:
# ```python {tokens=listing}, the language being whatever the code is in.
# The file is {"tokens": [[line, start, length, label], ...]}, lines and
# columns counting from 0, in Unicode code points (a Python str's indices,
# not LSP's UTF-16 units or tree-sitter's bytes), over the block's text.
# Any lexer can make them; a token that does not fit its line is dropped.

CODE_TOKENS_DIR = "story/code"
CODE_TOKENS_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_FENCE_TOKENS = re.compile(r"\{\s*tokens\s*=\s*\"?([A-Za-z][A-Za-z0-9_-]*)\"?\s*\}")


@dataclass(frozen=True)
class CodeToken:
    line: int
    start: int
    length: int
    label: str


def fence_info(info: str) -> tuple[str, str | None]:
    """A fence's language and the id of its tokens, if it names any."""

    tokens = _FENCE_TOKENS.search(info)
    rest = _FENCE_TOKENS.sub("", info).split()
    return (rest[0] if rest else ""), (tokens.group(1) if tokens else None)


def parse_code_tokens(data: bytes, context: str) -> list[CodeToken]:
    """A code tokens file's tokens, checked; raises ValueError on anything malformed."""

    import json

    from .typography import SYNTAX_NAME

    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{context} is not JSON: {error}") from None
    if not isinstance(document, dict) or not isinstance(document.get("tokens"), list):
        raise ValueError(f'{context} must be an object with a "tokens" list')
    tokens = []
    for index, entry in enumerate(document["tokens"]):
        if not (isinstance(entry, list) and len(entry) == 4
                and all(isinstance(n, int) and not isinstance(n, bool) for n in entry[:3])
                and isinstance(entry[3], str)):
            raise ValueError(f"{context} token {index} is not [line, start, length, label]: {entry!r}")
        line, start, length, label = entry
        if line < 0 or start < 0 or length <= 0:
            raise ValueError(f"{context} token {index} has a negative position or no length: {entry!r}")
        if not label.split() or not all(SYNTAX_NAME.fullmatch(name) for name in label.split()):
            raise ValueError(f"{context} token {index} label {label!r} is not syntax style names")
        tokens.append(CodeToken(line, start, length, label))
    return tokens


def code_tokens_json(tokens: list[tuple[int, int, int, str]] | list[CodeToken]) -> str:
    import json

    rows = [[t.line, t.start, t.length, t.label] if isinstance(t, CodeToken) else list(t) for t in tokens]
    return '{"tokens": [\n' + ",\n".join(json.dumps(row) for row in rows) + "\n]}\n"


def _segments(code: str, tokens: list[CodeToken]) -> list[list[tuple[str, str | None]]]:
    """Each line of ``code`` as runs of text, each with its token's label or None.

    Tokens past the end of a line, or overlapping one before them, are
    dropped rather than trusted: the file and the code can disagree.
    """

    lines = code.split("\n")
    by_line: dict[int, list[CodeToken]] = {}
    for token in tokens:
        by_line.setdefault(token.line, []).append(token)
    out = []
    for number, line in enumerate(lines):
        runs: list[tuple[str, str | None]] = []
        at = 0
        for token in sorted(by_line.get(number, []), key=lambda t: t.start):
            end = token.start + token.length
            if token.start < at or end > len(line):
                continue
            if token.start > at:
                runs.append((line[at:token.start], None))
            runs.append((line[token.start:end], token.label))
            at = end
        if at < len(line):
            runs.append((line[at:], None))
        out.append(runs)
    return out


def code_block_html(code: str, language: str, tokens: list[CodeToken] | None, style_of: StyleOf | None) -> str:
    """A code block as the story panel shows it, each labelled token in its style."""

    language_class = f' class="language-{html.escape(language, quote=True)}"' if language else ""
    if not tokens or style_of is None:
        return f"<pre><code{language_class}>{html.escape(code, quote=False)}</code></pre>\n"
    lines = []
    for runs in _segments(code.rstrip("\n"), tokens):
        parts = []
        for text, label in runs:
            escaped = html.escape(text, quote=False)
            css = code_css(style_of(label)) if label else ""
            parts.append(f'<span style="{html.escape(css, quote=True)}">{escaped}</span>' if css else escaped)
        lines.append("".join(parts))
    return f"<pre><code{language_class}>" + "\n".join(lines) + "\n</code></pre>\n"


def code_block_typst(code: str, tokens: list[CodeToken], style_of: StyleOf | None, string: Callable[[str], str]) -> str:
    """A code block's lines as Typst text runs, each labelled token in its style.

    Typst's raw text cannot be styled in parts, so the block is set as text
    in the code face (``ll-code-block`` in the preamble), its spaces made
    no-break so the columns stay where they were written.
    """

    lines = []
    for runs in _segments(code.rstrip("\n"), tokens):
        parts = []
        for text, label in runs:
            literal = string(text.replace(" ", " "))
            settings = typst_syntax_settings(style_of(label)) if label and style_of is not None else ""
            parts.append(f"#text({settings}, {literal})" if settings else f"#{literal}")
        lines.append("".join(parts))
    return "#ll-code-block[" + "#linebreak()\n".join(lines) + "]"


def verify_code_tokens(package, manifest: dict) -> None:
    """Every declared code tokens file is there and well formed; raises LimelightError if not."""

    from .reader import LimelightError

    for entry in manifest.get("codeTokens") or []:
        context = f"CodeTokens {entry['id']!r} ({entry['path']})"
        try:
            data = package.read_bytes(entry["path"])
        except FileNotFoundError:
            raise LimelightError(f"{context} is missing") from None
        try:
            parse_code_tokens(data, context)
        except ValueError as error:
            raise LimelightError(str(error)) from None
