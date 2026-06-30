"""Parse the Finviz quote page snapshot table directly.

finvizfinance 1.3.0's `ticker_fundament()` is broken against the current Finviz
DOM (raises AttributeError). The snapshot table itself is stable and simple, so
we parse it ourselves with requests + BeautifulSoup. One request per ticker
returns float, short interest, ATR, trend distance and recent performance —
everything the Momentum Quality Score needs that the Overview screener omits.
"""
from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36"
    )
}
_QUOTE_URL = "https://finviz.com/quote.ashx?t={ticker}&p=d"


def _to_float(raw: str | None) -> float | None:
    """Parse Finviz numeric strings: '59.64M', '8.70%', '10,080,715', '-', '3.89'."""
    if raw is None:
        return None
    s = raw.strip().replace(",", "").rstrip("%")
    if s in ("", "-", "--"):
        return None
    mult = 1.0
    if s and s[-1] in "KMBT":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[s[-1]]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _raw_pairs(soup: BeautifulSoup) -> dict[str, str]:
    cells = soup.select("table.snapshot-table2 td")
    pairs: dict[str, str] = {}
    for i in range(0, len(cells) - 1, 2):
        pairs[cells[i].get_text(strip=True)] = cells[i + 1].get_text(strip=True)
    return pairs


def fetch_quote(ticker: str, session: requests.Session | None = None) -> dict:
    """Return the parsed quote-page fields for one ticker.

    All numeric fields may be None if Finviz reports '-' (common for biotechs
    with no float/short data). Callers must handle None.

    Keys: shs_float, short_float_pct, short_ratio, avg_volume, rel_volume,
          atr14, sma20_dist_pct, sma50_dist_pct, sma200_dist_pct, change_pct,
          price, prev_close, perf_week_pct, perf_month_pct, earnings.
    """
    sess = session or requests.Session()
    url = _QUOTE_URL.format(ticker=ticker)
    resp = sess.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    p = _raw_pairs(BeautifulSoup(resp.text, "lxml"))

    return {
        "ticker": ticker,
        "shs_float": _to_float(p.get("Shs Float")),
        "short_float_pct": _to_float(p.get("Short Float")),
        "short_ratio": _to_float(p.get("Short Ratio")),
        "avg_volume": _to_float(p.get("Avg Volume")),
        "rel_volume": _to_float(p.get("Rel Volume")),
        "atr14": _to_float(p.get("ATR (14)") or p.get("ATR")),
        "sma20_dist_pct": _to_float(p.get("SMA20")),
        "sma50_dist_pct": _to_float(p.get("SMA50")),
        "sma200_dist_pct": _to_float(p.get("SMA200")),
        "change_pct": _to_float(p.get("Change")),
        "price": _to_float(p.get("Price")),
        "prev_close": _to_float(p.get("Prev Close")),
        "perf_week_pct": _to_float(p.get("Perf Week")),
        "perf_month_pct": _to_float(p.get("Perf Month")),
        "earnings": (p.get("Earnings") or "").strip() or None,
    }


def fetch_quotes(tickers: list[str], sleep: float = 0.5) -> dict[str, dict]:
    """Fetch quote pages for many tickers with a polite delay. Failures map to {}."""
    sess = requests.Session()
    out: dict[str, dict] = {}
    for i, t in enumerate(tickers):
        try:
            out[t] = fetch_quote(t, session=sess)
        except Exception as e:  # noqa: BLE001 — one bad ticker shouldn't kill the run
            out[t] = {"ticker": t, "_error": str(e)}
        if i < len(tickers) - 1:
            time.sleep(sleep)
    return out


if __name__ == "__main__":
    import json
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "OUST"
    print(json.dumps(fetch_quote(sym), indent=2))
