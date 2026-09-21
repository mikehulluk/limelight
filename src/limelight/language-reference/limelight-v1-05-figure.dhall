-- Limelight v1 figure language reference.
--
-- This is the stable import point for the figure-family modules.

let Types        = ./limelight-v1-05-figure-00-types.dhall

let FigureSpecs  = ./limelight-v1-05-figure-10-figurespec.dhall

let AxesSpecs    = ./limelight-v1-05-figure-11-figurespec-axes.dhall

let PlotArtists = ./limelight-v1-05-figure-12-figurespec-plotartists.dhall

let FigureViews = ./limelight-v1-05-figure-20-figureview.dhall

let Maps        = ./limelight-v1-05-figure-25-map.dhall

let TableViews  = ./limelight-v1-05-figure-26-tableview.dhall

let Forms        = ./limelight-v1-05-figure-30-form.dhall

in  { Frame = Types.Frame
    , ControlId = Types.ControlId
    , FigureSpecId = Types.FigureSpecId
    , FigureViewId = Types.FigureViewId
    , AxisSpecId = Types.AxisSpecId
    , AxisId = Types.AxisId
    , AxesSpecId = Types.AxesSpecId
    , AxisGroupId = Types.AxisGroupId

    , AxisDataType = AxesSpecs.AxisDataType
    , AxisScale = AxesSpecs.AxisScale
    , AxisSpec = AxesSpecs.AxisSpec
    , AxisLimit = AxesSpecs.AxisLimit
    , PlotPoint = AxesSpecs.PlotPoint
    , PlotArrow = AxesSpecs.PlotArrow
    , AxesDecorator = AxesSpecs.AxesDecorator
    , AxesSpec = AxesSpecs.AxesSpec

    , TextControlParameterMatch = PlotArtists.TextControlParameterMatch
    , LineArtist = PlotArtists.LineArtist
    , ScatterArtist = PlotArtists.ScatterArtist
    , TimeSeriesArtist = PlotArtists.TimeSeriesArtist
    , StemArtist = PlotArtists.StemArtist
    , MissingMarker = PlotArtists.MissingMarker
    , PlotArtist = PlotArtists.PlotArtist
    , AxesAction = AxesSpecs.AxesAction

    , DropdownCtrlSpec = Forms.DropdownCtrlSpec
    , SliderCtrlSpec = Forms.SliderCtrlSpec
    , FormCtrlSpec = Forms.FormCtrlSpec
    , FormSpec = Forms.FormSpec

    , FigureSize = FigureSpecs.FigureSize
    , FigureSpec = FigureSpecs.FigureSpec

    , FigureViewAction = FigureViews.FigureViewAction
    , FigureView = FigureViews.FigureView

    , MapLimit = Maps.MapLimit
    , MapDatasetScatter = Maps.MapDatasetScatter
    , MapGeoJsonLayer = Maps.MapGeoJsonLayer
    , MapAction = Maps.MapAction
    , MapSpec = Maps.MapSpec

    , TableViewSpec = TableViews.TableViewSpec
    , Alignment = TableViews.Alignment
    , FontStyle = TableViews.FontStyle
    , CellSelector = TableViews.CellSelector
    , ColumnFormat = TableViews.ColumnFormat
    , CellStyleRule = TableViews.CellStyleRule
    }
