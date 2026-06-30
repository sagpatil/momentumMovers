# Momentum Movers — Part 2: Daily Screener Dashboard

A self-refreshing pipeline + static web dashboard, separate from the Phase-1
backtest. A GitHub Actions cron scrapes the live Finviz momentum screener,
enriches each hit with fundamentals + news, scores a **Momentum Quality Score
(MQS)**, classifies the catalyst, tracks multi-day "repeat offenders", and
publishes a glassmorphism dashboard to GitHub Pages.

> This is a **triage tool**, not a validated edge. MQS ranks how clean a setup
> *looks* on daily data; it does not claim positive expectancy. The Part-1
> backtest's conclusions are deliberately **not** assumed here.

## Pipeline

```
screener_feed.py  → live Finviz screener (your URL) → ~35 tickers
enrich.py         → per ticker: quote_page.py (float, short float, ATR, SMA dist),
                    ticker_news() headlines, ~120d Alpaca daily bars for run context,
                    + intraday.py (latest daily bar O/H/L + hourly volume profile)
intraday.py       → real closing strength (close-in-range) from latest daily bar;
                    volume persistence (AM vs PM) from hourly bars
catalyst.py       → keyword rules first, Claude LLM fallback for ambiguous headlines
history.py        → append today to screener_history.parquet → consecutive-day streaks
mqs.py            → MQS (0–100) + tier + risk badges
build_snapshot.py → orchestrates all of the above → dashboard/public/data/latest.json
dashboard/        → Vite static app renders latest.json
```

The Finviz `ticker_fundament()` method is broken in finvizfinance 1.3.0, so
`quote_page.py` parses the quote-page snapshot table directly (more robust, one
request per ticker).

## MQS components (weights in `mqs.py:MQS_WEIGHTS`)

| Component | Weight | Source |
|---|---|---|
| Closing strength | 0.22 | real close-in-range from latest daily bar (top of range = strong) |
| Volume | 0.18 | relative volume, nudged by intraday persistence (building vs front-loaded) |
| Float | 0.12 | `Shs Float` — low float = squeeze fuel |
| Short float | 0.13 | `Short Float %` |
| Catalyst | 0.20 | news classification (strong/weak/none) |
| Persistence | 0.15 | consecutive-day screener streak |

## Tiers & badges

**Tier** (primary archetype, from daily bars):
- **Day-1 Breakout** — first appearance, fresh up-move.
- **Pullback** — pulled back but held ≥50% of the breakout→peak run.
- **Continuation** — multi-day runner still trending up.
- **Reversal/Failed** — gave back >50% of the run (pullback floor broken; MQS halved).

**Badges** (stack on any tier):
- `⚠️ Extended` / `🔴 Very Extended` — close is >4 / >7 ATR above EMA10.
- `Climactic Vol` — huge rel-vol while extended (exhaustion risk).
- `Pullback: tight/healthy/deep` — held EMA10 / EMA20 / only the 50% floor.

**Known data seam:** the screener row is *intraday today*; the bar-derived tier
context ends at *prior close*. A fresh intraday breakout can read as
"Continuation" until end-of-day bars catch up. The dashboard says so.

## Run locally

```bash
source .venv/bin/activate
pip install -r scrape/requirements-scrape.txt
python -m scrape.build_snapshot          # writes dashboard/public/data/latest.json

cd dashboard && npm install && npm run dev   # view at the printed localhost URL
```

Individual stages are runnable for debugging:
`python -m scrape.screener_feed` · `python -m scrape.quote_page OUST` ·
`python -m scrape.enrich OUST IRDM` · `python -m scrape.catalyst`.

## Deploy (GitHub Actions → Pages)

`.github/workflows/screener.yml` runs 21:30 UTC on weekdays (or manually via
*workflow_dispatch*). It runs the pipeline, commits the snapshot + history back to
the repo, builds the dashboard, and deploys to Pages.

**One-time setup:**
1. Repo *Settings → Pages → Source = GitHub Actions*.
2. Repo *Settings → Secrets and variables → Actions* — add:
   - `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (required for the bar context).
   - `ANTHROPIC_API_KEY` (optional; enables the LLM catalyst fallback — rules-only without it).
3. The Vite `base` in `dashboard/vite.config.js` defaults to `/momentumMovers/`.
   For a different repo name set `VITE_BASE`, or `/` for a custom domain.

## Caveats

- **Scraping is unofficial** — Finviz DOM changes can break the parser; runs are
  best-effort and never hard-fail on a single bad ticker.
- **Intraday is keyed to the latest *closed* session** — closing strength and the
  AM/PM volume split come from the most recent closed daily/hourly bars. The CI
  cron runs post-close so this is the current day; in a mid-session run it reflects
  the prior close. Hourly volume uses the free IEX feed (single-venue, so absolute
  share volume is understated, but the AM-vs-PM *ratio* and close-in-range are sound).
- Float / short-float are missing for some names (Finviz shows `-`); MQS falls
  back to neutral component scores there.
