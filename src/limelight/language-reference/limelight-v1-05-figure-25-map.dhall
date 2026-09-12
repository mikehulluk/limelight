-- Limelight v1 figure map spec types.
--
-- Map specs are placeholders for map-oriented figure regions. The runtime does
-- not render them yet; this shape gives us a stable place to develop the map
-- model alongside axes and forms.

let Core  = ./limelight-v1-00-core.dhall

let Types = ./limelight-v1-05-figure-00-types.dhall

let MapLimit =
      { longitudeLower : Double
      , longitudeUpper : Double
      , latitudeLower : Double
      , latitudeUpper : Double
      }

-- longitude/latitude/colorBy/sizeBy are `table_id['column_name']` refs - see
-- limelight-v1-05-figure-12-figurespec-plotartists.dhall for the grammar.
let MapDatasetScatter =
      { id : Core.Id
      , longitude : Text
      , latitude : Text
      , label : Optional Core.DisplayText
      , colorBy : Optional Text
      , sizeBy : Optional Text
      }

let MapGeoJsonLayer =
      { id : Core.Id
      , path : Text
      , label : Optional Core.DisplayText
      , fill : Optional Text
      , stroke : Optional Text
      , alpha : Optional Double
      }

let MapAction =
      < MapActionSetLimits : MapLimit
      | MapActionAddDatasetScatter : MapDatasetScatter
      | MapActionAddGeoJsonLayer : MapGeoJsonLayer
      >

let MapSpec =
      { id : Core.Id
      , frame : Types.Frame
      , title : Optional Core.DisplayText
      , caption : Optional Core.DisplayText
      , actions : List MapAction
      }

in  { MapSpec = MapSpec
    , MapLimit = MapLimit
    , MapDatasetScatter = MapDatasetScatter
    , MapGeoJsonLayer = MapGeoJsonLayer
    , MapAction = MapAction
    }
