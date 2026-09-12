-- Limelight v1 dataset types.
--
-- Sources describe bundled data available to Limelight.

let Core = ./limelight-v1-00-core.dhall

let DataType = < text | integer | natural | double | boolean | date | datetime | calendarPeriod >

let ArraySchema =
      { name : Text
      , kind : DataType
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
-- ArraySchema.name within this same source).
--
-- Unlike the old `Basis` family this replaced, this is not a separate,
-- DataSources-only vocabulary duplicating the same idea - it's the literal
-- `Index` also used by the Transformation/Presentation layers. A Source can
-- declare it directly because, for both IndexedTableFromCsv and
-- IndexedTableFromHdf5, the index shape is always author-declared in the
-- manifest, never inferred at load time - there's nothing left for a
-- TransformationLayer step to decide here; its role is optional post-load
-- IndexedArray/IndexedTable transformations, not index construction.
let StepUnit = < ns | us | s | Gs >

let EpochOffset = { epochOffsetGs : Natural, epochOffsetS : Natural, epochOffsetNs : Natural }

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

      -- Indices, from the column
      | irregularIndexArray :
          { irregularArrayCoordArray : Text, irregularArrayUnit : Optional Text }

      -- Specialisations:
      | irregularIndexTime :
          { irregularTimeCoordArray : Text, irregularTimeOrigin : Optional TimeOrigin }
      | irregularIndexCalendar :
          { irregularCalendarCoordArray : Text
          , irregularCalendar : Calendar
          , irregularCalendarUnit : CalendarUnit
          , irregularRenderAnchor : PeriodRenderAnchor
          }

      >

-- First eight lowercase hexadecimal characters of the package file's SHA-256
-- digest. This is a compact cache invalidation hint, not a security boundary.
let SourceFileFingerprint =
      { path : Text
      , sha256Prefix8 : Text
      }

let IndexedTableFromCsv =
      { id : Core.Id
      , title : Optional Core.DisplayText
      , path : Text
      , fileFingerprints : List SourceFileFingerprint
      -- The declared shape of the table before it is loaded: which named
      -- arrays it has, and the Index they share. Row count is not declared -
      -- it's a fact about loaded data, cheap to learn on first load, and the
      -- viewer can cache it from there rather than the schema carrying it up
      -- front.
      , schema : Optional (List ArraySchema)
      , header : Bool
      , index : Index
      }

-- An HDF5-backed source for very large (e.g. billion-point) time series. The
-- `path` file is opened directly by the large-series LOD pyramid cache
-- rather than loaded row-by-row like IndexedTableFromCsv. Each entry in
-- `yArrays` names a value array (`schema.name`, matched against
-- `table['column']` bindings) and the HDF5 dataset backing it (`dataset`).
let IndexedTableFromHdf5 =
      { id : Core.Id
      , title : Optional Core.DisplayText
      , path : Text
      , fileFingerprints : List SourceFileFingerprint
      , yArrays : List { schema : ArraySchema, dataset : Text }
      , index : Index
      , chunkSize : Natural
      }

-- Loading either Source variant produces one IndexedTable: the arrays
-- declared in `schema` (IndexedTableFromCsv) or `yArrays`
-- (IndexedTableFromHdf5), bound to the `index` each already declares. This is
-- a documentation contract, not an executable one; Dhall has no notion of
-- "load" or of IndexedTable as a type, since it holds actual data rather
-- than schema.
let Source = < csv : IndexedTableFromCsv | hdf : IndexedTableFromHdf5 >

in  { DataType = DataType
    , ArraySchema = ArraySchema
    , StepUnit = StepUnit
    , EpochOffset = EpochOffset
    , TimeOrigin = TimeOrigin
    , Calendar = Calendar
    , CalendarUnit = CalendarUnit
    , PeriodRenderAnchor = PeriodRenderAnchor
    , Index = Index
    , SourceFileFingerprint = SourceFileFingerprint
    , IndexedTableFromCsv = IndexedTableFromCsv
    , IndexedTableFromHdf5 = IndexedTableFromHdf5
    , Source = Source
    }
