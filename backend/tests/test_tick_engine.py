"""Integration test for one full simulation tick.

Real pieces: GeminiModelRouter (pointed at a live local aiohttp server that
answers with strict JSON), MatchingEngine, SQLAlchemy persistence on a
temporary SQLite database, settlement and bankruptcy logic.
Test-harness stand-in: the forecaster (a fixed prediction), so the tick
mechanics are verifiable without the model download; the real TimesFM path
is covered by test_timesfm_forecast.py.

Run: python tests/test_tick_engine.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("GEMINI_API_KEY_PRIMARY", "test-key-primary")
os.environ.setdefault("GEMINI_API_KEY_BACKUP", "test-key-backup")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import ai_clients
from ai_clients import GeminiModelRouter
from models import AgentState, AgentType, Base, OrderBook, OrderStatus, WorldState
from tick_engine import TickEngine


class FixedForecaster:
    """Test stand-in: constant bearish prediction (95 < spot 100 -> quants SELL)."""

    def forecast_next_tick(self, price_history: list[float]) -> float:
        return 95.0


async def fake_gemma(request: web.Request) -> web.Response:
    # Every cohort answers an aggressive strict-JSON BUY above spot.
    return web.json_response(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"action": "BUY", "qty": 40, "limit_price": 101.0}'}
                        ]
                    }
                }
            ]
        }
    )


async def main() -> None:
    app = web.Application()
    app.router.add_post("/v1beta/models/{model}:generateContent", fake_gemma)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    ai_clients.GEMINI_ENDPOINT_TEMPLATE = (
        f"http://127.0.0.1:{port}/v1beta/models/{{model}}:generateContent"
    )

    db_path = os.path.join(tempfile.mkdtemp(), "tick_test.db")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as db:
        db.add_all(
            [
                AgentState(
                    agent_type=AgentType.GEMMA_RETAIL_COHORT,
                    cash_balance=100_000.0,
                    stock_inventory=1_000,
                    risk_tolerance=0.5,
                ),
                AgentState(
                    agent_type=AgentType.GEMMA_RETAIL_COHORT,
                    cash_balance=100_000.0,
                    stock_inventory=1_000,
                    risk_tolerance=0.8,
                ),
                AgentState(
                    agent_type=AgentType.TIMESFM_INSTITUTIONAL,
                    cash_balance=5_000_000.0,
                    stock_inventory=50_000,
                    risk_tolerance=0.5,
                ),
                AgentState(  # bankrupt: must be excluded from the tick entirely
                    agent_type=AgentType.GEMMA_RETAIL_COHORT,
                    cash_balance=-10.0,
                    stock_inventory=0,
                    risk_tolerance=0.5,
                    is_bankrupt=True,
                ),
            ]
        )
        db.commit()

    tick = TickEngine(
        router=GeminiModelRouter(),
        forecaster=FixedForecaster(),
        session_factory=factory,
    )
    telemetry = await tick.execute_simulation_tick(
        tick_id=1,
        active_event="Test macro shock",
        current_price=100.0,
        price_history=[100.0] * 40,
    )
    await runner.cleanup()

    # Retail BUY 40 @ 101 (x2) vs institutional SELL 2500 @ 95 -> both cross.
    assert telemetry["orders_submitted"] == 3, telemetry
    assert telemetry["cleared_transactions"] == 2, telemetry
    assert telemetry["total_volume"] == 80, telemetry
    assert telemetry["clearing_price"] == 98.0, telemetry  # midpoint (101+95)/2
    assert telemetry["timesfm_prediction"] == 95.0
    assert telemetry["agents"]["retail_active"] == 2
    assert telemetry["agents"]["institutional_active"] == 1
    assert telemetry["news_headline"] == "Test macro shock"

    with factory() as db:
        ws = db.execute(select(WorldState)).scalar_one()
        assert ws.tick_id == 1 and ws.current_price == 98.0
        assert ws.news_headline == "Test macro shock"

        orders = list(db.execute(select(OrderBook)).scalars())
        assert len(orders) == 3
        statuses = sorted(o.status.value for o in orders)
        # Both retail BUYs fill; the big institutional SELL is partially
        # filled and dies with the tick (CANCELLED).
        assert statuses == ["CANCELLED", "FILLED", "FILLED"], statuses

        retail = list(
            db.execute(
                select(AgentState).where(
                    AgentState.agent_type == AgentType.GEMMA_RETAIL_COHORT,
                    AgentState.is_bankrupt.is_(False),
                )
            ).scalars()
        )
        for r in retail:
            assert r.stock_inventory == 1_040, r.stock_inventory
            assert abs(r.cash_balance - (100_000.0 - 40 * 98.0)) < 1e-6

        quant = db.execute(
            select(AgentState).where(
                AgentState.agent_type == AgentType.TIMESFM_INSTITUTIONAL
            )
        ).scalar_one()
        assert quant.stock_inventory == 50_000 - 80
        assert abs(quant.cash_balance - (5_000_000.0 + 80 * 98.0)) < 1e-6

    print("PASS: full tick — parallel cohorts, quant signal, CDA, settlement, persistence")


if __name__ == "__main__":
    asyncio.run(main())
