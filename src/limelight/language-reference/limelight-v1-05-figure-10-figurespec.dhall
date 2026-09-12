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

let FigureSpec =
      { id : Types.FigureSpecId
      , title : Core.DisplayText
      , caption : Optional Core.DisplayText
      , axesSpecs : List Axes.AxesSpec
      , mapSpecs : List Maps.MapSpec
      , formSpecs : List Forms.FormSpec
      , tableViewSpecs : List TableViews.TableViewSpec
      }

in  { FigureSpec = FigureSpec }
