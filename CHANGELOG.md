# Changelog

Notable changes to Limelight. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) once past 1.0; before that, minor
versions may break things.

## [Unreleased]

### Added

- A figure can say how big it is drawn: `FigureSpec.size` takes a `width`
  in millimetres or percent of the column, as an image does, and an
  `aspect` (height over width). `add_line_figure(size=FigureSize(...))`.
- A figure's panels can be sized: `heightRatio` on an AxesSpec sets its
  share of the stack, and `frame` places it exactly, as matplotlib's
  `add_axes` does, for panels side by side or an inset. `Panel(height=...)`,
  `Panel(frame=...)`, and `panel_height=` / `panel_frame=` for the first
  panel on `add_line_figure`.

### Changed

- `AxesSpec.frame` is now optional, and honoured when given; the writer no
  longer emits a placeholder frame for every panel.

## [0.0.6] - 2026-09-14

### Fixed

- Ctrl+wheel zooms the story over a figure as well as over text; before, it
  only scrolled there. A figure in Explore mode keeps the wheel for its plot.

## [0.0.5] - 2026-09-14

### Added

- A package may be named `.ll` as well as `.limelight`: the open dialog,
  the installers' file associations and the desktop entry accept both.
- The status bar shows a large-series cache being built, with a progress
  bar, rather than leaving the app looking hung while it streams the source.

### Fixed

- Zooming into a large series with irregular x now reaches the raw samples.
  The sample range was located from the coarsest pyramid level, so a tight
  span still resolved to millions of samples; it is now located from the
  per-chunk bounds and made exact from the two boundary chunks.

## [0.0.4] - 2026-09-14

### Added

- A figure stacks any number of panels, not just two: `add_line_figure`
  takes `panels=[Panel(...), ...]` (`y2=` remains as the two-panel form),
  and the app and PDF size the figure from the panel count. Panels' x-axes
  are linked by their `shareGroup`, which was validated but never honoured.
- A time-series artist takes `color`, `fillColor` and `fillAlpha` for its
  envelope's edges and band.
- A badge in the corner of a large-series plot says whether it shows a
  min/max envelope or raw samples, and roughly how many samples each
  bucket folds.

### Changed

- A large-series envelope draws in one colour, opaque, where it used to
  take a colour-cycle step for each of its min line, max line and fill.
  Once zoomed in far enough that every point is a real sample it draws as a
  plain line rather than a collapsed envelope.

## [0.0.3] - 2026-09-11

### Added

- `LL desktop-entry` gives a pip or uv install on Linux what the native
  installers have: an applications-menu entry, a taskbar icon, and
  `.limelight` files that open with Limelight. `--remove` undoes it.

### Fixed

- The window icon was an SVG, which advertised no sizes, so the window
  carried no icon for the taskbar; it is now published at real sizes.

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

[Unreleased]: https://github.com/mikehulluk/limelight/compare/v0.0.6...HEAD
[0.0.6]: https://github.com/mikehulluk/limelight/compare/v0.0.5...v0.0.6
[0.0.5]: https://github.com/mikehulluk/limelight/compare/v0.0.4...v0.0.5
[0.0.4]: https://github.com/mikehulluk/limelight/compare/v0.0.3...v0.0.4
[0.0.3]: https://github.com/mikehulluk/limelight/compare/v0.0.2...v0.0.3
[0.0.2]: https://github.com/mikehulluk/limelight/compare/v0.0.1...v0.0.2
[0.0.1]: https://github.com/mikehulluk/limelight/releases/tag/v0.0.1
