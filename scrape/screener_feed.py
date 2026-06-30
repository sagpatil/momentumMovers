"""Pull the live Finviz momentum screener.

Encodes the production screener URL:
  cap_smallover, sh_avgvol_o500, sh_curvol_o1000, sh_price_o7,
  sh_relvol_o1.5, ta_averagetruerange_o1, ta_change_u3, ta_sma200_pa
sorted by Change descending.

Returns a tidy DataFrame, one row per ticker currently passing the screen.
The Overview view gives identity + price/change/volume; per-ticker float,
short interest, ATR and trend distance come later from quote_page.py.
"""
from __future__ import annotations

import pandas as pd
from finvizfinance.screener.overview import Overview

# finvizfinance maps the URL filter codes to these human-readable option labels.
SCREENER_FILTERS = {
    "Market Cap.": "+Small (over $300mln)",   # cap_smallover
    "Average Volume": "Over 500K",            # sh_avgvol_o500
    "Current Volume": "Over 1M",              # sh_curvol_o1000
    "Price": "Over $7",                       # sh_price_o7
    "Relative Volume": "Over 1.5",            # sh_relvol_o1.5
    "Average True Range": "Over 1",           # ta_averagetruerange_o1
    "Change": "Up 3%",                        # ta_change_u3
    "200-Day Simple Moving Average": "Price above SMA200",  # ta_sma200_pa
}


def fetch_screener() -> pd.DataFrame:
    """Return the current screener hits as a DataFrame sorted by change desc.

    Columns: ticker, company, sector, industry, country, market_cap,
             price, change_pct, volume.
    `change_pct` and the raw Finviz `Change` are normalized to percent
    (Finviz returns 0.2868 for +28.68%).
    """
    view = Overview()
    view.set_filter(filters_dict=SCREENER_FILTERS)
    df = view.screener_view(order="Change", ascend=False, verbose=0)
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "ticker", "company", "sector", "industry", "country",
                "market_cap", "price", "change_pct", "volume",
            ]
        )

    df = df.rename(
        columns={
            "Ticker": "ticker",
            "Company": "company",
            "Sector": "sector",
            "Industry": "industry",
            "Country": "country",
            "Market Cap": "market_cap",
            "Price": "price",
            "Change": "change_pct",
            "Volume": "volume",
        }
    )
    df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce") * 100.0
    keep = [
        "ticker", "company", "sector", "industry", "country",
        "market_cap", "price", "change_pct", "volume",
    ]
    return df[[c for c in keep if c in df.columns]].reset_index(drop=True)


if __name__ == "__main__":
    out = fetch_screener()
    print(f"{len(out)} screener hits")
    print(out.to_string())
