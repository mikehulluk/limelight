"""Five years of daily share prices for eight listed companies central to AI.

Reads the CSV that example11_ai_stock_prices_data/fetch_prices.py saved, so
building the example needs no network. The story is written from the data:
every number in it is computed here.
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from limelight import (  # noqa: E402
    AxisDataType,
    ColumnFormat,
    Index,
    LimelightProject,
    LineArtist,
    Panel,
    SourceProvenance,
    TableViewSpec,
    array,
)

DATA = ROOT / "examples" / "example11_ai_stock_prices_data"
PRICES_CSV = DATA / "ai_stock_prices.csv"
SOURCE_JSON = DATA / "source.json"
OUTPUT = ROOT / "_build" / "examples" / "example11-ai-stock-prices.limelight"

CHIPS = ["NVDA", "AMD", "AVGO", "TSM"]
PLATFORMS = ["MSFT", "GOOGL", "AMZN", "META"]


@dataclass(frozen=True)
class Prices:
    days: list[date]
    closes: dict[str, list[float]]
    companies: dict[str, str]
    source: dict


def read_prices() -> Prices:
    source = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    companies = source["companies"]
    with PRICES_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return Prices(
        days=[date.fromisoformat(row["date"]) for row in rows],
        closes={ticker: [float(row[ticker]) for row in rows] for ticker in companies},
        companies=companies,
        source=source,
    )


def label(prices: Prices, ticker: str) -> str:
    return f"{prices.companies[ticker]} ({ticker})"


def build_project(prices: Prices) -> LimelightProject:
    first, last = prices.days[0], prices.days[-1]
    project = LimelightProject(
        title="Share prices of major AI companies",
        subtitle=f"Daily closes, {first:%d %B %Y} to {last:%d %B %Y}",
        description=(
            "Five years of daily closing prices for eight listed companies central to AI: "
            "chip companies and the large platform companies. Data from Yahoo Finance."
        ),
        authors=["Limelight examples"],
        document_version="0.1",
    )
    provenance = SourceProvenance(
        origin=f"Yahoo Finance: {prices.source['field']}",
        release_date=prices.source["fetched"][:10],
        url="https://query1.finance.yahoo.com/v8/finance/chart/",
    )

    calendar_days = [day.toordinal() for day in prices.days]
    common = {
        "date": array(prices.days, dtype="date", label="Date"),
        "calendar-day": array(calendar_days, dtype="natural", label="CalendarDay"),
    }
    index = Index.irregular_index_array(coordinate_array="calendar-day")

    project.add_csv_dataset(
        id="share-prices",
        title="Daily closing share prices",
        arrays={
            **common,
            **{
                ticker: array(closes, dtype="double", label=label(prices, ticker), unit="USD")
                for ticker, closes in prices.closes.items()
            },
        },
        index=index,
        provenance=provenance,
    )
    project.add_csv_dataset(
        id="indexed-prices",
        title="Closing share prices, first day = 100",
        arrays={
            **common,
            **{
                ticker: array(
                    [round(100.0 * close / closes[0], 4) for close in closes],
                    dtype="double",
                    label=label(prices, ticker),
                    description="The close divided by the first day's close, times 100",
                )
                for ticker, closes in prices.closes.items()
            },
        },
        index=index,
    )

    date_axis = AxisDataType.time_series(label="Date", calendar="CalendarDay", share_group="trading-days")

    def price_panel(title: str, tickers: list[str]) -> Panel:
        return Panel(
            title=title,
            lines=[LineArtist(ticker, label=label(prices, ticker)) for ticker in tickers],
            data="share-prices",
            x="calendar-day",
            x_axis=date_axis,
            y_axis=AxisDataType.continuous(label="Close", unit="USD"),
        )

    project.add_figure(
        id="share-prices",
        title="Daily closing share prices",
        caption="Daily closes in US dollars, adjusted for stock splits. TSMC is its New York-listed ADR.",
        panels=[price_panel("Chip companies", CHIPS), price_panel("Platform companies", PLATFORMS)],
    )
    project.add_figure(
        id="indexed-prices",
        title="Growth since the first day",
        caption="Each close divided by the company's first close, times 100, on a log scale.",
        panels=[
            Panel(
                lines=[LineArtist(ticker, label=label(prices, ticker)) for ticker in CHIPS + PLATFORMS],
                data="indexed-prices",
                x="calendar-day",
                x_axis=date_axis,
                y_axis=AxisDataType.continuous(label="First day = 100", scale="Log"),
            )
        ],
    )

    # One row per company, best performer first.
    summary = []
    for ticker, closes in prices.closes.items():
        high = max(range(len(closes)), key=closes.__getitem__)
        summary.append(
            {
                "company": prices.companies[ticker],
                "ticker": ticker,
                "first": closes[0],
                "last": closes[-1],
                "change": 100.0 * (closes[-1] / closes[0] - 1.0),
                "high": closes[high],
                "high-date": prices.days[high],
            }
        )
    summary.sort(key=lambda row: row["change"], reverse=True)
    project.add_csv_dataset(
        id="summary",
        title="Five-year summary",
        arrays={
            "company": array([row["company"] for row in summary], dtype="text", label="Company"),
            "ticker": array([row["ticker"] for row in summary], dtype="text", label="Ticker"),
            "first": array([row["first"] for row in summary], dtype="double", label=f"Close {first:%d %b %Y}", unit="USD"),
            "last": array([row["last"] for row in summary], dtype="double", label=f"Close {last:%d %b %Y}", unit="USD"),
            "change": array([round(row["change"], 2) for row in summary], dtype="double", label="Change", unit="%"),
            "high": array([row["high"] for row in summary], dtype="double", label="Highest close", unit="USD"),
            "high-date": array([row["high-date"] for row in summary], dtype="date", label="On"),
        },
    )
    money = ",.2f"
    project.add_figure(
        id="summary",
        title="Five-year summary",
        caption="Ordered by the change over the five years.",
        table_views=[
            TableViewSpec(
                id="summary-table",
                data="summary",
                columns=["company", "ticker", "first", "last", "change", "high", "high-date"],
                header_style=["Bold"],
                column_formats=[
                    ColumnFormat(column="first", format=money),
                    ColumnFormat(column="last", format=money),
                    ColumnFormat(column="change", format="+,.0f"),
                    ColumnFormat(column="high", format=money),
                ],
            )
        ],
    )

    best, worst = summary[0], summary[-1]
    story = f"""
# Share prices of major AI companies

Daily closing prices for eight listed companies central to AI, over the five
years from {first:%d %B %Y} to {last:%d %B %Y}: {prices.source['trading_days']:,} trading days.
The chip companies are {", ".join(prices.companies[t] for t in CHIPS)}; the platform
companies are {", ".join(prices.companies[t] for t in PLATFORMS)}.

Prices are in US dollars, adjusted for stock splits but not for dividends. TSMC is
its New York-listed ADR. The data is from Yahoo Finance, fetched on
{prices.source['fetched'][:10]}.

## Prices

{project.story_figure("share-prices", id="view-share-prices")}

## Growth

Prices start at very different levels, so [Figure](#view-indexed-prices) puts every
company on the same footing: each close is divided by that company's first close.
On a log scale, the same height means the same percentage change.

{project.story_figure("indexed-prices", id="view-indexed-prices")}

Over the five years {best['company']} changed most, {best['change']:+,.0f}%, and
{worst['company']} least, {worst['change']:+,.0f}%. [Table](#view-summary) has every company.

## Summary

{project.story_figure("summary", id="view-summary")}
"""
    project.set_story_markdown(story, base_dir=DATA)
    return project


def main() -> None:
    build_project(read_prices()).write_folder(OUTPUT, overwrite=True)
    print(OUTPUT)


if __name__ == "__main__":
    main()
