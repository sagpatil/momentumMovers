# Momentum Movers — Phase 1 Backtest

Reconstructs the Finviz momentum screener on historical Alpaca daily bars and
measures continuation edge across multiple entry/exit rules.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # already populated if you used MCP creds
```

## Run

```bash
# Smoke test on 3 tickers, 1 month
python run.py --start 2024-01-01 --end 2024-06-01 --symbols NVDA SMCI TSLA

# Limited universe (first 500 symbols) for a fast pass
python run.py --start 2024-01-01 --end 2026-06-27 --limit 500

# Full 5-year backtest across the entire Alpaca US equity universe
python run.py
```

Defaults: `--start 2021-01-01 --end 2026-06-27`. Expect the full run to take
30-60 min on first execution (rate-limited fetch), seconds on subsequent runs
(parquet cache in `data/bars/`).

## Outputs

- `results/signals.parquet` — every Day-1 the screener fired
- `results/trades.csv`      — every simulated trade with entry/exit/return
- `results/summary.csv`     — strategy × horizon performance table

## Strategies tested

| Strategy | Entry | When |
|---|---|---|
| `close_d1` | Day-1 close | unconditional |
| `open_d2` | Day-2 open | unconditional |
| `breakout_d2_high` | Day-1 high | only if Day-2 high > Day-1 high |
| `pullback_d2` | Day-1 close | only if Day-2 low ≤ Day-1 close |

Each is evaluated at 1, 3, 5, and 10 trading-day horizons. Costs: 5 bps/side.

## What's missing (Phase 2+)

- Survivorship bias: Alpaca's "active assets" excludes delisted names. Real
  win rate will be lower. Polygon.io or Norgate fix this.
- Market cap filter approximated (no historical shares-out on free tier).
- Intraday ORB / 9 EMA pullback entries require minute bars.
- News catalyst classification not yet wired in.
