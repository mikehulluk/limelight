-- Limelight v1 core data-model types.
--
-- Shared vocabulary for describing typed values and how they're indexed -
-- used directly by the DataSources layer (limelight-v1-03-datasets.dhall) and
-- by the Transformation/Presentation layers as well (Array, IndexedArray,
-- IndexedTable - none of which are
-- Dhall types themselves, since they hold actual loaded data rather than
-- schema; see the comments on `Source` in limelight-v1-03-datasets.dhall).

let Core = ./limelight-v1-00-core.dhall

-- `category` is a text-shaped value drawn from a fixed, unordered set of
-- labels (e.g. a sensor ID or region code) - distinct from `text`, which is
-- free-form. The distinction matters for irregularIndexArray coordinates
-- (see below) and for how a viewer offers a legend/color mapping.
let DataType = < text | category | integer | natural | double | boolean | date | datetime | calendarPeriod >

let ArraySchema =
      { name : Text
      , dtype : DataType
      , label : Optional Core.DisplayText
      , unit : Optional Text
      , description : Optional Core.DisplayText
      , nullable : Bool
      }

-- Index describes what row n means for the arrays in a table: NoIndex, a
-- compact regular sequence (regularTime/regularCalendar/regularInt), or a
-- reference to a materialized coordinate array declared alongside it
-- (irregularIndexTime/irregularIndexCalendar/irregularIndexArray, which name
-- that array the same way a `table['column']` binding does - by its
-- ArraySchema.name within the same source/table).
--
-- This is deliberately the same `Index` used at every layer - not a
-- separate DataSources-only vocabulary duplicating the same idea, the way
-- the old `Basis` family did. A Source can declare it directly because, for
-- every current Source variant, the index shape is always author-declared
-- in the manifest, never inferred at load time, which makes the
-- TransformationLayer optional, post-load machinery rather than something
-- that constructs an Index.
let StepUnit = < ns | us | ms | s | Gs >

let EpochOffset = { epochOffsetGs : Natural, epochOffsetS : Natural, epochOffsetNs : Natural }

-- Where a time index's zero is. `relative` means the values are elapsed time
-- in the index's StepUnit and are drawn as plain numbers in that unit;
-- `absoluteUtc` means they are offsets from the given UTC instant, and the
-- viewer draws them on a calendar (UTC) axis, in the same coordinates as a
-- UTC AxisLimit. The two are different axes, not different labels: an
-- absolute index belongs on a timeSeries axis, a relative one on a
-- continuous axis with the unit as its label.
let TimeOrigin = < relative | absoluteUtc : EpochOffset >

let Calendar = < prolepticGregorian >

let CalendarUnit = < day | week | month | quarter | year >

let PeriodRenderAnchor = < periodStart | periodMidpoint | periodEnd >

let Index =
      < noIndex
      | regularTime :
          { timeOrigin : TimeOrigin
          , timeStepNom : Natural
          , timeStepDenom : Natural
          , timeStepUnit : StepUnit
          }
      | regularCalendar :
          { calendar : Calendar
          , calendarUnit : CalendarUnit
          , startOrdinal : Natural
          , calendarStep : Natural
          , renderAnchor : PeriodRenderAnchor
          }
      | regularInt : { intOrigin : Integer, intStep : Integer, intUnit : Optional Text }
      | irregularIndexTime :
          { irregularTimeCoordArray : Text
          , irregularTimeOrigin : TimeOrigin
          , irregularTimeUnit : StepUnit
          }
      | irregularIndexCalendar :
          { irregularCalendarCoordArray : Text
          , irregularCalendar : Calendar
          , irregularCalendarUnit : CalendarUnit
          , irregularRenderAnchor : PeriodRenderAnchor
          }
      | irregularIndexArray :
          { irregularArrayCoordArray : Text, irregularArrayUnit : Optional Text }
      >

in  { DataType = DataType
    , ArraySchema = ArraySchema
    , StepUnit = StepUnit
    , EpochOffset = EpochOffset
    , TimeOrigin = TimeOrigin
    , Calendar = Calendar
    , CalendarUnit = CalendarUnit
    , PeriodRenderAnchor = PeriodRenderAnchor
    , Index = Index
    }
