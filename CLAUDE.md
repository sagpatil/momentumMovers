# CLAUDE.md

Working guidance for Claude Code in this repo. **`AGENTS.md` is the source of
truth** for architecture, the pipeline, core concepts, and how-to-run — read it
first. This file only adds the conventions and gotchas that aren't obvious from
the code.

## The two parts

- **Part 1 — backtest** (`backtest.py`, `screener.py`, `report.py`, `run.py`,
  `fetch_bars.py`, `universe.py`, `config.py`): a research backtest of the Finviz
  momentum screener. **Treat as frozen** — do not modify it unless explicitly
  asked. Its conclusions are *not* assumed to be ground truth elsewhere.
- **Part 2 — live dashboard** (`scrape/`, `dashboard/`, the workflow): a daily
  screener → MQS → static dashboard pipeline. This is where active work happens.
  Full docs: `scrape/README.md`.

## Always

- **Use the venv:** `source .venv/bin/activate`. Default Python is 3.9; code
  targets it.
- **Never commit secrets.** `.env` (Alpaca + Anthropic keys) is gitignored and
  must stay that way. Verify staged files before any commit.
- **Run the full flow locally** with `./scrape/run_local.sh` (`--dev` for hot
  reload, `--no-fetch` to skip the scrape). It reads `.env` directly — no GitHub
  secrets needed locally.
- **Commit/push only when asked.** End commit messages with the Co-Authored-By
  trailer.

## Gotchas (already-solved; don't reintroduce)

- `finvizfinance.ticker_fundament()` is **broken** in 1.3.0 — use
  `scrape/quote_page.py` (direct quote-page parser). `ticker_news()` and the
  screener views still work.
- `scrape/intraday.py` uses the **free IEX feed**: single-venue, so absolute
  hourly volume is understated. The AM/PM *ratio* and close-in-range are sound —
  rely on those, not absolute intraday volume. Even daily *relative* volume from
  IEX is unreliable (measured 0.6–1.1× on verified +5–10% breakout days), which
  is why burst detection in `scrape/enrich.py` deliberately has **no volume
  gate** — gain % + ATR range expansion only. Don't add one back.
- Intraday features key off the **latest closed session**, not literal today.
- Persistence comes from **bar-derived bursts** (`burst_age`/`burst_thrust_days`
  in `scrape/enrich.py`), not screener appearances — a rest day drops a name off
  the Finviz screen and used to reset the streak mid-run. The screener streak
  (`history.py`) is display-only and the fallback when bars are missing.
- Finviz chart PNGs hotlink fine:
  `charts2.finviz.com/chart.ashx?t=<ticker>&ty=c&ta=1&p=d&s=l` with
  `referrerpolicy="no-referrer"`. Light theme only (`th=` is ignored) — the
  drawer frames it on a white card.
- Screener-history streaks persist in
  `dashboard/public/data/screener_history.parquet` (committed) — **not** under
  the gitignored `/data/` bar cache. The root `.gitignore` anchors `/data/` so it
  doesn't swallow `dashboard/public/data/`.
- The Vite `base` is `/momentumMovers/` (for GitHub Pages). Local URLs include
  that path segment. Override with `VITE_BASE`.

## Tuning knobs

- Screener filters: `config.py:ScreenerFilter` (Part 1) and
  `scrape/screener_feed.py:SCREENER_FILTERS` (Part 2, the live screen).
- MQS weights + tier/extension thresholds: top of `scrape/mqs.py`. Pullback fails
  at >50% retrace; extension warns at >4 ATR above EMA10. Keep tier and badge
  logic consistent — a name can't be both `Reversal/Failed` and a healthy
  pullback.
- Burst (thrust/rest) thresholds: top of `scrape/enrich.py`. Burst persistence
  ramp (full credit days 3–5, tapering after — late bursts are risk, not
  quality): `BURST_*` constants in `scrape/mqs.py`.

## Conventions

- No comments except for a non-obvious *why*.
- Don't add abstractions ahead of need; the exit families / scrape stages are
  deliberately repetitive for readability.
- After a backtest change, delete strategies the run proves worse — keep summary
  tables lean (see AGENTS.md).

* Output the code directly. 
* Do NOT provide lengthy explanations or step-by-step logic unless explicitly asked. keep it concise with 1 -2 line items of whats going on
