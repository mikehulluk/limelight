-- Limelight v1 figure axes spec types.
--
-- Axes specs contain located axis bindings, axis data types, and axes actions.

let Core        = ./limelight-v1-00-core.dhall

let Types       = ./limelight-v1-05-figure-00-types.dhall

let PlotArtists = ./limelight-v1-05-figure-12-figurespec-plotartists.dhall

let AxisScale = < Linear | Log >

let AxisDataType = < continuous : { unit : Optional Text, scale : AxisScale } | discrete | timeSeries : { calendar : Optional Text } >

let AxisSpec =
      { id : Types.AxisSpecId
      , label : Optional Core.DisplayText
      , shareGroup : Optional Types.AxisGroupId
      , dataType : AxisDataType
      }

let AxisLimit =
      < AxisLimitXUtcTime : { xStart : Text, xEnd : Text }
      | AxisLimitXFloat : { xLower : Double, xUpper : Double }
      | AxisLimitYUtcTime : { yStart : Text, yEnd : Text }
      | AxisLimitYFloat : { yLower : Double, yUpper : Double }
      >

let PlotPoint = { x : Double, y : Double }

let PlotArrow = { start : PlotPoint, end : PlotPoint }

let AxesDecorator =
      < AxesDecoratorAnnotation :
          { arrow : PlotArrow
          , label : Optional Core.DisplayText
          , color : Optional Text
          , labelOffsetDx : Optional Double
          , labelOffsetDy : Optional Double
          }
      | AxesDecoratorVSpan :
          { xLimit : AxisLimit
          , label : Optional Core.DisplayText
          }
      -- A shaded box in data coordinates, bounded on both axes. Like a VSpan
      -- it decorates the data without changing the axes' limits. `yLimit` is
      -- what tells it apart from a VSpan once dhall-to-json has flattened the
      -- union, so it is required.
      | AxesDecoratorRect :
          { xLimit : AxisLimit
          , yLimit : AxisLimit
          , label : Optional Core.DisplayText
          , color : Optional Text
          , alpha : Optional Double
          }
      >

let AxesAction = < AxesActionAddData : PlotArtists.PlotArtist | AxesActionSetLimits : AxisLimit | AxesActionAddDecorator : AxesDecorator >

-- One panel of a figure. Panels without a `frame` are stacked top to bottom
-- in list order, their margins fitted to their labels automatically, each
-- as tall as its `heightRatio` (1 when absent) says relative to the others.
-- A `frame` places the panel exactly, in normalized figure coordinates, the
-- way matplotlib's `add_axes` does - for panels side by side, or an inset -
-- and leaves its margins to the author.
let AxesSpec =
      { id : Types.AxesSpecId
      , frame : Optional Types.Frame
      , heightRatio : Optional Double
      , title : Optional Core.DisplayText
      , caption : Optional Core.DisplayText
      , xAxis : AxisSpec
      , yAxis : AxisSpec
      , actions : List AxesAction
      }

in  { AxisDataType = AxisDataType
    , AxisScale = AxisScale
    , AxisSpec = AxisSpec
    , AxisLimit = AxisLimit
    , PlotPoint = PlotPoint
    , PlotArrow = PlotArrow
    , AxesDecorator = AxesDecorator
    , AxesAction = AxesAction
    , AxesSpec = AxesSpec
    }
