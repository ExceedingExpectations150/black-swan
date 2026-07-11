"""Economy rollup for the ChaosNet "Black Swan" market twin.

Computes the per-tick `/api/economy` contract dict (sector rollups, movers,
per-company volatility, system stress index) and persists/rehydrates
`EconomySnapshot` rows. Field names in the snapshot dict are a frozen
cross-team contract; do not rename them.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Company, EconomySnapshot, PriceTick

# Recent tick-over-tick returns window used for per-company volatility.
RETURNS_WINDOW: int = 20

# Minimum price points needed before volatility is meaningful.
MIN_PRICE_POINTS: int = 3

# system_stress_index = clamp01(w_vol * mean(vol) + w_sent * stdev(sent) + w_bk * bankrupt_count)
STRESS_VOLATILITY_WEIGHT: float = 2.5
STRESS_SENTIMENT_WEIGHT: float = 1.5
STRESS_BANKRUPTCY_WEIGHT: float = 0.15

# Entries reported in each movers list (gainers / losers / most volatile).
MOVERS_COUNT: int = 3


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _change_pct(company: Company) -> float:
    """Percent divergence of the simulated price from the real-market anchor."""
    return (company.current_price - company.anchor_price) / company.anchor_price * 100


def _company_volatilities(db: Session) -> dict[str, float]:
    """Population stdev of the last RETURNS_WINDOW tick-over-tick returns,
    for every ticker in ONE query (was one query per company — 51 round
    trips per tick). Every ticker gets one PriceTick per tick, so a tick_id
    window bounds each series to RETURNS_WINDOW + 1 points.

    Tickers with fewer than MIN_PRICE_POINTS points get 0.0.
    """
    latest = db.scalar(select(func.max(PriceTick.tick_id))) or 0
    rows = db.execute(
        select(PriceTick.ticker, PriceTick.price)
        .where(PriceTick.tick_id > latest - (RETURNS_WINDOW + 1))
        .order_by(PriceTick.tick_id)
    ).all()
    series: dict[str, list[float]] = {}
    for ticker, price in rows:
        series.setdefault(ticker, []).append(price)
    out: dict[str, float] = {}
    for ticker, prices in series.items():
        if len(prices) < MIN_PRICE_POINTS:
            out[ticker] = 0.0
            continue
        returns = [(after - before) / before for before, after in zip(prices, prices[1:])]
        out[ticker] = statistics.pstdev(returns)
    return out


def compute_economy_snapshot(db: Session, tick_id: int) -> dict:
    """Build the frozen-contract economy snapshot dict for `tick_id`.

    Also writes each computed volatility back onto Company.volatility
    (the caller commits).
    """
    companies = list(db.scalars(select(Company)))

    computed = _company_volatilities(db)
    change_pcts: dict[str, float] = {}
    volatilities: dict[str, float] = {}
    for company in companies:
        change_pcts[company.ticker] = _change_pct(company)
        volatility = computed.get(company.ticker, 0.0)
        volatilities[company.ticker] = volatility
        company.volatility = volatility

    grouped: dict[str, list[Company]] = {}
    for company in companies:
        grouped.setdefault(company.sector, []).append(company)
    sectors = [
        {
            "sector": sector,
            "avg_sentiment": statistics.mean(c.sentiment for c in members),
            "avg_change_pct": statistics.mean(change_pcts[c.ticker] for c in members),
            "market_cap": sum(c.current_price * c.shares_outstanding for c in members),
            "companies": sorted(c.ticker for c in members),
        }
        for sector, members in sorted(grouped.items())
    ]

    active = [c for c in companies if not c.is_bankrupt]
    by_change_desc = sorted(active, key=lambda c: change_pcts[c.ticker], reverse=True)
    biggest_gainers = [
        {"ticker": c.ticker, "change_pct": change_pcts[c.ticker]}
        for c in by_change_desc[:MOVERS_COUNT]
    ]
    biggest_losers = [
        {"ticker": c.ticker, "change_pct": change_pcts[c.ticker]}
        for c in by_change_desc[::-1][:MOVERS_COUNT]
    ]
    most_volatile = [
        {"ticker": c.ticker, "volatility": volatilities[c.ticker]}
        for c in sorted(companies, key=lambda c: volatilities[c.ticker], reverse=True)[
            :MOVERS_COUNT
        ]
    ]

    bankrupt_count = sum(1 for c in companies if c.is_bankrupt)
    mean_volatility = statistics.mean(volatilities.values()) if volatilities else 0.0
    sentiments = [c.sentiment for c in companies]
    sentiment_spread = statistics.pstdev(sentiments) if sentiments else 0.0
    system_stress_index = _clamp01(
        STRESS_VOLATILITY_WEIGHT * mean_volatility
        + STRESS_SENTIMENT_WEIGHT * sentiment_spread
        + STRESS_BANKRUPTCY_WEIGHT * bankrupt_count
    )

    narrative = (
        db.scalars(
            select(EconomySnapshot.narrative)
            .where(EconomySnapshot.tick_id <= tick_id)
            .order_by(EconomySnapshot.tick_id.desc())
            .limit(1)
        ).first()
        or ""
    )

    return {
        "tick_id": tick_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "system_stress_index": system_stress_index,
        "bankrupt_count": bankrupt_count,
        "sectors": sectors,
        "biggest_gainers": biggest_gainers,
        "biggest_losers": biggest_losers,
        "most_volatile": most_volatile,
        "narrative": narrative,
    }


def persist_economy_snapshot(db: Session, snapshot: dict) -> None:
    """Upsert the EconomySnapshot row for snapshot["tick_id"] (caller commits)."""
    row = db.get(EconomySnapshot, snapshot["tick_id"])
    if row is None:
        row = EconomySnapshot(tick_id=snapshot["tick_id"])
        db.add(row)
    row.ts = datetime.fromisoformat(snapshot["ts"])
    row.system_stress_index = snapshot["system_stress_index"]
    row.bankrupt_count = snapshot["bankrupt_count"]
    row.sectors_json = json.dumps(snapshot["sectors"])
    row.movers_json = json.dumps(
        {
            "biggest_gainers": snapshot["biggest_gainers"],
            "biggest_losers": snapshot["biggest_losers"],
            "most_volatile": snapshot["most_volatile"],
        }
    )
    row.narrative = snapshot["narrative"]


def snapshot_from_row(row: EconomySnapshot) -> dict:
    """Rebuild the frozen-contract dict from a stored EconomySnapshot row."""
    # SQLite returns naive datetimes; all stored timestamps are UTC.
    ts = row.ts if row.ts.tzinfo is not None else row.ts.replace(tzinfo=timezone.utc)
    movers = json.loads(row.movers_json)
    return {
        "tick_id": row.tick_id,
        "ts": ts.isoformat(),
        "system_stress_index": row.system_stress_index,
        "bankrupt_count": row.bankrupt_count,
        "sectors": json.loads(row.sectors_json),
        "biggest_gainers": movers.get("biggest_gainers", []),
        "biggest_losers": movers.get("biggest_losers", []),
        "most_volatile": movers.get("most_volatile", []),
        "narrative": row.narrative,
    }
