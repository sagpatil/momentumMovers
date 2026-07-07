"""Grade the signal log against what prices actually did → outcomes.parquet.

Derived data, never hand-maintained: every run rebuilds outcomes.parquet from
scratch out of signals.parquet + freshly fetched (split-adjusted) bars, so it is
always safe to delete and regenerate — only the signal log is sacred.

Consecutive signals for a ticker are grouped into an *episode* (a new one starts
after EPISODE_GAP_DAYS calendar days without a signal). The episode's entry is
the close of its first signal date — "what if we acted the day it first
appeared." Per episode we record fixed-horizon forward returns, when each exit
rule would have ended the trade, max gain / max drawdown vs entry (a.k.a.
MFE/MAE), and the R-multiple of the EMA10 exit.

Run manually or on a schedule:
    python -m scrape.evaluate
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from fetch_bars import load_all_bars
from scrape.signal_log import DATA_DIR, SIGNALS_PATH

OUTCOMES_PATH = DATA_DIR / "outcomes.parquet"

EPISODE_GAP_DAYS = 7          # calendar days without a signal -> new episode
HORIZONS = [1, 3, 5, 10, 20]  # trading days
RETRACE_FAIL_PCT = 50.0       # mirrors mqs.PULLBACK_FAIL_RETRACE_PCT
TRUNCATED_AFTER_DAYS = 7      # bars stop this long before today -> delisted/halted
_BAR_HISTORY_DAYS = 120       # context before entry for EMA/ATR


def _episodes(signals: pd.DataFrame) -> pd.DataFrame:
    """One row per episode: ticker, entry date, and the entry-day signal fields."""
    sig = signals.sort_values(["ticker", "date"]).copy()
    sig["_d"] = pd.to_datetime(sig["date"])
    out = []
    for t, g in sig.groupby("ticker"):
        gap = g["_d"].diff().dt.days.fillna(999)
        episode_id = (gap > EPISODE_GAP_DAYS).cumsum()
        for _, ep in g.groupby(episode_id):
            first = ep.iloc[0]
            out.append({
                "ticker": t,
                "entry_date": first["date"],
                "n_signals": len(ep),
                "last_signal_date": ep.iloc[-1]["date"],
                "mqs": first["mqs"],
                "tier": first["tier"],
                "burst_age": first["burst_age"],
                "catalyst_strength": first["catalyst_strength"],
                "sector": first["sector"],
            })
    return pd.DataFrame(out)


def _wilder_atr(s: pd.DataFrame) -> pd.Series:
    prev = s["close"].shift(1)
    tr = pd.concat(
        [s["high"] - s["low"], (s["high"] - prev).abs(), (s["low"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()


def _first_true_exit(cond: pd.Series, s: pd.DataFrame, entry_close: float) -> dict:
    hits = cond[cond].index
    if len(hits) == 0:
        return {"date": None, "ret_pct": None}
    j = hits[0]
    return {
        "date": str(s["date"].iloc[j])[:10],
        "ret_pct": round((float(s["close"].iloc[j]) / entry_close - 1) * 100, 2),
    }


def _grade(ep: dict, s: pd.DataFrame, today: date) -> dict:
    """Evaluate one episode against its ticker's bars (ascending, reset index)."""
    dts = pd.to_datetime(s["date"]).dt.date
    entry_dt = date.fromisoformat(ep["entry_date"])
    entry_candidates = np.flatnonzero(dts >= entry_dt)
    row = {**ep, "status": "open", "entry_close": None}
    if len(entry_candidates) == 0:
        row["status"] = "no_bars"
        return row
    e = int(entry_candidates[0])
    entry_close = float(s["close"].iloc[e])
    atr = _wilder_atr(s)
    ema10 = s["close"].ewm(span=10, adjust=False).mean()
    ema20 = s["close"].ewm(span=20, adjust=False).mean()
    entry_atr = float(atr.iloc[e]) if pd.notna(atr.iloc[e]) else None
    row.update({"entry_close": round(entry_close, 4), "entry_atr": entry_atr})

    for h in HORIZONS:
        j = e + h
        row[f"fwd_{h}d_pct"] = (
            round((float(s["close"].iloc[j]) / entry_close - 1) * 100, 2)
            if j < len(s) else None
        )

    fwd = s.iloc[e:].reset_index(drop=True)
    peak = fwd["high"].cummax()
    span = (peak - entry_close).clip(lower=1e-9)
    retrace = (peak - fwd["close"]) / span * 100.0
    # Retrace only arms once a real run exists, else a wick-sized span makes any
    # dip read as >50% given back.
    has_run = peak > entry_close * 1.02

    exits = {
        "ema10": _first_true_exit(
            (fwd["close"] < ema10.iloc[e:].reset_index(drop=True)).iloc[1:], fwd, entry_close
        ),
        "ema20": _first_true_exit(
            (fwd["close"] < ema20.iloc[e:].reset_index(drop=True)).iloc[1:], fwd, entry_close
        ),
        "retrace50": _first_true_exit(
            ((retrace > RETRACE_FAIL_PCT) & has_run).iloc[1:], fwd, entry_close
        ),
    }
    for name, ex in exits.items():
        row[f"exit_{name}_date"] = ex["date"]
        row[f"exit_{name}_ret_pct"] = ex["ret_pct"]

    end = len(fwd)
    if exits["ema20"]["date"] is not None:
        end = int(np.flatnonzero(fwd["date"].astype(str).str[:10] == exits["ema20"]["date"])[0]) + 1
        row["status"] = "closed"
    # Entry is the close, so the entry day's earlier high/low was never held.
    held = fwd.iloc[1:end]
    row["max_gain_pct"] = (
        round((float(held["high"].max()) / entry_close - 1) * 100, 2) if len(held) else None
    )
    row["max_drawdown_pct"] = (
        round((float(held["low"].min()) / entry_close - 1) * 100, 2) if len(held) else None
    )
    if entry_atr and exits["ema10"]["ret_pct"] is not None:
        row["r_multiple"] = round(exits["ema10"]["ret_pct"] / 100 * entry_close / entry_atr, 2)
    else:
        row["r_multiple"] = None

    last_bar = dts.iloc[-1]
    if row["status"] == "open" and (today - last_bar).days > TRUNCATED_AFTER_DAYS:
        row["status"] = "truncated"
    row["days_tracked"] = len(fwd)
    return row


def evaluate(today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    signals = pd.read_parquet(SIGNALS_PATH)
    eps = _episodes(signals)
    if eps.empty:
        return eps

    start = date.fromisoformat(eps["entry_date"].min()) - timedelta(days=_BAR_HISTORY_DAYS)
    bars = load_all_bars(start, today, sorted(eps["ticker"].unique()))
    by_sym = {sym: g.sort_values("date").reset_index(drop=True) for sym, g in bars.groupby("symbol")}

    rows = []
    for ep in eps.to_dict("records"):
        s = by_sym.get(ep["ticker"])
        if s is None or len(s) < 20:
            rows.append({**ep, "status": "no_bars"})
            continue
        rows.append(_grade(ep, s, today))
    out = pd.DataFrame(rows).sort_values(["entry_date", "ticker"]).reset_index(drop=True)
    OUTCOMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUTCOMES_PATH, index=False)
    return out


def _summary(out: pd.DataFrame) -> None:
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", None)
    graded = out[out["entry_close"].notna()].copy()
    print(f"\n{len(out)} episodes ({len(graded)} graded) -> {OUTCOMES_PATH}\n")
    if graded.empty:
        return
    num_cols = [c for c in graded.columns if c.startswith(("fwd_", "exit_")) and c.endswith("_pct")]
    num_cols += ["max_gain_pct", "max_drawdown_pct", "r_multiple", "mqs", "burst_age"]
    graded[num_cols] = graded[num_cols].apply(pd.to_numeric, errors="coerce")

    def block(title: str, key: pd.Series) -> None:
        g = graded.groupby(key)
        tbl = pd.DataFrame({
            "n": g.size(),
            "fwd5_avg": g["fwd_5d_pct"].mean().round(1),
            "fwd10_avg": g["fwd_10d_pct"].mean().round(1),
            "win5": g["fwd_5d_pct"].apply(lambda x: (x.dropna() > 0).mean() * 100).round(0),
            "max_gain_avg": g["max_gain_pct"].mean().round(1),
            "max_dd_avg": g["max_drawdown_pct"].mean().round(1),
            "r_avg": g["r_multiple"].mean().round(2),
        })
        print(f"--- {title}\n{tbl}\n")

    block("by tier at entry", graded["tier"])
    if graded["mqs"].notna().sum() >= 8:
        block("by MQS quartile", pd.qcut(graded["mqs"], 4, duplicates="drop"))
    if graded["burst_age"].notna().any():
        block("by burst age at entry", graded["burst_age"].clip(upper=5))


if __name__ == "__main__":
    _summary(evaluate())
