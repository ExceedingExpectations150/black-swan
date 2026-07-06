"""Unit test for the economy rollup module.

Seeds six companies across three sectors (one bankrupt) plus known
price-tick series on a temporary SQLite database, then verifies
change_pct, sector grouping/market caps, movers ordering, volatility,
stress bounds, and the persist/rehydrate round trip.

Run: python tests/test_economy.py
"""

from __future__ import annotations

import os
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from economy import (
    STRESS_BANKRUPTCY_WEIGHT,
    STRESS_SENTIMENT_WEIGHT,
    STRESS_VOLATILITY_WEIGHT,
    compute_economy_snapshot,
    persist_economy_snapshot,
    snapshot_from_row,
)
from models import Base, Company, EconomySnapshot, PriceTick

TICK_ID = 10

# ticker: (sector, anchor, current, sentiment, shares, is_bankrupt)
SEED = {
    "AAPL": ("Technology", 100.0, 110.0, 0.5, 1000, False),
    "MSFT": ("Technology", 200.0, 190.0, 0.3, 500, False),
    "NVDA": ("Technology", 50.0, 60.0, 0.1, 2000, False),
    "JPM": ("Finance", 100.0, 80.0, -0.4, 100, False),
    "LEH": ("Finance", 100.0, 10.0, -0.9, 100, True),
    "XOM": ("Energy", 50.0, 54.0, 0.2, 200, False),
}

NVDA_PRICES = [50.0, 55.0, 50.0, 60.0, 52.0, 58.0]  # noisy: 5 returns
JPM_PRICES = [80.0, 81.0]  # only 2 points -> volatility must be 0.0


def expected_change_pct(ticker: str) -> float:
    _, anchor, current, _, _, _ = SEED[ticker]
    return (current - anchor) / anchor * 100


def main() -> None:
    db_path = os.path.join(tempfile.mkdtemp(), "economy_test.db")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as db:
        for ticker, (sector, anchor, current, sentiment, shares, bankrupt) in SEED.items():
            db.add(
                Company(
                    ticker=ticker,
                    name=ticker,
                    sector=sector,
                    country="US",
                    city="New York",
                    lat=40.7,
                    lon=-74.0,
                    shares_outstanding=shares,
                    anchor_price=anchor,
                    current_price=current,
                    sentiment=sentiment,
                    is_bankrupt=bankrupt,
                )
            )
        for i, price in enumerate(NVDA_PRICES, start=1):
            db.add(PriceTick(tick_id=i, ticker="NVDA", price=price))
        for i, price in enumerate(JPM_PRICES, start=1):
            db.add(PriceTick(tick_id=i, ticker="JPM", price=price))
        # Earlier snapshot whose narrative the new snapshot must inherit.
        db.add(EconomySnapshot(tick_id=5, narrative="Calm before the storm."))
        db.commit()

        snapshot = compute_economy_snapshot(db, TICK_ID)

        # change_pct per company (via movers, which carry the values).
        assert snapshot["tick_id"] == TICK_ID
        gainers = snapshot["biggest_gainers"]
        losers = snapshot["biggest_losers"]
        assert [g["ticker"] for g in gainers] == ["NVDA", "AAPL", "XOM"], gainers
        assert gainers[0]["change_pct"] == expected_change_pct("NVDA")
        assert gainers[1]["change_pct"] == expected_change_pct("AAPL")
        assert gainers[2]["change_pct"] == expected_change_pct("XOM")
        assert [l["ticker"] for l in losers] == ["JPM", "MSFT", "XOM"], losers
        assert losers[0]["change_pct"] == expected_change_pct("JPM")
        # Bankrupt LEH (-90%) is excluded from movers despite the biggest drop.
        assert all(m["ticker"] != "LEH" for m in gainers + losers)
        print("PASS change_pct + gainers/losers ordering + bankrupt exclusion")

        # Sector grouping, averages, market caps.
        sectors = {s["sector"]: s for s in snapshot["sectors"]}
        assert set(sectors) == {"Technology", "Finance", "Energy"}
        tech = sectors["Technology"]
        assert tech["companies"] == ["AAPL", "MSFT", "NVDA"]
        assert tech["market_cap"] == 110.0 * 1000 + 190.0 * 500 + 60.0 * 2000
        assert tech["avg_sentiment"] == statistics.mean([0.5, 0.3, 0.1])
        assert tech["avg_change_pct"] == statistics.mean(
            [expected_change_pct(t) for t in ("AAPL", "MSFT", "NVDA")]
        )
        finance = sectors["Finance"]
        assert finance["companies"] == ["JPM", "LEH"]
        assert finance["market_cap"] == 80.0 * 100 + 10.0 * 100
        assert sectors["Energy"]["companies"] == ["XOM"]
        assert sectors["Energy"]["market_cap"] == 54.0 * 200
        print("PASS sector grouping, averages, market caps")

        # Volatility: pstdev of NVDA returns; 0.0 for the 2-point JPM series.
        nvda_returns = [
            (b - a) / a for a, b in zip(NVDA_PRICES, NVDA_PRICES[1:])
        ]
        expected_nvda_vol = statistics.pstdev(nvda_returns)
        assert expected_nvda_vol > 0
        most_volatile = snapshot["most_volatile"]
        assert most_volatile[0]["ticker"] == "NVDA"
        assert most_volatile[0]["volatility"] == expected_nvda_vol
        assert all(m["volatility"] == 0.0 for m in most_volatile[1:])
        # Write-back onto Company.volatility.
        nvda = db.get(Company, "NVDA")
        jpm = db.get(Company, "JPM")
        assert nvda.volatility == expected_nvda_vol
        assert jpm.volatility == 0.0
        print("PASS volatility (noisy > 0, 2-point = 0.0, Company write-back)")

        # Stress index: expected formula value, clamped to [0, 1].
        expected_stress = min(
            1.0,
            max(
                0.0,
                STRESS_VOLATILITY_WEIGHT * (expected_nvda_vol / len(SEED))
                + STRESS_SENTIMENT_WEIGHT
                * statistics.pstdev([v[3] for v in SEED.values()])
                + STRESS_BANKRUPTCY_WEIGHT * 1,
            ),
        )
        assert snapshot["system_stress_index"] == expected_stress
        assert 0.0 <= snapshot["system_stress_index"] <= 1.0
        assert snapshot["bankrupt_count"] == 1
        assert snapshot["narrative"] == "Calm before the storm."
        print("PASS stress index, bankrupt_count, narrative carry-forward")

        # Persist + rehydrate round trip.
        persist_economy_snapshot(db, snapshot)
        db.commit()

    with factory() as db:
        row = db.scalars(
            select(EconomySnapshot).where(EconomySnapshot.tick_id == TICK_ID)
        ).one()
        assert snapshot_from_row(row) == snapshot
        # Upsert path: persisting again must update, not duplicate.
        persist_economy_snapshot(db, snapshot)
        db.commit()
        count = len(
            list(db.scalars(select(EconomySnapshot).where(EconomySnapshot.tick_id == TICK_ID)))
        )
        assert count == 1
        print("PASS persist + snapshot_from_row round trip, upsert idempotent")

    print("ALL ECONOMY TESTS PASSED")


if __name__ == "__main__":
    main()
