"""Database configuration and initial market seeding for ChaosNet "Black Swan".

Connection targets (PRD.md Section 2): SQLite for local development,
PostgreSQL for production — selected via the DATABASE_URL environment variable.
"""

from __future__ import annotations

import os
import random

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from models import AgentState, AgentType, Base

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./blackswan.db")

RETAIL_COHORT_COUNT: int = 50
INSTITUTIONAL_COUNT: int = 5

RETAIL_CASH_BASELINE: float = 100_000.0
RETAIL_INVENTORY_BASELINE: int = 1_000
RETAIL_RISK_MIN: float = 0.1
RETAIL_RISK_MAX: float = 0.9

INSTITUTIONAL_CASH_RESERVE: float = 5_000_000.0
INSTITUTIONAL_INVENTORY_RESERVE: int = 50_000
INSTITUTIONAL_RISK_TOLERANCE: float = 0.5


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


def seed_initial_market_state() -> int:
    """Populate the agent roster if the market has never been seeded.

    If `agent_states` is empty, creates exactly 50 "gemma_retail_cohort"
    agents with randomized risk tolerances (0.1 - 0.9) and 5
    "timesfm_institutional" agents with large cash/inventory reserves.

    Returns the number of agents created (0 if the table was already seeded).
    """
    with SessionLocal() as db:
        existing: AgentState | None = db.execute(select(AgentState).limit(1)).scalar_one_or_none()
        if existing is not None:
            return 0

        agents: list[AgentState] = [
            AgentState(
                agent_type=AgentType.GEMMA_RETAIL_COHORT,
                cash_balance=RETAIL_CASH_BASELINE,
                stock_inventory=RETAIL_INVENTORY_BASELINE,
                risk_tolerance=round(random.uniform(RETAIL_RISK_MIN, RETAIL_RISK_MAX), 3),
                is_bankrupt=False,
            )
            for _ in range(RETAIL_COHORT_COUNT)
        ]
        agents.extend(
            AgentState(
                agent_type=AgentType.TIMESFM_INSTITUTIONAL,
                cash_balance=INSTITUTIONAL_CASH_RESERVE,
                stock_inventory=INSTITUTIONAL_INVENTORY_RESERVE,
                risk_tolerance=INSTITUTIONAL_RISK_TOLERANCE,
                is_bankrupt=False,
            )
            for _ in range(INSTITUTIONAL_COUNT)
        )

        db.add_all(agents)
        db.commit()
        return len(agents)


if __name__ == "__main__":
    init_db()
    created: int = seed_initial_market_state()
    print(f"Database ready at {DATABASE_URL} — seeded {created} agents.")
