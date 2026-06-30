"""Intraday + latest-daily-bar features for the current session's movers.

Two things the daily-history context in enrich.py can't give us:

  (A) Real closing strength — where the close sits inside the *latest closed
      daily bar's* range. (close - low) / (high - low). 1.0 = closed at the high,
      ~0.5 = mid, low = faded with a big upper wick. This is the "relative close"
      the spec asks for, and it needs the day's true O/H/L, not an EMA proxy.

  (B) Volume persistence — split the regular session into morning vs afternoon
      using hourly bars and measure how much volume came late. A front-loaded
      gap-and-fade (all volume at the open) is weaker than sustained / building
      afternoon volume. Daily volume is a single number and can't show this.

Both key off the *latest available* bar, not literal today, so the dev
environment (where the last closed session may be yesterday) and the post-close
CI run both behave correctly.

Free-tier IEX feed, batched in one request each. Best-effort: any failure leaves
the feature absent and the MQS component falls back to neutral.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import Adjustment
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY

# US regular trading hours in UTC (covers both EST and EDT loosely: 13:30–20:00
# EDT-summer / 14:30–21:00 EST-winter). We bucket on the hour and treat the
# session as 13:00–21:00 UTC, splitting AM/PM at 17:00 UTC (~late morning ET).
_SESSION_START_UTC = 13
_SESSION_END_UTC = 21
_AM_PM_SPLIT_UTC = 17


def _client() -> StockHistoricalDataClient:
    return StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def latest_daily_bars(tickers: list[str]) -> dict[str, dict]:
    """Return {ticker: {open, high, low, close, volume, date, close_position}}
    for each ticker's most recent closed daily bar."""
    if not tickers:
        return {}
    req = StockBarsRequest(
        symbol_or_symbols=tickers,
        timeframe=TimeFrame.Day,
        start=datetime.now(timezone.utc) - timedelta(days=7),
        feed="iex",
        adjustment=Adjustment.ALL,
    )
    try:
        df = _client().get_stock_bars(req).df
    except Exception:  # noqa: BLE001
        return {}
    if df is None or df.empty:
        return {}
    df = df.reset_index()
    out: dict[str, dict] = {}
    for sym, g in df.groupby("symbol"):
        last = g.sort_values("timestamp").iloc[-1]
        rng = float(last["high"]) - float(last["low"])
        close_pos = (float(last["close"]) - float(last["low"])) / rng if rng > 0 else None
        out[sym] = {
            "open": float(last["open"]),
            "high": float(last["high"]),
            "low": float(last["low"]),
            "close": float(last["close"]),
            "volume": float(last["volume"]),
            "date": pd.Timestamp(last["timestamp"]).tz_convert("UTC").date().isoformat(),
            "close_position": round(close_pos, 3) if close_pos is not None else None,
        }
    return out


def volume_persistence(tickers: list[str]) -> dict[str, dict]:
    """Return {ticker: {am_vol, pm_vol, pm_share, persistence}} for the latest
    session, from hourly bars.

    pm_share = afternoon volume / total session volume (0..1).
    persistence: 'building' (pm >= am), 'balanced', or 'front_loaded' (am >> pm).
    A building/sustained profile is the higher-quality momentum signal.
    """
    if not tickers:
        return {}
    req = StockBarsRequest(
        symbol_or_symbols=tickers,
        timeframe=TimeFrame.Hour,
        start=datetime.now(timezone.utc) - timedelta(days=4),
        feed="iex",
        adjustment=Adjustment.ALL,
    )
    try:
        df = _client().get_stock_bars(req).df
    except Exception:  # noqa: BLE001
        return {}
    if df is None or df.empty:
        return {}
    df = df.reset_index()
    df["ts"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("UTC")
    df["d"] = df["ts"].dt.date
    df["h"] = df["ts"].dt.hour

    out: dict[str, dict] = {}
    for sym, g in df.groupby("symbol"):
        latest_day = g["d"].max()
        day = g[(g["d"] == latest_day)
                & (g["h"] >= _SESSION_START_UTC) & (g["h"] < _SESSION_END_UTC)]
        if day.empty:
            continue
        am = float(day[day["h"] < _AM_PM_SPLIT_UTC]["volume"].sum())
        pm = float(day[day["h"] >= _AM_PM_SPLIT_UTC]["volume"].sum())
        total = am + pm
        if total <= 0:
            continue
        pm_share = pm / total
        if pm_share >= 0.5:
            persistence = "building"
        elif pm_share >= 0.33:
            persistence = "balanced"
        else:
            persistence = "front_loaded"
        out[sym] = {
            "am_vol": round(am),
            "pm_vol": round(pm),
            "pm_share": round(pm_share, 3),
            "persistence": persistence,
        }
    return out


def fetch_intraday(tickers: list[str]) -> dict[str, dict]:
    """Combine latest-daily-bar and volume-persistence into one per-ticker dict."""
    daily = latest_daily_bars(tickers)
    persist = volume_persistence(tickers)
    out: dict[str, dict] = {}
    for t in tickers:
        out[t] = {"latest_bar": daily.get(t), "vol_profile": persist.get(t)}
    return out


if __name__ == "__main__":
    import json
    import sys

    syms = sys.argv[1:] or ["OUST", "IRDM"]
    print(json.dumps(fetch_intraday(syms), indent=2, default=str))
