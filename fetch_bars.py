"""Fetch daily OHLCV bars from Alpaca and cache to parquet.

Alpaca's `get_stock_bars` accepts up to ~200 symbols per request. We batch and
write one parquet per batch keyed by hash; on second run, only missing batches
are fetched. Final output is a single tidy DataFrame: (symbol, date, o,h,l,c,v).

Free-tier rate limit is 200 req/min; we throttle and retry on 429s.
"""
from __future__ import annotations

import hashlib
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from alpaca.data.enums import Adjustment
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from tqdm import tqdm

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, DATA_DIR

BARS_DIR = DATA_DIR / "bars"
BARS_DIR.mkdir(exist_ok=True)

BATCH_SIZE = 200
SLEEP_BETWEEN_BATCHES = 0.35  # ~170 req/min, safely under 200


def _batch_key(symbols: list[str], start: date, end: date) -> str:
    h = hashlib.sha1(",".join(sorted(symbols)).encode()).hexdigest()[:10]
    return f"{start.isoformat()}_{end.isoformat()}_{h}.parquet"


def fetch_bars(symbols: list[str], start: date, end: date) -> pd.DataFrame:
    client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    batches = [symbols[i : i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]

    frames: list[pd.DataFrame] = []
    for batch in tqdm(batches, desc="fetching bars"):
        cache_path = BARS_DIR / _batch_key(batch, start, end)
        if cache_path.exists():
            frames.append(pd.read_parquet(cache_path))
            continue

        df = _fetch_batch_with_retry(client, batch, start, end)
        if not df.empty:
            df.to_parquet(cache_path, index=False)
        frames.append(df)
        time.sleep(SLEEP_BETWEEN_BATCHES)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out


def _fetch_batch_with_retry(
    client: StockHistoricalDataClient,
    symbols: list[str],
    start: date,
    end: date,
    max_attempts: int = 4,
) -> pd.DataFrame:
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
        end=datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc),
        feed="iex",  # free tier
        adjustment=Adjustment.ALL,  # split + dividend adjusted (required for SMA200 sanity)
    )
    for attempt in range(max_attempts):
        try:
            resp = client.get_stock_bars(req)
            df = resp.df.reset_index() if resp.df is not None else pd.DataFrame()
            if df.empty:
                return df
            df = df.rename(columns={"timestamp": "date"})
            df["date"] = pd.to_datetime(df["date"]).dt.tz_convert("UTC").dt.date
            return df[["symbol", "date", "open", "high", "low", "close", "volume"]]
        except Exception as e:
            wait = 2 ** attempt
            tqdm.write(f"  retry {attempt + 1}/{max_attempts} after {wait}s: {e}")
            time.sleep(wait)
    tqdm.write(f"  batch failed permanently: {symbols[:3]}...")
    return pd.DataFrame()


def load_all_bars(start: date, end: date, symbols: list[str] | None = None) -> pd.DataFrame:
    """Convenience: pull cached batches OR fetch missing ones, return one frame."""
    if symbols is None:
        from universe import fetch_universe

        symbols = fetch_universe()["symbol"].tolist()
    return fetch_bars(symbols, start, end)


if __name__ == "__main__":
    df = load_all_bars(date(2024, 1, 1), date(2024, 2, 1), ["AAPL", "MSFT", "NVDA"])
    print(df.head())
    print(f"rows: {len(df):,}")
