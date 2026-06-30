"""Phase 1 entrypoint: universe -> bars -> screener -> backtest -> report."""
from __future__ import annotations

import argparse
import sys
from datetime import date

import pandas as pd

from backtest import simulate
from config import BACKTEST, DATA_DIR, RESULTS_DIR
from fetch_bars import load_all_bars
from report import print_report, save_report
from screener import apply_screener, compute_indicators
from universe import fetch_universe


def main():
    ap = argparse.ArgumentParser(description="Momentum Movers — Phase 1 daily backtest")
    ap.add_argument("--start", default=BACKTEST.start_date.isoformat())
    ap.add_argument("--end", default=BACKTEST.end_date.isoformat())
    ap.add_argument("--limit", type=int, default=None, help="Limit universe size (debugging)")
    ap.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Restrict to these symbols (skips full universe fetch)",
    )
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print(f"[1/5] Loading universe...")
    if args.symbols:
        symbols = args.symbols
        print(f"      restricted to {len(symbols)} symbols: {symbols}")
    else:
        u = fetch_universe()
        symbols = u["symbol"].tolist()
        if args.limit:
            symbols = symbols[: args.limit]
        print(f"      {len(symbols):,} symbols")

    print(f"[2/5] Fetching daily bars {start} -> {end} (cached to {DATA_DIR / 'bars'})...")
    bars = load_all_bars(start, end, symbols)
    print(f"      {len(bars):,} bar rows across {bars['symbol'].nunique()} symbols")

    if bars.empty:
        print("No bars returned. Check API creds / date range / symbol list.")
        sys.exit(1)

    print(f"[3/5] Computing indicators...")
    bars = compute_indicators(bars)

    print(f"[4/5] Applying screener...")
    signals = apply_screener(bars)
    print(f"      {len(signals):,} qualifying Day-1 signals")
    signals.to_parquet(RESULTS_DIR / "signals.parquet", index=False)

    if signals.empty:
        print("No signals. Loosen filters in config.py?")
        sys.exit(0)

    print(f"[5/5] Simulating strategies...")
    trades = simulate(signals, bars)
    save_report(trades, RESULTS_DIR)
    print_report(trades)
    print(f"\nWrote: {RESULTS_DIR / 'trades.csv'}, {RESULTS_DIR / 'summary.csv'}, "
          f"{RESULTS_DIR / 'signals.parquet'}")


if __name__ == "__main__":
    main()
