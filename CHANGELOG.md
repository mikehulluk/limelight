# Changelog

Notable changes to Limelight. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) once past 1.0; before that, minor
versions may break things.

## [Unreleased]

## [0.0.1] - 2026-09-11

First release on PyPI, as `limelight-app`.

### Added

- The `.limelight` package format: a folder or archive holding a Dhall
  manifest, CSV and HDF5 datasets, figure specs, image assets, and a Markdown
  story that walks a reader through the analysis. The language reference in
  `src/limelight/language-reference/` defines it.
- `limelight.writer`, the Python library that builds packages, and
  `limelight.reader`, which opens them without the app.
- The desktop app (`limelight-gui`): story, figures, datasets and control
  parameters, with PDF export of the story.
- `limelight-cli` with `summary`, `verify`, `json` and `pdf` subcommands, and
  `LL` as a short form of both commands.
- A built-in evaluator for the subset of Dhall that manifests use, so reading
  a package needs no `dhall-to-json`; the tool is only consulted for
  hand-written manifests that go beyond that subset.
- Native installers for Windows, macOS and Linux under `packaging/`, with
  `.limelight` file associations.

[Unreleased]: https://github.com/mikehulluk/limelight/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/mikehulluk/limelight/releases/tag/v0.0.1
