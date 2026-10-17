"""Fetch five years of daily closing prices for the example's companies.

Run by hand to refresh the data; the example itself only reads the CSV this
writes, so building it needs no network. Prices come from Yahoo Finance's
chart endpoint: the daily close in US dollars, adjusted for splits (so
NVIDIA's 2024 ten-for-one split does not show as a 90% fall) but not for
dividends. Only days on which every company traded are kept.

    python examples/example11_ai_stock_prices_data/fetch_prices.py
"""

from __future__ import annotations

import csv
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRICES_CSV = HERE / "ai_stock_prices.csv"
SOURCE_JSON = HERE / "source.json"

# Ticker -> company. TSMC trades in New York as an ADR, so every price is in
# US dollars on the same exchange calendar.
COMPANIES = {
    "NVDA": "NVIDIA",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "META": "Meta",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    "TSM": "TSMC",
}

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5y&interval=1d"


def fetch_closes(ticker: str) -> dict[str, float]:
    request = urllib.request.Request(URL.format(ticker=ticker), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)["chart"]["result"][0]
    if result["meta"]["currency"] != "USD":
        raise ValueError(f"{ticker} is priced in {result['meta']['currency']}, not USD")
    closes = result["indicators"]["quote"][0]["close"]
    return {
        datetime.fromtimestamp(stamp, timezone.utc).date().isoformat(): round(close, 4)
        for stamp, close in zip(result["timestamp"], closes)
        if close is not None
    }


def main() -> None:
    closes = {ticker: fetch_closes(ticker) for ticker in COMPANIES}
    dates = sorted(set.intersection(*(set(by_date) for by_date in closes.values())))

    with PRICES_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", *COMPANIES])
        for day in dates:
            writer.writerow([day, *(closes[ticker][day] for ticker in COMPANIES)])

    SOURCE_JSON.write_text(
        json.dumps(
            {
                "source": "Yahoo Finance chart API (query1.finance.yahoo.com/v8/finance/chart), range=5y, interval=1d",
                "field": "close: daily close in USD, split-adjusted, not dividend-adjusted",
                "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "companies": COMPANIES,
                "first_date": dates[0],
                "last_date": dates[-1],
                "trading_days": len(dates),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(dates)} trading days, {dates[0]} to {dates[-1]}, to {PRICES_CSV}")


if __name__ == "__main__":
    main()
