"""Time-series history of screener appearances → 'repeat offender' streaks.

Each run appends one row per ticker to data/screener_history.parquet. The streak
is how many *consecutive trading days* (back from today) a ticker has appeared in
the screener — the persistence signal that distinguishes a one-day pump from a
multi-day mover. We collapse to trading days by treating each distinct run-date as
one step (weekends/holidays simply produce no row).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from config import ROOT

# Stored under the dashboard's committed data dir (not the gitignored data/ bar
# cache) so streaks persist across CI runs and ship with the site.
HISTORY_PATH = ROOT / "dashboard" / "public" / "data" / "screener_history.parquet"


def load_history() -> pd.DataFrame:
    if HISTORY_PATH.exists():
        return pd.read_parquet(HISTORY_PATH)
    return pd.DataFrame(columns=["date", "ticker", "change_pct", "price", "mqs"])


def append_today(rows: list[dict], run_date: date) -> pd.DataFrame:
    """Append today's hits (idempotent for a given run_date) and persist."""
    hist = load_history()
    iso = run_date.isoformat()
    hist = hist[hist["date"] != iso]  # replace any prior run for the same date
    today = pd.DataFrame(
        [{"date": iso, "ticker": r["ticker"], "change_pct": r.get("change_pct"),
          "price": r.get("price"), "mqs": r.get("mqs")} for r in rows]
    )
    out = today if hist.empty else pd.concat([hist, today], ignore_index=True)
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(HISTORY_PATH, index=False)
    return out


def compute_streaks(hist: pd.DataFrame, run_date: date) -> dict[str, int]:
    """For each ticker, count consecutive run-dates ending at run_date.

    Run-dates are the distinct dates present in history, sorted. A ticker's streak
    is the number of trailing run-dates (including today) it appears in without a
    gap. First appearance = 1.
    """
    if hist.empty:
        return {}
    run_dates = sorted(hist["date"].unique())
    if run_dates[-1] != run_date.isoformat():
        run_dates.append(run_date.isoformat())
    by_date = {d: set(hist.loc[hist["date"] == d, "ticker"]) for d in run_dates}

    streaks: dict[str, int] = {}
    today_tickers = by_date.get(run_date.isoformat(), set())
    for t in today_tickers:
        streak = 0
        for d in reversed(run_dates):
            if t in by_date.get(d, set()):
                streak += 1
            else:
                break
        streaks[t] = streak
    return streaks
