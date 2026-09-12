-- Limelight v1 shared figure types.
--
-- These are small aliases and layout records shared by figure specs, forms,
-- presets, and views.

let Core = ./limelight-v1-00-core.dhall

-- Matplotlib `Figure.add_axes` uses `(left, bottom, width, height)` in
-- normalized figure coordinates. Limelight uses the same shape for now.
let Frame =
      { left : Double
      , bottom : Double
      , width : Double
      , height : Double
      }

let ControlId    = Core.Id

let FigureSpecId = Core.Id

let FigureViewId = Core.Id

let AxisSpecId   = Core.Id

let AxisId       = AxisSpecId

let AxesSpecId   = Core.Id

let AxisGroupId  = Core.Id

in  { Frame = Frame
    , ControlId = ControlId
    , FigureSpecId = FigureSpecId
    , FigureViewId = FigureViewId
    , AxisSpecId = AxisSpecId
    , AxisId = AxisId
    , AxesSpecId = AxesSpecId
    , AxisGroupId = AxisGroupId
    }
