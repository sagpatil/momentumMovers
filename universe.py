"""Fetch the tradable US equity universe from Alpaca.

Filters to common-stock-like assets on NYSE/NASDAQ/AMEX, tradable, fractionable
flag ignored. Caches result to parquet daily.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, DATA_DIR


def fetch_universe(refresh: bool = False) -> pd.DataFrame:
    cache_path: Path = DATA_DIR / f"universe_{date.today().isoformat()}.parquet"
    if cache_path.exists() and not refresh:
        return pd.read_parquet(cache_path)

    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    assets = client.get_all_assets(
        GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
    )
    rows = [
        {
            "symbol": a.symbol,
            "name": a.name,
            "exchange": str(a.exchange),
            "tradable": a.tradable,
            "shortable": a.shortable,
            "marginable": a.marginable,
        }
        for a in assets
    ]
    df = pd.DataFrame(rows)

    # Keep tradable common-stock-like names on major exchanges.
    df = df[df["tradable"]]
    df = df[df["exchange"].str.contains("NYSE|NASDAQ|AMEX|ARCA", na=False)]
    # Drop tickers with non-equity suffixes that are usually warrants/units/preferreds.
    bad_suffix = r"\.(WS|WT|U|PR|PA|PB|PC|PD|PE|PF|PG|PH|PI|PJ|PK|PL|PM|PN|PO|PP|PQ|PR|PS|PT|PU|PV|PW|PX|PY|PZ)$"
    df = df[~df["symbol"].str.contains(bad_suffix, regex=True, na=False)]
    df = df[~df["symbol"].str.contains(r"[/\^]", regex=True, na=False)]
    df = df.reset_index(drop=True)

    df.to_parquet(cache_path, index=False)
    return df


if __name__ == "__main__":
    u = fetch_universe()
    print(f"Universe size: {len(u):,}")
    print(u.head(10).to_string())
