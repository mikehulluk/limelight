from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from limelight.writer import LANGUAGE_REFERENCE_DIR


ROOT = Path(__file__).resolve().parents[1]

MEASUREMENTS_CSV = """day,voltage,temperature
2026-08-01,4.18,21.4
2026-08-01,4.16,21.7
2026-08-02,4.10,22.0
2026-08-02,4.08,22.2
2026-08-03,4.01,22.5
2026-08-03,3.98,22.8
2026-08-04,3.92,23.1
"""

DAILY_SUMMARY_CSV = """day,mean-voltage,mean-temperature
2026-08-01,4.17,21.55
2026-08-02,4.09,22.10
2026-08-03,3.995,22.65
2026-08-04,3.92,23.10
"""

STORY_MARKDOWN = """# Overview

Measurements were collected once per minute over seven days.

@figure(figure-view-0)

# Daily data

The table below is the bundled daily aggregate used by the figure.
"""

PROJECT_DHALL_TEMPLATE = """-- Example Limelight v1 project manifest.
--
-- This is the `project.dhall` file for a folder-form Limelight package.

let Limelight = ./language-reference/limelight-v1.dhall

let measurementSchema =
      [ { name = "day"
        , dtype = Limelight.DataType.date
        , label = Some "Day"
        , unit = None Text
        , description = Some "Collection day"
        , nullable = False
        }
      , { name = "voltage"
        , dtype = Limelight.DataType.double
        , label = Some "Voltage"
        , unit = Some "V"
        , description = Some "Measured battery voltage"
        , nullable = False
        }
      , { name = "temperature"
        , dtype = Limelight.DataType.double
        , label = Some "Temperature"
        , unit = Some "C"
        , description = Some "Ambient temperature near the logger"
        , nullable = False
        }
      ]

let dailySummarySchema =
      [ { name = "day"
        , dtype = Limelight.DataType.date
        , label = Some "Day"
        , unit = None Text
        , description = Some "Collection day"
        , nullable = False
        }
      , { name = "mean-voltage"
        , dtype = Limelight.DataType.double
        , label = Some "Mean voltage"
        , unit = Some "V"
        , description = Some "Daily mean voltage"
        , nullable = False
        }
      , { name = "mean-temperature"
        , dtype = Limelight.DataType.double
        , label = Some "Mean temperature"
        , unit = Some "C"
        , description = Some "Daily mean temperature"
        , nullable = False
        }
      ]

in    { limelightVersion = "1.0"
      , project =
        { title = "Battery discharge check"
        , subtitle = Some "Seven day Arduino logger sample"
        , description =
            Some
              "A small Limelight project showing raw voltage readings, a daily aggregate, and a guided story."
        , authors = [ "Limelight examples" ]
        , created = Some "2026-08-05"
        , updated = None Text
        , documentVersion = Some "0.1"
        }
      , controlParameters =
        [ { id = "sensor-site"
          , label = "Sensor site"
          , dataType =
              Limelight.ControlParameterDataType.discrete
                { options =
                  [ { value = "workbench", label = "Workbench" }
                  , { value = "window-sill", label = "Window sill" }
                  ]
                , default = Some "workbench"
                }
          }
        ]
      , sources =
        [ Limelight.Source.csv
            { id = "raw-measurements"
            , title = Some "Raw measurements"
            , path = "data/measurements.csv"
            , fileFingerprints =
              [ { path = "data/measurements.csv"
                , sha256Prefix8 = "__MEASUREMENTS_SHA256_PREFIX8__"
                }
              ]
            , schema = Some measurementSchema
            , header = True
            , index = Limelight.Index.noIndex
            , provenance =
                Some
                  { origin = Some "Synthetic sensor readings generated for this example."
                  , releaseDate = Some "2026-08-05"
                  , url = None Text
                  }
            , signatures = [] : List Limelight.DocumentSignature
            }
        , Limelight.Source.csv
            { id = "daily-summary"
            , title = Some "Daily summary"
            , path = "data/daily-summary.csv"
            , fileFingerprints =
              [ { path = "data/daily-summary.csv"
                , sha256Prefix8 = "__DAILY_SUMMARY_SHA256_PREFIX8__"
                }
              ]
            , schema = Some dailySummarySchema
            , header = True
              -- One row per day from 2026-08-01, which is what the figure's
              -- calendar axis draws against. A day's ordinal is one less than
              -- Python's date.toordinal().
            , index =
                Limelight.Index.regularCalendar
                  { calendar = Limelight.Calendar.prolepticGregorian
                  , calendarUnit = Limelight.CalendarUnit.day
                  , startOrdinal = 739828
                  , calendarStep = 1
                  , renderAnchor = Limelight.PeriodRenderAnchor.periodStart
                  }
            , provenance = None Limelight.SourceProvenance
            , signatures = [] : List Limelight.DocumentSignature
            }
        ]
      , figures =
        [ { id = "voltage-over-time"
          , title = "Mean voltage over time"
          , caption =
              Some
                "Daily averaging removes individual sensor jitter while preserving the discharge trend."
          -- Drawn at two thirds of the column, a little squarer than the
          -- default; the size a figure is drawn at is the figure's to say.
          , size =
              Some
                { width = Some { value = 66.0, unit = Limelight.ImageWidthUnit.percent }
                , aspect = Some 0.7
                }
          , axesSpecs =
            [ { id = "voltage-plot"
              -- No frame: the panel is laid out by the renderer.
              , frame = None Limelight.Frame
              , heightRatio = None Double
              , title = None Text
              , caption = None Limelight.DisplayText
              , xAxis =
                { id = "day-axis"
                , label = Some "Day"
                , shareGroup = Some "time"
                , dataType = Limelight.AxisDataType.timeSeries
                    { calendar = Some "CalendarDay" }
                }
              , yAxis =
                { id = "mean-voltage-axis"
                , label = Some "Mean voltage"
                , shareGroup = None Limelight.AxisGroupId
                , dataType = Limelight.AxisDataType.continuous
                    { unit = Some "V", scale = Limelight.AxisScale.Linear }
                }
              , actions =
                [ Limelight.AxesAction.AxesActionAddData
                    ( Limelight.PlotArtist.line
                        { id = "mean-voltage-line"
                        , y = "daily-summary['mean-voltage']"
                        , label = Some "Mean voltage"
                        , alpha = None Double
                        , xOverride = None Text
                        , color = None Text
                        , linestyle = None Text
                        , marker = None Text
                        , visibleWhen = None Limelight.TextControlParameterMatch
                        }
                    )
                ]
              }
            ]
          , mapSpecs =
            [] : List Limelight.MapSpec
          , formSpecs =
            [ { id = "voltage-controls"
              , frame = None Limelight.Frame
              , title = None Text
              , caption = None Limelight.DisplayText
              , controls =
                [ Limelight.FormCtrlSpec.dropdown
                    { id = "sensor-site-control"
                    , controlParameter = "sensor-site"
                    , label = None Text
                    }
                ]
              }
            ]
          , tableViewSpecs =
            [] : List Limelight.TableViewSpec
          }
        ]
      , figureViews =
        [ { id = "figure-view-0"
          , ref = "voltage-over-time"
          , storyNumber = Some 1
          , index = None Integer
          , actions =
            [ { ref = "voltage-plot"
              , action =
                  Limelight.AxesAction.AxesActionSetLimits
                    ( Limelight.AxisLimit.AxisLimitXUtcTime
                        { xStart = "2026-08-01"
                        , xEnd = "2026-08-04"
                        }
                    )
              }
            , { ref = "voltage-plot"
              , action =
                  Limelight.AxesAction.AxesActionAddDecorator
                    ( Limelight.AxesDecorator.AxesDecoratorVSpan
                        { xLimit =
                            Limelight.AxisLimit.AxisLimitXUtcTime
                              { xStart = "2026-08-01"
                              , xEnd = "2026-08-04"
                              }
                        , label = Some "Collection period"
                        }
                    )
              }
            ]
          }
        ]
      , assets = [] : List Limelight.ImageAsset
      , story =
        { documentPath = "story/index.md"
        , format = Limelight.StoryFormat.markdown
        , page =
          { width = Limelight.PageWidth.millimetres 210.0
          , height = Limelight.PageHeight.millimetres 297.0
          , marginLR = 10.0
          , marginTB = 15.0
          }
        , spacing =
          { blockGap = 3.0
          , figureGap = 4.5
          , headingGapBefore = 5.0
          , headingGapAfter = 2.0
          }
        , signatures = [] : List Limelight.DocumentSignature
        }
      }
    : Limelight.Manifest
"""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_package(output: Path) -> Path:
    if output.exists():
        if output.is_file():
            output.unlink()
        elif output.suffix == ".limelight":
            shutil.rmtree(output)
        else:
            raise ValueError(f"Refusing to overwrite non-.limelight folder {output}")

    output.mkdir(parents=True, exist_ok=True)
    write_text(output / "data" / "measurements.csv", MEASUREMENTS_CSV)
    write_text(output / "data" / "daily-summary.csv", DAILY_SUMMARY_CSV)
    write_text(output / "story" / "index.md", STORY_MARKDOWN)

    shutil.copytree(LANGUAGE_REFERENCE_DIR, output / "language-reference")

    measurements_fingerprint = hashlib.sha256(MEASUREMENTS_CSV.encode("utf-8")).hexdigest()[:8]
    daily_summary_fingerprint = hashlib.sha256(DAILY_SUMMARY_CSV.encode("utf-8")).hexdigest()[:8]
    manifest = (
        PROJECT_DHALL_TEMPLATE
        .replace("__MEASUREMENTS_SHA256_PREFIX8__", measurements_fingerprint)
        .replace("__DAILY_SUMMARY_SHA256_PREFIX8__", daily_summary_fingerprint)
    )
    write_text(output / "project.dhall", manifest)
    return output


def main() -> None:
    output = ROOT / "_build" / "examples" / "example00-simple-physicalmeasurements.limelight"
    write_package(output)
    print(output)


if __name__ == "__main__":
    main()
