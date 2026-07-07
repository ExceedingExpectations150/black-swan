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

    def _fetch(self, tickers: list[str]) -> tuple[dict[str, float], list[str]]:
        """Batch-download last closes. Returns (resolved quotes, missing)."""
        if not tickers:
            return {}, []

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
        return quotes, missing

    def get_quotes(self, tickers: list[str]) -> dict[str, float]:
        """Fetch the last close for every ticker in one batch download.

        Raises RuntimeError if any ticker comes back without a positive price.
        """
        quotes, missing = self._fetch(tickers)
        if missing:
            raise RuntimeError(
                "No positive market price returned for ticker(s): "
                + ", ".join(missing)
                + ". Refusing to fabricate prices."
            )
        return quotes

    def get_quotes_available(self, tickers: list[str]) -> dict[str, float]:
        """Fetch quotes, returning only the tickers that resolved.

        For seeding a large company list: an individual delisted/renamed
        symbol is skipped (never fabricated), but a total failure (zero
        resolved) still raises so the caller can hard-fault.
        """
        quotes, missing = self._fetch(tickers)
        if missing:
            logger.warning("no price for %d ticker(s), skipping: %s", len(missing), ", ".join(missing))
        if not quotes:
            raise RuntimeError(
                "Market data provider returned no prices at all — refusing to "
                "seed a fabricated market."
            )
        return quotes


# Editable seed list — ~50 global mega-caps across sectors and geographies.
# Keys match models.Company columns; anchor/current prices are filled at seed
# time from a MarketDataProvider, never here. Individual symbols that fail to
# resolve at seed time are skipped (see YFinanceProvider.get_quotes_available),
# so a delisted/renamed ticker won't block the whole seed.
def _co(ticker, name, sector, country, city, lat, lon, shares, description):
    return {
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "country": country,
        "city": city,
        "lat": lat,
        "lon": lon,
        "shares_outstanding": shares,
        "description": description,
    }


CURATED_COMPANIES: list[dict] = [
    # --- United States: technology ---
    _co("AAPL", "Apple", "Technology", "United States", "Cupertino", 37.3349, -122.0090, 14_800_000_000, "Apple designs the iPhone, Mac, and services ecosystem from Apple Park in Cupertino."),
    _co("MSFT", "Microsoft", "Technology", "United States", "Redmond", 47.6396, -122.1283, 7_430_000_000, "Microsoft builds Windows, Office, and the Azure cloud platform from its Redmond campus."),
    _co("NVDA", "NVIDIA", "Technology", "United States", "Santa Clara", 37.3705, -121.9628, 24_400_000_000, "NVIDIA designs the GPUs and AI accelerators that power datacenter and gaming workloads."),
    _co("GOOGL", "Alphabet", "Technology", "United States", "Mountain View", 37.4220, -122.0841, 12_300_000_000, "Alphabet is the parent of Google Search, YouTube, Android, and the Google Cloud platform."),
    _co("META", "Meta Platforms", "Technology", "United States", "Menlo Park", 37.4848, -122.1484, 2_550_000_000, "Meta operates Facebook, Instagram, and WhatsApp and leads consumer mixed-reality hardware."),
    _co("AVGO", "Broadcom", "Technology", "United States", "Palo Alto", 37.4419, -122.1430, 4_700_000_000, "Broadcom supplies semiconductors and infrastructure software across networking and storage."),
    _co("ORCL", "Oracle", "Technology", "United States", "Austin", 30.2672, -97.7431, 2_750_000_000, "Oracle provides enterprise databases, applications, and a growing cloud infrastructure business."),
    _co("AMD", "AMD", "Technology", "United States", "Santa Clara", 37.3861, -121.9631, 1_620_000_000, "Advanced Micro Devices designs CPUs and GPUs competing across PC, datacenter, and gaming markets."),
    _co("CRM", "Salesforce", "Technology", "United States", "San Francisco", 37.7897, -122.3972, 960_000_000, "Salesforce is the leading cloud customer-relationship-management software provider."),
    _co("ADBE", "Adobe", "Technology", "United States", "San Jose", 37.3308, -121.8932, 440_000_000, "Adobe makes Creative Cloud, Document Cloud, and digital-experience marketing software."),
    _co("CSCO", "Cisco", "Technology", "United States", "San Jose", 37.4110, -121.9500, 4_000_000_000, "Cisco Systems builds networking hardware, security, and collaboration software."),
    _co("INTC", "Intel", "Technology", "United States", "Santa Clara", 37.3880, -121.9640, 4_300_000_000, "Intel designs and manufactures x86 processors and is building a foundry business."),
    _co("IBM", "IBM", "Technology", "United States", "Armonk", 41.1090, -73.7202, 920_000_000, "IBM provides hybrid-cloud software, consulting, and enterprise infrastructure."),
    _co("QCOM", "Qualcomm", "Technology", "United States", "San Diego", 32.8951, -117.1960, 1_110_000_000, "Qualcomm designs mobile chipsets and licenses foundational wireless patents."),
    _co("NFLX", "Netflix", "Technology", "United States", "Los Gatos", 37.2358, -121.9624, 430_000_000, "Netflix is the largest subscription streaming-entertainment service worldwide."),
    # --- United States: other sectors ---
    _co("AMZN", "Amazon", "Consumer", "United States", "Seattle", 47.6062, -122.3321, 10_500_000_000, "Amazon runs the largest Western e-commerce marketplace and the AWS cloud platform."),
    _co("TSLA", "Tesla", "Automotive", "United States", "Austin", 30.2210, -97.6170, 3_220_000_000, "Tesla manufactures electric vehicles and energy storage from Gigafactory Texas in Austin."),
    _co("JPM", "JPMorgan Chase", "Financials", "United States", "New York", 40.7546, -73.9754, 2_780_000_000, "JPMorgan Chase is the largest US bank by assets, headquartered in Manhattan."),
    _co("V", "Visa", "Financials", "United States", "San Francisco", 37.7749, -122.4194, 1_950_000_000, "Visa operates the world's largest card-payments processing network."),
    _co("MA", "Mastercard", "Financials", "United States", "Purchase", 41.0400, -73.7140, 920_000_000, "Mastercard runs a global payments network connecting banks, merchants, and consumers."),
    _co("BAC", "Bank of America", "Financials", "United States", "Charlotte", 35.2271, -80.8431, 7_800_000_000, "Bank of America is a leading US consumer and investment bank headquartered in Charlotte."),
    _co("WMT", "Walmart", "Consumer", "United States", "Bentonville", 36.3729, -94.2088, 8_050_000_000, "Walmart is the world's largest retailer by revenue, based in Bentonville, Arkansas."),
    _co("COST", "Costco", "Consumer", "United States", "Issaquah", 47.5301, -122.0326, 440_000_000, "Costco Wholesale operates a global membership warehouse-club retail chain."),
    _co("PG", "Procter & Gamble", "Consumer", "United States", "Cincinnati", 39.1031, -84.5120, 2_350_000_000, "Procter & Gamble makes household and personal-care brands sold worldwide."),
    _co("KO", "Coca-Cola", "Consumer", "United States", "Atlanta", 33.7680, -84.3947, 4_310_000_000, "The Coca-Cola Company is the world's largest non-alcoholic beverage business."),
    _co("PEP", "PepsiCo", "Consumer", "United States", "Purchase", 41.0400, -73.7140, 1_370_000_000, "PepsiCo owns Pepsi, Frito-Lay snacks, and Quaker foods brands."),
    _co("HD", "Home Depot", "Consumer", "United States", "Atlanta", 33.7490, -84.3880, 990_000_000, "The Home Depot is the largest US home-improvement retailer."),
    _co("MCD", "McDonald's", "Consumer", "United States", "Chicago", 41.8817, -87.6389, 720_000_000, "McDonald's operates the world's largest fast-food franchise network."),
    _co("DIS", "Walt Disney", "Consumer", "United States", "Burbank", 34.1808, -118.3090, 1_810_000_000, "The Walt Disney Company spans film studios, streaming, parks, and media networks."),
    _co("NKE", "Nike", "Consumer", "United States", "Beaverton", 45.5100, -122.8340, 1_490_000_000, "Nike is the world's largest athletic footwear and apparel brand."),
    _co("JNJ", "Johnson & Johnson", "Healthcare", "United States", "New Brunswick", 40.4862, -74.4518, 2_410_000_000, "Johnson & Johnson develops pharmaceuticals and medical devices."),
    _co("LLY", "Eli Lilly", "Healthcare", "United States", "Indianapolis", 39.7684, -86.1581, 950_000_000, "Eli Lilly is a pharmaceutical leader in diabetes and obesity treatments."),
    _co("UNH", "UnitedHealth", "Healthcare", "United States", "Minnetonka", 44.9211, -93.4687, 920_000_000, "UnitedHealth Group is the largest US health insurer and health-services company."),
    _co("MRK", "Merck", "Healthcare", "United States", "Rahway", 40.6070, -74.2810, 2_530_000_000, "Merck & Co. develops vaccines and oncology and other prescription medicines."),
    _co("ABBV", "AbbVie", "Healthcare", "United States", "North Chicago", 42.3200, -87.8400, 1_770_000_000, "AbbVie is a research-based biopharmaceutical company spun out of Abbott."),
    _co("XOM", "Exxon Mobil", "Energy", "United States", "Irving", 32.8140, -96.9489, 4_400_000_000, "Exxon Mobil is the largest US integrated oil and gas company."),
    _co("CVX", "Chevron", "Energy", "United States", "San Ramon", 37.7799, -121.9780, 1_840_000_000, "Chevron is a global integrated energy company based in San Ramon, California."),
    # --- International ---
    _co("2222.SR", "Saudi Aramco", "Energy", "Saudi Arabia", "Dhahran", 26.3260, 50.1150, 242_000_000_000, "Saudi Aramco is the world's largest oil producer, based in Dhahran."),
    _co("TSM", "TSMC", "Technology", "Taiwan", "Hsinchu", 24.7736, 121.0025, 5_190_000_000, "TSMC fabricates the majority of the world's advanced chips from Hsinchu Science Park."),
    _co("7203.T", "Toyota", "Automotive", "Japan", "Toyota City", 35.0827, 137.1568, 13_100_000_000, "Toyota is the world's largest automaker by volume, based in Toyota City, Aichi."),
    _co("SAP.DE", "SAP", "Technology", "Germany", "Walldorf", 49.2939, 8.6412, 1_170_000_000, "SAP is Europe's largest software company, supplying enterprise resource planning systems."),
    _co("SIE.DE", "Siemens", "Industrials", "Germany", "Munich", 48.1351, 11.5820, 800_000_000, "Siemens is a diversified industrial and automation conglomerate based in Munich."),
    _co("NESN.SW", "Nestle", "Consumer", "Switzerland", "Vevey", 46.4614, 6.8420, 2_590_000_000, "Nestle is the world's largest food and beverage company, based in Vevey."),
    _co("ROG.SW", "Roche", "Healthcare", "Switzerland", "Basel", 47.5596, 7.5886, 800_000_000, "Roche is a Swiss pharmaceutical and diagnostics leader based in Basel."),
    _co("NVO", "Novo Nordisk", "Healthcare", "Denmark", "Bagsvaerd", 55.7561, 12.4470, 4_460_000_000, "Novo Nordisk is a global leader in diabetes and obesity treatments."),
    _co("ASML", "ASML", "Technology", "Netherlands", "Veldhoven", 51.4200, 5.4040, 390_000_000, "ASML builds the extreme-ultraviolet lithography machines essential to advanced chipmaking."),
    _co("MC.PA", "LVMH", "Consumer", "France", "Paris", 48.8700, 2.3040, 500_000_000, "LVMH is the world's largest luxury-goods conglomerate, based in Paris."),
    _co("SHEL", "Shell", "Energy", "United Kingdom", "London", 51.5074, -0.1278, 6_400_000_000, "Shell is a global integrated energy major headquartered in London."),
    _co("005930.KS", "Samsung Electronics", "Technology", "South Korea", "Suwon", 37.2497, 127.0518, 5_920_000_000, "Samsung Electronics leads global memory-chip and smartphone markets from Suwon."),
    _co("9988.HK", "Alibaba", "Technology", "China", "Hangzhou", 30.2807, 120.0250, 19_100_000_000, "Alibaba operates China's largest e-commerce and cloud platforms from Hangzhou."),
    _co("RELIANCE.NS", "Reliance Industries", "Energy", "India", "Mumbai", 19.0760, 72.8777, 6_770_000_000, "Reliance Industries spans energy, retail, and telecom, based in Mumbai."),
    _co("BHP", "BHP Group", "Materials", "Australia", "Melbourne", -37.8136, 144.9631, 5_070_000_000, "BHP is one of the world's largest mining companies, based in Melbourne."),
]
