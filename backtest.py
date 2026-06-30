"""Simulate entries on Day 1 / 2 / 3 against future bars with multiple exit rules.

For every signal (Day-1 hit from the screener), we:
  1. Compute up to 7 candidate entries (some unconditional, some triggered)
  2. For each *triggered* entry, run every exit rule and emit one row per (entry, exit)

This way we can compare "best entry to wait for" × "best exit logic" independently.

Entries
-------
  close_d1            buy at Day-1 close                           (always fires)
  open_d2             buy at Day-2 open                            (always fires)
  breakout_d2_high    buy at Day-1 high, if Day-2 high > Day-1 high
  pullback_d2         buy at Day-1 close, if Day-2 low <= Day-1 close
  confirm_d3_open     buy at Day-3 open, if Day-2 close > Day-1 close
  confirm_d3_open_v2  buy at Day-3 open, if Day-2 close > Day-1 high           (stronger)
  confirm_d3_open_v3  buy at Day-3 open, if Day-2 close > Day-1 close
                        AND Day-2 volume > 0.7 * Day-1 volume                  (volume held)

Exits
-----
  hold_Nd       exit at close of N bars after entry-bar (N in BACKTEST.horizons)
  atr_trail     start trail at entry - mult * atr_at_entry; ratchet up off highest_high;
                exit at the trail price when bar.low <= trail
  ema9_break    exit at close of the first bar where close < ema9 (computed on full series)

ATR-trail and EMA-break iterate forward up to BACKTEST.max_hold_bars; if still open they
exit at that final bar's close so every trade terminates.

Costs
-----
5 bps per side, applied as a flat percent deduction from gross return.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
from tqdm import tqdm

from config import BACKTEST


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    strategy: str
    triggered: bool
    entry_price: float | None = None
    # Index of the bar in which the entry occurred (close_d1 -> i, open_d2 -> i+1, etc.)
    entry_bar_idx: int | None = None
    # Whether intraday OHLC of the entry bar should be used for stop iteration.
    # True for any entry that fills at open or intraday; False for close fills.
    include_entry_bar_in_iter: bool = False
    atr_at_entry: float | None = None


@dataclass
class ExitFill:
    exit_rule: str
    exit_price: float
    bars_held: int  # bars between entry_bar_idx and exit bar (inclusive of exit bar)


# ---------------------------------------------------------------------------
# Entry computation
# ---------------------------------------------------------------------------


def _compute_entries(s: pd.DataFrame, i: int) -> list[Entry]:
    """Build up to 7 Entry candidates for signal at row index `i` in symbol frame `s`."""
    if i + 1 >= len(s):
        return []
    d1 = s.iloc[i]
    d2 = s.iloc[i + 1]
    d3 = s.iloc[i + 2] if i + 2 < len(s) else None
    atr = float(d1["atr14"])
    out: list[Entry] = []

    # open_d2 — always
    out.append(
        Entry(
            "open_d2",
            triggered=True,
            entry_price=float(d2["open"]),
            entry_bar_idx=i + 1,
            include_entry_bar_in_iter=True,  # full intraday of day 2 still ahead
            atr_at_entry=atr,
        )
    )

    # breakout_d2_high — fills if D2 high > D1 high (assume fill at D1 high)
    trig = float(d2["high"]) > float(d1["high"])
    out.append(
        Entry(
            "breakout_d2_high",
            triggered=trig,
            entry_price=float(d1["high"]) if trig else None,
            entry_bar_idx=i + 1 if trig else None,
            include_entry_bar_in_iter=True,
            atr_at_entry=atr if trig else None,
        )
    )

    # confirm_d3_* — require D3 to exist
    if d3 is not None:
        d2_close = float(d2["close"])
        d1_close = float(d1["close"])
        d1_high = float(d1["high"])
        d1_vol = float(d1["volume"])
        d2_vol = float(d2["volume"])

        # v1: D2 close > D1 close
        trig_v1 = d2_close > d1_close
        out.append(
            Entry(
                "confirm_d3_open",
                triggered=trig_v1,
                entry_price=float(d3["open"]) if trig_v1 else None,
                entry_bar_idx=i + 2 if trig_v1 else None,
                include_entry_bar_in_iter=True,
                atr_at_entry=atr if trig_v1 else None,
            )
        )

        # v2: D2 close > D1 high (stronger)
        trig_v2 = d2_close > d1_high
        out.append(
            Entry(
                "confirm_d3_open_v2",
                triggered=trig_v2,
                entry_price=float(d3["open"]) if trig_v2 else None,
                entry_bar_idx=i + 2 if trig_v2 else None,
                include_entry_bar_in_iter=True,
                atr_at_entry=atr if trig_v2 else None,
            )
        )

        # v3: D2 close > D1 close AND D2 vol > 0.7 * D1 vol
        trig_v3 = trig_v1 and (d2_vol > 0.7 * d1_vol)
        out.append(
            Entry(
                "confirm_d3_open_v3",
                triggered=trig_v3,
                entry_price=float(d3["open"]) if trig_v3 else None,
                entry_bar_idx=i + 2 if trig_v3 else None,
                include_entry_bar_in_iter=True,
                atr_at_entry=atr if trig_v3 else None,
            )
        )

    return out


# ---------------------------------------------------------------------------
# Exit computation
# ---------------------------------------------------------------------------


def _iter_start(entry: Entry) -> int:
    return entry.entry_bar_idx if entry.include_entry_bar_in_iter else entry.entry_bar_idx + 1


def _fixed_horizon_exits(s: pd.DataFrame, entry: Entry) -> list[ExitFill]:
    out: list[ExitFill] = []
    for h in BACKTEST.horizons:
        # Exit at close of the bar `h` bars after entry_bar_idx
        idx = entry.entry_bar_idx + h
        if idx >= len(s):
            continue
        out.append(
            ExitFill(
                exit_rule=f"hold_{h}d",
                exit_price=float(s.iloc[idx]["close"]),
                bars_held=h,
            )
        )
    return out


def _atr_trail_exit(s: pd.DataFrame, entry: Entry, mult: float) -> ExitFill | None:
    """Trailing stop at (highest_high_since_entry - mult * atr_at_entry).

    Stop is checked against bar.low; on hit we exit at the stop price (assumes
    intrabar fill at the trail level — a common backtest simplification).
    """
    if entry.atr_at_entry is None or np.isnan(entry.atr_at_entry):
        return None
    stop_distance = mult * entry.atr_at_entry

    start = _iter_start(entry)
    end = min(start + BACKTEST.max_hold_bars, len(s))
    if start >= end:
        return None

    highest_high = entry.entry_price
    trail = entry.entry_price - stop_distance

    for j in range(start, end):
        bar = s.iloc[j]
        if float(bar["low"]) <= trail:
            return ExitFill(
                exit_rule=f"atr_trail_{mult:g}x",
                exit_price=float(trail),
                bars_held=j - entry.entry_bar_idx,
            )
        highest_high = max(highest_high, float(bar["high"]))
        trail = max(trail, highest_high - stop_distance)

    # Never stopped out within max_hold — exit at the cap.
    last_idx = end - 1
    return ExitFill(
        exit_rule=f"atr_trail_{mult:g}x",
        exit_price=float(s.iloc[last_idx]["close"]),
        bars_held=last_idx - entry.entry_bar_idx,
    )


def _hold_with_stop_exit(
    s: pd.DataFrame, entry: Entry, hold_bars: int, stop_pct: float
) -> ExitFill | None:
    """Hold for `hold_bars` bars but exit early if intraday low hits -`stop_pct`%."""
    stop_price = entry.entry_price * (1.0 - stop_pct / 100.0)
    horizon_idx = entry.entry_bar_idx + hold_bars
    if horizon_idx >= len(s):
        return None

    start = _iter_start(entry)
    for j in range(start, horizon_idx + 1):
        bar = s.iloc[j]
        if float(bar["low"]) <= stop_price:
            return ExitFill(
                exit_rule=f"hold_{hold_bars}d_stop_{stop_pct:g}pct",
                exit_price=float(stop_price),
                bars_held=j - entry.entry_bar_idx,
            )

    # Reached horizon without hitting stop — exit at close of horizon bar.
    return ExitFill(
        exit_rule=f"hold_{hold_bars}d_stop_{stop_pct:g}pct",
        exit_price=float(s.iloc[horizon_idx]["close"]),
        bars_held=hold_bars,
    )


def _hold_then_lower_low_exit(s: pd.DataFrame, entry: Entry, hold_bars: int) -> ExitFill | None:
    """Hold for `hold_bars`, then exit at close of first subsequent bar where low < prior low."""
    start_watch = entry.entry_bar_idx + hold_bars + 1  # first bar we can compare to prior
    end = min(entry.entry_bar_idx + BACKTEST.max_hold_bars, len(s) - 1)
    if start_watch >= len(s):
        return None

    for j in range(start_watch, end + 1):
        if float(s.iloc[j]["low"]) < float(s.iloc[j - 1]["low"]):
            return ExitFill(
                exit_rule=f"hold_{hold_bars}d_then_lower_low",
                exit_price=float(s.iloc[j]["close"]),
                bars_held=j - entry.entry_bar_idx,
            )

    # Never triggered within max_hold — exit at the cap.
    return ExitFill(
        exit_rule=f"hold_{hold_bars}d_then_lower_low",
        exit_price=float(s.iloc[end]["close"]),
        bars_held=end - entry.entry_bar_idx,
    )


def _hold_then_pct_trail_exit(
    s: pd.DataFrame, entry: Entry, hold_bars: int, trail_pct: float
) -> ExitFill | None:
    """Hold for `hold_bars`, then trail `trail_pct`% below highest high since hold-end.

    Peak starts at the high of the hold-end bar (entry_bar_idx + hold_bars). From the
    next bar onward, exit when bar.low <= peak * (1 - trail_pct/100), filled at the
    trail price (intrabar simplification).
    """
    hold_end = entry.entry_bar_idx + hold_bars
    if hold_end >= len(s):
        return None

    peak = float(s.iloc[hold_end]["high"])
    trail = peak * (1.0 - trail_pct / 100.0)
    end = min(entry.entry_bar_idx + BACKTEST.max_hold_bars, len(s))

    for j in range(hold_end + 1, end):
        bar = s.iloc[j]
        if float(bar["low"]) <= trail:
            return ExitFill(
                exit_rule=f"hold_{hold_bars}d_then_trail_{trail_pct:g}pct",
                exit_price=float(trail),
                bars_held=j - entry.entry_bar_idx,
            )
        peak = max(peak, float(bar["high"]))
        trail = peak * (1.0 - trail_pct / 100.0)

    last_idx = end - 1
    return ExitFill(
        exit_rule=f"hold_{hold_bars}d_then_trail_{trail_pct:g}pct",
        exit_price=float(s.iloc[last_idx]["close"]),
        bars_held=last_idx - entry.entry_bar_idx,
    )


def _hold_then_atr_trail_exit(
    s: pd.DataFrame, entry: Entry, hold_bars: int, mult: float
) -> ExitFill | None:
    """Hold for `hold_bars`, then chandelier trail at peak - mult*ATR."""
    if entry.atr_at_entry is None or np.isnan(entry.atr_at_entry):
        return None
    hold_end = entry.entry_bar_idx + hold_bars
    if hold_end >= len(s):
        return None

    stop_distance = mult * entry.atr_at_entry
    peak = float(s.iloc[hold_end]["high"])
    trail = peak - stop_distance
    end = min(entry.entry_bar_idx + BACKTEST.max_hold_bars, len(s))

    for j in range(hold_end + 1, end):
        bar = s.iloc[j]
        if float(bar["low"]) <= trail:
            return ExitFill(
                exit_rule=f"hold_{hold_bars}d_then_atr_trail_{mult:g}x",
                exit_price=float(trail),
                bars_held=j - entry.entry_bar_idx,
            )
        peak = max(peak, float(bar["high"]))
        trail = max(trail, peak - stop_distance)

    last_idx = end - 1
    return ExitFill(
        exit_rule=f"hold_{hold_bars}d_then_atr_trail_{mult:g}x",
        exit_price=float(s.iloc[last_idx]["close"]),
        bars_held=last_idx - entry.entry_bar_idx,
    )


def _hold_then_ema_break_exit(
    s: pd.DataFrame, entry: Entry, hold_bars: int, ema_col: str
) -> ExitFill | None:
    """Hold for `hold_bars`, then exit at close when close < `ema_col`."""
    if ema_col not in s.columns:
        return None
    start = entry.entry_bar_idx + hold_bars
    end = min(entry.entry_bar_idx + BACKTEST.max_hold_bars, len(s))
    if start >= end:
        return None

    for j in range(start, end):
        bar = s.iloc[j]
        ema_val = bar[ema_col]
        if pd.isna(ema_val):
            continue
        if float(bar["close"]) < float(ema_val):
            return ExitFill(
                exit_rule=f"hold_{hold_bars}d_then_{ema_col}_break",
                exit_price=float(bar["close"]),
                bars_held=j - entry.entry_bar_idx,
            )

    last_idx = end - 1
    return ExitFill(
        exit_rule=f"hold_{hold_bars}d_then_{ema_col}_break",
        exit_price=float(s.iloc[last_idx]["close"]),
        bars_held=last_idx - entry.entry_bar_idx,
    )


def _staged_stop_be_trail_exit(
    s: pd.DataFrame,
    entry: Entry,
    init_stop_pct: float,
    be_trigger_pct: float,
    hold_bars: int,
    trail_pct: float,
) -> ExitFill | None:
    """Three-stage exit:
      1. Initial fixed stop at entry*(1 - init_stop_pct/100)
      2. After bar.high >= entry*(1 + be_trigger_pct/100), move stop to entry (breakeven)
      3. After hold_bars elapsed, switch to trailing stop at peak*(1 - trail_pct/100),
         tracking peak from the hold-end bar onward.
      Stop is monotonic (only raises).
    """
    entry_px = entry.entry_price
    stop = entry_px * (1.0 - init_stop_pct / 100.0)
    be_trigger = entry_px * (1.0 + be_trigger_pct / 100.0)
    hold_end = entry.entry_bar_idx + hold_bars
    end = min(entry.entry_bar_idx + BACKTEST.max_hold_bars, len(s))

    start = _iter_start(entry)
    if start >= end:
        return None

    peak = entry_px
    rule = f"staged_{init_stop_pct:g}pct_be{be_trigger_pct:g}pct_h{hold_bars}_trail{trail_pct:g}pct"

    for j in range(start, end):
        bar = s.iloc[j]
        if float(bar["low"]) <= stop:
            return ExitFill(exit_rule=rule, exit_price=float(stop), bars_held=j - entry.entry_bar_idx)
        # Breakeven arm during phase 1+2
        if float(bar["high"]) >= be_trigger:
            stop = max(stop, entry_px)
        # Phase 3: trail off peak from hold-end onward
        if j >= hold_end:
            peak = max(peak, float(bar["high"]))
            stop = max(stop, peak * (1.0 - trail_pct / 100.0))

    last_idx = end - 1
    return ExitFill(
        exit_rule=rule, exit_price=float(s.iloc[last_idx]["close"]), bars_held=last_idx - entry.entry_bar_idx
    )


def _tiered_take_trail_exit(
    s: pd.DataFrame, entry: Entry, take_pct: float, take_frac: float, trail_pct: float
) -> ExitFill | None:
    """Sell `take_frac` of position at +`take_pct`% (intraday touch),
    then trail the remaining (1 - take_frac) at `trail_pct` off peak.
    Reported exit_price is the position-weighted blended price.
    If +take_pct never hits within max_hold, full position exits at the cap close.
    """
    entry_px = entry.entry_price
    take_trigger = entry_px * (1.0 + take_pct / 100.0)
    end = min(entry.entry_bar_idx + BACKTEST.max_hold_bars, len(s))
    start = _iter_start(entry)
    if start >= end:
        return None

    rule = f"tiered_{take_frac:g}at{take_pct:g}pct_trail{trail_pct:g}pct"
    take_bar = None
    peak = entry_px

    for j in range(start, end):
        bar = s.iloc[j]
        peak = max(peak, float(bar["high"]))
        if float(bar["high"]) >= take_trigger:
            take_bar = j
            # Initialize remaining-half trail from current peak.
            peak = max(peak, take_trigger)
            break

    if take_bar is None:
        # Never hit take target — exit whole position at cap close.
        last_idx = end - 1
        return ExitFill(
            exit_rule=rule, exit_price=float(s.iloc[last_idx]["close"]), bars_held=last_idx - entry.entry_bar_idx
        )

    # Trail the remainder from bar after the take.
    trail = peak * (1.0 - trail_pct / 100.0)
    remainder_exit = None
    remainder_bar = None
    for j in range(take_bar + 1, end):
        bar = s.iloc[j]
        if float(bar["low"]) <= trail:
            remainder_exit = trail
            remainder_bar = j
            break
        peak = max(peak, float(bar["high"]))
        trail = max(trail, peak * (1.0 - trail_pct / 100.0))

    if remainder_exit is None:
        last_idx = end - 1
        remainder_exit = float(s.iloc[last_idx]["close"])
        remainder_bar = last_idx

    blended = take_frac * take_trigger + (1.0 - take_frac) * remainder_exit
    return ExitFill(
        exit_rule=rule,
        exit_price=float(blended),
        bars_held=remainder_bar - entry.entry_bar_idx,
    )


def _hold_then_atr_trail_cap_exit(
    s: pd.DataFrame, entry: Entry, hold_bars: int, mult: float, cap_pct: float
) -> ExitFill | None:
    """ATR trail post-hold, with a hard cap that the stop never sits more than
    `cap_pct`% below the peak. stop = max(peak - mult*ATR, peak * (1 - cap_pct/100))."""
    if entry.atr_at_entry is None or np.isnan(entry.atr_at_entry):
        return None
    hold_end = entry.entry_bar_idx + hold_bars
    if hold_end >= len(s):
        return None

    stop_distance = mult * entry.atr_at_entry
    peak = float(s.iloc[hold_end]["high"])
    trail = max(peak - stop_distance, peak * (1.0 - cap_pct / 100.0))
    end = min(entry.entry_bar_idx + BACKTEST.max_hold_bars, len(s))
    rule = f"hold_{hold_bars}d_then_atr_trail_{mult:g}x_cap{cap_pct:g}pct"

    for j in range(hold_end + 1, end):
        bar = s.iloc[j]
        if float(bar["low"]) <= trail:
            return ExitFill(exit_rule=rule, exit_price=float(trail), bars_held=j - entry.entry_bar_idx)
        peak = max(peak, float(bar["high"]))
        trail = max(trail, peak - stop_distance, peak * (1.0 - cap_pct / 100.0))

    last_idx = end - 1
    return ExitFill(
        exit_rule=rule, exit_price=float(s.iloc[last_idx]["close"]), bars_held=last_idx - entry.entry_bar_idx
    )


def _donchian_exit(s: pd.DataFrame, entry: Entry, lookback: int) -> ExitFill | None:
    """Exit at close when close < min(low) over previous `lookback` bars."""
    start = _iter_start(entry)
    end = min(entry.entry_bar_idx + BACKTEST.max_hold_bars, len(s))
    if start >= end or start - lookback < 0:
        return None

    lows = s["low"].to_numpy()
    closes = s["close"].to_numpy()
    rule = f"donchian_{lookback}"

    for j in range(start, end):
        floor = float(np.min(lows[j - lookback : j]))
        if closes[j] < floor:
            return ExitFill(exit_rule=rule, exit_price=float(closes[j]), bars_held=j - entry.entry_bar_idx)

    last_idx = end - 1
    return ExitFill(exit_rule=rule, exit_price=float(closes[last_idx]), bars_held=last_idx - entry.entry_bar_idx)


def _all_exits(s: pd.DataFrame, entry: Entry) -> Iterable[ExitFill]:
    yield from _fixed_horizon_exits(s, entry)
    for hold_bars, stop_pct in BACKTEST.hold_stop_combos:
        fill = _hold_with_stop_exit(s, entry, hold_bars, stop_pct)
        if fill is not None:
            yield fill
    for hold_bars in BACKTEST.hold_then_lower_low_bars:
        fill = _hold_then_lower_low_exit(s, entry, hold_bars)
        if fill is not None:
            yield fill
    for hold_bars, trail_pct in BACKTEST.hold_then_pct_trail_combos:
        fill = _hold_then_pct_trail_exit(s, entry, hold_bars, trail_pct)
        if fill is not None:
            yield fill
    for hold_bars, mult in BACKTEST.hold_then_atr_trail_combos:
        fill = _hold_then_atr_trail_exit(s, entry, hold_bars, mult)
        if fill is not None:
            yield fill
    for hold_bars, ema_col in BACKTEST.hold_then_ema_break_combos:
        fill = _hold_then_ema_break_exit(s, entry, hold_bars, ema_col)
        if fill is not None:
            yield fill
    for init_stop, be_trig, hold_bars, trail_pct in BACKTEST.staged_stop_be_trail:
        fill = _staged_stop_be_trail_exit(s, entry, init_stop, be_trig, hold_bars, trail_pct)
        if fill is not None:
            yield fill
    for take_pct, take_frac, trail_pct in BACKTEST.tiered_take_trail:
        fill = _tiered_take_trail_exit(s, entry, take_pct, take_frac, trail_pct)
        if fill is not None:
            yield fill
    for hold_bars, mult, cap_pct in BACKTEST.hold_then_atr_trail_cap:
        fill = _hold_then_atr_trail_cap_exit(s, entry, hold_bars, mult, cap_pct)
        if fill is not None:
            yield fill
    for lookback in BACKTEST.donchian_lookbacks:
        fill = _donchian_exit(s, entry, lookback)
        if fill is not None:
            yield fill
    for mult in BACKTEST.atr_trail_mults:
        fill = _atr_trail_exit(s, entry, mult)
        if fill is not None:
            yield fill


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _index_bars(bars: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for sym, g in bars.groupby("symbol"):
        g = g.sort_values("date").reset_index(drop=True)
        g.index = pd.to_datetime(g["date"])
        out[sym] = g
    return out


def _cost_adjusted(ret_pct: float) -> float:
    return ret_pct - 2 * BACKTEST.cost_bps_per_side / 100.0


def simulate(signals: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    by_sym = _index_bars(bars)
    rows: list[dict] = []

    for sig in tqdm(signals.itertuples(index=False), total=len(signals), desc="simulating"):
        sym = sig.symbol
        if sym not in by_sym:
            continue
        s = by_sym[sym]
        sig_ts = pd.Timestamp(sig.date)
        if sig_ts not in s.index:
            continue
        i = s.index.get_loc(sig_ts)

        for entry in _compute_entries(s, i):
            if not entry.triggered:
                rows.append(_row_untriggered(sym, sig_ts, entry.strategy))
                continue
            for fill in _all_exits(s, entry):
                gross = (fill.exit_price / entry.entry_price - 1.0) * 100.0
                rows.append(
                    {
                        "symbol": sym,
                        "signal_date": sig_ts,
                        "strategy": entry.strategy,
                        "exit_rule": fill.exit_rule,
                        "entry_price": entry.entry_price,
                        "exit_price": fill.exit_price,
                        "bars_held": fill.bars_held,
                        "ret_pct": _cost_adjusted(gross),
                        "triggered": True,
                    }
                )

    return pd.DataFrame(rows)


def _row_untriggered(symbol: str, signal_date: pd.Timestamp, strategy: str) -> dict:
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "strategy": strategy,
        "exit_rule": None,
        "entry_price": np.nan,
        "exit_price": np.nan,
        "bars_held": np.nan,
        "ret_pct": np.nan,
        "triggered": False,
    }
