"""Heuristic behavioral trading agents — the always-on real market layer.

Every retail cohort is a live trader with a persistent strategy and real
state (cash, holdings). Each tick it examines the market — current price,
recent price history, its fundamental anchor, and news sentiment — and posts
genuine limit orders into the continuous double auction. Prices move only
because orders cross in the matching engine: there is NO synthetic price
shaping anywhere in this module or downstream of it.

Strategy mix (classic heterogeneous-agent market microstructure, in the
spirit of Chiarella/LeBaron agent-based models):

- fundamentalists (~40%): estimate fair value (the real-market anchor price
  shifted by their belief about the news) and trade toward it. They
  stabilize the market and anchor it to fundamentals.
- chartists (~35%): extrapolate recent returns and chase the trend. They
  amplify moves and create momentum runs and reversals.
- noise traders (~25%): small random orders around the current price. They
  provide liquidity, keep the book two-sided, and create micro-volatility.

The Black Swan event enters through BELIEFS, not prices: an active event is
a market-wide fear prior layered onto per-company news sentiment, so
fundamentalists mark fair values down, chartists dump into weakness, and the
crash (or rally, for positive news) emerges from real order flow. Cohorts
whose decisions were already made by the LLM swarm this tick are skipped —
the LLM overrides the heuristic when quota allows.

Randomness here is agent behavior (which names a trader looks at, how a
noise trader leans), seeded per (agent, tick) so a tick is reproducible.
"""

from __future__ import annotations

import hashlib
import random
import uuid

from models import AgentState, Company, OrderBook, OrderStatus, OrderType

# Strategy assignment weights (persistent per agent via agent_id hash).
FUNDAMENTALIST_SHARE: float = 0.40
CHARTIST_SHARE: float = 0.35  # remainder are noise traders

# Fundamentalists: how strongly sentiment shifts the fair-value belief, and
# the mispricing below which they sit out.
FUND_SENTIMENT_WEIGHT: float = 0.15
FUND_MIN_MISPRICING: float = 0.004

# Chartists: lookback for the momentum signal and how much sentiment
# (including event fear) feeds their signal.
CHARTIST_LOOKBACK: int = 5
CHARTIST_SENTIMENT_WEIGHT: float = 0.6
CHARTIST_MIN_SIGNAL: float = 0.002

# An active Black Swan headline is a market-wide fear prior on top of
# per-company sentiment, scaled by a DECAYING intensity supplied per tick by
# the tick engine (1.0 at the shock, decaying toward a small residual). This
# shifts what agents BELIEVE; prices only move if their resulting orders
# actually cross. EVENT_FEAR_PRIOR is the peak magnitude at intensity 1.0.
EVENT_FEAR_PRIOR: float = -0.35

# Order sizing.
MAX_CASH_FRACTION_PER_ORDER: float = 0.25
MAX_INVENTORY_FRACTION_PER_ORDER: float = 0.50
NOISE_CASH_FRACTION: float = 0.04
TICKERS_PER_AGENT: int = 3


def strategy_of(agent_id: str) -> str:
    """Stable strategy assignment from the agent's identity.

    Uses a digest, not builtin hash(), which is salted per process — the
    same agent must keep the same personality across backend restarts.
    """
    digest = int(hashlib.md5(agent_id.encode()).hexdigest(), 16)
    bucket = (digest % 1000) / 1000.0
    if bucket < FUNDAMENTALIST_SHARE:
        return "fundamentalist"
    if bucket < FUNDAMENTALIST_SHARE + CHARTIST_SHARE:
        return "chartist"
    return "noise"


def _effective_sentiment(company: Company, event_intensity: float, risk: float) -> float:
    """The agent's belief about a company: news sentiment plus event fear.

    `event_intensity` is 0.0 with no event, 1.0 at the shock, decaying in
    between. Cautious agents (low risk tolerance) feel the fear more strongly.
    """
    fear = EVENT_FEAR_PRIOR * event_intensity * (1.5 - risk)
    return max(-1.0, min(1.0, company.sentiment + fear))


def _order(
    tick_id: int, agent: AgentState, ticker: str, side: OrderType, qty: int, limit: float
) -> OrderBook:
    return OrderBook(
        order_id=str(uuid.uuid4()),
        tick_id=tick_id,
        agent_id=agent.agent_id,
        ticker=ticker,
        order_type=side,
        quantity=qty,
        limit_price=round(max(0.01, limit), 4),
        status=OrderStatus.PENDING,
    )


def build_behavioral_orders(
    tick_id: int,
    cohorts: list[AgentState],
    companies: list[Company],
    holdings: dict[str, dict[str, int]],
    histories: dict[str, list[float]],
    event_intensity: float,
    decided_agent_ids: set[str],
) -> list[OrderBook]:
    """Real limit orders for every cohort the LLM swarm did not decide for."""
    orders: list[OrderBook] = []
    tradable = [c for c in companies if c.current_price > 0]
    if not tradable:
        return orders

    for agent in cohorts:
        if agent.agent_id in decided_agent_ids:
            continue
        rng = random.Random(f"{agent.agent_id}:{tick_id}")
        strategy = strategy_of(agent.agent_id)
        risk = float(agent.risk_tolerance or 0.5)
        held = holdings.get(agent.agent_id, {})

        # Attention: a few names per tick, biased toward stocks in the news.
        weights = [1.0 + 4.0 * abs(c.sentiment) for c in tradable]
        k = min(TICKERS_PER_AGENT, len(tradable))
        watched: list[Company] = []
        pool = list(zip(tradable, weights))
        for _ in range(k):
            total = sum(w for _, w in pool)
            pick = rng.uniform(0.0, total)
            acc = 0.0
            for i, (comp, w) in enumerate(pool):
                acc += w
                if pick <= acc:
                    watched.append(comp)
                    pool.pop(i)
                    break

        for company in watched:
            price = company.current_price
            belief = _effective_sentiment(company, event_intensity, risk)

            if strategy == "fundamentalist":
                fair = company.anchor_price * (1.0 + FUND_SENTIMENT_WEIGHT * belief)
                mispricing = (fair - price) / price
                if abs(mispricing) < FUND_MIN_MISPRICING:
                    continue
                conviction = min(1.0, abs(mispricing) * 10.0) * (0.4 + 0.6 * risk)
                if mispricing > 0:
                    limit = min(fair, price * (1.0 + 0.002 + 0.01 * conviction))
                    side = OrderType.BUY
                else:
                    limit = max(fair, price * (1.0 - 0.002 - 0.01 * conviction))
                    side = OrderType.SELL

            elif strategy == "chartist":
                hist = histories.get(company.ticker) or []
                lookback = min(CHARTIST_LOOKBACK, len(hist))
                momentum = (
                    (price - hist[-lookback]) / hist[-lookback]
                    if lookback >= 2 and hist[-lookback] > 0
                    else 0.0
                )
                signal = momentum + CHARTIST_SENTIMENT_WEIGHT * belief * 0.02
                if abs(signal) < CHARTIST_MIN_SIGNAL:
                    continue
                conviction = min(1.0, abs(signal) * 25.0) * (0.4 + 0.6 * risk)
                if signal > 0:
                    limit = price * (1.0 + 0.002 + 0.012 * conviction)
                    side = OrderType.BUY
                else:
                    limit = price * (1.0 - 0.002 - 0.012 * conviction)
                    side = OrderType.SELL

            else:  # noise trader — liquidity provider
                side = rng.choice((OrderType.BUY, OrderType.SELL))
                limit = price * (1.0 + rng.uniform(-0.008, 0.008))
                conviction = rng.uniform(0.2, 0.6)

            if side == OrderType.BUY:
                budget_fraction = (
                    NOISE_CASH_FRACTION
                    if strategy == "noise"
                    else MAX_CASH_FRACTION_PER_ORDER * conviction
                )
                qty = int(agent.cash_balance * budget_fraction / limit)
            else:
                have = held.get(company.ticker, 0)
                sell_fraction = (
                    0.15 if strategy == "noise" else MAX_INVENTORY_FRACTION_PER_ORDER * conviction
                )
                qty = int(have * sell_fraction)
                qty = min(qty, have)
            if qty <= 0:
                continue
            orders.append(_order(tick_id, agent, company.ticker, side, qty, limit))

    return orders
