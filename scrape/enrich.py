"""Enrich screener hits with quote-page fundamentals, news, and bar-derived context.

For each ticker passing the screen we gather:
  - quote-page fields (float, short float, ATR, SMA distances, perf) via quote_page
  - recent headlines via finvizfinance ticker_news()
  - a ~80-day daily bar history (Alpaca) to derive the *run context* the MQS needs:
      run_low / run_high / breakout context, retrace_pct (for the pullback floor),
      ema10/ema20, up_streak (consecutive up-days), dist_above_ema10_atr (extension).

The bar-derived block is what lets us distinguish a Day-1 breakout from a flag
pullback from an extended runner — none of which the once-a-day screener row alone
can tell apart.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
from finvizfinance.quote import finvizfinance

from fetch_bars import load_all_bars
from scrape.intraday import fetch_intraday
from scrape.quote_page import fetch_quotes

# How far back to look when reconstructing the current run.
_BAR_LOOKBACK_DAYS = 120
# A "run" is the stretch of recent strength we measure retrace against: walk back
# from today while price stays above this fraction of the trailing SMA20-ish base.
_RUN_PULLBACK_FLOOR_DAYS = 40


def _recent_news(ticker: str, days: int = 5, limit: int = 6) -> list[dict]:
    """Last `days` of headlines (capped at `limit`). Returns [] on any failure."""
    try:
        df = finvizfinance(ticker).ticker_news()
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=days)
    df = df[df["Date"] >= cutoff]
    out = []
    for _, r in df.head(limit).iterrows():
        out.append(
            {
                "date": r["Date"].strftime("%Y-%m-%d %H:%M") if pd.notna(r["Date"]) else None,
                "title": str(r.get("Title", "")).strip(),
                "source": str(r.get("Source", "")).strip(),
                "link": str(r.get("Link", "")).strip(),
            }
        )
    return out


def _bar_context(s: pd.DataFrame) -> dict:
    """Derive run/pullback/extension context from one symbol's daily bars.

    `s` must be sorted ascending by date with columns open/high/low/close/volume.
    """
    if s is None or len(s) < 20:
        return {}
    s = s.sort_values("date").reset_index(drop=True)
    close = s["close"]
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()

    # Wilder ATR(14) as a fallback if the quote page didn't give one.
    prev_close = close.shift(1)
    tr = pd.concat(
        [s["high"] - s["low"], (s["high"] - prev_close).abs(), (s["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    last = len(s) - 1
    last_close = float(close.iloc[last])
    last_atr = float(atr.iloc[last]) if pd.notna(atr.iloc[last]) else None
    last_ema10 = float(ema10.iloc[last])
    last_ema20 = float(ema20.iloc[last])

    # Consecutive up-days ending today (close > prior close).
    up_streak = 0
    for j in range(last, 0, -1):
        if float(close.iloc[j]) > float(close.iloc[j - 1]):
            up_streak += 1
        else:
            break

    # The current run: walk back from today to the most recent significant swing low
    # within the lookback window. run_low = that trough, run_high = peak since then.
    window = s.iloc[max(0, last - _RUN_PULLBACK_FLOOR_DAYS) : last + 1].reset_index(drop=True)
    run_low_idx = int(window["low"].idxmin())
    run_low = float(window["low"].iloc[run_low_idx])
    after_low = window.iloc[run_low_idx:]
    run_high = float(after_low["high"].max())

    # Retrace: how far price has pulled back from the run peak toward the run low.
    # 0% = sitting at the peak, 100% = back at the run-start low.
    span = run_high - run_low
    retrace_pct = float((run_high - last_close) / span * 100.0) if span > 0 else 0.0

    dist_ema10_atr = (last_close - last_ema10) / last_atr if last_atr and last_atr > 0 else None

    return {
        "bars_n": int(len(s)),
        "last_close": last_close,
        "atr14_bars": last_atr,
        "ema10": last_ema10,
        "ema20": last_ema20,
        "above_ema10": last_close >= last_ema10,
        "above_ema20": last_close >= last_ema20,
        "up_streak": up_streak,
        "run_low": run_low,
        "run_high": run_high,
        "retrace_pct": round(retrace_pct, 1),
        "dist_above_ema10_atr": round(dist_ema10_atr, 2) if dist_ema10_atr is not None else None,
    }


def enrich(tickers: list[str], quote_sleep: float = 0.5, news_sleep: float = 0.3) -> dict[str, dict]:
    """Return {ticker: {quote..., news: [...], bars: {...}}} for every ticker."""
    quotes = fetch_quotes(tickers, sleep=quote_sleep)

    end = date.today()
    start = end - timedelta(days=_BAR_LOOKBACK_DAYS)
    try:
        bars = load_all_bars(start, end, tickers)
    except Exception:  # noqa: BLE001 — bar context is best-effort; quote/news still work
        bars = pd.DataFrame()
    by_sym = {sym: g for sym, g in bars.groupby("symbol")} if not bars.empty else {}

    # Latest daily bar (real closing strength) + hourly volume persistence,
    # both in one batched request each. Best-effort — empty dict on failure.
    intraday = fetch_intraday(tickers)

    out: dict[str, dict] = {}
    for i, t in enumerate(tickers):
        rec = dict(quotes.get(t, {"ticker": t}))
        rec["bars"] = _bar_context(by_sym.get(t))
        intr = intraday.get(t, {})
        rec["latest_bar"] = intr.get("latest_bar")
        rec["vol_profile"] = intr.get("vol_profile")
        rec["news"] = _recent_news(t)
        out[t] = rec
        if i < len(tickers) - 1:
            time.sleep(news_sleep)
    return out


if __name__ == "__main__":
    import json
    import sys

    syms = sys.argv[1:] or ["OUST", "IRDM"]
    data = enrich(syms)
    print(json.dumps(data, indent=2, default=str))
