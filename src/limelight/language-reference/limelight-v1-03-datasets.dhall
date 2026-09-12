-- Limelight v1 dataset types.
--
-- Sources describe bundled data available to Limelight.

let Core = ./limelight-v1-00-core.dhall

let CoreData = ./limelight-v1-02-coredata.dhall

let ArraySchema = CoreData.ArraySchema

let Index = CoreData.Index

-- First eight lowercase hexadecimal characters of the package file's SHA-256
-- digest. This is a compact cache invalidation hint, not a security boundary.
let SourceFileFingerprint =
      { path : Text
      , sha256Prefix8 : Text
      }

-- Where a bundled source's data came from. Every field is optional since
-- provenance is often not known (e.g. synthetic example data).
let SourceProvenance =
      { origin : Optional Text
      , releaseDate : Optional Text
      , url : Optional Text
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
      , provenance : Optional SourceProvenance
      , signatures : List Core.DocumentSignature
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
      , largeSeriesChunkSize : Natural
      , provenance : Optional SourceProvenance
      , signatures : List Core.DocumentSignature
      }

-- Loading either Source variant produces one IndexedTable: the arrays
-- declared in `schema` (IndexedTableFromCsv) or `yArrays`
-- (IndexedTableFromHdf5), bound to the `index` each already declares. This is
-- a documentation contract, not an executable one; Dhall has no notion of
-- "load" or of IndexedTable as a type, since it holds actual data rather
-- than schema.
let Source = < csv : IndexedTableFromCsv | hdf : IndexedTableFromHdf5 >

in  { SourceFileFingerprint = SourceFileFingerprint
    , SourceProvenance = SourceProvenance
    , IndexedTableFromCsv = IndexedTableFromCsv
    , IndexedTableFromHdf5 = IndexedTableFromHdf5
    , Source = Source
    }
