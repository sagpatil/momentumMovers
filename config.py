"""Configuration: Alpaca creds, screener filter values, backtest params."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
DATA_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

ALPACA_API_KEY = os.environ["ALPACA_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]


@dataclass(frozen=True)
class ScreenerFilter:
    """Reconstructs the Finviz screener used in production.

    URL: cap_smallover,sh_avgvol_o1000,sh_curvol_o1000,sh_price_o7,
         sh_relvol_o1.5,ta_averagetruerange_o1,ta_change_u3,ta_sma200_sb50
    """
    min_market_cap: float = 300_000_000      # cap_smallover
    min_avg_volume: int = 500_000            # sh_avgvol_o1000  (3-month avg approximated as 63-day)
    min_curr_volume: int = 750_000           # sh_curvol_o1000
    min_price: float = 7.0                   # sh_price_o7
    min_rel_volume: float = 1.25              # sh_relvol_o1.5  (today / 50-day avg)
    min_atr: float = 1.0                     # ta_averagetruerange_o1  (14-day)
    min_change_pct: float = 3.5              # ta_change_u3
    sma200_above_pct: float = 0.0            # ta_sma200_sb50  (price >= 1.0 * SMA200, i.e. above the line)
    # Day-1 closing strength: 1.0 = closed at high of day, 0.0 = closed at low.
    # 0.8 keeps signals where the close was in the top 20% of the daily range.
    min_close_position: float = 0.6


@dataclass(frozen=True)
class BacktestConfig:
    start_date: date = date(2021, 1, 1)
    end_date: date = date(2026, 6, 27)
    # Fixed-horizon "naked" exits (trading days)
    horizons: tuple = (1, 3, 5, 10)
    # Fixed-horizon exits with a hard stop loss applied each bar.
    # Each tuple is (hold_bars, stop_pct).
    hold_stop_combos: tuple = ((10, 8.0), (15, 8.0), (12, 7.0), (15, 10.0))
    # Hold for N bars, then exit at close of the first subsequent bar where
    # bar.low < previous bar.low (first "lower low" after the hold window).
    hold_then_lower_low_bars: tuple = (12,)
    # Hold for N bars, then trail at `trail_pct`% off the highest high since hold-end.
    # Each tuple is (hold_bars, trail_pct).
    hold_then_pct_trail_combos: tuple = ((10, 4.0),)
    # Hold for N bars, then trail at peak - mult*ATR. Each tuple is (hold_bars, mult).
    hold_then_atr_trail_combos: tuple = ((10, 2.0), (10, 3.0))
    # Hold for N bars, then exit at close when close < EMA. Each tuple is (hold_bars, ema_col).
    hold_then_ema_break_combos: tuple = ((10, "ema10"), (10, "ema20"))
    # Staged stop: init_stop_pct (initial fixed stop), be_trigger_pct (move stop to entry
    # after this much profit), hold_bars (switch to trail mode), trail_pct (peak trail).
    staged_stop_be_trail: tuple = ((8.0, 5.0, 10, 4.0),)
    # Tiered take-profit: sell `take_frac` of position at `take_pct` gain, trail the rest
    # at `trail_pct` off peak. Tuple: (take_pct, take_frac, trail_pct).
    tiered_take_trail: tuple = ((10.0, 0.5, 4.0), (10.0, 0.5, 2.0))
    # ATR trail post-hold with a hard percentage cap (stop never drops more than cap_pct
    # below the peak, even if mult*ATR is wider). Tuple: (hold_bars, mult, cap_pct).
    hold_then_atr_trail_cap: tuple = ((10, 3.0, 8.0),)
    # Donchian-style exit: exit at close when close < min low over last N bars.
    donchian_lookbacks: tuple = ()
    # Cap for trail / EMA-break iteration so a never-stopped trade still terminates
    max_hold_bars: int = 60
    # ATR trailing-stop multipliers (emits one exit per multiplier)
    atr_trail_mults: tuple = (2.0, 3.0)
    # Slippage + commission assumption per side (bps)
    cost_bps_per_side: float = 5.0


SCREENER = ScreenerFilter()
BACKTEST = BacktestConfig()
