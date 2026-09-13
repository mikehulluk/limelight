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

let LineArtist =
      { id : Core.Id
      , y : Text
      , label : Optional Core.DisplayText
      , alpha : Optional Double
      , xOverride : Optional Text
      , color : Optional Text
      , linestyle : Optional Text
      , marker : Optional Text
      , visibleWhen : Optional TextControlParameterMatch
      }

let ScatterArtist =
      { id : Core.Id
      , x : Text
      , y : Text
      , label : Optional Core.DisplayText
      , colorBy : Optional Text
      , sizeBy : Optional Text
      , color : Optional Text
      , marker : Optional Text
      , visibleWhen : Optional TextControlParameterMatch
      }

-- A large-series (min/max envelope) artist, backed by an entire hdf Source.
-- `color` is the envelope's edge lines (and the line itself once zoomed in
-- to raw samples); `fillColor` and `fillAlpha` are the band between the
-- edges, defaulting to the same colour, fully opaque.
let TimeSeriesArtist =
      { id : Core.Id
      , y : Text
      , label : Optional Core.DisplayText
      , transform : Transforms.Transform
      , targetBuckets : Optional Natural
      , color : Optional Text
      , fillColor : Optional Text
      , fillAlpha : Optional Double
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
    , StemArtist = StemArtist
    , PlotArtist = PlotArtist
    }
