"""Live integration test for the yfinance market data provider.

Run: python tests/test_market_data.py
Requires network access; yfinance may print benign warnings.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_data import CURATED_COMPANIES, YFinanceProvider


def main() -> None:
    provider = YFinanceProvider()
    tickers = [company["ticker"] for company in CURATED_COMPANIES]
    assert len(tickers) == 12, f"expected 12 curated companies, got {len(tickers)}"

    # 1. All 12 curated tickers must return a positive real price.
    quotes = provider.get_quotes(tickers)
    assert set(quotes) == set(tickers), f"missing tickers: {set(tickers) - set(quotes)}"
    for ticker in tickers:
        price = quotes[ticker]
        print(f"{ticker:>10} -> {price}")
        assert isinstance(price, float), f"{ticker}: price is {type(price)}, not float"
        assert price > 0.0, f"{ticker}: price {price} is not positive"

    # 2. Hard-fault path: a nonsense ticker must raise, never default.
    try:
        provider.get_quotes(["ZZZZFAKE99"])
    except RuntimeError as exc:
        assert "ZZZZFAKE99" in str(exc), f"error does not name the ticker: {exc}"
        print(f"hard-fault OK: {exc}")
    else:
        raise AssertionError("get_quotes did not raise RuntimeError for a fake ticker")

    print("ALL MARKET DATA TESTS PASSED")


if __name__ == "__main__":
    main()
