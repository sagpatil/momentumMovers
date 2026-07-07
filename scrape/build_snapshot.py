"""Orchestrator: screener → enrich → catalyst → streaks → MQS → JSON.

Run daily (locally or via GitHub Actions). Writes:
  dashboard/public/data/latest.json        what the dashboard reads
  dashboard/public/data/<run_date>.json     dated archive snapshot
  data/screener_history.parquet             appended time series (streaks)

Designed to never hard-fail on a single bad ticker; partial data still produces
a usable snapshot.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from config import ROOT
from scrape import catalyst, history, mqs
from scrape.enrich import enrich
from scrape.screener_feed import fetch_screener

SNAPSHOT_DIR = ROOT / "dashboard" / "public" / "data"


def build(run_date: date | None = None, write: bool = True) -> dict:
    run_date = run_date or date.today()
    screener = fetch_screener()
    tickers = screener["ticker"].tolist()

    enriched = enrich(tickers)
    catalysts = catalyst.classify_catalysts(enriched)

    # Streaks need today in history first; append a minimal version, compute, then
    # we'll rewrite history with MQS attached at the end.
    base_rows = screener.to_dict("records")
    hist = history.append_today(base_rows, run_date)
    streaks = history.compute_streaks(hist, run_date)

    rows: list[dict] = []
    for rec_screen in base_rows:
        t = rec_screen["ticker"]
        e = enriched.get(t, {"ticker": t})
        cat = catalysts.get(t, {"strength": "none", "score": 0.15, "label": "?", "reason": ""})
        streak = streaks.get(t, 1)
        scored = mqs.score(e, streak=streak, catalyst_score=cat["score"])

        bars = e.get("bars") or {}
        rows.append({
            **rec_screen,
            "mqs": scored["mqs"],
            "tier": scored["tier"],
            "badges": scored["badges"],
            "extension": scored["extension"],
            "pullback_depth": scored["pullback_depth"],
            "retrace_pct": scored["retrace_pct"],
            "dist_above_ema10_atr": scored["dist_above_ema10_atr"],
            "up_streak": scored["up_streak"],
            "burst_age": scored["burst_age"],
            "burst_thrust_days": scored["burst_thrust_days"],
            "close_position": scored["close_position"],
            "vol_profile": scored["vol_profile"],
            "latest_bar": e.get("latest_bar"),
            "run_low": bars.get("run_low"),
            "run_high": bars.get("run_high"),
            "streak": streak,
            "components": scored["components"],
            "shs_float": e.get("shs_float"),
            "short_float_pct": e.get("short_float_pct"),
            "short_ratio": e.get("short_ratio"),
            "rel_volume": e.get("rel_volume"),
            "atr14": e.get("atr14"),
            "sma50_dist_pct": e.get("sma50_dist_pct"),
            "sma200_dist_pct": e.get("sma200_dist_pct"),
            "perf_week_pct": e.get("perf_week_pct"),
            "earnings": e.get("earnings"),
            "catalyst": cat,
            "news": e.get("news", [])[:5],
        })

    rows.sort(key=lambda r: r["mqs"], reverse=True)

    # Rewrite history with MQS now known (keeps the parquet's mqs column meaningful).
    history.append_today(rows, run_date)

    snapshot = {
        "run_date": run_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters_url": "cap_smallover,sh_avgvol_o500,sh_curvol_o1000,sh_price_o7,"
                       "sh_relvol_o1.5,ta_averagetruerange_o1,ta_change_u3,ta_sma200_pa",
        "n": len(rows),
        "weights": mqs.MQS_WEIGHTS,
        "rows": rows,
    }

    if write:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        (SNAPSHOT_DIR / "latest.json").write_text(json.dumps(snapshot, indent=2, default=str))
        (SNAPSHOT_DIR / f"{run_date.isoformat()}.json").write_text(
            json.dumps(snapshot, default=str)
        )
        print(f"wrote {len(rows)} rows -> {SNAPSHOT_DIR/'latest.json'}")
    return snapshot


if __name__ == "__main__":
    build()
