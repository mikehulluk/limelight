-- Limelight v1 shared figure types.
--
-- These are small aliases and layout records shared by figure specs, forms,
-- presets, and views.

let Core = ./limelight-v1-00-core.dhall

-- Where a region sits in its figure: `(left, bottom, width, height)` in
-- normalized figure coordinates, as matplotlib's `Figure.add_axes` takes
-- them. An AxesSpec with a frame is placed exactly there; the other specs
-- carry the field, unread, for the day their layout is authored too.
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
