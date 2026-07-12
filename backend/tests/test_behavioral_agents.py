"""The behavioral layer must produce a REAL market: orders that cross in the
CDA and move prices through trades — never through any synthetic overlay."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavioral_agents import build_behavioral_orders, strategy_of
from matching_engine import MatchingEngine
from models import AgentState, AgentType, Company, OrderType


def _cohorts(n: int) -> list[AgentState]:
    return [
        AgentState(
            agent_id=f"cohort-{i}",
            agent_type=AgentType.GEMMA_RETAIL_COHORT,
            cash_balance=100_000.0,
            risk_tolerance=0.1 + 0.8 * (i / max(1, n - 1)),
            is_bankrupt=False,
        )
        for i in range(n)
    ]


def _companies() -> list[Company]:
    out = []
    for ticker, price, sent in (("AAPL", 230.0, 0.0), ("NVDA", 900.0, -0.5), ("JPM", 210.0, 0.3)):
        c = Company(
            ticker=ticker,
            name=ticker,
            sector="T",
            country="US",
            city="X",
            lat=0.0,
            lon=0.0,
            description="",
            anchor_price=price,
            current_price=price,
            shares_outstanding=1_000_000,
            sentiment=sent,
            volatility=0.0,
            is_bankrupt=False,
        )
        out.append(c)
    return out


def _holdings(cohorts, companies):
    return {a.agent_id: {c.ticker: 500 for c in companies} for a in cohorts}


def _histories(companies):
    # gentle uptrend so chartists have a real signal
    return {c.ticker: [c.current_price * (0.99 + 0.002 * i) for i in range(10)] for c in companies}


def test_strategies_are_stable_and_mixed():
    cohorts = _cohorts(150)
    kinds = {s: 0 for s in ("fundamentalist", "chartist", "noise")}
    for a in cohorts:
        s = strategy_of(a.agent_id)
        assert s == strategy_of(a.agent_id)  # stable
        kinds[s] += 1
    assert all(v > 10 for v in kinds.values()), kinds


def test_orders_flow_and_market_clears():
    cohorts = _cohorts(150)
    companies = _companies()
    orders = build_behavioral_orders(
        1, cohorts, companies, _holdings(cohorts, companies),
        _histories(companies), event_intensity=0.0, event_direction=0.0,
        decided_agent_ids=set(),
    )
    assert len(orders) > 50, f"expected an active market, got {len(orders)} orders"
    buys = sum(1 for o in orders if o.order_type == OrderType.BUY)
    sells = len(orders) - buys
    assert buys > 0 and sells > 0, "book must be two-sided"

    engine = MatchingEngine()
    for company in companies:
        book = [o for o in orders if o.ticker == company.ticker]
        price, transactions, volume = engine.resolve_order_book(book, company.current_price)
        assert volume > 0, f"{company.ticker}: no trades cleared — market is dead"
        assert price > 0
        assert len(transactions) > 0


def test_event_moves_prices_through_beliefs_both_directions():
    cohorts = _cohorts(150)
    engine = MatchingEngine()

    def clearing(intensity: float, direction: float, tick: int) -> float:
        companies = _companies()
        orders = build_behavioral_orders(
            tick, cohorts, companies, _holdings(cohorts, companies),
            _histories(companies), event_intensity=intensity,
            event_direction=direction, decided_agent_ids=set(),
        )
        book = [o for o in orders if o.ticker == "AAPL"]
        price, _, volume = engine.resolve_order_book(book, 230.0)
        assert volume > 0
        return price

    calm = sum(clearing(0.0, 0.0, t) for t in range(1, 6)) / 5
    panic = sum(clearing(1.0, -1.0, t) for t in range(1, 6)) / 5
    euphoria = sum(clearing(1.0, 1.0, t) for t in range(1, 6)) / 5
    # A bearish shock pushes prices DOWN and a bullish catalyst pushes them UP,
    # both purely through order flow — good news can rally the market.
    assert panic < calm, f"bearish event must push down: calm={calm} panic={panic}"
    assert euphoria > calm, f"bullish event must push up: calm={calm} euphoria={euphoria}"


def test_llm_decided_cohorts_are_skipped():
    cohorts = _cohorts(20)
    companies = _companies()
    decided = {a.agent_id for a in cohorts[:10]}
    orders = build_behavioral_orders(
        1, cohorts, companies, _holdings(cohorts, companies),
        _histories(companies), event_intensity=0.0, event_direction=0.0,
        decided_agent_ids=decided,
    )
    assert all(o.agent_id not in decided for o in orders)


if __name__ == "__main__":
    test_strategies_are_stable_and_mixed()
    test_orders_flow_and_market_clears()
    test_event_moves_prices_through_beliefs_both_directions()
    test_llm_decided_cohorts_are_skipped()
    print("ALL BEHAVIORAL TESTS PASSED")
