"""Bugtest + grade harness for the Black Swan market engine.

Runs the REAL engine (behavioral crowd, conditioned TimesFM quant funds, CDA,
settlement) through a full scenario — calm -> sustained Black Swan -> event
cleared — and grades the result against an economic-realism rubric. No torch,
DB, or Gemini needed (stub forecaster + injected event impact, exactly as the
analyst would feed it).

The rubric catches the "-99% collapse" bug: a real crash is deep but BOUNDED
and finds a floor; it must not spiral every name to ~zero.

Run: python grade_market.py
"""

from __future__ import annotations

import random
import statistics
import uuid

from matching_engine import MatchingEngine
from models import AgentState, AgentType, Company
from tick_engine import TickEngine

TICKERS = [f"T{i:02d}" for i in range(30)]
ANCHOR = 100.0
RETAIL = 50
INSTITUTIONAL = 5
FORECAST_WINDOW = 10

CALM_UNTIL = 5
EVENT_UNTIL = 160  # long active event — stresses the floor (the -99% scenario)
TOTAL_TICKS = 200
SEEDS = (7, 13, 29, 101, 250)

# Injected analyst impact (what conditions the quant forecast): half the market
# hit hard (a sector shock), the rest a broad risk-off tilt.
def _event_impact() -> dict[str, float]:
    impact = {}
    for i, t in enumerate(TICKERS):
        impact[t] = -0.15 if i % 2 == 0 else -0.05
    return impact


class _StubForecaster:
    def forecast_next_tick(self, hist: list[float]) -> float:
        w = hist[-FORECAST_WINDOW:]
        return float(statistics.mean(w)) if w else ANCHOR

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
        AgentState(agent_id=str(uuid.uuid4()), agent_type=AgentType.GEMMA_RETAIL_COHORT,
                   cash_balance=100_000.0, risk_tolerance=round(rng.uniform(0.1, 0.9), 3),
                   is_bankrupt=False)
        for _ in range(RETAIL)
    ]
    institutional = [
        AgentState(agent_id=str(uuid.uuid4()), agent_type=AgentType.TIMESFM_INSTITUTIONAL,
                   cash_balance=5_000_000.0, risk_tolerance=0.5, is_bankrupt=False)
        for _ in range(INSTITUTIONAL)
    ]
    holdings: dict[str, dict[str, int]] = {}
    for a in retail:
        holdings[a.agent_id] = {t: 40 for t in TICKERS}
    for a in institutional:
        holdings[a.agent_id] = {t: 2000 for t in TICKERS}
    histories = {t: [ANCHOR] for t in TICKERS}
    return companies, retail, institutional, holdings, histories


def _clear(engine, companies, orders, agents, holdings):
    by_agent = {a.agent_id: a for a in agents}
    total_volume = 0
    for company in companies:
        book = [o for o in orders if o.ticker == company.ticker]
        cp, txns, vol = engine.matching_engine.resolve_order_book(book, company.current_price)
        if not txns and book:
            cp = engine._one_sided_pressure(book, company.current_price)
        for t in txns:
            engine._settle_transaction(t, company.ticker, by_agent, holdings)
        company.current_price = cp
        total_volume += vol
    for a in agents:
        if a.cash_balance <= 0:
            a.is_bankrupt = True
    return total_volume


def _index(companies):
    return statistics.mean(c.current_price / c.anchor_price for c in companies) * 100.0


EVENT_HEADLINE = "Major bank collapse triggers global credit freeze"


def run(seed: int) -> dict:
    rng_world = random.Random(seed)
    companies, retail, institutional, holdings, histories = _build_world(rng_world)
    agents = retail + institutional
    known = set(TICKERS)
    impact = _event_impact()
    engine = TickEngine(router=_DummyRouter(), forecaster=_StubForecaster(), matching_engine=MatchingEngine())

    index_at = {0: _index(companies)}
    min_price_ratio = 1.0
    trades_ticks = 0

    for tick in range(1, TOTAL_TICKS + 1):
        event_active = CALM_UNTIL < tick <= EVENT_UNTIL
        event_str = EVENT_HEADLINE if event_active else ""
        event_intensity = engine._event_intensity(event_str, tick)
        engine.event_impact = impact if event_active else {}
        if event_intensity > 0:
            engine._apply_event_sentiment(companies, event_intensity)

        rng = random.Random(tick * 1_000_003 + 1)
        local = engine.behavioral.generate(tick, companies, retail, holdings, histories, event_intensity, rng)
        raw = engine._forecast_all(histories)
        anchors = {c.ticker: c.anchor_price for c in companies}
        forecasts = engine._condition_forecast(raw, anchors, engine.event_impact)
        retail_orders = engine._materialize_retail(local, retail, holdings, known, tick)
        inst_orders = engine._build_institutional_orders(tick, institutional, holdings, forecasts, companies)
        vol = _clear(engine, companies, retail_orders + inst_orders, agents, holdings)
        for c in companies:
            histories[c.ticker].append(c.current_price)
        if vol > 0:
            trades_ticks += 1
        index_at[tick] = _index(companies)
        min_price_ratio = min(min_price_ratio, min(c.current_price / c.anchor_price for c in companies))

    return {"index_at": index_at, "min_price_ratio": min_price_ratio, "trades_ticks": trades_ticks}


def grade(r: dict) -> bool:
    idx = r["index_at"]
    calm = idx[CALM_UNTIL]
    event_lows = [idx[t] for t in range(CALM_UNTIL + 1, EVENT_UNTIL + 1)]
    trough = min(event_lows)
    late_event = [idx[t] for t in range(EVENT_UNTIL - 14, EVENT_UNTIL + 1)]
    end = idx[TOTAL_TICKS]
    crash_pct = (trough - calm) / calm * 100
    # slope over the last 15 event ticks (per tick %): near-zero = found a floor
    late_slope = (late_event[-1] - late_event[0]) / max(1, len(late_event) - 1) / late_event[0] * 100
    recover_pct = (end - trough) / trough * 100

    checks = []
    def chk(label, ok):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    print(f"\nGRADE  calm={calm:.1f} trough={trough:.1f} end={end:.1f} "
          f"minRatio={r['min_price_ratio']*100:.1f}% crash={crash_pct:.1f}% "
          f"lateSlope={late_slope:+.2f}%/tick recover={recover_pct:+.1f}%")
    chk("trades clear on >=80% of ticks", r["trades_ticks"] >= TOTAL_TICKS * 0.8)
    chk(f"a real crash happened (<= -8%)", crash_pct <= -8.0)
    chk(f"crash is BOUNDED, not a collapse (trough >= 35, i.e. >-65%)", trough >= 35.0)
    chk(f"no company pinned near zero (min ratio >= 8%)", r["min_price_ratio"] >= 0.08)
    chk(f"market finds a floor late in event (slope >= -0.4%/tick)", late_slope >= -0.4)
    chk(f"recovery after event clears (>= +2%)", recover_pct >= 2.0)
    chk("no NaN / explosion", all(1.0 < idx[t] < 400.0 for t in idx))

    passed = all(checks)
    print(f"\nOVERALL: {'ALL PASS' if passed else f'{sum(checks)}/{len(checks)} - NEEDS FIX'}")
    return passed


if __name__ == "__main__":
    all_ok = True
    for seed in SEEDS:
        print(f"\n===== SEED {seed} =====")
        all_ok = grade(run(seed)) and all_ok
    print(f"\n########## {'ALL SEEDS PASS' if all_ok else 'SOME SEEDS FAILED'} ##########")
    raise SystemExit(0 if all_ok else 1)
