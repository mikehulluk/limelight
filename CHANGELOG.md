# Changelog

Notable changes to Limelight. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) once past 1.0; before that, minor
versions may break things.

## [Unreleased]

## [0.0.17] - 2026-09-22

### Fixed

- The large-series cache is safe to share between processes. Two Limelights
  on one machine - two windows on a package, or the app and `LL` at once -
  used to write the cache's index over each other, losing entries, and both
  build the same pyramid; on Windows the second could fail replacing a file
  the first had open. A build now takes a lock named for its series (a file
  under the cache's `locks/`), so the second process waits, showing the wait
  in the status bar, and then finds the cache ready; index updates and font
  instancing take a lock the same way. Adds a dependency on `filelock`.

## [0.0.16] - 2026-09-22

### Changed

- The Story tab no longer repeats the project title above the story; the
  window title and Document Information carry it.

## [0.0.15] - 2026-09-21

### Added

- **Document Information** has a Typography tab - every declared font,
  whether this machine has it on screen and in figures, and every text
  style with the family and file it really gets, marked when a whole stack
  is missing and a renderer substituted its own default - and a Spacing tab
  with the page and the column's rhythm.
- The `typography` block sets two of the figures' distances, in points:
  `axisLabelPadPt` between an axis and its label, `figureTitlePadPt`
  between the axes and the title. Both default to matplotlib's (4 and 6),
  and a document written before them reads as having those.

### Fixed

- The y labels of stacked panels line up: the label of a panel with wide
  tick labels used to sit further out than its neighbours'.

## [0.0.14] - 2026-09-21

### Added

- **A document sets its own type.** The story's `typography` block says how
  every kind of text is set - `body`, `heading1`-`heading3`, `code`,
  `caption` and `captionLabel` (the "Figure 3."), `figureTitle`,
  `axisLabel`, `tickLabel`, `legend`, `annotation`, `badge`, `tableHeading`,
  `tableHeader`, `tableCell`, `tableNote` - each a stack of fonts, a size in
  points, a weight and a slant, plus the body's `lineHeight`. The story
  panel, the PDF and the figures all draw from it, so a document reads the
  same on every machine. The writer emits the full block; `Typography` and
  `TextStyle` (`limelight.typography`) change it.
- **Fonts travel with the document.** The manifest's `fonts` list declares
  each font as `builtin` (shipped with Limelight: Ubuntu, Ubuntu Mono, Noto
  Sans, DejaVu Sans, DejaVu Sans Mono), `system` (the reader's, by family
  name; used where present and skipped where not) or `bundled` (static
  files in the package with their licence, fingerprinted like a source's
  files). A text style's stack is tried in order, for a whole face the
  machine lacks and for single glyphs a face has not got, and its last font
  must be builtin or bundled. `Project.add_builtin_font`, `add_system_font`
  and `add_bundled_font` declare them.
- `verify` checks the typography names declared fonts, every stack ends in
  a guaranteed one, and a bundled font's files are present, match their
  fingerprints, are static (not variable) and come with their licence.

### Changed

- The default face is Ubuntu, falling back to Noto Sans, for prose, captions
  and figures alike, and Ubuntu Mono for code; it used to be whatever the
  desktop's UI font was (Segoe UI on Windows, Noto Sans headless), so the
  same package rendered and printed differently by machine. The shipped
  fonts add about 4 MB to the package.
- Figure text is set per figure from the document's typography rather than
  through matplotlib's process-wide rcParams, so two documents open in tabs
  keep their own type. Bold in figures now works with a variable system
  font: the weights a document asks for are instanced from it, once, into
  the cache directory.
- A table view's column headers are bold by default (`tableHeader`), as the
  story's own tables already were.
- The caption's "Figure 3." is a `<span class="limelight-caption-label">`
  rather than `<b>`, set by `captionLabel`.
- A UTC tick label's time is as long as the ticks need: `HH:MM` while every
  tick is on a whole minute, seconds once one is not, milliseconds once one
  is off a whole second. Ticks a few hours apart read `12:00` rather than
  `12:00:00`.

## [0.0.13] - 2026-09-21

### Fixed

- Help ▸ Check for Updates on a pip or uv install upgraded the bare
  `limelight-app`, which since 0.0.10 is the library without Qt, so the
  app it had just updated could not start. The upgrade now names
  `limelight-app[gui]`. A copy already caught by this is repaired with
  `uv tool install --force "limelight-app[gui]"` (or the pip equivalent).

## [0.0.12] - 2026-09-21

### Added

- A missing sample (a NaN cell) is marked with a small red cross at its x,
  at the height of the last good sample before it, once the view is zoomed
  to raw samples; zoomed out, a bucket's good samples draw and there is
  nothing to mark. Line artists mark their NaN cells the same way. A new
  `missingMarker` on line and timeSeries artists (`cross`, the default, or
  `none`) turns it off per artist.
- **Debug mode** (View ▸ Debug Mode, Ctrl+Shift+D, or the Debug button on a
  figure's toolbar): one flag every view reads. With it on, each
  large-series plot carries its badge (raw samples, or ~N samples per
  bucket) and each static figure in the story shows what its last render
  cost; with it off the document reads as a document.
- The story's status bar counts a render burst ("Rendering figures: 3 of
  12") and says how long it took once it lands. Under a figure, "Rendering…"
  shows while its render is out. Static renders log their worker time and
  total time to the timing probe (`--debug-timing`).

### Changed

- A UTC axis labels every tick on two lines, the full time over the date,
  instead of a bare "30" between "16:02" and "16:03" with the day in the
  corner. A view of a few seconds shows milliseconds; a view of whole days
  keeps only the date.
- A story figure re-renders only when the column's width changes, and not
  for a change under 8 px; a height-only resize (its own, after each
  render) no longer schedules the next render.
- Axis labels are a step smaller, level with the tick numbers.

## [0.0.11] - 2026-09-20

### Fixed

- The app no longer crashes opening a story whose page is about as tall as
  the window. A figure's height follows the column's width, so with the
  vertical scrollbar shown only as needed the column could be caught between
  two states - bar shown, figure shorter, no bar needed; bar gone, figure
  taller, bar needed - and Qt resolved each inside the other until the stack
  overflowed (a segmentation fault). The story's vertical scrollbar is now
  always shown, so the column's width does not depend on the page's height.

## [0.0.10] - 2026-09-19

### Changed

- **Qt is now the `gui` extra.** `pip install "limelight-app[gui]"` installs
  the desktop app and `LL pdf`; plain `pip install limelight-app` is the
  library and the rest of the CLI, without PySide6. The app and `pdf` say
  which extra to install when it is missing.
- **A time index with an absolute UTC origin is drawn on a UTC axis**, in
  matplotlib date numbers (days since 1970-01-01), the coordinate space the
  UTC axis limits and decorators already used. A `regularTime` index used to
  resolve to seconds (or its unit) since 1970 and `irregularIndexTime` to its
  raw coordinates, so a series and its own limits sat on different axes and
  neither got a calendar axis. A relative index still resolves to elapsed
  time in its unit. Every `timeSeries` axis other than `monthOrdinal1970`
  now gets the date formatter, and hover shows a UTC instant.
- A relative time index on a `timeSeries` axis is refused by `verify` (it
  would read as dates near 1970); use a `continuous` axis with the unit as
  its label. `add_line_figure` with no `x_axis` infers a `timeSeries` axis
  only from an absolute time index, a number line otherwise.
- The large-series cache stores the file's own coordinates and is not
  rebuilt when the axis they are drawn on changes (`SourceSpec` carries the
  `x0`/`dx` mapping for irregular sources too). Level buckets are NaN-aware:
  a NaN sample is a gap, and only a bucket with no samples at all is NaN.
  Cache schema version 1 → 2; existing caches are rebuilt.
- Negative tick labels use a hyphen-minus (`axes.unicode_minus` off), so they
  no longer render as boxes where the story font lacks U+2212.
- `limelight-v1-02-datasets.dhall`, an unused duplicate of the dataset
  types, no longer ships in packages.

### Added

- An hdf array can bind **one column of a 2-D dataset** (`column = Some j`;
  `hdf_array(..., column=j)`), so a file laid out rows × columns is
  referenced as it stands. Each column gets its own cache.
- `verify` (and `add_hdf_dataset`) check hdf bindings against the file: the
  dataset exists, is 1-D or 2-D as bound, the column is in range, and every
  array of a source is the same length.
- `EpochOffset.from_unix_ns/us/s` and `from_datetime`, and `to_unix_ns`.

## [0.0.9] - 2026-09-16

### Added

- A package can carry metadata: a top-level `metadata` list in the manifest
  of `{ name, type, value }` entries, the types being `int`, `float`,
  `datetime` (ISO 8601), `string` and `version` (semantic). `verify` checks
  each value reads as its type; `LL meta <package>` prints them as a JSON
  list with typed values; `summary` and the app's Document Information list
  them. `project.add_metadata(name, value)` types an entry from its value.

### Changed

- Figure text has a hierarchy: axis labels at the story's size, tick numbers
  and legends a step smaller, and the title at the body size in bold rather
  than larger than the story's headings. Decorator, annotation and arrow
  labels are the same small step, and the large-series badge smaller still,
  all relative to the story's size rather than fixed point sizes.
- `set_story_markdown` requires `base_dir`, what relative image paths in the
  story resolve against; it used to default to the working directory.
- A figure's pop-out window and Explore view open at the figure's own size
  (its `size`, or the column and its panel count), shrunk to fit the screen.
- `largeseries.SourceSpec` states every field; `SourceSpec.uniform(...)` and
  `SourceSpec.irregular(...)` build the two kinds. A uniform grid's `dx`
  must be positive.
- Building a large-series cache checks that an irregular x array is sorted,
  raising `NonMonotonicXError` at the first sample that steps back; every
  range query relies on it, and used to be quietly wrong when it was not.
- `targetBuckets` on a time-series artist now caps the envelope's resolution
  (one bucket per pixel column by default); it had been read and ignored.
- The `frame` of a MapSpec, TableViewSpec and FormSpec is optional, and the
  writer no longer emits a placeholder for it.

### Fixed

- `verify` rejects a time-series artist over a source with a calendar index,
  which the app could not draw and used to fail on when the figure opened.
- Check for Updates tells a uv tool install by the receipt uv leaves in its
  environment, rather than by the look of its path.

## [0.0.8] - 2026-09-14

### Added

- Help > Check for Updates... looks up the latest release on GitHub and
  shows its notes. A Windows installer install downloads the new installer,
  checks it against the release's `SHA256SUMS`, and runs it silently,
  reopening Limelight after; a pip or uv install is upgraded in place with
  the tool that installed it and offered a restart; the macOS and Linux
  bundles are pointed at the download. The About box now says which
  version this is and how it was installed.
- Each release publishes a `SHA256SUMS` file alongside its installers.

## [0.0.7] - 2026-09-14

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

[Unreleased]: https://github.com/mikehulluk/limelight/compare/v0.0.17...HEAD
[0.0.17]: https://github.com/mikehulluk/limelight/compare/v0.0.16...v0.0.17
[0.0.16]: https://github.com/mikehulluk/limelight/compare/v0.0.15...v0.0.16
[0.0.15]: https://github.com/mikehulluk/limelight/compare/v0.0.14...v0.0.15
[0.0.14]: https://github.com/mikehulluk/limelight/compare/v0.0.13...v0.0.14
[0.0.13]: https://github.com/mikehulluk/limelight/compare/v0.0.12...v0.0.13
[0.0.12]: https://github.com/mikehulluk/limelight/compare/v0.0.11...v0.0.12
[0.0.11]: https://github.com/mikehulluk/limelight/compare/v0.0.10...v0.0.11
[0.0.10]: https://github.com/mikehulluk/limelight/compare/v0.0.9...v0.0.10
[0.0.9]: https://github.com/mikehulluk/limelight/compare/v0.0.8...v0.0.9
[0.0.8]: https://github.com/mikehulluk/limelight/compare/v0.0.7...v0.0.8
[0.0.7]: https://github.com/mikehulluk/limelight/compare/v0.0.6...v0.0.7
[0.0.6]: https://github.com/mikehulluk/limelight/compare/v0.0.5...v0.0.6
[0.0.5]: https://github.com/mikehulluk/limelight/compare/v0.0.4...v0.0.5
[0.0.4]: https://github.com/mikehulluk/limelight/compare/v0.0.3...v0.0.4
[0.0.3]: https://github.com/mikehulluk/limelight/compare/v0.0.2...v0.0.3
[0.0.2]: https://github.com/mikehulluk/limelight/compare/v0.0.1...v0.0.2
[0.0.1]: https://github.com/mikehulluk/limelight/releases/tag/v0.0.1
