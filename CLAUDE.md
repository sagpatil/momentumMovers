# CLAUDE.md

Working guidance for Claude Code in this repo. Architecture and how-to-run live
elsewhere — **`AGENTS.md`** for the Part 1 backtest, **`scrape/README.md`** for
the Part 2 pipeline (the more current of the two for Part 2). This file only
adds the conventions and gotchas that aren't obvious from the code.

## The two parts

- **Part 1 — backtest** (`backtest.py`, `screener.py`, `report.py`, `run.py`,
  `fetch_bars.py`, `universe.py`, `config.py`): a research backtest of the Finviz
  momentum screener. **Treat as frozen** — do not modify unless explicitly
  asked. Its conclusions are *not* assumed to be ground truth elsewhere.
  (Part 2 does reuse `fetch_bars.load_all_bars` and `config.ROOT`.)
- **Part 2 — live dashboard** (`scrape/`, `dashboard/`,
  `.github/workflows/screener.yml`): daily screener → enrich → MQS → static
  dashboard, plus a signal log and an on-demand outcomes evaluator. This is
  where active work happens.

## Always

- **Use the venv:** `source .venv/bin/activate`. Default Python is 3.9; code
  targets it.
- **Never commit secrets.** `.env` (Alpaca + Anthropic keys) is gitignored and
  must stay that way. Verify staged files before any commit.
- **Run the full flow locally** with `./scrape/run_local.sh` (`--dev` for hot
  reload, `--no-fetch` to skip the scrape). It reads `.env` directly — no GitHub
  secrets needed locally.
- **Commit/push only when asked.** End commit messages with the Co-Authored-By
  trailer. CI pushes daily snapshot commits, so pull/rebase before pushing.

## Data files (`dashboard/public/data/`, committed)

| File | What | Rule |
|---|---|---|
| `latest.json`, `<date>.json` | daily snapshot + dated archive | written by `build_snapshot.py` |
| `screener_history.parquet` | screener appearances → display streak | append-only, replace-by-date |
| `signals.parquet` | point-in-time signal diary (full row context) | **sacred** — append-only; backfill: `python -m scrape.signal_log --backfill` |
| `runs.parquet` | per-run provenance (git SHA, MQS weights, filters) | sacred, same pattern |
| `outcomes.parquet` | graded episodes (fwd returns, exits, max gain/drawdown, R) | **derived** — rebuild with `python -m scrape.evaluate` (CI runs it Friday evenings, report in the Actions job summary); never hand-edit, never a daily-pipeline dependency |

The root `.gitignore` anchors `/data/` (the bar cache) so it doesn't swallow
`dashboard/public/data/`. CI commits everything above except `outcomes.parquet`.

## Gotchas (already-solved; don't reintroduce)

- `finvizfinance.ticker_fundament()` is **broken** in 1.3.0 — use
  `scrape/quote_page.py` (direct quote-page parser). `ticker_news()` and the
  screener views still work.
- Alpaca bars/intraday use the **free IEX feed** (single venue). Absolute volume
  is understated *and even daily relative volume is unreliable* — measured
  0.6–1.1× on verified +5–10% breakout days. That's why burst detection in
  `scrape/enrich.py` has **no volume gate** (gain % + ATR range expansion only).
  Don't add one back. The AM/PM ratio and close-in-range remain sound.
- Bar-derived features key off the **latest closed session**, not literal today
  — a fresh intraday move shows up the next run.
- Persistence comes from **bar-derived bursts** (`burst_age` /
  `burst_thrust_days` in `scrape/enrich.py`), *not* screener appearances — a
  rest day drops a name off the Finviz screen and used to reset the streak
  mid-run, mislabeling day 5 of a move as Day-1. The screener streak
  (`history.py`) is display-only and the fallback when bars are missing.
- Finviz chart PNGs hotlink fine:
  `charts2.finviz.com/chart.ashx?t=<ticker>&ty=c&ta=1&p=d&s=l` with
  `referrerpolicy="no-referrer"`. Light theme only (`th=` ignored) — the drawer
  frames it on a white card.
- The Vite `base` is `/momentumMovers/` (for GitHub Pages). Local URLs include
  that path segment. Override with `VITE_BASE`.
- Evaluator grading (`scrape/evaluate.py`): entry is the **close** of the first
  signal date, so the entry day's own high/low is excluded from
  max_gain/max_drawdown (a.k.a. MFE/MAE); the
  retrace-50% exit only arms once peak > entry×1.02 (else a wick-sized span
  fires it spuriously). Early on, closed-trade stats skew negative — losers exit
  fast, winners stay open.

## Tuning knobs

- Screener filters: `config.py:ScreenerFilter` (Part 1) and
  `scrape/screener_feed.py:SCREENER_FILTERS` (Part 2, the live screen).
- MQS weights + tier/extension thresholds: top of `scrape/mqs.py`. Pullback
  fails at >50% retrace; extension warns at >4 ATR above EMA10. Keep tier and
  badge logic consistent — a name can't be both `Reversal/Failed` and a healthy
  pullback.
- Burst thrust/rest thresholds: top of `scrape/enrich.py`. Burst persistence
  ramp (full credit days 3–5, tapering after — late bursts are risk, not
  quality): `BURST_*` constants in `scrape/mqs.py`.
- Episode grouping + exit-rule horizons: constants at the top of
  `scrape/evaluate.py`.

## Conventions

- No comments except for a non-obvious *why*.
- Don't add abstractions ahead of need; the exit families / scrape stages are
  deliberately repetitive for readability.
- After a backtest change, delete strategies the run proves worse — keep summary
  tables lean (see AGENTS.md).
- Output the code directly. No lengthy explanations or step-by-step logic unless
  explicitly asked — keep it concise, 1–2 line items of what's going on.
