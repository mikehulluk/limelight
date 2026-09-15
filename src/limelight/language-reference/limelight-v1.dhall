-- Limelight v1 public language reference.
--
-- This file is the stable import point. The numbered files split the reference
-- by topic, but example manifests should usually import this file.

let Core       = ./limelight-v1-00-core.dhall

let Document   = ./limelight-v1-01-document.dhall

let CoreData   = ./limelight-v1-02-coredata.dhall

let Datasets   = ./limelight-v1-03-datasets.dhall

let Transforms = ./limelight-v1-04-transform.dhall

let Figure     = ./limelight-v1-05-figure.dhall

let Assets     = ./limelight-v1-06-assets.dhall

-- The top-level project.dhall value.
--
-- Validation happens after Dhall parsing and normalization:
--
-- 1. Check compatible `limelightVersion`.
-- 2. Check ID spelling and uniqueness.
-- 3. Check package paths.
-- 4. Resolve references.
-- 5. Validate schemas and table arrays.
-- 6. Validate figure spec bindings.
-- 7. Validate story document references and the assets they name.
-- 8. Validate shared control parameter declarations and references.
-- 9. Validate source file fingerprints.
let Manifest =
      { limelightVersion : Core.Version
      , project : Document.Project
      , metadata : List Document.Metadata
      , controlParameters : List Core.ControlParameter
      , sources : List Datasets.Source
      , figures : List Figure.FigureSpec
      , figureViews : List Figure.FigureView
      , assets : List Assets.ImageAsset
      , story : Document.Story
      }

-- Each imported module's own export record is re-exported here as-is (no
-- field-name collisions across them) rather than hand-listing every type
-- again - that manual list is exactly what kept going stale whenever a type
-- was added, renamed, or moved between files.
in    Core
   // Document
   // CoreData
   // Datasets
   // Transforms
   // Figure
   // Assets
   // { Manifest = Manifest }
