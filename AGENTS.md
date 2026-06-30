# AGENTS.md — Momentum Movers Backtest

Guide for AI agents (and future humans) working on this codebase. Read this before making changes.

## What this project does

Reconstructs a Finviz daily momentum screener on historical Alpaca OHLCV bars, then simulates many entry × exit combinations to find which (entry, exit) pair best captures multi-day continuation after a "Day-1 spike."

**Domain:** US equity swing-trading. Universe: ~11k Alpaca-tradable symbols. Timeframe: 2021–present. Bar resolution: daily.

**Goal of Phase 1:** rank entry and exit strategies by risk-adjusted return on real historical signals. Not a live trading system.

## Pipeline (run.py)

```
universe.py  → fetch tradable symbol list from Alpaca
fetch_bars.py → daily OHLCV for each symbol, cached as parquet in data/bars/
screener.py  → compute indicators (ATR14, EMA10/20, SMA200, rel_volume, etc.)
              → apply Finviz-equivalent filter, emit Day-1 signals
backtest.py  → for each signal, simulate every (entry, exit) pair against future bars
report.py    → aggregate trades into strategy × exit_rule performance table
```

Outputs land in `results/`:
- `signals.parquet` — every Day-1 the screener fired
- `trades.csv` — one row per (signal × entry × exit) with realized return
- `summary.csv` — aggregated metrics per (strategy, exit_rule)

## Core concepts

### Day-1 signal
A bar where: price > $7, vol > 750k, rel_vol > 1.25, change > 3.5%, ATR14 > $1, close above SMA200, close in top 40% of daily range. Tuned in `config.py:ScreenerFilter`.

### Entry strategies (in `backtest.py:_compute_entries`)
Each signal can produce up to 6 candidate entries:
- `open_d2` — buy at Day-2 open (unconditional)
- `breakout_d2_high` — buy at Day-1 high *if* Day-2 high exceeds it
- `confirm_d3_open` — buy at Day-3 open *if* Day-2 close > Day-1 close
- `confirm_d3_open_v2` — buy at Day-3 open *if* Day-2 close > Day-1 *high* (stronger)
- `confirm_d3_open_v3` — v1 + Day-2 volume held above 0.7× Day-1 volume

(`close_d1` was removed — too aggressive, no confirmation of continuation.)

### Exit rules (configured in `config.py:BacktestConfig`)
Each triggered entry runs through every exit rule, producing one trade row per (entry, exit). Current families:
- `hold_Nd` — exit at close N bars after entry (1/3/5/10)
- `hold_Nd_stop_Xpct` — hold N days but hard stop if intraday low hits -X% (10d/8%, 15d/8%, 12d/7%, 15d/10%)
- `hold_Nd_then_lower_low` — hold N days then exit at close of first bar where low < prior bar's low
- `hold_Nd_then_trail_Xpct` — hold N days then trail X% off peak (winner so far)
- `hold_Nd_then_atr_trail_Mx` — hold N days then chandelier trail (peak - M × ATR)
- `hold_Nd_then_atr_trail_Mx_capXpct` — ATR trail with hard percentage cap
- `hold_Nd_then_EMA_break` — hold N days, exit at close < EMA10/EMA20
- `staged_Xpct_beYpct_hN_trailZpct` — initial X% stop, breakeven move at +Y%, switch to Z% trail after N days
- `tiered_Fat10pct_trailXpct` — sell F at +10%, trail remainder X% off peak
- `atr_trail_Mx` — pure ATR chandelier from entry (no hold window)

All trail/EMA exits cap iteration at `max_hold_bars` (60).

### Metrics (in `report.py:summarize`)
- `n_trades` — sample size; treat <100 as noise
- `win_rate_%` — % of trades with positive ret after costs
- `avg_ret_%` — mean return per trade after 10 bps round-trip cost
- `profit_factor` — sum(wins) / |sum(losses)|; >1.5 is meaningful edge
- `sharpe_per_trade` — avg_ret / std_ret; **primary ranking metric**
- `median_ret_%` — robust to outliers; negative median + positive mean = lottery distribution

### Cost model
5 bps per side flat, applied to gross return. No slippage modeling beyond this. Stop fills assumed at the stop price (no gap-through penalty) — optimistic.

## How to run

```bash
source .venv/bin/activate

# Default: 2021-01-01 to today, full universe, uses cached bars if present
python run.py

# Custom date window
python run.py --start 2023-01-01 --end 2026-06-27

# Smoke test (3 tickers, fast)
python run.py --start 2024-01-01 --end 2024-06-01 --symbols NVDA SMCI TSLA

# Restricted universe (alphabetical first-N, useful for debugging only — biases sample)
python run.py --limit 500
```

First full run: 30-60 min (rate-limited Alpaca fetch).
Cached runs: ~10 sec (bars in `data/bars/`, simulation re-runs in ~7 sec).

## How to interpret results

Print order in `print_report`: strategy × exit_rule grid, then conditional-entry trigger rates.

**Reading a row well:**
1. Check `n_trades` first. <100 → ignore. 100-300 → directional. 500+ → reliable.
2. Compare `sharpe_per_trade` across rows of the *same* strategy — that isolates exit effect.
3. Cross-check `profit_factor` vs `sharpe`. High PF + low sharpe = few huge wins offsetting many small losses (fragile).
4. **Suspicious pattern:** very high `win_rate_%` (>65) with low PF. Usually means a tiered/take-profit exit that books partial wins easily but lets remainders lose big. See `tiered_*` rules.

**Best combos discovered so far (as of last run):**
- `breakout_d2_high + tiered_0.5at10pct_trail2pct` — top sharpe (0.211), N=795
- `breakout_d2_high + hold_10d_then_trail_4pct` — close second (sharpe 0.210, PF 1.89), N=775
- `confirm_d3_open_v2 + hold_10d_then_atr_trail_3x_cap8pct` — strong all-around (sharpe 0.209, PF 1.97), N=447
- `confirm_d3_open_v2 + staged_8pct_be5pct_h10_trail4pct` — top profit factor (2.30, sharpe 0.206), N=459

## Known limitations

- **Survivorship bias:** Alpaca's tradable universe excludes delisted tickers. Real edge will be ~20% lower than reported. Fix requires Polygon/Norgate historical universe.
- **Market cap filter approximated:** Alpaca free tier has no historical shares-out. We use price+volume filters as a proxy. `USE_CAP_PROXY=False` in `screener.py` to skip entirely.
- **Stop fills are optimistic:** assumed fill at stop price, no gap-through penalty.
- **No intraday data:** EMA pullback / ORB entries need minute bars. Not implemented.
- **No news / catalyst overlay:** signal quality varies (earnings beat vs. random pump) — not separated.

## How to extend

### Add a new exit rule

1. Write a function in `backtest.py` with signature `(s: DataFrame, entry: Entry, ...) -> ExitFill | None`. Use `_iter_start(entry)` for the first eligible bar; respect `BACKTEST.max_hold_bars` and always return a fill at the cap close if not triggered earlier.
2. Add a config tuple to `BacktestConfig` listing the variants.
3. Wire it into `_all_exits` (one loop per config tuple).
4. Update `report.py:_exit_sort_key` if the rule name needs a custom sort bucket. Otherwise it falls through to bucket 5.
5. Re-run; the new exit will appear for every entry strategy.

### Add a new entry strategy

1. Append an `Entry(...)` to the list returned by `_compute_entries` in `backtest.py`.
2. Set `triggered=False` if the entry is conditional and the condition isn't met — the framework will report untriggered signals separately via `report.py:trigger_rates`.
3. Make sure `entry_bar_idx` points to the bar where the fill occurs (`i` = signal day, `i+1` = next day, etc.) and `include_entry_bar_in_iter=True` only if intraday OHLC of the entry bar should be considered for stop iteration (i.e., entry at open/intraday — not at close).
4. Re-run; the new entry will be evaluated against every exit rule automatically.

### Tune the screener

Edit `ScreenerFilter` in `config.py`. Looser filters → more signals but lower-quality. Currently set near the loose end (rel_vol 1.25, change 3.5%) to give 800+ signals over 5 years.

## File-by-file map

| File | Role |
|---|---|
| `config.py` | All tunable knobs (screener thresholds, exit parameters) |
| `universe.py` | Pull Alpaca tradable equity symbols |
| `fetch_bars.py` | Daily OHLCV fetch with batching + parquet cache + 429 retry |
| `screener.py` | Indicators + Finviz-equivalent filter → Day-1 signals |
| `backtest.py` | Per-signal entry computation + exit simulation |
| `report.py` | Aggregation, sorting, terminal/CSV output |
| `run.py` | CLI entrypoint, wires the 5 stages |
| `data/bars/` | Cached parquet OHLCV (one file per batch hash) |
| `results/` | signals.parquet, trades.csv, summary.csv |
| `scrape/` | **Part 2** — live daily screener → MQS → dashboard pipeline (see `scrape/README.md`) |
| `dashboard/` | **Part 2** — Vite static dashboard; reads `dashboard/public/data/latest.json` |
| `.github/workflows/screener.yml` | **Part 2** — daily cron: run pipeline, commit snapshot, deploy to Pages |

## Part 2 — Daily Screener Dashboard (separate from the backtest)

A standalone pipeline that scrapes the *live* Finviz momentum screener, scores
each hit with a Momentum Quality Score (MQS), classifies the news catalyst, and
publishes a dashboard. It does **not** reuse or trust the backtest's conclusions
— it's a fresh-perspective triage tool. Full docs in `scrape/README.md`.

Key facts for agents:
- finvizfinance `ticker_fundament()` is broken in 1.3.0 → `scrape/quote_page.py`
  parses the quote page directly. Don't reintroduce `ticker_fundament()`.
- `scrape/enrich.py` reuses the backtest's `fetch_bars.load_all_bars` for the
  ~120-day bar context (run peak/retrace, EMA10/20, up-streak, extension).
- `scrape/intraday.py` adds real closing strength (latest daily bar O/H/L) and
  volume persistence (hourly AM/PM split), keyed to the latest *closed* session.
  Uses the free IEX feed. Closing strength replaced the old EMA proxy in MQS.
- Streaks persist in `dashboard/public/data/screener_history.parquet` (committed,
  NOT under the gitignored `data/`).
- MQS weights + tier/extension thresholds are all tunable at the top of
  `scrape/mqs.py`. Pullback fails at >50% retrace; extension warns at >4 ATR.

## Conventions

- Don't add comments unless explaining a non-obvious *why* (see CLAUDE.md global instructions).
- Don't introduce abstractions ahead of need. The exit families are deliberately repetitive — easier to read than a generic framework.
- When a new exit/entry is added, *delete* losing strategies after the run confirms they're worse than current alternatives. Avoid bloated summary tables.
- Always use the venv: `source .venv/bin/activate`.
- Default Python is 3.9 (system) — code targets that.
