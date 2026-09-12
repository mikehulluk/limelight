-- Limelight v1 figure form spec types.
--
-- Form specs expose controls that edit project-level control parameters.

let Core  = ./limelight-v1-00-core.dhall

let Types = ./limelight-v1-05-figure-00-types.dhall

let DropdownCtrlSpec = { id : Types.ControlId, controlParameter : Core.ControlParameterId, label : Optional Core.DisplayText }

let SliderCtrlSpec = { id : Types.ControlId, controlParameter : Core.ControlParameterId, label : Optional Core.DisplayText }

let FormCtrlSpec = < dropdown : DropdownCtrlSpec | slider : SliderCtrlSpec >

let FormSpec =
      { id : Core.Id
      , frame : Types.Frame
      , title : Optional Core.DisplayText
      , caption : Optional Core.DisplayText
      , controls : List FormCtrlSpec
      }

in  { DropdownCtrlSpec = DropdownCtrlSpec
    , SliderCtrlSpec = SliderCtrlSpec
    , FormCtrlSpec = FormCtrlSpec
    , FormSpec = FormSpec
    }
