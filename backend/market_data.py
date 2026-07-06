"""Live market data for the ChaosNet "Black Swan" market twin.

Provides real quotes via yfinance and the curated seed list of real
companies used to populate the `companies` table (see models.Company).

HARD-FAULT RULE: prices are never fabricated or defaulted. If any
requested ticker cannot be resolved to a positive price, get_quotes
raises RuntimeError naming the missing tickers.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Protocol

import yfinance as yf

logger = logging.getLogger("chaosnet.market_data")


class MarketDataProvider(Protocol):
    """Anything that can turn tickers into real last prices."""

    def get_quotes(self, tickers: list[str]) -> dict[str, float]: ...


# Real-market index strip for the dashboard top bar. Yahoo symbols.
INDEX_SYMBOLS: list[dict[str, str]] = [
    {"symbol": "^GSPC", "name": "S&P 500"},
    {"symbol": "^IXIC", "name": "NASDAQ"},
    {"symbol": "^DJI", "name": "DOW JONES"},
    {"symbol": "^FTSE", "name": "FTSE 100"},
    {"symbol": "^N225", "name": "NIKKEI 225"},
]

_INDEX_CACHE_TTL_SECONDS: float = 60.0
_index_cache: dict[str, Any] = {"ts": 0.0, "data": []}


def get_indices() -> list[dict[str, Any]]:
    """Return the real-market index strip, cached for a minute.

    Best-effort by design: a provider failure logs loudly and returns the
    last good cache (or []). The frontend renders "—" for an empty strip —
    values are never fabricated. Each entry:
    {symbol, name, value, change, change_pct, sparkline: [floats]}.
    """
    now = time.monotonic()
    if _index_cache["data"] and now - _index_cache["ts"] < _INDEX_CACHE_TTL_SECONDS:
        return _index_cache["data"]

    symbols = [entry["symbol"] for entry in INDEX_SYMBOLS]
    try:
        frame = yf.download(
            tickers=symbols,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception as exc:  # network / provider hiccup — non-fatal refresh
        logger.warning("index refresh failed: %s", exc)
        return _index_cache["data"]

    result: list[dict[str, Any]] = []
    for entry in INDEX_SYMBOLS:
        symbol = entry["symbol"]
        try:
            closes = frame[symbol]["Close"].dropna() if len(symbols) > 1 else frame["Close"].dropna()
            values = [float(v) for v in closes.tolist() if v == v and v > 0]
        except (KeyError, IndexError, TypeError):
            values = []
        if len(values) < 2:
            continue  # skip a symbol we can't resolve; never fabricate it
        value = values[-1]
        prev = values[-2]
        change = value - prev
        change_pct = (change / prev * 100.0) if prev else 0.0
        result.append(
            {
                "symbol": symbol,
                "name": entry["name"],
                "value": round(value, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "sparkline": [round(v, 2) for v in values[-30:]],
            }
        )

    if result:
        _index_cache["ts"] = now
        _index_cache["data"] = result
    return result or _index_cache["data"]


class YFinanceProvider:
    """MarketDataProvider backed by Yahoo Finance via the yfinance package."""

    def get_quotes(self, tickers: list[str]) -> dict[str, float]:
        """Fetch the last close for every ticker in one batch download.

        Raises RuntimeError if any ticker comes back without a positive price.
        """
        if not tickers:
            return {}

        frame = yf.download(
            tickers=tickers,
            period="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )

        quotes: dict[str, float] = {}
        missing: list[str] = []
        for ticker in tickers:
            price: float | None = None
            if frame is not None and not frame.empty:
                try:
                    if len(tickers) == 1:
                        series = frame["Close"]
                    else:
                        series = frame[ticker]["Close"]
                    valid = series.dropna()
                    if not valid.empty:
                        price = float(valid.iloc[-1])
                except (KeyError, IndexError):
                    price = None
            if price is None or math.isnan(price) or price <= 0.0:
                missing.append(ticker)
            else:
                quotes[ticker] = price

        if missing:
            raise RuntimeError(
                "No positive market price returned for ticker(s): "
                + ", ".join(missing)
                + ". Refusing to fabricate prices."
            )
        return quotes


# Editable seed list. Keys match models.Company columns; anchor/current
# prices are filled at seed time from a MarketDataProvider, never here.
CURATED_COMPANIES: list[dict] = [
    {
        "ticker": "AAPL",
        "name": "Apple",
        "sector": "Technology",
        "country": "United States",
        "city": "Cupertino",
        "lat": 37.3349,
        "lon": -122.0090,
        "description": "Apple designs the iPhone, Mac, and services ecosystem from its Apple Park headquarters in Cupertino.",
        "shares_outstanding": 14_800_000_000,
    },
    {
        "ticker": "MSFT",
        "name": "Microsoft",
        "sector": "Technology",
        "country": "United States",
        "city": "Redmond",
        "lat": 47.6396,
        "lon": -122.1283,
        "description": "Microsoft builds Windows, Office, and the Azure cloud platform from its Redmond campus.",
        "shares_outstanding": 7_430_000_000,
    },
    {
        "ticker": "NVDA",
        "name": "NVIDIA",
        "sector": "Technology",
        "country": "United States",
        "city": "Santa Clara",
        "lat": 37.3705,
        "lon": -121.9628,
        "description": "NVIDIA designs the GPUs and AI accelerators that power modern datacenter and gaming workloads.",
        "shares_outstanding": 24_400_000_000,
    },
    {
        "ticker": "TSLA",
        "name": "Tesla",
        "sector": "Automotive",
        "country": "United States",
        "city": "Austin",
        "lat": 30.2210,
        "lon": -97.6170,
        "description": "Tesla manufactures electric vehicles and energy storage systems, headquartered at Gigafactory Texas in Austin.",
        "shares_outstanding": 3_220_000_000,
    },
    {
        "ticker": "JPM",
        "name": "JPMorgan Chase",
        "sector": "Financials",
        "country": "United States",
        "city": "New York",
        "lat": 40.7546,
        "lon": -73.9754,
        "description": "JPMorgan Chase is the largest US bank by assets, headquartered on Madison Avenue in Manhattan.",
        "shares_outstanding": 2_780_000_000,
    },
    {
        "ticker": "2222.SR",
        "name": "Saudi Aramco",
        "sector": "Energy",
        "country": "Saudi Arabia",
        "city": "Dhahran",
        "lat": 26.3260,
        "lon": 50.1150,
        "description": "Saudi Aramco is the world's largest oil producer, operating from Dhahran in Saudi Arabia's Eastern Province.",
        "shares_outstanding": 242_000_000_000,
    },
    {
        "ticker": "TSM",
        "name": "TSMC",
        "sector": "Technology",
        "country": "Taiwan",
        "city": "Hsinchu",
        "lat": 24.7736,
        "lon": 121.0025,
        "description": "Taiwan Semiconductor Manufacturing Company fabricates the majority of the world's advanced chips from Hsinchu Science Park.",
        "shares_outstanding": 5_190_000_000,
    },
    {
        "ticker": "7203.T",
        "name": "Toyota",
        "sector": "Automotive",
        "country": "Japan",
        "city": "Toyota City",
        "lat": 35.0527,
        "lon": 137.1568,
        "description": "Toyota Motor Corporation is the world's largest automaker by volume, based in Toyota City, Aichi Prefecture.",
        "shares_outstanding": 13_100_000_000,
    },
    {
        "ticker": "SAP.DE",
        "name": "SAP",
        "sector": "Technology",
        "country": "Germany",
        "city": "Walldorf",
        "lat": 49.2939,
        "lon": 8.6412,
        "description": "SAP is Europe's largest software company, supplying enterprise resource planning systems from Walldorf, Germany.",
        "shares_outstanding": 1_170_000_000,
    },
    {
        "ticker": "NESN.SW",
        "name": "Nestle",
        "sector": "Consumer",
        "country": "Switzerland",
        "city": "Vevey",
        "lat": 46.4614,
        "lon": 6.8420,
        "description": "Nestle is the world's largest food and beverage company, headquartered on the shore of Lake Geneva in Vevey.",
        "shares_outstanding": 2_590_000_000,
    },
    {
        "ticker": "005930.KS",
        "name": "Samsung Electronics",
        "sector": "Technology",
        "country": "South Korea",
        "city": "Suwon",
        "lat": 37.2497,
        "lon": 127.0518,
        "description": "Samsung Electronics leads the global memory chip and smartphone markets from Samsung Digital City in Suwon.",
        "shares_outstanding": 5_920_000_000,
    },
    {
        "ticker": "9988.HK",
        "name": "Alibaba",
        "sector": "Technology",
        "country": "China",
        "city": "Hangzhou",
        "lat": 30.2807,
        "lon": 120.0250,
        "description": "Alibaba Group operates China's largest e-commerce and cloud computing platforms from its Xixi campus in Hangzhou.",
        "shares_outstanding": 19_100_000_000,
    },
]
