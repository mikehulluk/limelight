-- Limelight v1 figure table view spec types.
--
-- A table view is a scrollable, read-only table shown as figure content,
-- backed directly by a source's declared arrays (no plotting involved).

let Core  = ./limelight-v1-00-core.dhall

let Types = ./limelight-v1-05-figure-00-types.dhall

let Alignment = < Left | Center | Right >

let FontStyle = < Bold | Italic >

-- A cell selector for styling. `None` on either field means "all" (a
-- wildcard), e.g. { row = None, column = Some 0 } means "column 0, every
-- row" (like tbl[:, 0]). Row/column indices refer to the displayed data
-- grid (after any column filter/order), 0-indexed; they never refer to the
-- header row, which is styled separately via `headerStyle`.
let CellSelector =
      { row : Optional Natural
      , column : Optional Natural
      }

-- How wide a column is, on screen and on paper: `auto` fits the column to
-- its content (the default), wrapping it only when the table would not fit
-- otherwise; `mm` is a fixed width; `share` takes that share of whatever
-- width the other columns leave, as Typst's `fr` does. The payloads are
-- records with distinct field names so the three stay apart once the union
-- is flattened to JSON.
let ColumnWidth =
      < auto
      | mm : { widthMm : Double }
      | share : { widthShare : Double }
      >

-- `format`, when present, is a Python format-spec mini-language string
-- (e.g. ".2f", ",.0f", "%") applied to the raw value before it is shown.
-- `alignment`, when absent, defaults to right for numeric columns and left
-- otherwise. `width`, when absent, is `auto`.
let ColumnFormat =
      { column : Text
      , alignment : Optional Alignment
      , format : Optional Text
      , width : Optional ColumnWidth
      }

let CellStyleRule =
      { selector : CellSelector
      , styles : List FontStyle
      }

-- `data` is a source id. `columns = None` shows every declared array (plus
-- any synthesized index column) - the same "whole table" behavior as
-- LimelightRuntime.table_preview with no column filter. `Some [...]` restricts
-- and orders the shown columns. `rowLimit` caps how many rows are shown, on
-- screen and on paper, with a note saying how many were left out; `None`
-- leaves the viewer's default (2000), which a table printed in full, such as
-- an appendix, raises to its row count.
let TableViewSpec =
      { id : Core.Id
      , frame : Optional Types.Frame
      , title : Optional Core.DisplayText
      , caption : Optional Core.DisplayText
      , data : Text
      , columns : Optional (List Text)
      , rowLimit : Optional Natural
      , columnFormats : List ColumnFormat
      , headerStyle : List FontStyle
      , cellStyles : List CellStyleRule
      }

in  { TableViewSpec = TableViewSpec
    , Alignment = Alignment
    , FontStyle = FontStyle
    , CellSelector = CellSelector
    , ColumnFormat = ColumnFormat
    , ColumnWidth = ColumnWidth
    , CellStyleRule = CellStyleRule
    }
