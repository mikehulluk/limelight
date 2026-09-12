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
-- `storyNumber` is the number shown for this view ("Figure 3."), assigned by
-- the builder from story order and recorded here so the runtime never has to
-- re-derive it. `index` is the superseded field it replaces; readers fall back
-- to it so packages published before `storyNumber` existed keep the numbering
-- they shipped with.
let FigureView =
      { id : Types.FigureViewId
      , ref : Types.FigureSpecId
      , storyNumber : Optional Natural
      , index : Optional Integer
      , actions : List FigureViewAction
      }

in  { PlotPoint = PlotPoint
    , PlotArrow = PlotArrow
    , FigureViewAction = FigureViewAction
    , FigureView = FigureView
    }
