"""Aggregate trade results into strategy x exit_rule performance tables."""
from __future__ import annotations

import numpy as np
import pandas as pd
from tabulate import tabulate


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per (strategy, exit_rule) with the headline edge metrics."""
    df = trades[trades["triggered"]].dropna(subset=["ret_pct"])
    if df.empty:
        return pd.DataFrame()

    rows = []
    for (strat, exit_rule), g in df.groupby(["strategy", "exit_rule"]):
        wins = g[g["ret_pct"] > 0]["ret_pct"]
        losses = g[g["ret_pct"] <= 0]["ret_pct"]
        n = len(g)
        win_rate = len(wins) / n * 100.0 if n else 0.0
        avg_win = wins.mean() if len(wins) else 0.0
        avg_loss = losses.mean() if len(losses) else 0.0
        avg_ret = g["ret_pct"].mean()
        median_ret = g["ret_pct"].median()
        profit_factor = (
            wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else np.inf
        )
        sharpe = avg_ret / g["ret_pct"].std() if g["ret_pct"].std() else 0.0
        avg_bars_held = g["bars_held"].mean()

        rows.append(
            {
                "strategy": strat,
                "exit_rule": exit_rule,
                "n_trades": n,
                "avg_bars_held": round(avg_bars_held, 1),
                "win_rate_%": round(win_rate, 1),
                "avg_win_%": round(avg_win, 2),
                "avg_loss_%": round(avg_loss, 2),
                "avg_ret_%": round(avg_ret, 2),
                "median_ret_%": round(median_ret, 2),
                "profit_factor": round(profit_factor, 2),
                "expectancy_%": round(avg_ret, 2),
                "sharpe_per_trade": round(sharpe, 3),
            }
        )

    out = pd.DataFrame(rows)
    # Sort: strategy alpha, then put fixed horizons in numeric order, then trail/ema at end.
    out["_exit_sort"] = out["exit_rule"].map(_exit_sort_key)
    out = out.sort_values(["strategy", "_exit_sort"]).drop(columns="_exit_sort").reset_index(drop=True)
    return out


def _exit_sort_key(rule: str) -> tuple:
    if rule.startswith("hold_") and "_then_lower_low" in rule:
        bars_part = rule.removeprefix("hold_").removesuffix("d_then_lower_low")
        return (2, int(bars_part), 0)
    if rule.startswith("hold_") and "_then_trail_" in rule:
        bars_part = rule.removeprefix("hold_").split("d_then_trail_")[0]
        return (2, int(bars_part), 1)
    if rule.startswith("hold_") and "_then_atr_trail_" in rule:
        bars_part = rule.removeprefix("hold_").split("d_then_atr_trail_")[0]
        return (2, int(bars_part), 2)
    if rule.startswith("hold_") and "_break" in rule and "_then_" in rule:
        bars_part = rule.removeprefix("hold_").split("d_then_")[0]
        return (2, int(bars_part), 3)
    if rule.startswith("staged_"):
        return (2, 99, 4)
    if rule.startswith("tiered_"):
        return (2, 99, 5)
    if rule.startswith("donchian_"):
        return (2, 99, 6)
    if rule.startswith("hold_") and "_stop_" not in rule:
        return (0, int(rule.removeprefix("hold_").removesuffix("d")), 0)
    if rule.startswith("hold_") and "_stop_" in rule:
        # e.g. "hold_10d_stop_8pct" -> bucket 1, ordered by hold bars
        bars_part = rule.removeprefix("hold_").split("d_stop_")[0]
        return (1, int(bars_part), 0)
    if rule.startswith("atr_trail"):
        # e.g. "atr_trail_2x" -> ordered by multiplier
        mult_part = rule.removeprefix("atr_trail_").removesuffix("x")
        return (3, float(mult_part), 0)
    if rule.startswith("ema"):
        return (4, 0, 0)
    return (5, 0, 0)


def trigger_rates(trades: pd.DataFrame) -> pd.DataFrame:
    """How often each conditional entry fires (% of signals where triggered=True)."""
    conditional = (
        "breakout_d2_high",
        "confirm_d3_open",
        "confirm_d3_open_v2",
        "confirm_d3_open_v3",
    )
    rows = []
    for strat in conditional:
        sub = trades[trades["strategy"] == strat]
        if sub.empty:
            continue
        sigs = sub.drop_duplicates(["symbol", "signal_date"])
        # A signal is "triggered" if any of its rows for this strategy have triggered=True.
        fired = (
            sub[sub["triggered"]]
            .drop_duplicates(["symbol", "signal_date"])
            .shape[0]
        )
        rows.append(
            {
                "strategy": strat,
                "signals_total": len(sigs),
                "signals_triggered": fired,
                "trigger_rate_%": round(fired / len(sigs) * 100.0, 1) if len(sigs) else 0.0,
            }
        )
    return pd.DataFrame(rows)


def print_report(trades: pd.DataFrame) -> None:
    summary = summarize(trades)
    triggers = trigger_rates(trades)
    print("\n=== Strategy x Exit Performance ===")
    print(tabulate(summary, headers="keys", tablefmt="github", showindex=False))
    if not triggers.empty:
        print("\n=== Conditional Entry Trigger Rates ===")
        print(tabulate(triggers, headers="keys", tablefmt="github", showindex=False))


def save_report(trades: pd.DataFrame, results_dir) -> None:
    summary = summarize(trades)
    trades.to_csv(results_dir / "trades.csv", index=False)
    summary.to_csv(results_dir / "summary.csv", index=False)
