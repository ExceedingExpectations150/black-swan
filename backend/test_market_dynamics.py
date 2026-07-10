"""Order-flow price-discovery test for ChaosNet "Black Swan".

Proves the market moves ONLY because agents actually trade — no sine, no
scripted path — without needing torch, Gemini, or a database. It drives the
real engine methods (LocalBehavioralEngine.generate, TickEngine._materialize_retail,
_build_institutional_orders, _one_sided_pressure, _settle_transaction, and the
MatchingEngine) over a mini in-memory world with a stub forecaster.

Scenario: calm -> Black Swan event -> event cleared. Asserts that trades clear,
the event produces a real order-flow crash, and value/quant buyers stabilize the
market afterward. Run: python test_market_dynamics.py
"""

from __future__ import annotations

import random
import statistics
import uuid

from behavioral_engine import LocalBehavioralEngine  # noqa: F401  (import sanity)
from matching_engine import MatchingEngine
from models import AgentState, AgentType, Company
from tick_engine import TickEngine

TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
ANCHOR = 100.0
RETAIL = 50
INSTITUTIONAL = 5
CALM_UNTIL = 5
EVENT_UNTIL = 20
TOTAL_TICKS = 35
FORECAST_WINDOW = 10


class _StubForecaster:
    """Lagging mean forecaster — no torch. Trails the recent average, so after
    a crash the forecast sits ABOVE the depressed price and quant funds buy the
    dip, exactly as a real TimesFM history-based forecast would behave."""

    def forecast_next_tick(self, price_history: list[float]) -> float:
        window = price_history[-FORECAST_WINDOW:]
        return float(statistics.mean(window)) if window else ANCHOR

    def forecast_batch(self, histories: dict[str, list[float]]) -> dict[str, float]:
        return {t: self.forecast_next_tick(s) for t, s in histories.items()}


class _DummyRouter:
    pass


def _build_world(rng: random.Random):
    companies = [
        Company(ticker=t, anchor_price=ANCHOR, current_price=ANCHOR, sentiment=0.0, is_bankrupt=False)
        for t in TICKERS
    ]
    retail = [
        AgentState(
            agent_id=str(uuid.uuid4()),
            agent_type=AgentType.GEMMA_RETAIL_COHORT,
            cash_balance=100_000.0,
            risk_tolerance=round(rng.uniform(0.1, 0.9), 3),
            is_bankrupt=False,
        )
        for _ in range(RETAIL)
    ]
    institutional = [
        AgentState(
            agent_id=str(uuid.uuid4()),
            agent_type=AgentType.TIMESFM_INSTITUTIONAL,
            cash_balance=5_000_000.0,
            risk_tolerance=0.5,
            is_bankrupt=False,
        )
        for _ in range(INSTITUTIONAL)
    ]
    holdings: dict[str, dict[str, int]] = {}
    for a in retail:
        holdings[a.agent_id] = {t: 40 for t in TICKERS}
    for a in institutional:
        holdings[a.agent_id] = {t: 2000 for t in TICKERS}
    histories: dict[str, list[float]] = {t: [ANCHOR] for t in TICKERS}
    return companies, retail, institutional, holdings, histories


def _clear(engine: TickEngine, companies, orders, agents, holdings) -> tuple[dict, int]:
    """Replicates TickEngine._clear_markets minus the DB writes (same logic)."""
    by_agent = {a.agent_id: a for a in agents}
    updates: dict[str, tuple[float, int]] = {}
    total_volume = 0
    for company in companies:
        book = [o for o in orders if o.ticker == company.ticker]
        clearing_price, transactions, volume = engine.matching_engine.resolve_order_book(
            book, company.current_price
        )
        if not transactions and book:
            clearing_price = engine._one_sided_pressure(book, company.current_price)
        for t in transactions:
            engine._settle_transaction(t, company.ticker, by_agent, holdings)
        company.current_price = clearing_price
        updates[company.ticker] = (clearing_price, volume)
        total_volume += volume
    for a in agents:
        if a.cash_balance <= 0:
            a.is_bankrupt = True
    return updates, total_volume


def _index(companies) -> float:
    return statistics.mean(c.current_price / c.anchor_price for c in companies) * 100.0


def main() -> int:
    rng_world = random.Random(42)
    companies, retail, institutional, holdings, histories = _build_world(rng_world)
    agents = retail + institutional
    known = {t for t in TICKERS}

    engine = TickEngine(router=_DummyRouter(), forecaster=_StubForecaster(), matching_engine=MatchingEngine())

    index_at: dict[int, float] = {0: _index(companies)}
    ticks_with_trades = 0
    from tick_engine import EVENT_FEAR_INTENSITY

    print(f"{'tick':>4} {'phase':<8} {'index':>8} {'volume':>8}")
    for tick in range(1, TOTAL_TICKS + 1):
        event_active = CALM_UNTIL < tick <= EVENT_UNTIL
        event_intensity = EVENT_FEAR_INTENSITY if event_active else 0.0
        if event_intensity:
            engine._apply_event_sentiment(companies)

        rng = random.Random(tick * 1_000_003 + 1)
        local_intents = engine.behavioral.generate(
            tick, companies, retail, holdings, histories, event_intensity, rng
        )
        forecasts = engine._forecast_all(histories)  # uses the stub forecaster
        retail_orders = engine._materialize_retail(local_intents, retail, holdings, known, tick)
        inst_orders = engine._build_institutional_orders(
            tick, institutional, holdings, forecasts, companies
        )
        orders = retail_orders + inst_orders

        _, volume = _clear(engine, companies, orders, agents, holdings)
        for c in companies:
            histories[c.ticker].append(c.current_price)
        if volume > 0:
            ticks_with_trades += 1
        idx = _index(companies)
        index_at[tick] = idx
        phase = "calm" if tick <= CALM_UNTIL else ("EVENT" if event_active else "recover")
        print(f"{tick:>4} {phase:<8} {idx:>8.2f} {volume:>8}")

    # -------------------------------------------------------------- checks
    ok = True

    def check(label: str, condition: bool) -> None:
        nonlocal ok
        print(f"[{'PASS' if condition else 'FAIL'}] {label}")
        ok = ok and condition

    calm_idx = index_at[CALM_UNTIL]
    crash_idx = index_at[EVENT_UNTIL]
    end_idx = index_at[TOTAL_TICKS]
    crash_pct = (crash_idx - calm_idx) / calm_idx * 100.0
    recover_pct = (end_idx - crash_idx) / crash_idx * 100.0

    check("trades clear on (nearly) every tick", ticks_with_trades >= TOTAL_TICKS - 2)
    check(f"event crashes the market via order flow (drop {crash_pct:.1f}%)", crash_pct <= -3.0)
    check(f"market stabilizes/recovers after event (change {recover_pct:+.1f}%)", recover_pct >= -1.0)
    check("no price explosion / NaN", all(10.0 < index_at[t] < 300.0 for t in index_at))
    check("no total wipeout of retail", sum(1 for a in retail if not a.is_bankrupt) >= RETAIL // 2)

    print(
        f"\nsummary: calm={calm_idx:.2f} crash={crash_idx:.2f} end={end_idx:.2f} "
        f"| crash {crash_pct:+.1f}% | recover {recover_pct:+.1f}% "
        f"| trading ticks {ticks_with_trades}/{TOTAL_TICKS}"
    )
    print("RESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
