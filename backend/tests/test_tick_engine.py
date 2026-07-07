"""Integration test for one full MULTI-TICKER simulation tick.

Real pieces: GeminiModelRouter against a live local fake-Gemma server (which
answers the PR-desk batch and the swarm batches differently based on the
prompt), CorporatePRDesk parsing, MatchingEngine per ticker, SQLAlchemy
persistence on a temp SQLite DB, settlement, sentiment nudges, economy
snapshot. Test stand-in: the forecaster (fixed bearish prediction) so tick
mechanics verify without the model download (real TimesFM path is covered
by test_timesfm_forecast.py).

Run: python tests/test_tick_engine.py
"""

from __future__ import annotations

import asyncio
import json
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
from models import (
    AgentHolding,
    AgentState,
    AgentType,
    Base,
    Company,
    EconomySnapshot,
    PriceTick,
    SocialPost,
    WorldState,
)
import tick_engine
from tick_engine import TickEngine

# This test asserts exact CDA-cleared prices, so disable the event macro-shock
# overlay (verified separately) to keep the clearing math deterministic.
tick_engine.EVENT_SHOCK_VOL = 0.0
tick_engine.EVENT_SHOCK_DRIFT = 0.0

SWARM_REPLY = (
    "Let me think about each cohort's situation...\n"
    '[{"cohort": 0, "orders": [{"ticker": "AAA", "action": "BUY", "qty": 50, "limit_price": 105.0}]},'
    ' {"cohort": 1, "orders": []}]'
)
PR_REPLY = (
    "Here are the corporate posts.\n"
    '[{"ticker": "AAA", "content": "AAA momentum is real. Shipping continues.", "sentiment": 0.8, "stance": "hype"},'
    ' {"ticker": "BBB", "content": "BBB fundamentals unchanged; we remain focused.", "sentiment": -0.2, "stance": "defensive"},'
    ' {"ticker": "CCC", "content": "CCC has no comment on market speculation.", "sentiment": 0.0, "stance": "deflect"}]'
)


class FixedBearForecaster:
    """Test stand-in: always forecasts 5% below the last price -> quants SELL."""

    def forecast_next_tick(self, price_history: list[float]) -> float:
        return price_history[-1] * 0.95


async def fake_gemma(request: web.Request) -> web.Response:
    body = await request.json()
    prompt: str = body["contents"][0]["parts"][0]["text"]
    reply = SWARM_REPLY if "retail trading cohorts" in prompt else PR_REPLY
    return web.json_response(
        {"candidates": [{"content": {"parts": [{"text": reply}]}}]}
    )


def seed(factory: sessionmaker) -> tuple[list[str], list[str]]:
    with factory() as db:
        for ticker, name, sector, anchor in [
            ("AAA", "Alpha Corp", "Technology", 100.0),
            ("BBB", "Beta Inc", "Technology", 50.0),
            ("CCC", "Gamma Bank", "Financials", 200.0),
        ]:
            db.add(
                Company(
                    ticker=ticker,
                    name=name,
                    sector=sector,
                    country="USA",
                    city="Testville",
                    lat=0.0,
                    lon=0.0,
                    description="test",
                    shares_outstanding=1_000_000,
                    anchor_price=anchor,
                    current_price=anchor,
                )
            )
        retail = [
            AgentState(agent_type=AgentType.GEMMA_RETAIL_COHORT, cash_balance=100_000.0, risk_tolerance=0.5),
            AgentState(agent_type=AgentType.GEMMA_RETAIL_COHORT, cash_balance=100_000.0, risk_tolerance=0.8),
        ]
        inst = [AgentState(agent_type=AgentType.TIMESFM_INSTITUTIONAL, cash_balance=5_000_000.0, risk_tolerance=0.5)]
        db.add_all(retail + inst)
        db.flush()
        for agent in retail:
            for t in ("AAA", "BBB", "CCC"):
                db.add(AgentHolding(agent_id=agent.agent_id, ticker=t, quantity=100))
        for t in ("AAA", "BBB", "CCC"):
            db.add(AgentHolding(agent_id=inst[0].agent_id, ticker=t, quantity=2_000))
        db.commit()
        return [a.agent_id for a in retail], [inst[0].agent_id]


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
    retail_ids, inst_ids = seed(factory)

    tick = TickEngine(
        router=GeminiModelRouter(),
        forecaster=FixedBearForecaster(),
        session_factory=factory,
    )
    events = await tick.execute_simulation_tick(tick_id=1, active_event="Test macro shock")
    await runner.cleanup()

    types = [e["type"] for e in events]
    assert types[0] == "tick_start" and types[-1] == "tick_end", types
    assert types[1] == "news" and events[1]["payload"]["headline"] == "Test macro shock"
    assert types.count("social_post") == 3, types
    assert types.count("price_update") == 1 and types.count("economy_update") == 1
    for e in events:
        assert set(e) == {"type", "tick_id", "ts", "payload"}, e  # envelope contract
        assert e["tick_id"] == 1

    prices = {p["ticker"]: p for p in next(e for e in events if e["type"] == "price_update")["payload"]["prices"]}
    # AAA: retail BUY 50@105 (both cohorts' batch reply targets cohort 0 of
    # each batch of 2 -> one order) vs institutional SELL 100@95 -> 50 @ 100.0
    assert prices["AAA"]["price"] == 100.0 and prices["AAA"]["volume"] == 50, prices["AAA"]
    # BBB/CCC: only institutional sells (at 0.95 x price), nothing crosses ->
    # one-sided pressure moves price 25% of the way to the best unmatched ask.
    assert prices["BBB"]["price"] == 49.375 and prices["BBB"]["volume"] == 0, prices["BBB"]
    assert prices["CCC"]["price"] == 197.5 and prices["CCC"]["volume"] == 0, prices["CCC"]

    with factory() as db:
        aaa = db.get(Company, "AAA")
        assert abs(aaa.sentiment - 0.3 * 0.8) < 1e-9, aaa.sentiment  # nudge from 0
        posts = list(db.execute(select(SocialPost)).scalars())
        assert len(posts) == 3 and all(p.tick_id == 1 for p in posts)
        pt = list(db.execute(select(PriceTick)).scalars())
        assert {p.ticker for p in pt} == {"AAA", "BBB", "CCC"}
        buyer = db.get(AgentState, retail_ids[0])
        assert abs(buyer.cash_balance - (100_000.0 - 50 * 100.0)) < 1e-6, buyer.cash_balance
        buyer_aaa = db.get(AgentHolding, (retail_ids[0], "AAA"))
        assert buyer_aaa.quantity == 150, buyer_aaa.quantity
        quant = db.get(AgentState, inst_ids[0])
        assert abs(quant.cash_balance - (5_000_000.0 + 50 * 100.0)) < 1e-6
        quant_aaa = db.get(AgentHolding, (inst_ids[0], "AAA"))
        assert quant_aaa.quantity == 1_950, quant_aaa.quantity
        assert db.get(EconomySnapshot, 1) is not None
        assert db.get(WorldState, 1) is not None

    print("PASS: multi-ticker tick — PR desk, swarm batch, per-ticker CDA, settlement, economy")


if __name__ == "__main__":
    asyncio.run(main())
