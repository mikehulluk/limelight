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
pip install limelight-app
```

The distribution is `limelight-app` (PyPI would not allow plain `limelight`),
but the package imports as `limelight` and the commands are named as below.

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

## Developing

```bash
uv sync --all-groups
uv run pytest
```
