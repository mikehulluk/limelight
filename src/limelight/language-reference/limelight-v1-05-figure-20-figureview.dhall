-- Limelight v1 figure view types.
--
-- These describe authored presentation state applied to FigureSpec
-- declarations by stories, popout windows, and interactive exploration.

let Types     = ./limelight-v1-05-figure-00-types.dhall

let AxesSpecs = ./limelight-v1-05-figure-11-figurespec-axes.dhall

let PlotPoint = AxesSpecs.PlotPoint

-- A story-controlled arrow in plot data coordinates.
let PlotArrow = AxesSpecs.PlotArrow

let FigureViewAction = { ref : Types.AxesSpecId, action : AxesSpecs.AxesAction }

-- A FigureView is a story-facing rendered instance of a FigureSpec with
-- concrete axes actions applied on top of the underlying FigureSpec.
-- It carries no number: "Figure 3." and "Table 1." are worked out from the
-- story, in the order a reader meets each view, by whoever reads it.
let FigureView =
      { id : Types.FigureViewId
      , ref : Types.FigureSpecId
      , actions : List FigureViewAction
      }

in  { PlotPoint = PlotPoint
    , PlotArrow = PlotArrow
    , FigureViewAction = FigureViewAction
    , FigureView = FigureView
    }
