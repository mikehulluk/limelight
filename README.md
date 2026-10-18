# Limelight

Limelight is a portable package format for data-driven figures and analysis
stories, and a desktop app for reading them.

A Limelight package is a folder, or an archive with the same layout, named
with either a `.limelight` or a `.ll` extension, holding a `project.dhall` manifest alongside the datasets it
describes, the figure specs drawn from them, and a story that walks a reader
through the analysis. The app opens either form, validates the manifest,
resolves the data, renders the figures, and follows the story.

Packages are meant to be produced by libraries and tools rather than by hand;
`limelight.writer` is the Python library for building them, and
`limelight.reader` for opening them without the app.

## Installing

```bash
pip install "limelight-app[gui]"    # the desktop app, the CLI and the library
pip install limelight-app           # the library and CLI only: no Qt
```

The distribution is `limelight-app` (PyPI would not allow plain `limelight`),
but the package imports as `limelight` and the commands are named as below.
The `gui` extra is Qt (PySide6), which only the desktop app and `LL pdf`
need, and Typst, which `LL pdf` sets the story's PDF with; a program that
builds or reads packages with `limelight.writer` and `limelight.reader` leaves
it out.

A package's `project.dhall` manifest is read by a built-in evaluator, so
nothing beyond the Python dependencies is needed. A hand-written manifest
that uses Dhall features beyond what the writer emits (functions, `if`,
`merge`, interpolation) is handed to `dhall-to-json` instead, if one is on
`PATH` or named by `LIMELIGHT_DHALL_TO_JSON`.

Native installers for Windows, macOS and Linux are built by `packaging/`;
see `packaging/README.md`.

## Using it

```bash
limelight-gui example.limelight            # open a package in the desktop app
limelight-cli summary example.limelight    # print a summary of a package
limelight-cli verify example.limelight     # validate one or more packages
limelight-cli pdf example.limelight        # render a package's story to PDF
limelight-cli meta example.limelight       # print a package's metadata as JSON
```

`LL` is the short form of both: `LL example.limelight` opens the app, and
`LL verify example.limelight` (or `summary`, `json`, `pdf`) runs the CLI.

On a Linux desktop, `LL desktop-entry` gives a pip or uv install what the
native installers have: an entry in the applications menu, a taskbar icon,
and `.limelight` files that open with Limelight. `LL desktop-entry --remove`
takes it away again.

The scripts in `examples/` build sample packages into `_build/examples/`, and
`src/limelight/language-reference/` holds the Dhall types a manifest is
written against; the writer copies them into every package it builds.

## Type

A document says how every kind of its text is set - body, headings, code,
captions, figure titles, axis and tick labels, legends, annotations, table
text - in the `typography` block of its story: a stack of fonts, a size in
points, a weight and a slant for each, so the document reads the same on
every machine, on screen and in its PDF. The block also sets two of the
figures' distances, in points: `axisLabelPadPt` between an axis and its
label and `figureTitlePadPt` between the axes and the title. Maths is set in
its own face, `mathsFont` - `new-computer-modern` (the default) or `fira`, a
sans-serif - at `mathsScale` times the size of the text around it; both
faces ship with Limelight, for the screen and the PDF, so a document's maths
is set the same way on every machine, with or without a network. The writer
fills the block in; `Typography` and `TextStyle` change it. File › Document
Information shows what a document is set in, and which face each style
really gets on this machine.

### Syntax styles

A tool that writes a document can label the names and words in its maths and
code, and the document says how each kind is set: the `syntax` list in its
typography gives a colour, a weight and a slant to a style name. Names are
dotted, general to particular, and are the labelling tool's own - LSP
semantic token types (`variable`, `function`, `variable.readonly`), TextMate
scopes (`keyword.control`), Pygments token types (`name.builtin`) - plus any
of its own, such as `symbol.velocity` for one name in particular. A label is
a list of names, general to particular; each name also stands for its dotted
prefixes, and each property comes from the last name in that order that sets
it, so `variable symbol.velocity` takes the variables' style unless
`symbol.velocity` sets its own. A label with no style is set plain.

Tokens are labelled in three places:

- in maths, `\llrole{variable symbol.velocity}{v}`;
- on inline code, `` `velocity`{role="variable symbol.velocity"} ``;
- in a code block, by a file of tokens beside the story, so the code stays as
  written. The fence names it after the code's language,
  ```` ```python {tokens=listing} ````, and the file,
  `story/code/listing.json`, is `{"tokens": [[line, start, length, label],
  ...]}`, lines and columns from 0 in Unicode code points. It is declared in
  the manifest's `codeTokens` list; `Project.add_code_tokens` writes both,
  and any lexer can make them - `examples/create_example02_signal_filters.py`
  uses Python's own `tokenize`. A fence without tokens is set plain on
  screen; in the PDF Typst highlights it by its language.

A reader can override any style by name in the `syntax` object of their
`limelight-setting.json`, for instance `{"syntax": {"keyword": {"colour":
"#d73a49", "weight": 700}}}`; File › Document Information lists every style
and where each property came from.

Fonts are declared in the manifest's `fonts` list. Limelight ships Ubuntu,
Ubuntu Mono, Noto Sans, and matplotlib's DejaVu Sans and DejaVu Sans Mono,
so a document that names only those (the default: Ubuntu, falling back to
Noto Sans) needs nothing installed. A `system` font is used where the
reader's machine has it and skipped where it does not, so it goes first in a
stack, never last. A `bundled` font travels in the package, one static file
per weight with its licence beside it (`Project.add_bundled_font`); most
open licences allow this and ask for exactly that, while a font that came
with an operating system usually may not be redistributed. The fonts
Limelight ships are under the Ubuntu Font Licence and the SIL Open Font
Licence, in `src/limelight/fonts/` with their licence texts.

## Developing

```bash
uv sync --all-extras --all-groups
uv run pytest
```
