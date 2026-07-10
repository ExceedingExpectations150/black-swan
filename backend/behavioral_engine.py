"""Local behavioral trading engine for ChaosNet "Black Swan".

Free, deterministically-seeded retail decision model — the market's guaranteed
liquidity source. It runs every tick with NO external API and NO paid LLM, so
price discovery never depends on Gemini quota. When Gemini quota IS available,
the Gemma swarm's decisions override this engine per agent (see tick_engine).

Each retail agent forms an expected next price from four real signals —
momentum (recent return), value (gap versus its real-market anchor), crowd
sentiment, and event fear — then posts a limit order on the side of its
conviction. Optimists bid ABOVE the current price, pessimists offer BELOW it;
the CDA clears where the two crowds cross, and the volume-weighted balance of
that flow is what moves the price. A Black Swan event injects fear into the
expected return, tilting the crowd to the sell side, so the crash emerges from
genuine order flow — not a scripted price path.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any

from models import AgentState, Company

# Signal weights. Tuned (see test_market_dynamics.py) so a sustained event
# produces a clear multi-percent decline over ~10-20 ticks, and value buyers
# stage a partial recovery once the event clears — without runaway explosions.
MOMENTUM_WEIGHT: float = 0.6
VALUE_WEIGHT: float = 0.15
SENTIMENT_WEIGHT: float = 0.03
EVENT_FEAR_WEIGHT: float = 0.03
BASE_NOISE_SIGMA: float = 0.008
# Always-on market factor: one small systematic draw per tick shared by the
# whole crowd, so names co-move (market beta) and the market "breathes" with
# realistic intraday wander even with no event active — instead of sitting flat
# at the open. Mean zero, so it adds texture without a built-in drift.
MARKET_FACTOR_VOL: float = 0.0035
# Per-tick expected-return clamp so no single order sits absurdly far from the
# current price (keeps limit prices — and therefore clearing prices — sane).
MAX_EXPECTED_RETURN: float = 0.20
# Fraction of cash a retail agent will commit to one BUY order (before the
# tick engine applies its own affordability cap).
CASH_ORDER_FRACTION: float = 0.20
# Baseline |expected return| an agent needs before it bothers to trade; scaled
# up for cautious (low-risk) agents so they sit out weak signals.
MIN_SIGNAL: float = 0.003
# Divisor mapping |expected return| -> conviction in [0, 1] (5% move = full).
CONVICTION_SCALE: float = 0.05


@dataclass(frozen=True)
class _Traits:
    """Stable per-agent personality, derived once from the agent id."""

    momentum: float   # +trend-follower / -contrarian
    value: float      # dip-buying strength vs the real-market anchor
    sentiment: float  # sensitivity to crowd sentiment
    fear: float       # panic amplification during an active event
    activity: float   # probability of acting on any one eligible name


class LocalBehavioralEngine:
    """Rule-based retail crowd — always-on order flow with no external calls."""

    def __init__(self) -> None:
        self._traits: dict[str, _Traits] = {}

    def _traits_for(self, agent_id: str) -> _Traits:
        cached = self._traits.get(agent_id)
        if cached is not None:
            return cached
        # SHA-1 of the agent id -> a stable seed, so each agent keeps the same
        # personality across ticks and restarts (no schema columns needed).
        seed = int(hashlib.sha1(agent_id.encode()).hexdigest()[:8], 16)
        r = random.Random(seed)
        traits = _Traits(
            momentum=r.uniform(-1.0, 1.0),
            value=r.uniform(0.0, 1.0),
            sentiment=r.uniform(0.0, 1.5),
            fear=r.uniform(0.5, 2.0),
            activity=r.uniform(0.35, 0.9),
        )
        self._traits[agent_id] = traits
        return traits

    def generate(
        self,
        tick_id: int,
        companies: list[Company],
        retail: list[AgentState],
        holdings: dict[str, dict[str, int]],
        histories: dict[str, list[float]],
        event_intensity: float,
        rng: random.Random,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return {agent_id: [order-intent dicts]} for the whole retail crowd.

        Intent shape: {"ticker", "action", "qty", "limit_price"}. Quantities
        are desired sizes; the tick engine applies the final cash/holding caps
        when it materializes these into OrderBook rows.
        """
        intents: dict[str, list[dict[str, Any]]] = {}
        # One systematic market factor per tick, shared by the whole crowd, so
        # names co-move and the market breathes even with no event (realistic
        # open instead of a flat tape).
        market_factor = rng.gauss(0.0, MARKET_FACTOR_VOL)
        for company in companies:
            hist = histories.get(company.ticker) or [company.current_price]
            recent_return = 0.0
            if len(hist) >= 2 and hist[-2] > 0:
                recent_return = (hist[-1] - hist[-2]) / hist[-2]
            value_gap = 0.0
            if company.anchor_price > 0:
                # Positive when the sim price is BELOW the real anchor (cheap).
                value_gap = (company.anchor_price - company.current_price) / company.anchor_price
            sentiment = company.sentiment

            for agent in retail:
                traits = self._traits_for(agent.agent_id)
                if rng.random() > traits.activity:
                    continue
                noise = rng.gauss(0.0, BASE_NOISE_SIGMA * (0.5 + agent.risk_tolerance))
                expected_return = (
                    MOMENTUM_WEIGHT * traits.momentum * recent_return
                    + VALUE_WEIGHT * traits.value * value_gap
                    + SENTIMENT_WEIGHT * traits.sentiment * sentiment
                    - EVENT_FEAR_WEIGHT * traits.fear * event_intensity
                    + market_factor
                    + noise
                )
                expected_return = max(
                    -MAX_EXPECTED_RETURN, min(MAX_EXPECTED_RETURN, expected_return)
                )
                if abs(expected_return) < MIN_SIGNAL * (1.5 - agent.risk_tolerance):
                    continue

                limit_price = round(max(0.01, company.current_price * (1.0 + expected_return)), 2)
                conviction = min(1.0, abs(expected_return) / CONVICTION_SCALE)
                if expected_return > 0:
                    action = "BUY"
                    budget = (
                        agent.cash_balance
                        * CASH_ORDER_FRACTION
                        * (0.3 + 0.7 * agent.risk_tolerance)
                    )
                    qty = int(budget * conviction / limit_price)
                else:
                    action = "SELL"
                    held = holdings.get(agent.agent_id, {}).get(company.ticker, 0)
                    qty = int(held * (0.2 + 0.8 * conviction))
                if qty <= 0:
                    continue
                intents.setdefault(agent.agent_id, []).append(
                    {
                        "ticker": company.ticker,
                        "action": action,
                        "qty": qty,
                        "limit_price": limit_price,
                    }
                )
        return intents
