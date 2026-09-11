# Changelog

Notable changes to Limelight. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) once past 1.0; before that, minor
versions may break things.

## [Unreleased]

## [0.0.2] - 2026-09-11

### Added

- `verify` rejects a line drawn against a time axis whose source has no index
  to take its x values from, which used to crash the app instead.

### Changed

- The app hides a tab it has nothing for: a package without control
  parameters has no Parameters tab, and likewise Story, Figures and Data.
  The "ControlParameters" tab is now "Parameters".
- A project with no story set writes an empty story rather than a title
  page, so a package can be figures alone.
- The Help menu's first entry reads "Report an Issue..." and the button no
  longer shows a menu-indicator arrow.

### Fixed

- A package containing a figure without a caption failed to open in the app.
- `example00` declared a calendar axis over a source with no index and could
  not be opened; it now carries the daily calendar index it meant.

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
- `AxesDecoratorRect`, a shaded box bounded on both axes, written with
  `axes_action_add_rect_decorator`; bounds are dates on a calendar axis and
  numbers elsewhere.

[Unreleased]: https://github.com/mikehulluk/limelight/compare/v0.0.2...HEAD
[0.0.2]: https://github.com/mikehulluk/limelight/compare/v0.0.1...v0.0.2
[0.0.1]: https://github.com/mikehulluk/limelight/releases/tag/v0.0.1
