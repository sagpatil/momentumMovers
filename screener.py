"""Reconstruct the Finviz screener on historical OHLCV data.

For every (symbol, date) we compute the filter columns and emit a long DataFrame
of qualifying signals. Each row represents one "Day 1" — a day the stock would
have appeared in the Finviz screener at close.

NOTE on market cap: Alpaca free tier does not give historical shares outstanding.
We approximate market cap as `close * current_shares_out`. For backtest purposes
we instead substitute a proxy: `close * 63-day-avg-volume * 50` ~ rough dollar
volume floor. Better yet, we skip the cap filter entirely and rely on price +
volume filters, which already screen out tiny names. Set USE_CAP_PROXY=False to
disable.

The rest of the Finviz filters map cleanly:
  - sh_avgvol_o1000      -> avg_vol_63d > 1M
  - sh_curvol_o1000      -> volume     > 1M
  - sh_price_o7          -> close      > 7
  - sh_relvol_o1.5       -> volume / avg_vol_50d > 1.5
  - ta_averagetruerange_o1 -> atr14    > 1
  - ta_change_u3         -> daily pct change > 3%
  - ta_sma200_sb50       -> close >= 1.5 * sma200
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import SCREENER

USE_CAP_PROXY = False  # set True to use $-volume proxy for market cap


def _atr14(bars: pd.DataFrame) -> pd.Series:
    """Wilder ATR(14), vectorized across all symbols."""
    g = bars.groupby("symbol", group_keys=False)
    prev_close = g["close"].shift(1)
    tr = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.groupby(bars["symbol"]).transform(
        lambda s: s.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    )


def compute_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """Add indicator columns required for the screener, per symbol."""
    bars = bars.sort_values(["symbol", "date"]).copy()
    g = bars.groupby("symbol", group_keys=False)

    bars["prev_close"] = g["close"].shift(1)
    bars["change_pct"] = (bars["close"] / bars["prev_close"] - 1.0) * 100.0
    bars["avg_vol_63d"] = g["volume"].transform(lambda s: s.rolling(63, min_periods=20).mean())
    bars["avg_vol_50d"] = g["volume"].transform(lambda s: s.rolling(50, min_periods=20).mean())
    bars["rel_volume"] = bars["volume"] / bars["avg_vol_50d"]
    bars["sma200"] = g["close"].transform(lambda s: s.rolling(200, min_periods=120).mean())
    bars["ema20"] = g["close"].transform(
        lambda s: s.ewm(span=20, adjust=False, min_periods=20).mean()
    )
    bars["ema10"] = g["close"].transform(
        lambda s: s.ewm(span=10, adjust=False, min_periods=10).mean()
    )
    bars["atr14"] = _atr14(bars)

    # Day-1 quality features used later for ranking / continuation analysis.
    daily_range = (bars["high"] - bars["low"]).replace(0, np.nan)
    bars["close_position"] = (bars["close"] - bars["low"]) / daily_range  # 1.0 = closed at HOD
    return bars


def apply_screener(bars: pd.DataFrame) -> pd.DataFrame:
    """Return rows where the Finviz screener would have fired at close."""
    f = SCREENER
    cond = (
        (bars["close"] > f.min_price)
        & (bars["volume"] > f.min_curr_volume)
        & (bars["avg_vol_63d"] > f.min_avg_volume)
        & (bars["rel_volume"] > f.min_rel_volume)
        & (bars["atr14"] > f.min_atr)
        & (bars["change_pct"] > f.min_change_pct)
        & (bars["close"] >= bars["sma200"] * (1 + f.sma200_above_pct / 100.0))
        & (bars["close_position"] >= f.min_close_position)
    )
    if USE_CAP_PROXY:
        dollar_vol = bars["close"] * bars["avg_vol_63d"]
        cond &= dollar_vol > 50_000_000  # crude floor

    qualifying = bars[cond].copy()
    qualifying = qualifying.dropna(subset=["sma200", "atr14", "avg_vol_50d", "close_position"])
    return qualifying[
        [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "change_pct",
            "rel_volume",
            "atr14",
            "close_position",
            "sma200",
        ]
    ].reset_index(drop=True)


if __name__ == "__main__":
    from datetime import date

    from fetch_bars import load_all_bars

    df = load_all_bars(date(2024, 1, 1), date(2024, 6, 1), ["NVDA", "SMCI", "TSLA", "AAPL"])
    df = compute_indicators(df)
    hits = apply_screener(df)
    print(hits.head(20).to_string())
    print(f"\nQualifying signals: {len(hits)}")
