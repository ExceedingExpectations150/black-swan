"""Simulation tick orchestrator for ChaosNet "Black Swan" (PRD.md Phase 3).

One tick = prompt all Gemma retail cohorts in parallel, run the TimesFM
point forecast for the institutional quants, resolve every order through
the Continuous Double Auction, settle cash/inventory, flag bankruptcies,
persist the new WorldState, and return a telemetry payload for WebSockets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
import uuid
from typing import Any

import aiohttp
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_clients import GeminiModelRouter, TimesFMForecaster
from database import SessionLocal
from matching_engine import ClearedTransaction, MatchingEngine
from models import AgentState, AgentType, OrderBook, OrderStatus, OrderType, WorldState

logger = logging.getLogger("chaosnet.tick")

# Fraction of an agent's cash/inventory committed per order.
RETAIL_MAX_ORDER_FRACTION: float = 0.20
INSTITUTIONAL_ORDER_FRACTION: float = 0.05
# Volatility of recent returns is scaled by this factor into the 0-1 stress index.
STRESS_WINDOW_TICKS: int = 20
STRESS_SCALE: float = 25.0

COHORT_PROMPT_TEMPLATE: str = """You are a retail trading cohort in a stock market simulation.
Your risk tolerance is {risk:.2f} on a 0-1 scale (0 = very cautious, 1 = very aggressive).
Current stock price: ${price:.2f}. Your cash: ${cash:.2f}. Your shares: {inventory}.
Breaking market news: {headline}
Decide your next trading action for this tick.
Respond with ONLY a strict JSON object, no markdown, no explanation:
{{"action": "BUY" | "SELL" | "HOLD", "qty": <positive integer>, "limit_price": <positive float>}}"""


def _parse_cohort_decision(raw: str) -> dict[str, Any] | None:
    """Parse a cohort's strict-JSON decision; None if the reply is unusable.

    Tolerates markdown fences and reasoning preamble (Gemma 4 often narrates
    before answering) by validating every JSON-object candidate in the reply
    and returning the first one that matches the decision schema.
    """
    for candidate in re.findall(r"\{.*?\}", raw, flags=re.DOTALL):
        try:
            decision = json.loads(candidate)
            action = str(decision["action"]).upper()
            if action == "HOLD":
                return {"action": "HOLD", "qty": 0, "limit_price": 0.0}
            qty = int(decision["qty"])
            limit_price = float(decision["limit_price"])
            if action in ("BUY", "SELL") and qty > 0 and limit_price > 0:
                return {"action": action, "qty": qty, "limit_price": limit_price}
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return None


class TickEngine:
    """Drives one full market tick across both AI compute layers."""

    def __init__(
        self,
        router: GeminiModelRouter,
        forecaster: TimesFMForecaster,
        matching_engine: MatchingEngine | None = None,
        session_factory: Any = SessionLocal,
    ) -> None:
        self.router = router
        self.forecaster = forecaster
        self.matching_engine = matching_engine or MatchingEngine()
        self.session_factory = session_factory

    async def execute_simulation_tick(
        self,
        tick_id: int,
        active_event: str,
        current_price: float,
        price_history: list[float],
    ) -> dict[str, Any]:
        """Run one tick and return the telemetry payload for broadcasting."""
        with self.session_factory() as db:
            agents: list[AgentState] = list(
                db.execute(
                    select(AgentState).where(AgentState.is_bankrupt.is_(False))
                ).scalars()
            )
            retail = [a for a in agents if a.agent_type == AgentType.GEMMA_RETAIL_COHORT]
            institutional = [
                a for a in agents if a.agent_type == AgentType.TIMESFM_INSTITUTIONAL
            ]

            # Both compute layers run concurrently: Gemma swarm over HTTP,
            # TimesFM on a worker thread so it doesn't block the event loop.
            cohort_task = self._prompt_retail_cohorts(retail, active_event, current_price)
            forecast_task = asyncio.to_thread(
                self.forecaster.forecast_next_tick, price_history
            )
            cohort_decisions, prediction = await asyncio.gather(cohort_task, forecast_task)

            orders: list[OrderBook] = []
            orders += self._build_retail_orders(tick_id, retail, cohort_decisions)
            orders += self._build_institutional_orders(
                tick_id, institutional, prediction, current_price
            )

            clearing_price, transactions, total_volume = (
                self.matching_engine.resolve_order_book(orders, current_price)
            )

            self._settle(agents, transactions)

            # Per-tick batch auction: whatever did not fill is dead at tick end.
            for order in orders:
                if order.status == OrderStatus.PENDING:
                    order.status = OrderStatus.CANCELLED

            stress = self._stress_index(price_history + [clearing_price])
            world_state = WorldState(
                tick_id=tick_id,
                current_price=clearing_price,
                news_headline=active_event,
                system_stress_index=stress,
            )
            db.add(world_state)
            db.add_all(orders)
            db.commit()

            bankrupt_count = sum(1 for a in agents if a.is_bankrupt)
            telemetry: dict[str, Any] = {
                "tick_id": tick_id,
                "clearing_price": round(clearing_price, 4),
                "previous_price": round(current_price, 4),
                "timesfm_prediction": round(float(prediction), 4),
                "total_volume": total_volume,
                "cleared_transactions": len(transactions),
                "orders_submitted": len(orders),
                "system_stress_index": round(stress, 4),
                "news_headline": active_event,
                "agents": {
                    "retail_active": len(retail),
                    "institutional_active": len(institutional),
                    "bankrupt_total": bankrupt_count,
                },
                "timestamp": world_state.timestamp.isoformat()
                if world_state.timestamp
                else None,
            }
            return telemetry

    async def _prompt_retail_cohorts(
        self, retail: list[AgentState], active_event: str, current_price: float
    ) -> list[dict[str, Any] | None]:
        """Prompt every Gemma cohort in parallel; returns per-agent decisions."""
        headline = active_event if active_event else "No major news this tick."
        async with aiohttp.ClientSession() as session:

            async def ask(agent: AgentState) -> dict[str, Any] | None:
                prompt = COHORT_PROMPT_TEMPLATE.format(
                    risk=agent.risk_tolerance,
                    price=current_price,
                    cash=agent.cash_balance,
                    inventory=agent.stock_inventory,
                    headline=headline,
                )
                try:
                    raw = await self.router.prompt_cohort(session, prompt)
                except RuntimeError as exc:
                    logger.warning("cohort %s failed: %s", agent.agent_id[:8], exc)
                    return None
                decision = _parse_cohort_decision(raw)
                if decision is None:
                    logger.warning(
                        "cohort %s returned unparseable decision: %.120s",
                        agent.agent_id[:8],
                        raw,
                    )
                return decision

            return list(await asyncio.gather(*(ask(a) for a in retail)))

    def _build_retail_orders(
        self,
        tick_id: int,
        retail: list[AgentState],
        decisions: list[dict[str, Any] | None],
    ) -> list[OrderBook]:
        orders: list[OrderBook] = []
        for agent, decision in zip(retail, decisions):
            if decision is None or decision["action"] == "HOLD":
                continue
            qty = decision["qty"]
            limit_price = decision["limit_price"]
            if decision["action"] == "BUY":
                affordable = int(
                    (agent.cash_balance * RETAIL_MAX_ORDER_FRACTION) / limit_price
                )
                qty = min(qty, affordable)
                order_type = OrderType.BUY
            else:
                qty = min(qty, agent.stock_inventory)
                order_type = OrderType.SELL
            if qty <= 0:
                continue
            orders.append(
                OrderBook(
                    # Set eagerly: the column default only fires at flush, and
                    # the matching engine needs distinct ids before persistence.
                    order_id=str(uuid.uuid4()),
                    tick_id=tick_id,
                    agent_id=agent.agent_id,
                    order_type=order_type,
                    quantity=qty,
                    limit_price=limit_price,
                    status=OrderStatus.PENDING,
                )
            )
        return orders

    def _build_institutional_orders(
        self,
        tick_id: int,
        institutional: list[AgentState],
        prediction: float,
        current_price: float,
    ) -> list[OrderBook]:
        """Quants trade the TimesFM signal: BUY if forecast > price, else SELL."""
        orders: list[OrderBook] = []
        for agent in institutional:
            if prediction > current_price:
                qty = int(
                    (agent.cash_balance * INSTITUTIONAL_ORDER_FRACTION) / prediction
                )
                order_type = OrderType.BUY
            else:
                qty = int(agent.stock_inventory * INSTITUTIONAL_ORDER_FRACTION)
                order_type = OrderType.SELL
            if qty <= 0:
                continue
            orders.append(
                OrderBook(
                    order_id=str(uuid.uuid4()),
                    tick_id=tick_id,
                    agent_id=agent.agent_id,
                    order_type=order_type,
                    quantity=qty,
                    limit_price=float(prediction),
                    status=OrderStatus.PENDING,
                )
            )
        return orders

    def _settle(
        self, agents: list[AgentState], transactions: list[ClearedTransaction]
    ) -> None:
        """Move cash and inventory for every cleared trade; flag bankruptcies."""
        by_id: dict[str, AgentState] = {a.agent_id: a for a in agents}
        for t in transactions:
            buyer = by_id[t.buyer_agent_id]
            seller = by_id[t.seller_agent_id]
            notional = t.quantity * t.price
            buyer.cash_balance -= notional
            buyer.stock_inventory += t.quantity
            seller.cash_balance += notional
            seller.stock_inventory -= t.quantity
        for agent in agents:
            if agent.cash_balance <= 0:
                agent.is_bankrupt = True

    @staticmethod
    def _stress_index(prices: list[float]) -> float:
        """0-1 stress from volatility of recent tick-over-tick returns."""
        window = prices[-STRESS_WINDOW_TICKS:]
        if len(window) < 3:
            return 0.0
        returns = [
            (b - a) / a for a, b in zip(window, window[1:]) if a != 0
        ]
        if len(returns) < 2:
            return 0.0
        return min(1.0, statistics.stdev(returns) * STRESS_SCALE)
