"""Momentum Quality Score (MQS), tier classification, and risk badges.

This is a *triage* score, not a validated edge. It ranks today's screener hits by
how clean the momentum setup looks on daily data. Everything is transparent: each
component returns its raw inputs so the dashboard can explain the number.

Components (each normalized 0..1, then weighted):
  closing_strength  close position in the day's range (proxy from change vs prev close
                    + intraday range when available)  — wick / fade detector
  volume            relative volume, rewarded up to a sane cap
  float             low float = squeeze fuel (small Shs Float scores higher)
  short_float       high short interest = squeeze fuel
  catalyst          set later by catalyst.py (strong news > weak/none)
  persistence       multi-day screener appearances (streak) set later by history.py

Tier (primary archetype) is independent of the risk badges:
  Day-1 Breakout    first appearance, strong close, high rel-vol
  Pullback          pulled back but held >= 50% of the breakout->peak run
  Continuation      multi-day runner still trending up, not yet over-extended
  Reversal/Failed   gave back > 50% of the run (broke the pullback floor)

Badges (stack on any tier):
  Extended          (close - ema10) / atr14  > 4    (>7 = Very Extended)
  Climactic Vol     huge rel-vol + big upper wick (exhaustion)
  Pullback depth    Tight (>EMA10) / Healthy (>EMA20) / Deep (held 50% but lost EMA20)
"""
from __future__ import annotations

# ------------------------------- tunables ----------------------------------

MQS_WEIGHTS = {
    "closing_strength": 0.22,
    "volume": 0.18,
    "float": 0.12,
    "short_float": 0.13,
    "catalyst": 0.20,
    "persistence": 0.15,
}

# Pullback floor: above this % retrace of the run it's still a pullback; beyond it
# the breakout has failed.
PULLBACK_FAIL_RETRACE_PCT = 50.0

# Extension thresholds in ATRs above EMA10.
EXT_WARN_ATR = 4.0
EXT_DANGER_ATR = 7.0

# Float buckets (shares). Below LOW_FLOAT = powder keg.
LOW_FLOAT = 20_000_000
HIGH_FLOAT = 150_000_000


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ----------------------------- sub-scores -----------------------------------


def _closing_strength_score(rec: dict) -> float:
    """Where the close sits in the latest daily bar's range (the "relative close").

    close_position: 1.0 = closed at the high (strong), ~0.5 = mid, low = faded
    with a big upper wick. Computed from real O/H/L by intraday.latest_daily_bars.
    Falls back to an EMA10-distance proxy only if the daily bar is unavailable.
    """
    bar = rec.get("latest_bar") or {}
    cp = bar.get("close_position")
    if cp is not None:
        # Reward closing in the top of the range; a close below mid-range is weak.
        # cp 0.5 -> 0.5, cp 0.9+ -> ~1.0, cp 0.3 -> ~0.3.
        return _clamp(cp)
    # Fallback proxy (no daily bar): closeness above EMA10 in a healthy band.
    dist = (rec.get("bars") or {}).get("dist_above_ema10_atr")
    if dist is None:
        return 0.5
    return _clamp(1.0 - abs(dist - 1.5) / 3.0)


def _volume_score(rec: dict) -> float:
    """Relative volume, nudged by intraday persistence: a 'building' afternoon
    profile is rewarded, a 'front_loaded' gap-and-fade is penalized."""
    rv = rec.get("rel_volume")
    base = 0.4 if rv is None else _clamp((rv - 1.5) / 3.5 + 0.3)

    vp = rec.get("vol_profile") or {}
    persistence = vp.get("persistence")
    if persistence == "building":
        base = _clamp(base + 0.15)
    elif persistence == "front_loaded":
        base = _clamp(base - 0.15)
    return base


def _float_score(rec: dict) -> float:
    f = rec.get("shs_float")
    if f is None:
        return 0.5
    if f <= LOW_FLOAT:
        return 1.0
    if f >= HIGH_FLOAT:
        return 0.1
    # linear between
    return _clamp(1.0 - (f - LOW_FLOAT) / (HIGH_FLOAT - LOW_FLOAT))


def _short_float_score(rec: dict) -> float:
    sf = rec.get("short_float_pct")
    if sf is None:
        return 0.4
    # 5% -> 0.25, 15% -> 0.75, 20%+ -> 1.0
    return _clamp(sf / 20.0)


# ----------------------------- tier + badges --------------------------------


def classify(rec: dict, streak: int) -> dict:
    """Return tier, badges, and the raw fields behind each decision."""
    bars = rec.get("bars") or {}
    retrace = bars.get("retrace_pct")
    dist = bars.get("dist_above_ema10_atr")
    above_ema10 = bars.get("above_ema10")
    above_ema20 = bars.get("above_ema20")
    up_streak = bars.get("up_streak", 0)
    rv = rec.get("rel_volume")

    badges: list[str] = []

    # Extension badge (orthogonal to tier).
    extension = None
    if dist is not None:
        if dist > EXT_DANGER_ATR:
            extension = "very_extended"
            badges.append("🔴 Very Extended")
        elif dist > EXT_WARN_ATR:
            extension = "extended"
            badges.append("⚠️ Extended")

    # Climactic volume: huge rel-vol while already extended = exhaustion risk.
    if rv is not None and rv >= 4.0 and dist is not None and dist > EXT_WARN_ATR:
        badges.append("Climactic Vol")

    # Intraday volume profile (from hourly bars).
    vp = rec.get("vol_profile") or {}
    if vp.get("persistence") == "building":
        badges.append("Vol building")
    elif vp.get("persistence") == "front_loaded":
        badges.append("Vol fading")

    # Closing-strength badges from the real daily bar.
    bar = rec.get("latest_bar") or {}
    cp = bar.get("close_position")
    if cp is not None:
        if cp >= 0.9:
            badges.append("Closed at HOD")
        elif cp < 0.4:
            badges.append("Upper wick / fade")

    # Pullback depth descriptor.
    pullback_depth = None
    if retrace is not None and retrace > 5.0:  # meaningfully off the highs
        if above_ema10:
            pullback_depth = "tight"
        elif above_ema20:
            pullback_depth = "healthy"
        else:
            pullback_depth = "deep"
        badges.append(f"Pullback: {pullback_depth}")

    # Primary tier.
    if retrace is not None and retrace > PULLBACK_FAIL_RETRACE_PCT:
        tier = "Reversal/Failed"
    elif retrace is not None and retrace > 5.0:
        tier = "Pullback"
    elif streak <= 1 and up_streak <= 2:
        tier = "Day-1 Breakout"
    else:
        tier = "Continuation"

    return {
        "tier": tier,
        "badges": badges,
        "extension": extension,
        "pullback_depth": pullback_depth,
        "retrace_pct": retrace,
        "dist_above_ema10_atr": dist,
        "up_streak": up_streak,
        "close_position": cp,
        "vol_profile": vp or None,
    }


# ------------------------------- top-level ----------------------------------


def score(rec: dict, streak: int = 1, catalyst_score: float | None = None) -> dict:
    """Compute the full MQS for one enriched record.

    `catalyst_score` (0..1) comes from catalyst.py; `streak` from history.py.
    Returns the 0..100 score, the component breakdown, tier and badges.
    """
    comps = {
        "closing_strength": _closing_strength_score(rec),
        "volume": _volume_score(rec),
        "float": _float_score(rec),
        "short_float": _short_float_score(rec),
        "catalyst": catalyst_score if catalyst_score is not None else 0.4,
        "persistence": _clamp((streak - 1) / 4.0),  # 1 day ->0, 5+ days ->1
    }
    mqs = sum(comps[k] * MQS_WEIGHTS[k] for k in MQS_WEIGHTS) * 100.0
    cls = classify(rec, streak)

    # Failed breakouts are de-prioritized regardless of other strengths.
    if cls["tier"] == "Reversal/Failed":
        mqs *= 0.5

    return {
        "mqs": round(mqs, 1),
        "components": {k: round(v, 3) for k, v in comps.items()},
        "weights": MQS_WEIGHTS,
        **cls,
    }
