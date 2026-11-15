-- Limelight v1 plot artist types.
--
-- Plot artists bind source data arrays to rendered lines and scatter points.
--
-- Data-binding fields (`y`, `x`, `colorBy`, `sizeBy`, ...) are strings of the
-- form `table_id['column_name']`. Limelight resolves this at load time into an
-- IndexedArray - the named column plus the Index attached to that table. A
-- line/time-series artist's x-axis is implicit (the referenced array's own
-- Index) by default; a line artist may set `xOverride` to instead plot
-- against an explicit column (e.g. a phase plot, where the x-axis is another
-- data array rather than the table's Index). Scatter-style artists always
-- name both axes explicitly via `x`. Fields on the same artist are not
-- required to name the same table - cross-table references are permitted,
-- not a validation error.

let Core = ./limelight-v1-00-core.dhall

let Transforms = ./limelight-v1-04-transform.dhall

let TextControlParameterMatch = { controlParameter : Core.ControlParameterId, value : Text }

-- `missingMarker` says how a missing sample (a null / NaN cell) is shown:
-- `cross` (the default) draws a small red cross at the sample's x, at the
-- height of the last good sample before it, so one bad reading is seen and
-- not just a hole in the line; `none` leaves the gap.
let MissingMarker = < cross | none >

-- `drawStyle` says how a line joins its samples: `straight` (the default)
-- draws a straight segment from each sample to the next; `stepsPre` holds
-- each sample's value back to the previous sample, so the jump is at the
-- earlier x; `stepsPost` holds it forward to the next sample, so the jump is
-- at the later x; `stepsMid` puts the jump halfway between the two samples.
-- A series that changes only at events (a balance, a rate) reads correctly
-- only as steps.
let DrawStyle = < straight | stepsPre | stepsPost | stepsMid >

-- `fillColor`/`fillAlpha` shade the area between the line and `fillBaseline`
-- (0 by default) - a filled line with `drawStyle = stepsPost` and narrow,
-- regularly-spaced samples renders as a histogram, each sample's value held
-- as a rectangle out to the next sample. Unset `fillColor` draws no fill.
let LineArtist =
      { id : Core.Id
      , y : Text
      , label : Optional Core.DisplayText
      , alpha : Optional Double
      , xOverride : Optional Text
      , color : Optional Text
      , linestyle : Optional Text
      , linewidth : Optional Double
      , drawStyle : Optional DrawStyle
      , marker : Optional Text
      , missingMarker : Optional MissingMarker
      , fillColor : Optional Text
      , fillAlpha : Optional Double
      , fillBaseline : Optional Double
      , visibleWhen : Optional TextControlParameterMatch
      }

-- `edgeColor`/`edgeWidth` are each marker's outline, drawn white and 0.45pt
-- wide when unset. That outline keeps markers distinguishable from each
-- other and from a panel's lines, but at high point density it can read as
-- a web of faint lines between overlapping markers; set `edgeWidth = 0.0`
-- (or match `edgeColor` to `color`) for a dense cloud with no visible edge.
-- `size` is every marker's area in points squared (24 when unset) when
-- `sizeBy` does not give one per point. A numeric `colorBy` draws a colour
-- bar beside the panel unless `colorbar = Some False`, labelled `colorLabel`
-- (the `colorBy` column's name when unset).
let ScatterArtist =
      { id : Core.Id
      , x : Text
      , y : Text
      , label : Optional Core.DisplayText
      , colorBy : Optional Text
      , sizeBy : Optional Text
      , color : Optional Text
      , marker : Optional Text
      , alpha : Optional Double
      , size : Optional Double
      , colorbar : Optional Bool
      , colorLabel : Optional Core.DisplayText
      , edgeColor : Optional Text
      , edgeWidth : Optional Double
      , visibleWhen : Optional TextControlParameterMatch
      }

-- A large-series (min/max envelope) artist, backed by an entire hdf Source.
-- The envelope is drawn at one min/max bucket per pixel column; `targetBuckets`
-- caps that, for a series that need not be drawn at full resolution.
-- `color` is the envelope's edge lines (and the line itself once zoomed in
-- to raw samples); `fillColor` and `fillAlpha` are the band between the
-- edges, defaulting to the same colour, fully opaque. `missingMarker` applies
-- once the view is zoomed to raw samples; zoomed out, a bucket with some
-- missing samples draws its good ones and there is nothing to mark.
let TimeSeriesArtist =
      { id : Core.Id
      , y : Text
      , label : Optional Core.DisplayText
      , transform : Transforms.Transform
      , targetBuckets : Optional Natural
      , color : Optional Text
      , fillColor : Optional Text
      , fillAlpha : Optional Double
      , missingMarker : Optional MissingMarker
      , visibleWhen : Optional TextControlParameterMatch
      }

-- A stem ("lollipop") artist: a vertical line plus a marker from `baseline`
-- up to each y value, at each x position. Distinguished from ScatterArtist
-- (which also names both axes explicitly via `x`) by the presence of the
-- `baseline` field - unlike the Optional fields above, this one is required
-- (rather than Optional) specifically so it always serializes as a JSON key,
-- since dhall-to-json omits Optional fields entirely when their value is None.
let StemArtist =
      { id : Core.Id
      , x : Text
      , y : Text
      , label : Optional Core.DisplayText
      , baseline : Double
      , color : Optional Text
      , visibleWhen : Optional TextControlParameterMatch
      }

let PlotArtist =
      < line : LineArtist | scatter : ScatterArtist | timeSeries : TimeSeriesArtist | stem : StemArtist >

in  { TextControlParameterMatch = TextControlParameterMatch
    , LineArtist = LineArtist
    , ScatterArtist = ScatterArtist
    , TimeSeriesArtist = TimeSeriesArtist
    , MissingMarker = MissingMarker
    , DrawStyle = DrawStyle
    , StemArtist = StemArtist
    , PlotArtist = PlotArtist
    }
