-- Limelight v1 figure spec types.
--
-- Figure specs combine authored plot/axes specs and form specs into one
-- reusable figure definition.

let Core  = ./limelight-v1-00-core.dhall

let Types = ./limelight-v1-05-figure-00-types.dhall

let Forms = ./limelight-v1-05-figure-30-form.dhall

let Axes  = ./limelight-v1-05-figure-11-figurespec-axes.dhall

let Maps  = ./limelight-v1-05-figure-25-map.dhall

let TableViews = ./limelight-v1-05-figure-26-tableview.dhall

let Assets = ./limelight-v1-06-assets.dhall

-- How big a figure is drawn, in the story and on the page.
--
-- `width` is in millimetres or percent of the text column, as for an image
-- (absent: the whole column). `aspect` is height over width (absent: from
-- the number of stacked panels). A figure narrower than the column is
-- centred in it.
let FigureSize =
      { width : Optional Assets.ImageWidth
      , aspect : Optional Double
      }

let FigureSpec =
      { id : Types.FigureSpecId
      , title : Core.DisplayText
      , caption : Optional Core.DisplayText
      , size : Optional FigureSize
      , axesSpecs : List Axes.AxesSpec
      , mapSpecs : List Maps.MapSpec
      , formSpecs : List Forms.FormSpec
      , tableViewSpecs : List TableViews.TableViewSpec
      }

in  { FigureSize = FigureSize, FigureSpec = FigureSpec }
