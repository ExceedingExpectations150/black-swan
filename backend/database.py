"""Database configuration and initial market seeding for ChaosNet "Black Swan".

Connection targets (PRD.md Section 2): SQLite for local development,
PostgreSQL for production — selected via the DATABASE_URL environment variable.
"""

from __future__ import annotations

import os
import random

from dotenv import load_dotenv
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from models import AgentState, AgentType, Base

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./blackswan.db")

RETAIL_COHORT_COUNT: int = 50
INSTITUTIONAL_COUNT: int = 5

RETAIL_CASH_BASELINE: float = 100_000.0
RETAIL_RISK_MIN: float = 0.1
RETAIL_RISK_MAX: float = 0.9
RETAIL_SHARES_PER_TICKER: int = 40

INSTITUTIONAL_CASH_RESERVE: float = 5_000_000.0
INSTITUTIONAL_RISK_TOLERANCE: float = 0.5
INSTITUTIONAL_SHARES_PER_TICKER: int = 2_000


def _build_engine(url: str) -> Engine:
    # SQLite needs check_same_thread=False so FastAPI worker threads can share it.
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


engine: Engine = _build_engine(DATABASE_URL)

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
)


def get_db():
    """FastAPI dependency that yields a session and always closes it."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables defined on the declarative Base."""
    Base.metadata.create_all(bind=engine)


def seed_companies(db: Session) -> int:
    """Seed the curated company list with REAL anchor prices.

    Fetching anchors at seed time is a setup step: if the market data
    provider cannot return a real price for every ticker, this raises and
    the seed fails — fabricated prices are forbidden (anti-mock rule).
    """
    from market_data import CURATED_COMPANIES, YFinanceProvider

    from models import Company

    existing = db.execute(select(Company).limit(1)).scalar_one_or_none()
    if existing is not None:
        return 0

    tickers = [c["ticker"] for c in CURATED_COMPANIES]
    # Tolerant: skip any individual symbol that can't be priced (never
    # fabricated); raises only if the provider returns nothing at all.
    quotes: dict[str, float] = YFinanceProvider().get_quotes_available(tickers)

    seeded = 0
    for spec in CURATED_COMPANIES:
        anchor = quotes.get(spec["ticker"])
        if anchor is None:
            continue  # unresolved ticker — not seeded, not fabricated
        seeded += 1
        db.add(
            Company(
                ticker=spec["ticker"],
                name=spec["name"],
                sector=spec["sector"],
                country=spec["country"],
                city=spec["city"],
                lat=spec["lat"],
                lon=spec["lon"],
                description=spec["description"],
                shares_outstanding=spec["shares_outstanding"],
                anchor_price=anchor,
                current_price=anchor,
            )
        )
    return seeded


def seed_initial_market_state() -> int:
    """Populate companies, the agent roster, and holdings if never seeded.

    If `agent_states` is empty: seeds the curated companies (real anchor
    prices, hard-fault on fetch failure), creates exactly 50
    "gemma_retail_cohort" agents with randomized risk tolerances (0.1 - 0.9)
    and 5 "timesfm_institutional" agents with large cash reserves, and gives
    every agent a deterministic per-ticker starting position.

    Returns the number of agents created (0 if already seeded).
    """
    from models import AgentHolding, Company

    with SessionLocal() as db:
        existing: AgentState | None = db.execute(select(AgentState).limit(1)).scalar_one_or_none()
        if existing is not None:
            return 0

        seed_companies(db)
        tickers: list[str] = list(db.execute(select(Company.ticker)).scalars())

        agents: list[AgentState] = [
            AgentState(
                agent_type=AgentType.GEMMA_RETAIL_COHORT,
                cash_balance=RETAIL_CASH_BASELINE,
                risk_tolerance=round(random.uniform(RETAIL_RISK_MIN, RETAIL_RISK_MAX), 3),
                is_bankrupt=False,
            )
            for _ in range(RETAIL_COHORT_COUNT)
        ]
        agents.extend(
            AgentState(
                agent_type=AgentType.TIMESFM_INSTITUTIONAL,
                cash_balance=INSTITUTIONAL_CASH_RESERVE,
                risk_tolerance=INSTITUTIONAL_RISK_TOLERANCE,
                is_bankrupt=False,
            )
            for _ in range(INSTITUTIONAL_COUNT)
        )
        db.add_all(agents)
        db.flush()  # assign agent_ids before holdings reference them

        for agent in agents:
            per_ticker = (
                RETAIL_SHARES_PER_TICKER
                if agent.agent_type == AgentType.GEMMA_RETAIL_COHORT
                else INSTITUTIONAL_SHARES_PER_TICKER
            )
            for ticker in tickers:
                db.add(AgentHolding(agent_id=agent.agent_id, ticker=ticker, quantity=per_ticker))

        db.commit()
        return len(agents)


def reset_world() -> int:
    """Wipe all dynamic state and reseed a pristine market (keeps real anchors).

    Deletes orders/prices/economy/world/holdings/agents and resets every
    company back to its real anchor price, then reseeds the agent roster with
    full cash and starting holdings. Companies (and their real anchor prices)
    are preserved, so no market-data fetch is needed. Returns agents created.
    """
    from models import (
        AgentHolding,
        Company,
        EconomySnapshot,
        OrderBook,
        PriceTick,
        SocialPost,
        WorldState,
    )

    with SessionLocal() as db:
        for model in (
            OrderBook,
            PriceTick,
            SocialPost,
            EconomySnapshot,
            WorldState,
            AgentHolding,
            AgentState,
        ):
            db.execute(delete(model))
        db.execute(
            update(Company).values(
                current_price=Company.anchor_price,
                sentiment=0.0,
                volatility=0.0,
                is_bankrupt=False,
            )
        )
        db.commit()
    return seed_initial_market_state()


if __name__ == "__main__":
    init_db()
    created: int = seed_initial_market_state()
    print(f"Database ready at {DATABASE_URL} — seeded {created} agents.")
