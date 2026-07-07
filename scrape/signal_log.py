"""Durable point-in-time signal log → signals.parquet + runs.parquet.

signals.parquet is the strategy's diary: one flat row per ticker per hit day,
capturing everything that cannot be reconstructed later (catalyst, float/short
interest, rel volume, and the MQS/tier as computed *that day*). Bars are always
re-fetchable; this file is not. Append-only, replace-by-date (idempotent reruns).

runs.parquet is provenance: one row per run recording the git SHA, MQS weights
and screener filters that produced that day's signals, so scores stay
interpretable as the code evolves.

Backfill from the dated dashboard JSON archives:
    python -m scrape.signal_log --backfill
"""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd

from config import ROOT

DATA_DIR = ROOT / "dashboard" / "public" / "data"
SIGNALS_PATH = DATA_DIR / "signals.parquet"
RUNS_PATH = DATA_DIR / "runs.parquet"

_COMPONENTS = ["closing_strength", "volume", "float", "short_float", "catalyst", "persistence"]


def _flatten(rows: list[dict], run_date: date) -> pd.DataFrame:
    out = []
    for r in rows:
        comps = r.get("components") or {}
        cat = r.get("catalyst") or {}
        out.append({
            "date": run_date.isoformat(),
            "ticker": r.get("ticker"),
            "price": r.get("price"),
            "change_pct": r.get("change_pct"),
            "volume": r.get("volume"),
            "rel_volume": r.get("rel_volume"),
            "mqs": r.get("mqs"),
            "tier": r.get("tier"),
            "badges": " | ".join(r.get("badges") or []),
            "extension": r.get("extension"),
            "pullback_depth": r.get("pullback_depth"),
            "burst_age": r.get("burst_age"),
            "burst_thrust_days": r.get("burst_thrust_days"),
            "up_streak": r.get("up_streak"),
            "streak": r.get("streak"),
            "retrace_pct": r.get("retrace_pct"),
            "dist_above_ema10_atr": r.get("dist_above_ema10_atr"),
            "close_position": r.get("close_position"),
            "run_low": r.get("run_low"),
            "run_high": r.get("run_high"),
            "ema10": r.get("ema10"),
            "ema20": r.get("ema20"),
            "atr14": r.get("atr14"),
            "atr14_bars": r.get("atr14_bars"),
            "shs_float": r.get("shs_float"),
            "short_float_pct": r.get("short_float_pct"),
            "short_ratio": r.get("short_ratio"),
            "sector": r.get("sector"),
            "industry": r.get("industry"),
            "market_cap": r.get("market_cap"),
            "catalyst_strength": cat.get("strength"),
            "catalyst_label": cat.get("label"),
            "catalyst_reason": cat.get("reason"),
            **{f"comp_{k}": comps.get(k) for k in _COMPONENTS},
        })
    return pd.DataFrame(out)


def _append_by_date(path: Path, df: pd.DataFrame, dates: set[str]) -> None:
    if path.exists():
        prior = pd.read_parquet(path)
        prior = prior[~prior["date"].isin(dates)]
        df = df if prior.empty else pd.concat([prior, df], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values(["date", "ticker" if "ticker" in df.columns else "date"]).to_parquet(
        path, index=False
    )


def _git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=ROOT, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def append_today(rows: list[dict], run_date: date) -> None:
    _append_by_date(SIGNALS_PATH, _flatten(rows, run_date), {run_date.isoformat()})


def append_run(run_date: date, weights: dict, filters_url: str, n_hits: int) -> None:
    df = pd.DataFrame([{
        "date": run_date.isoformat(),
        "git_sha": _git_sha(),
        "mqs_weights": json.dumps(weights),
        "filters": filters_url,
        "n_hits": n_hits,
    }])
    _append_by_date(RUNS_PATH, df, {run_date.isoformat()})


def backfill(snapshot_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Rebuild signals.parquet rows from the dated JSON archives (idempotent)."""
    frames, dates = [], set()
    for f in sorted(snapshot_dir.glob("20??-??-??.json")):
        snap = json.loads(f.read_text())
        d = date.fromisoformat(snap["run_date"])
        frames.append(_flatten(snap["rows"], d))
        dates.add(snap["run_date"])
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    _append_by_date(SIGNALS_PATH, df, dates)
    return df


if __name__ == "__main__":
    import sys

    if "--backfill" in sys.argv:
        df = backfill()
        print(f"backfilled {len(df)} signal rows over {df['date'].nunique()} days -> {SIGNALS_PATH}")
    else:
        print(__doc__)
