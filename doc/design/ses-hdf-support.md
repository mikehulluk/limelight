# Design: Limelight support for BigO/SES recording HDF5

Status: **Part A built (unreleased, after 0.0.22); Parts B–C proposed, for review.**
2026-11-07. Issue ID prefix: **SES** (e.g. SES-C3).

This note is written for review from the BigO side. It records what Limelight
does today with the SES whole-recording HDF5, what was found when the example
files were run through BigO's exporter, and what is proposed in each repo.
Questions for the reviewer are collected in §5; please answer by ID.

Related BigO documents: `doc/design/claude.design.limelight-download.md`
(LLD), `doc/design/claude.design.hdf5-recording-store.md`,
`doc/design/claude.design.missing-samples.md` (MS), and the exporter
`src/python/src/bigo/cloud/server/filewriter/limelight_export.py`.

## 1. Summary

| ID     | Where     | What | State |
|--------|-----------|------|-------|
| SES-A  | Limelight | An hdf source can name its file by URL + SHA-256 + size instead of carrying it; the app asks before downloading. | **Built**, unreleased |
| SES-B  | —         | Findings from running the exporter on the example files. | Done |
| SES-C1 | BigO      | Give the exporter a measured sample period. Keep the regular time axis. | Proposed |
| SES-C2 | Limelight | Draw a real pause in sampling as a gap. Only needed if such pauses exist. | Deferred |
| SES-C3 | Limelight | Status-aware samples: bind `/status`, declare what each code means, draw each reason distinctly. | Proposed |

Headline: **the exporter works today, unchanged, against current Limelight
`main`**. All three example files convert and pass `LL verify`, and NaN
cells are drawn as gaps with a red cross.

Two things are missing:

- **A correct sample period.** The irregular `/time_us` in the example files
  most likely comes from how BigO builds it (§3.3). Limelight's regular time
  axis is still the right model (C1).
- **Any notion of *why* a sample is missing** (C3).

Limelight aims to stay a general-purpose tool. Every proposal here is
general, and SES is one user of it.

## 2. SES-A — HDF5 data by URL (built)

A large HDF5 need not be inside the package. The cloud could serve a
`.limelight` that is a few KB of manifest pointing at the stored whole-recording
HDF5, rather than a zip holding a copy of it.

### 2.1 Manifest

`limelight-v1-03-datasets.dhall`:

```dhall
let RemoteFile = { url : Text, sha256 : Text, sizeBytes : Natural }

let IndexedTableFromHdf5 =
      { …                                  -- unchanged fields
      , remote : Optional RemoteFile        -- new
      , …
      }
```

- `url` must be `https://` with a host and no credentials.
- `sha256` is the full digest, 64 lower-case hex characters.
- `sizeBytes` must be greater than zero.

When `remote` is `Some`, the file is not in the package. `path` is then only
the file's name, and nothing is stored at it. Packages written before this
change have no `remote` field and still read.

### 2.2 Writer

```python
project.add_hdf_dataset(
    id="recording",
    hdf5_path=input,                 # local copy: bindings are checked against it
    y_arrays=[...],
    index=...,
    url="https://<cloud>/…/recording.h5",
)
```

The writer hashes and sizes the local file and records both. It does not copy
the file into the package. The bytes served at `url` must be identical to the
local file.

### 2.3 Reader behaviour

- **App.** On opening (or reloading) a package, before drawing:
  - Remote files that are already cached are used.
  - Files from a trusted domain are downloaded with a cancellable progress
    dialog.
  - For anything else, a dialog lists each source, its size and its URL. It has
    one "Always allow downloads from <host>" checkbox per host, and
    Download / Don't Download buttons.
  - If the reader declines, each figure or table using the file shows "The data
    for source … is at <url> (…), which has not been downloaded" in its place.
- **Settings.** File › Settings… holds the trusted domains, with Add and
  Remove. A domain covers its subdomains. The list is stored in
  `<config>/limelight/app-settings.json`. A hand-written
  `limelight-setting.json` can add more under `trustedDownloadHosts`.
- **Cache.** Downloads go to `<user cache>/limelight/downloads/<sha256>.h5`, or
  to `LIMELIGHT_DOWNLOAD_DIR` if that is set. A download is written to a
  `.part` file and only renamed into place if its size and SHA-256 match, so
  each file is fetched once across packages.
- **CLI.** `LL verify` and `LL pdf` download only from trusted hosts, or from
  any host with `--allow-downloads`. `verify` prints `UNCHECKED …` for a file
  it did not fetch and still passes the package.
- **Signatures.** A remote file's signatures are checked against the declared
  digest, so they need no download.

### 2.4 Code

| Change | Files |
|---|---|
| New modules | `src/limelight/remote_files.py` (fetch, verify, cache, host rules), `src/limelight/qt_downloads.py` (prompt, progress, Settings dialog) |
| Changed modules | `writer.py`, `hdf_sources.py` (`hdf_file_path`, the one place a source's file is resolved), `app.py`, `largeseries_bridge.py`, `semantic.py`, `signing.py`, `settings.py`, `cli.py`, `qt_app.py` |
| Tests | `tests/test_remote_files.py` (20 tests; the network is faked) |

### 2.5 For BigO

- The URL must serve exactly the bytes of the stored HDF5. Re-encoding or
  compressing the file in transit breaks this; HTTP `Content-Encoding` is fine,
  since `urllib` does not decode it.
- The download is a plain GET with no authentication, so the URL has to be
  fetchable as given. A signed, expiring URL works, until it expires.

## 3. SES-B — Findings from the example files

Files: `~/Downloads/*.hdf5` on the author's machine. They share one layout:

- datasets: `/values [T,R] f64`, `/status [T,R] u8`, `/time_us [T] i64`,
  `/sensor_s16 [R] u16`
- attributes: `format_version=1`, `n_samples`, `n_sensors`,
  `sample_idx_start`, `sample_rate_us`, `time_us_start`, `u32`, `nr32`

There was also one unrelated `prism-pipeline` matrix file (`N0x…116A-8886.h5`),
which is not considered here.

### 3.1 The exporter runs unchanged

`convert_whole_recording_hdf5_to_limelight` was run on
`aged-red-bat_recording-164`,
`inprogress_meaty-smoke-dab_recording-000_part-0_fit-4783` and
`800000000000000000000A3B_22`, with no `time_fit` in the metadata (so the
block-affine path is used). All three wrote packages and `LL verify` said `OK`.

### 3.2 Status and NaN agree

In every file, `values` is NaN exactly where `status != 0`, and never where
`status == 0`. Only codes 0 and 1 occur (36 bad reads in the in-progress file).
No file has `ST_NOT_SCHEDULED` (2) or `ST_ROW_GAP` (3).

### 3.3 `/time_us` is not evenly spaced, and why

| File | rows | `sample_rate_us` | median Δt | span from `/time_us` | span the regular axis draws |
|---|---:|---:|---:|---:|---:|
| `aged-red-bat_recording-164` | 120 000 | 10 000 | 10 000 | **1807.8 s** | 1200.0 s |
| `8000000000002c61_recording-89` | 120 000 | 10 000 | 10 000 | **1791.9 s** | 1200.0 s |
| `inprogress_meaty-smoke-dab…` | 576 | 50 000 | **56 701** | 32.6 s | 28.8 s |
| `800000000000000000000A3B_22` | 312 528 | 11 519 | 11 519 | 2.3 s (**goes backwards**) | 3600.0 s |

**How `/time_us` is built.** See `blockTimes` and `blocksGrid` in
`src/hsk/src/BigO/Tds/Cloud/Output.hs`:

- Each block's rows start at that block's own start time: its node time, or the
  recording's fit applied to it.
- Rows are then spaced by a period per block:
  - **Without a stored fit:** the nominal `sample_period_us`.
  - **With a fit:** the block's measured
    `(nodeTimeLast − nodeTimeFirst) / nSamples × slope`.
- `blocksGrid` refuses blocks that are not contiguous in sample index. So every
  row is a real, consecutive sample, and no ticks are missing between blocks.

**What the files show.** `aged-red-bat` and `recording-89` were built without a
fit. Inside each block, rows are exactly 10 ms apart. But consecutive blocks of
4 000 rows start about **60.3 s apart, not 40 s**:

| | block start spacing | real period, if sampling is continuous |
|---|---|---|
| recording-164 | 60.426, 60.368, 60.318, 60.306 … s | 15.11, 15.09, 15.08, 15.08 ms |
| recording-89 | 60.559, 60.457, 60.377, 60.477 … s | 15.14, 15.11, 15.09, 15.12 ms |

The spacing varies smoothly and slightly, which looks like clock drift on a
steady rate rather than a programmed 20 s pause.

**Most likely explanation.** The node samples steadily every ~15.1 ms while
declaring 10 ms. Each block's rows are squeezed into 40 s at the nominal
period, and the time then jumps about 20 s to where the next block really
starts. `/time_us` is therefore a sawtooth: right at each block start, and
increasingly early within the block.

**Supporting evidence.** The in-progress file is a single block, and its rate
was measured: 56 701 µs per sample against a nominal 50 000. That is the same
pattern (about 13% slower than declared) with no jumps.

**Not yet confirmed.** The sample values are synthetic counters and do not
encode time. Comparing a block's stored `nodeTimeFirst` and `nodeTimeLast`
with `nSamples × sample_period_us` would settle it (SES-Q1).

**Consequences:**

- The regular axis the exporter draws, which uses the nominal
  `sample_rate_us`, is too short by the same ratio: 1200 s against about
  1808 s.
- A regular axis with the **measured** period would be close to right. This
  is what LLD-2 intended.
- Smaller issue: the fit branch divides by `nSamples` rather than
  `nSamples − 1`. That leaves a step of about one sample period at each block
  boundary.

**The `A3B_22` file** is a separate case. Its `/time_us` steps back 2 338 357 µs
every 204 rows: it looks like 204-row blocks stitched together, each with the
same times. Limelight's irregular axis rejects it (`NonMonotonicXError`). It is
presumably old test data from July (SES-Q2).

To reproduce, run in an environment that has `h5py`:

```python
import h5py, numpy as np
with h5py.File("aged-red-bat_recording-164.hdf5") as h:
    t = h["time_us"][()]
    starts = t[::4000]
    print(np.diff(starts)[:6] / 1e6)          # ~60.3 s between block starts
    print(np.diff(starts)[:6] / 4000 / 1e3)   # ~15.1 ms per sample if continuous
```

### 3.4 Irregular time from `/time_us` also works in Limelight

As a check, `/time_us` can be bound as an array and used as the index:

```python
y_arrays=[hdf_array("time_us", "/time_us"), hdf_array("sensor_0", "/values", column=0), …]
index=Index.irregular_index_time("time_us", origin=EpochOffset.from_unix_us(0), unit="us")
```

With this, `aged-red-bat` draws over its full 30 minutes (08:03–08:33 UTC).
It is not proposed: it would faithfully reproduce the sawtooth in §3.3.

### 3.5 The time axis does not affect the large-series cache

For a regular index, the min/max pyramid is built and stored by sample index
only. The axis is applied when the cache is queried (`x = x0 + i·dx`, in
`largeseries/cache.py`). So:

- Correcting the origin or period, or switching between a fit and the
  block-header affine, changes no cached data and triggers no rebuild.
- The y envelope is the same in every case, because buckets are counts of
  samples, not spans of time.

An irregular index (§3.4) would differ in three ways:

- `/time_us` is read when the cache is built.
- It must be non-decreasing.
- `x_min`/`x_max` are stored for every bucket at every level, which makes the
  cache larger.

Even then, the y envelope is identical. Only where buckets are placed changes.

## 4. Proposals

### 4.1 SES-C1 (BigO) — a measured period on a regular axis

Keep `Index.regular_time(...)` in `limelight_export.py`, as LLD-2 intended.
Make sure its period is measured, not nominal:

- **With a `time_fit`:** use it, as now.
- **Without one:** do not use the `sample_rate_us` attribute. Use the measured
  rate over the whole recording, `(time_us[-1] − time_us[0]) / (T − 1)`. That
  is exact at the first and last rows even when `/time_us` is a sawtooth.
  Better still, have the Haskell side always send a fit, even a provisional
  one for an in-progress block.

Upstream of the exporter, two fixes would make `/time_us` itself regular:

- Make `blockTimes` use measured per-block rates even without a fit.
- Divide by `nSamples − 1` rather than `nSamples`.

Root cause, if §3.3 is confirmed: the node runs at about 1.5× its declared
period at 10 ms (about 1.13× at 50 ms). That is for the firmware side
(SES-Q1).

This needs no Limelight change, and per §3.5 it does not touch the cache.

The irregular index (§3.4) is **not** recommended. If BigO keeps per-block
timing on purpose, Limelight could instead add a general **piecewise-regular**
index: a short list of `(first row, start time, period)` segments. It would be
exact, compact, and keep the cache in sample-index space, and it suits any
logger that timestamps blocks. It is only worth building if blocks really need
their own clocks (SES-Q3).

### 4.2 SES-C2 (Limelight) — draw a real pause as a gap (deferred)

This is only needed if a recording can genuinely pause between consecutive
samples. §3.3 suggests the example files do not. It is kept here for SES-Q3.

**Today.** Consecutive samples are joined, in both the raw view and the
min/max envelope. A real pause between two samples would be drawn as a
straight line. In the envelope view, a bucket that straddles it bridges it.

**Proposal.** Add an optional gap threshold to the irregular time index:

```dhall
-- irregularIndexTime gains:
, irregularTimeGapOver : Optional Natural   -- in irregularTimeUnit; a step longer than this is a gap
```

Writer:
`Index.irregular_index_time("time_us", origin=…, unit="us", gap_over=1_000_000)`.

Rendering:

- **Raw view.** The line breaks at each step longer than `gap_over`, the same
  way it breaks at NaN.
- **Pyramid.** A bucket records whether it contains such a step. The envelope
  is split after that bucket, so no band is drawn across the gap.
- **Cache.** The schema version is bumped. Gap flags are a `uint8` per bucket
  per level.

**Alternative (BigO only, no Limelight change).** The converter fills each gap
with rows of `ST_ROW_GAP` and NaN. With SES-C3 these are drawn as a gap with a
reason.

- That costs rows: about 2 000 per 20 s gap at 100 Hz, and more at higher rates
  or over long gaps.
- It also needs `/time_us` to be filled across the gap.

See SES-Q3.

### 4.3 SES-C3 (Limelight) — status-aware samples

**Problem.** Every non-valid cell becomes NaN, and Limelight reads NaN as
"missing, draw a gap with a red cross". That throws away two things:

1. **The reason.** Bad read, not scheduled and row gap all look the same.
2. **The right drawing.** `ST_NOT_SCHEDULED` is decimation, not loss. A sensor
   read on every 10th tick would be about 90% NaN. Limelight would draw it as
   disconnected points under a blanket of red crosses, not as a line. This is
   inferred from how NaN is drawn; none of the example files has status 2.

**Proposal.** A source declares what its status codes mean, once. Each array
can then name a status dataset (or a column of one) alongside its values.

```dhall
let StatusMeaning = < valid | skip | absent >

let StatusCode =
      { code : Natural
      , meaning : StatusMeaning
      , label : Text                    -- e.g. "Bad read"; shown in legend, tooltips, tables
      , marker : Optional Text          -- raw-view marker for `missing`; default "cross"
      , color : Optional Text
      }

let HdfStatusBinding = { dataset : Text, column : Optional Natural }

-- HdfArrayBinding gains:
, status : Optional HdfStatusBinding

-- IndexedTableFromHdf5 gains:
, statusCodes : List StatusCode
```

Writer, as the BigO exporter would use it:

```python
codes = [
    StatusCode(0, "valid",   "Valid"),
    StatusCode(1, "absent",  "Bad read",      marker="cross"),
    StatusCode(2, "skip",    "Not scheduled"),
    StatusCode(3, "absent",  "Row gap",       marker="none"),
]
project.add_hdf_dataset(
    id="recording", …, status_codes=codes,
    y_arrays=[hdf_array(f"sensor_{s16}", "/values", column=j,
                        status=hdf_status("/status", column=j)) for j, s16 in …],
)
```

**Semantics.** Status is authoritative: the value under a non-`valid` code is
ignored, whether or not it is NaN.

| Meaning | Line | Envelope (zoomed out) | Raw view (zoomed in) | Table cell | Counted as missing |
|---|---|---|---|---|---|
| `valid` | drawn | included in min/max | drawn | value | no |
| `skip` | joins the neighbouring valid samples | excluded; no mark | no mark | blank, label in tooltip | no |
| `missing` | gap | excluded; a per-code rug mark along the axes' bottom edge where a bucket has any | the code's marker, in its colour | the code's label, in grey | yes |

Other rules:

- **Codes not declared** are treated as `missing`, with the label
  "Status <n>". `LL verify` warns about them; see SES-Q5.
- **NaN under a `valid` code** is still drawn as a gap, as now. This keeps
  packages that have no `status` binding working exactly as today.
- **Legend.** Each `missing` code that occurs in view gets a legend entry with
  its marker and label.
- **Pyramid.** For each bucket and level, store a count per `missing` code
  (`uint32`, or a presence bitmask if only presence is ever shown; see
  SES-Q4). `skip` cells are dropped before the min/max is taken. The cache key
  gains the status binding, and the schema version is bumped.
- **PDF.** The same envelope, rug and legend as the screen. The table view
  shows labels.
- **Verify.** The status dataset must exist, have the same length as the
  values (and the same column count, for a 2-D dataset), and be an integer
  type.

Size: comparable to the discrete-axis work in 0.0.22. It touches the
manifest types, the writer, semantic validation, the large-series pyramid and
cache, the matplotlib artist, the table view, the PDF and the tests.

**Not proposed:** deriving status from NaN alone, or keeping a separate
missing-mask format. `/status` is already what BigO writes and treats as
authoritative (see the comments in `bigoHdfConv.c`).

### 4.3.1 Detailed design — for review before it is built

Status: **agreed 2026-11-07** (decisions below); **built** on branch
`feat/status-aware-samples`, unreleased. SES-Q6 is BigO's: it only
decides which codes BigO declares, not anything in Limelight. Nothing here is
specific to BigO: the concept is a general **sample status**, meaning one
integer code per sample plus a table that says what each code means. SES is
one user of it.

#### D1 — The model

| Rule | Detail |
|---|---|
| Kinds of sample | Every sample is one of: `valid`; `skip`; or missing, of a particular kind. |
| Kinds of missing | Up to 32 per series, numbered as bits. Bit 0 is a NaN under a `valid` code, or in a series with no status binding. Bit 1 is a code the table does not declare. Bits 2–31 are the declared `missing` codes, in declaration order, so at most 30 per source. |
| Status is authoritative | A value under a non-`valid` code is ignored, whatever it holds. |
| Code type | Codes are any integers, `Integer` in Dhall. They are not limited to `uint8`, so other formats fit. |
| No status binding | A series without one behaves as today. Its only kind of missing is NaN (bit 0). |

#### D2 — Large-series engine (`largeseries/`)

- **Where it lives.** A new `status.py` holds `StatusMap`, which maps
  code → valid, skip or missing bit.
- **Source.** `SourceSpec` gains an optional status dataset and column, plus a
  `StatusMap`.
- **Pyramid buckets.** Min/max are taken over `valid` samples only. Each bucket
  also stores a `uint32` **presence mask**: bit *k* is set if the bucket holds
  any sample of missing kind *k*. Levels are combined by OR.
  - This means presence only, not counts. Counts are a bigger cache and only
    matter if SES-Q4 says they are wanted.
- **Cache identity.**
  - The content hash covers the status data and the code→meaning mapping, so
    the same data read with a different table gets a different cache.
  - The memo key and the build lock use the y selector, plus the status
    selector, plus a fingerprint of the map.
- **Schema.** The version goes to 3, so every existing cache rebuilds once.
- **Queries** return a per-point mask:
  - A bucket with no valid value and an empty mask held only skipped samples.
    It is **dropped**, not drawn as NaN, so the line joins across it.
  - Skipped raw samples are dropped the same way.
  - A bucket with no valid value and a non-empty mask is a gap (NaN), as today.

#### D3 — Drawing (`largeseries/mpl.py`)

For each kind of missing that the series contains, one marks line and one rug
line are drawn. Kinds that never occur get no artists, so the legend only
names kinds that are really there.

| Zoom | What is drawn |
|---|---|
| Raw view (zoomed in) | Each missing sample gets its kind's marker, at the height of the last good sample before it. This is today's red cross, made per kind. |
| Envelope view (zoomed out) | Each bucket holding a kind gets a tick in that kind's colour along the foot of the axes (the "rug"). |

- **Kind styles.** Each kind has a `label`, a `marker` (`cross`, `circle`,
  `square`, `triangle`, `diamond` or `none`) and a `color`.
  - Defaults: NaN is a red cross with no label, so its look is unchanged.
    An undeclared code is a grey cross labelled "Unknown status".
  - A declared code with no `color` takes the red default.
  - `marker = none` means the kind is never marked, neither zoomed in nor in
    the rug. It is still a gap.
- **Artist switch.** The artist-level `missingMarker = none` still turns all
  marks off for that artist.
- **Legend.** Declared kinds that occur get a legend entry with their marker
  and label. The same label from several series in one panel is shown once.

**⚠ Behaviour change (D3-a).** Zoomed out, plain NaNs in existing packages
would now leave rug ticks. Today they leave nothing. This is arguably what a
reader wants, but it changes every existing figure that has NaNs. The options:

- (a) Rug for every kind, including NaN.
- (b) Rug only for declared status kinds.
- (c) A per-artist switch.

**Decision needed.**

#### D4 — Manifest and writer

```dhall
let StatusMeaning = < valid | skip | absent >
let StatusMarker  = < cross | circle | square | triangle | diamond | none >
let StatusCode =
      { code : Integer, meaning : StatusMeaning, label : Text
      , marker : Optional StatusMarker, color : Optional Text }
let HdfStatusBinding = { dataset : Text, column : Optional Natural }
-- HdfArrayBinding     gains  status      : Optional HdfStatusBinding
-- IndexedTableFromHdf5 gains statusCodes : List StatusCode
```

- **Changes from the earlier sketch:** `code` is `Integer` rather than
  `Natural`, and `marker` is an enum rather than free text. The meaning is
  `absent`, not `missing`: `missing` is a Dhall keyword, so a manifest could
  not name it. Throughout this note, a "missing" code means one whose
  meaning is `absent`.
- **Older packages** lack both fields and read as before.
- **Writer:**
  - `StatusCode(code, meaning, label, marker=None, color=None)`
  - `hdf_array(..., status="/status", status_column=j)`
  - `add_hdf_dataset(..., status_codes=[...])`
- **When the writer checks.** `add_hdf_dataset` checks each status binding
  against the file, in the same place the value bindings are checked.

**Open (D4-a).** The table is declared per source and the binding per array.
Should a source be able to hold arrays whose codes mean different things?
For example, two different status datasets with different meanings. The
proposal says no: one table per source. A file that needs two can be declared
as two sources.

#### D5 — Validation

- **Semantic checks:**
  - codes are unique;
  - at most 30 codes mean `missing`;
  - every label is non-empty;
  - every colour is one matplotlib accepts (`is_color_like`, as for other
    artists' colours);
  - an array that has a `status` binding is in a source that declares
    `statusCodes`.
- **`LL verify`:**
  - the status dataset exists;
  - it is an integer type;
  - it has the same row count as the values (and a valid column, for a 2-D
    dataset).
  - It also scans for codes nobody declared and reports them. Whether that
    warns or fails is SES-Q5. The proposal: warn, as `NOTE …`, and pass.

#### D6 — Elsewhere in the app

| Where | Behaviour |
|---|---|
| Table view (HDF source) | `valid` shows the value. `skip` is a blank cell. `missing` shows the code's label (an undeclared code shows "Unknown status"; a NaN is blank, as today). |
| CSV export | `skip` and `missing` cells are blank. The labels are *not* written, so a numeric column stays numeric. |
| PDF | Uses the same matplotlib artists, so it gets the rug, marks and legend. |
| Line, scatter and stem artists | Unaffected: they cannot draw an HDF source. |
| CSV sources | No status binding for now. The model would carry over, as a status column in the CSV, if wanted later. |

#### D7 — Tests

- `StatusMap` classification.
- Pyramid masks, including cascade OR and skipped-only buckets being dropped.
- Cache identity: the same data with a different map gives a different cache.
- Queries in the raw view, the envelope view and the reduced raw fallback.
- Marks and rug per kind; the legend shows only kinds present; labels are
  de-duplicated.
- Writer → manifest → verify round trip; semantic errors.
- Table cells; CSV export.
- An end-to-end package built from an SES-shaped file.

#### Decisions

| ID | Question | Decision (2026-11-07) |
|---|---|---|
| D3-a | Should a zoomed-out view mark plain NaNs (a visible change for existing packages)? | **Yes.** Every kind of missing, NaN included, gets ticks along the foot of the axes when zoomed out. Note it in the CHANGELOG. |
| D4-a | One code table per source? | **(A): one table per source.** A file whose status datasets give one code different meanings is declared as two sources over the same file. (B) can be added later without breaking packages. |
| SES-Q4 | Presence or counts? | **Presence only.** A bucket says whether it holds any missing samples of each kind, not how many. |
| SES-Q5 | Should undeclared codes warn or fail? | **Fail.** `LL verify` reports the package `INVALID`, naming the source, the status dataset and the undeclared codes. The app still draws them, as "Unknown status". |
| SES-Q6 | Do BigO's four codes map to valid / missing (cross) / skip / missing (no marker)? | Open, for BigO. |

#### D4-a — options

The question only matters for a file whose status datasets give the same code
different meanings. For example, a logger file with
`/gps_status`, where `1` means "no fix", and `/adc_status`, where `1` means
"clipped".

**SES, either way:** one `/status` with one meaning per code. One table per
source is enough.

**(A) One table per source.** A file with two meanings is declared as two
sources over the same file.

```python
gps = [StatusCode(0, "valid", "Fix"), StatusCode(1, "absent", "No fix"), StatusCode(2, "skip", "Stale")]
adc = [StatusCode(0, "valid", "OK"),  StatusCode(1, "absent", "Clipped", marker="triangle")]
project.add_hdf_dataset(id="gps", hdf5_path=h5, path="data/log.h5", status_codes=gps,
    y_arrays=[hdf_array("lat", "/gps", column=0, status="/gps_status"), …])
project.add_hdf_dataset(id="adc", hdf5_path=h5, path="data/log.h5", status_codes=adc,
    y_arrays=[hdf_array("v0", "/adc", column=0, status="/adc_status", status_column=0), …])
```

| | |
|---|---|
| For | Simplest manifest. Every array of a source reads its codes the same way. |
| Against | Arrays that share an index are split across sources, so a figure or table names two `data` ids. The file is one file. |

**(B) Named tables in a source.** Each status binding names the table it uses.

```python
project.add_hdf_dataset(id="log", hdf5_path=h5,
    status_tables={"gps": gps, "adc": adc},
    y_arrays=[hdf_array("lat", "/gps", column=0, status="/gps_status", status_table="gps"),
              hdf_array("v0",  "/adc", column=0, status="/adc_status", status_column=0, status_table="adc"), …])
```

```dhall
-- IndexedTableFromHdf5 gains  statusTables : List { id : Text, codes : List StatusCode }
-- HdfStatusBinding           = { dataset : Text, column : Optional Natural, table : Text }
```

| | |
|---|---|
| For | One source per file, however its status is laid out. |
| Against | One more name to declare and validate, and a source whose arrays read their codes differently. |

(A) can be widened to (B) later without breaking packages. A one-table source
would read as a source with one table.

## 5. Questions for the BigO reviewer

- **SES-Q1.** Is the node really sampling at ~15.1 ms when it declares
  10 ms (and at ~56.7 ms when it declares 50 ms)? §3.3 explains the reasoning.
  - To check: for `recording-164`, compare each block's stored
    `nodeTimeLast − nodeTimeFirst` with `nSamples × sample_period_us`.
  - If confirmed: is the slower rate expected (for example, sensor reads
    overrunning the tick)? Should `sample_period_us` report the real rate?
  - If it is not confirmed, and the ~20 s jumps are real pauses, then LLD-2's
    single affine time model is wrong for these recordings.
- **SES-Q2.** Is `/time_us` guaranteed non-decreasing in production
  whole-recording files? The `800000000000000000000A3B_22` file is not. Is
  that file obsolete test data?
- **SES-Q3.** Can a recording ever pause between consecutive sample indices,
  or can blocks deliberately run on different clocks? If so, which fits
  better:
  - Limelight detects time jumps (SES-C2)?
  - The converter writes `ST_ROW_GAP` rows (which makes C2 unnecessary once
    C3 exists)?
  - A piecewise-regular index (§4.1)?

  If not, C2 and the piecewise index can both be dropped.
- **SES-Q4.** In the zoomed-out view, is showing *where* each kind of missing
  sample occurs enough (a presence mark), or should Limelight show *how many*
  (counts, for example in a tooltip)?
- **SES-Q5.** Should a status code Limelight was not told about fail
  `LL verify`, or only warn?
- **SES-Q6.** Is the meaning table in §4.3 right for BigO's four codes? In
  particular:
  - Should `ST_NOT_SCHEDULED` be `skip` (joined across, unmarked)?
  - Should `ST_ROW_GAP` be `missing` without a marker?
- **SES-Q7.** Would the cloud serve the whole-recording HDF5 at a stable
  `https` URL, so the `.limelight` download can reference it (SES-A) instead
  of zipping a copy? What authentication would that URL need? Limelight
  currently sends a plain unauthenticated GET.
- **SES-Q8.** Can the Haskell side always send a `time_fit`, including a
  provisional one for an in-progress block? If it can, the exporter never
  needs the nominal `sample_rate_us` (§4.1). In the example, that is
  50 000 µs against a measured 56 701 µs.

## 6. Out of scope

- Prism outputs (LLD-4).
- Type-checking a manifest against the language reference (LLD-8). This is
  still not done.
- The unrelated `prism-pipeline` matrix HDF5 layout (`/<s16> [n,2]` with
  `time,value` columns).
