"""Simulation tick orchestrator for ChaosNet "Black Swan".

Multi-ticker orchestration (full per-ticker CDA — the lighter beta-derived
fallback was NOT chosen; every company clears through the real matching
engine each tick). Canonical per-tick sequence:

  1. tick_start
  2. macro news (headline passed in from the controller)
  3. Corporate PR agents post -> per-company sentiment nudges
  4. behavioral swarm (batched Gemma calls) -> per-ticker orders; cohorts the
     LLM did not decide for trade via their heuristic strategy
     (behavioral_agents.py: fundamentalists / chartists / noise traders)
  5. quant funds (TimesFM per ticker) -> per-ticker orders
  6. CDA match per ticker -> price_ticks, settlement, bankruptcies
  7. economy analysis (+ Macro Analyst every N ticks)
  8. return the ordered WebSocket event list for broadcast

Prices are set exclusively by the matching engine on real order flow — no
synthetic price shaping. Gemma quota discipline: ONE PR-desk call +
ceil(cohorts/COHORT_BATCH_SIZE) swarm calls per tick, all through the shared
429-rotating router. TimesFM runs locally (free).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_clients import GeminiModelRouter, TimesFMForecaster
from behavioral_agents import build_behavioral_orders
from database import SessionLocal
from economy import compute_economy_snapshot, persist_economy_snapshot
from matching_engine import ClearedTransaction, MatchingEngine
from models import (
    AgentHolding,
    AgentState,
    AgentType,
    AuthorType,
    Company,
    CompanySnapshot,
    OrderBook,
    OrderStatus,
    OrderType,
    PriceTick,
    SocialPost,
    WorldState,
)
from social_agents import CompanyPRContext, NewsPublisher, MacroAnalyst, PostDraft

logger = logging.getLogger("chaosnet.tick")

# Cohorts are batched per Gemini call. Free-tier flash allows ~5
# requests/min, so a tick's LLM footprint must stay small: with the default
# batch of 50, a 150-cohort swarm costs 3 swarm calls + 1 PR-desk call per
# tick. Tune via BLACKSWAN_SWARM_BATCH alongside BLACKSWAN_RETAIL_COHORTS.
COHORT_BATCH_SIZE: int = max(1, int(os.getenv("BLACKSWAN_SWARM_BATCH", "50")))
RETAIL_MAX_ORDER_FRACTION: float = 0.80
INSTITUTIONAL_CASH_FRACTION_PER_TICKER: float = 0.05
INSTITUTIONAL_INVENTORY_FRACTION: float = 0.15
SOCIAL_DIGEST_POSTS: int = 8
SENTIMENT_CARRYOVER: float = 0.7
ANALYST_EVERY_N_TICKS: int = 5
TIMESFM_CONTEXT: int = 512
# One-sided book pressure: when real orders exist but nothing crosses (e.g.
# a panic where everyone sells and nobody bids), the indicative price moves
# this fraction of the way toward the dominant side's best unmatched quote.
# Deterministic mechanics on real order flow — not a stand-in for the CDA,
# which still sets the price whenever a trade clears.
IMBALANCE_PRESSURE: float = 0.25
# NOTE: the former EVENT_SHOCK_* synthetic price overlay is gone. All price
# movement now comes from real order flow: heuristic behavioral cohorts
# (behavioral_agents.py), LLM swarm cohorts when quota allows, and TimesFM
# institutional funds — matched in the CDA. The Black Swan event impacts
# prices only through agent beliefs (sentiment + fear prior), never through
# a multiplier on the clearing price.

SWARM_PROMPT_TEMPLATE: str = """You are a JSON API simulating {n} retail trading cohorts in a multi-stock market. You never explain; you only output JSON.

CRITICAL INSTRUCTION: You MUST aggressively analyze the MACRO NEWS and SOCIAL FEED. If the news is positive for a specific ticker, cohorts should aggressively BUY that ticker with all available cash. If the news is negative for a ticker or its competitors, cohorts should aggressively SELL and dump their holdings. 


MACRO NEWS: {headline}

MARKET SNAPSHOT (ticker | price | change vs real anchor | crowd sentiment -1..1):
{market_block}

RECENT SOCIAL FEED:
{social_block}

COHORTS (index | risk tolerance 0=cautious..1=aggressive | cash | holdings):
{cohort_block}

For EACH cohort decide zero or more limit orders consistent with its risk
profile, cash, and holdings. Aggressive cohorts heavily buy/sell based on the news;
cautious ones de-risk. A cohort may do nothing (empty orders list) if the news is irrelevant to them.
Do not think out loud. Your ENTIRE reply must be only a strict JSON array
starting with the character [ and nothing else, exactly this shape:
[{{"cohort": 0, "orders": [{{"ticker": "AAPL", "action": "BUY", "qty": 10, "limit_price": 232.5}}]}}]"""


# Simulated-clock override. The controller sets this at the top of every tick
# (single-writer: ticks execute one at a time on the event loop), so every
# event envelope and DB timestamp within a tick carries simulated time rather
# than wall-clock time. Empty string = fall back to wall clock.
_SIM_TS: str = ""


def _set_sim_ts(iso: str) -> None:
    global _SIM_TS
    _SIM_TS = iso


def _now_iso() -> str:
    return _SIM_TS or datetime.now(timezone.utc).isoformat()


def _event(event_type: str, tick_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": event_type, "tick_id": tick_id, "ts": _now_iso(), "payload": payload}


def _change_pct(company: Company) -> float:
    if company.anchor_price == 0:
        return 0.0
    return (company.current_price - company.anchor_price) / company.anchor_price * 100.0


def _post_payload(post: SocialPost) -> dict[str, Any]:
    return {
        "post_id": post.post_id,
        "tick_id": post.tick_id,
        "ts": post.ts.isoformat() if post.ts else _now_iso(),
        "author_type": post.author_type.value,
        "author_ticker": post.author_ticker,
        "author_display": post.author_display,
        "handle": post.handle,
        "content": post.content,
        "sentiment": post.sentiment,
        "likes": post.likes,
        "reposts": post.reposts,
    }


def parse_swarm_reply(raw: str, n_cohorts: int) -> dict[int, list[dict[str, Any]]]:
    """Extract per-cohort order lists from a batched swarm reply.

    Tolerates reasoning preamble and fences; validates every order shape.
    Unknown cohort indices and malformed orders are dropped (an absent
    decision is a HOLD — nothing is fabricated).

    Selection: real-JSON decode at every '[' position, keep the LAST array
    whose elements look like cohort entries. Gemma 4 chain-of-thought can
    contain draft arrays before the answer, and the answer itself NESTS
    per-cohort "orders" arrays — a plain last-array rule would latch onto
    an inner (possibly empty) orders list.
    """
    decoder = json.JSONDecoder()
    entries: list[Any] | None = None
    for match in re.finditer(r"\[", raw):
        try:
            value, _ = decoder.raw_decode(raw, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and any(
            isinstance(e, dict) and "cohort" in e for e in value
        ):
            entries = value
    if entries is None:
        return {}
    decisions: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        try:
            idx = int(entry["cohort"])
            if not 0 <= idx < n_cohorts:
                continue
            orders: list[dict[str, Any]] = []
            for o in entry.get("orders", []):
                action = str(o["action"]).upper()
                qty = int(o["qty"])
                limit_price = float(o["limit_price"])
                ticker = str(o["ticker"]).upper()
                if action in ("BUY", "SELL") and qty > 0 and limit_price > 0:
                    orders.append(
                        {"ticker": ticker, "action": action, "qty": qty, "limit_price": limit_price}
                    )
            decisions[idx] = orders
        except (KeyError, TypeError, ValueError):
            continue
    return decisions


class TickEngine:
    """Drives one full multi-ticker market tick across all AI layers."""

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
        self.news_desk = NewsPublisher(router)
        self.analyst = MacroAnalyst(router)

    async def execute_simulation_tick(
        self, tick_id: int, active_event: str, sim_time: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Run one tick; returns the ordered event list for /ws broadcast."""
        self._sim_time = sim_time or datetime.now(timezone.utc)
        _set_sim_ts(self._sim_time.isoformat())
        events: list[dict[str, Any]] = [_event("tick_start", tick_id, {"tick_id": tick_id})]
        events.append(_event("news", tick_id, {"headline": active_event}))

        with self.session_factory() as db:
            companies: list[Company] = list(
                db.execute(select(Company).where(Company.is_bankrupt.is_(False))).scalars()
            )
            agents: list[AgentState] = list(
                db.execute(select(AgentState).where(AgentState.is_bankrupt.is_(False))).scalars()
            )
            retail = [a for a in agents if a.agent_type == AgentType.GEMMA_RETAIL_COHORT]
            institutional = [a for a in agents if a.agent_type == AgentType.TIMESFM_INSTITUTIONAL]
            holdings = self._load_holdings(db, agents)
            recent_posts: list[SocialPost] = list(
                db.execute(
                    select(SocialPost).order_by(SocialPost.tick_id.desc()).limit(SOCIAL_DIGEST_POSTS)
                ).scalars()
            )
            histories = self._load_price_histories(db, companies)

            async with aiohttp.ClientSession() as http:
                # 3 & 4 & 5. Run News Publisher, Behavioral swarm (Gemma), 
                # and quant funds (TimesFM) concurrently. 
                # The swarm will react to the previous tick's feed to decouple them.
                news_task = self._run_news_desk(db, http, companies, active_event, tick_id)
                swarm_task = self._run_swarm(
                    http, companies, retail, holdings, recent_posts, active_event, tick_id
                )
                quant_task = asyncio.to_thread(self._forecast_all, histories)
                
                posts, swarm_result, forecasts = await asyncio.gather(
                    news_task, swarm_task, quant_task
                )
                swarm_orders, llm_decided = swarm_result

                events += [_event("social_post", tick_id, _post_payload(p)) for p in posts]

                # Cohorts the LLM decided for keep their LLM decision — even
                # a deliberate HOLD (an entry with no orders). Every other
                # cohort trades via its heuristic strategy, so the market is
                # fully populated with real order flow regardless of LLM
                # quota. Prices are set ONLY by the CDA below.
                decided = llm_decided | {o.agent_id for o in swarm_orders}
                behavioral_orders = build_behavioral_orders(
                    tick_id,
                    retail,
                    companies,
                    holdings,
                    histories,
                    event_active=bool(active_event),
                    decided_agent_ids=decided,
                )
                orders = (
                    swarm_orders
                    + behavioral_orders
                    + self._build_institutional_orders(
                        tick_id, institutional, holdings, forecasts, companies
                    )
                )

                # 6. CDA per ticker (the one and only matching engine).
                price_updates = self._clear_markets(
                    db, tick_id, companies, orders, agents, holdings
                )
                events.append(_event("price_update", tick_id, {"prices": price_updates}))

                # 7. Economy analysis (+ periodic macro analyst). The analyst
                # is best-effort like the PR desk / swarm: a rate-limit or API
                # error must not crash the whole simulation loop.
                snapshot = compute_economy_snapshot(db, tick_id)
                if tick_id % ANALYST_EVERY_N_TICKS == 0:
                    digest = self._economy_digest(snapshot)
                    try:
                        narrative = await self.analyst.narrate(http, digest)
                    except RuntimeError as exc:
                        logger.warning("macro analyst failed (tick %d): %s", tick_id, exc)
                        narrative = ""
                    if narrative:
                        snapshot["narrative"] = narrative
                persist_economy_snapshot(db, snapshot)

            events.append(
                _event(
                    "company_update",
                    tick_id,
                    {
                        "companies": [
                            {
                                "ticker": c.ticker,
                                "sentiment": round(c.sentiment, 4),
                                "volatility": round(c.volatility, 6),
                                "market_cap": c.current_price * c.shares_outstanding,
                                "is_bankrupt": c.is_bankrupt,
                            }
                            for c in companies
                        ]
                    },
                )
            )
            events.append(_event("economy_update", tick_id, snapshot))

            db.add_all(
                CompanySnapshot(
                    tick_id=tick_id,
                    ticker=c.ticker,
                    current_price=c.current_price,
                    sentiment=c.sentiment,
                    volatility=c.volatility,
                    is_bankrupt=c.is_bankrupt,
                )
                for c in companies
            )

            # Legacy single-asset world_states row now tracks the sim index
            # (100 = at anchor) so the original PRD tables keep filling.
            index_level = 100.0 * (
                1.0 + sum(_change_pct(c) for c in companies) / (100.0 * max(1, len(companies)))
            )
            db.add(
                WorldState(
                    tick_id=tick_id,
                    current_price=index_level,
                    news_headline=active_event,
                    system_stress_index=snapshot["system_stress_index"],
                    timestamp=self._sim_time,
                )
            )
            db.commit()

        events.append(_event("tick_end", tick_id, {"tick_id": tick_id}))
        return events

    # ------------------------------------------------------------------ #
    # News Desk                                                           #
    # ------------------------------------------------------------------ #

    async def _run_news_desk(
        self,
        db: Session,
        http: aiohttp.ClientSession,
        companies: list[Company],
        headline: str,
        tick_id: int,
    ) -> list[SocialPost]:
        by_sector: dict[str, list[Company]] = {}
        for c in companies:
            by_sector.setdefault(c.sector, []).append(c)

        contexts: list[CompanyPRContext] = []
        for c in companies:
            peers = [p for p in by_sector[c.sector] if p.ticker != c.ticker]
            if peers:
                rival = max(peers, key=lambda p: abs(_change_pct(p)))
                note = f"{rival.name} is {_change_pct(rival):+.1f}% vs anchor"
            else:
                note = "no direct competitor in the index"
            contexts.append(
                CompanyPRContext(
                    ticker=c.ticker,
                    name=c.name,
                    sector=c.sector,
                    price=c.current_price,
                    change_pct=round(_change_pct(c), 2),
                    sentiment=round(c.sentiment, 3),
                    competitor_note=note,
                )
            )

        try:
            drafts: list[PostDraft] = await asyncio.wait_for(
                self.news_desk.generate_posts(
                    http, contexts, headline or "No major macro news."
                ),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            logger.warning("News desk call timed out after 60s (tick %d)", tick_id)
            return []
        except RuntimeError as exc:
            logger.warning("News desk call failed (tick %d): %s", tick_id, exc)
            return []

        by_ticker = {c.ticker: c for c in companies}
        posts: list[SocialPost] = []
        for d in drafts:
            company = by_ticker.get(d.ticker)
            if company is None:
                continue
            company.sentiment = max(
                -1.0,
                min(1.0, SENTIMENT_CARRYOVER * company.sentiment + (1 - SENTIMENT_CARRYOVER) * d.sentiment),
            )
            post = SocialPost(
                post_id=str(uuid.uuid4()),
                tick_id=tick_id,
                author_type=AuthorType.ANALYST,
                author_ticker=d.ticker,
                author_display="Financial News",
                handle="@FinancialNews",
                content=d.content,
                sentiment=d.sentiment,
                likes=int(150 * abs(d.sentiment)) + 25,
                reposts=(int(150 * abs(d.sentiment)) + 25) // 6,
                ts=getattr(self, "_sim_time", None) or datetime.now(timezone.utc),
            )
            db.add(post)
            posts.append(post)
        return posts

    # ------------------------------------------------------------------ #
    # Behavioral swarm                                                    #
    # ------------------------------------------------------------------ #

    async def _run_swarm(
        self,
        http: aiohttp.ClientSession,
        companies: list[Company],
        retail: list[AgentState],
        holdings: dict[str, dict[str, int]],
        feed: list[SocialPost],
        headline: str,
        tick_id: int,
    ) -> tuple[list[OrderBook], set[str]]:
        market_block = "\n".join(
            f"{c.ticker} | ${c.current_price:.2f} | {_change_pct(c):+.2f}% | {c.sentiment:+.2f}"
            for c in companies
        )
        social_block = (
            "\n".join(
                f"{p.handle}: {p.content[:90]}" for p in feed[-SOCIAL_DIGEST_POSTS:]
            )
            or "(feed is quiet)"
        )
        known = {c.ticker for c in companies}
        by_ticker_price = {c.ticker: c.current_price for c in companies}

        async def run_batch(batch: list[AgentState], base: int) -> list[OrderBook]:
            cohort_block = "\n".join(
                "{i} | {r:.2f} | ${cash:,.0f} | {h}".format(
                    i=i,
                    r=a.risk_tolerance,
                    cash=a.cash_balance,
                    h=", ".join(
                        f"{t}:{q}" for t, q in sorted(holdings[a.agent_id].items()) if q > 0
                    )
                    or "none",
                )
                for i, a in enumerate(batch)
            )
            prompt = SWARM_PROMPT_TEMPLATE.format(
                n=len(batch),
                headline=headline or "No major macro news.",
                market_block=market_block,
                social_block=social_block,
                cohort_block=cohort_block,
            )
            try:
                raw = await asyncio.wait_for(
                    self.router.prompt_cohort(http, prompt),
                    timeout=60.0
                )
            except asyncio.TimeoutError:
                logger.warning("swarm batch @%d timed out after 60s (tick %d)", base, tick_id)
                return [], set()
            except RuntimeError as exc:
                logger.warning("swarm batch @%d failed (tick %d): %s", base, tick_id, exc)
                return [], set()
            decisions = parse_swarm_reply(raw, len(batch))
            if not decisions:
                logger.warning(
                    "swarm batch @%d (tick %d): reply yielded no decisions: %.150s",
                    base,
                    tick_id,
                    raw,
                )
            built: list[OrderBook] = []
            for idx, orders in decisions.items():
                agent = batch[idx]
                for o in orders:
                    if o["ticker"] not in known:
                        continue
                    qty = o["qty"]
                    if o["action"] == "BUY":
                        affordable = int(
                            agent.cash_balance * RETAIL_MAX_ORDER_FRACTION / o["limit_price"]
                        )
                        qty = min(qty, affordable)
                        side = OrderType.BUY
                    else:
                        qty = min(qty, holdings[agent.agent_id].get(o["ticker"], 0))
                        side = OrderType.SELL
                    if qty <= 0:
                        continue
                    built.append(
                        OrderBook(
                            order_id=str(uuid.uuid4()),
                            tick_id=tick_id,
                            agent_id=agent.agent_id,
                            ticker=o["ticker"],
                            order_type=side,
                            quantity=qty,
                            limit_price=o["limit_price"],
                            status=OrderStatus.PENDING,
                        )
                    )
            # An entry in the reply is a decision even when its orders list
            # is empty — a deliberate HOLD must not be overridden by the
            # heuristic layer downstream.
            decided = {batch[idx].agent_id for idx in decisions}
            return built, decided

        batches = [
            retail[i : i + COHORT_BATCH_SIZE] for i in range(0, len(retail), COHORT_BATCH_SIZE)
        ]
        results = await asyncio.gather(
            *(run_batch(b, i * COHORT_BATCH_SIZE) for i, b in enumerate(batches))
        )
        orders = [order for built, _ in results for order in built]
        decided_ids = set().union(*(decided for _, decided in results)) if results else set()
        return orders, decided_ids

    # ------------------------------------------------------------------ #
    # Quant funds                                                         #
    # ------------------------------------------------------------------ #

    def _forecast_all(self, histories: dict[str, list[float]]) -> dict[str, float]:
        forecasts: dict[str, float] = {}
        for ticker, series in histories.items():
            forecasts[ticker] = self.forecaster.forecast_next_tick(series)
        return forecasts

    def _build_institutional_orders(
        self,
        tick_id: int,
        institutional: list[AgentState],
        holdings: dict[str, dict[str, int]],
        forecasts: dict[str, float],
        companies: list[Company],
    ) -> list[OrderBook]:
        orders: list[OrderBook] = []
        by_ticker = {c.ticker: c for c in companies}
        for agent in institutional:
            for ticker, prediction in forecasts.items():
                company = by_ticker.get(ticker)
                if company is None:
                    continue
                if prediction > company.current_price:
                    qty = int(
                        agent.cash_balance * INSTITUTIONAL_CASH_FRACTION_PER_TICKER / prediction
                    )
                    side = OrderType.BUY
                else:
                    qty = int(
                        holdings[agent.agent_id].get(ticker, 0) * INSTITUTIONAL_INVENTORY_FRACTION
                    )
                    side = OrderType.SELL
                if qty <= 0:
                    continue
                orders.append(
                    OrderBook(
                        order_id=str(uuid.uuid4()),
                        tick_id=tick_id,
                        agent_id=agent.agent_id,
                        ticker=ticker,
                        order_type=side,
                        quantity=qty,
                        limit_price=float(prediction),
                        status=OrderStatus.PENDING,
                    )
                )
        return orders

    # ------------------------------------------------------------------ #
    # Clearing & settlement                                               #
    # ------------------------------------------------------------------ #

    def _clear_markets(
        self,
        db: Session,
        tick_id: int,
        companies: list[Company],
        orders: list[OrderBook],
        agents: list[AgentState],
        holdings: dict[str, dict[str, int]],
    ) -> list[dict[str, Any]]:
        by_agent = {a.agent_id: a for a in agents}
        price_updates: list[dict[str, Any]] = []

        for company in companies:
            book = [o for o in orders if o.ticker == company.ticker]
            clearing_price, transactions, volume = self.matching_engine.resolve_order_book(
                book, company.current_price
            )
            if not transactions and book:
                clearing_price = self._one_sided_pressure(book, company.current_price)
            for t in transactions:
                self._settle_transaction(t, company.ticker, by_agent, holdings)
            for o in book:
                if o.status == OrderStatus.PENDING:
                    o.status = OrderStatus.CANCELLED

            company.current_price = clearing_price
            db.add(
                PriceTick(
                    tick_id=tick_id,
                    ticker=company.ticker,
                    price=clearing_price,
                    volume=volume,
                    ts=getattr(self, "_sim_time", None) or datetime.now(timezone.utc),
                )
            )
            price_updates.append(
                {
                    "ticker": company.ticker,
                    "price": round(clearing_price, 4),
                    "change_pct": round(_change_pct(company), 4),
                    "volume": volume,
                }
            )

        db.add_all(orders)
        self._flush_holdings(db, holdings)
        for agent in agents:
            if agent.cash_balance <= 0:
                agent.is_bankrupt = True
        return price_updates

    @staticmethod
    def _one_sided_pressure(book: list[OrderBook], baseline: float) -> float:
        """Indicative price for a non-crossing book (see IMBALANCE_PRESSURE)."""
        buys = [o for o in book if o.order_type == OrderType.BUY]
        sells = [o for o in book if o.order_type == OrderType.SELL]
        buy_qty = sum(o.quantity for o in buys)
        sell_qty = sum(o.quantity for o in sells)
        if sell_qty > buy_qty and sells:
            best = min(o.limit_price for o in sells)
        elif buy_qty > sell_qty and buys:
            best = max(o.limit_price for o in buys)
        else:
            return baseline
        return baseline + (best - baseline) * IMBALANCE_PRESSURE

    def _settle_transaction(
        self,
        t: ClearedTransaction,
        ticker: str,
        by_agent: dict[str, AgentState],
        holdings: dict[str, dict[str, int]],
    ) -> None:
        buyer = by_agent[t.buyer_agent_id]
        seller = by_agent[t.seller_agent_id]
        notional = t.quantity * t.price
        buyer.cash_balance -= notional
        seller.cash_balance += notional
        holdings[buyer.agent_id][ticker] = holdings[buyer.agent_id].get(ticker, 0) + t.quantity
        holdings[seller.agent_id][ticker] = holdings[seller.agent_id].get(ticker, 0) - t.quantity

    # ------------------------------------------------------------------ #
    # State loading / persistence helpers                                 #
    # ------------------------------------------------------------------ #

    def _load_holdings(
        self, db: Session, agents: list[AgentState]
    ) -> dict[str, dict[str, int]]:
        holdings: dict[str, dict[str, int]] = {a.agent_id: {} for a in agents}
        for row in db.execute(select(AgentHolding)).scalars():
            if row.agent_id in holdings:
                holdings[row.agent_id][row.ticker] = row.quantity
        return holdings

    def _flush_holdings(self, db: Session, holdings: dict[str, dict[str, int]]) -> None:
        rows = {(r.agent_id, r.ticker): r for r in db.execute(select(AgentHolding)).scalars()}
        for agent_id, per_ticker in holdings.items():
            for ticker, qty in per_ticker.items():
                row = rows.get((agent_id, ticker))
                if row is None:
                    db.add(AgentHolding(agent_id=agent_id, ticker=ticker, quantity=qty))
                elif row.quantity != qty:
                    row.quantity = qty

    def _load_price_histories(
        self, db: Session, companies: list[Company]
    ) -> dict[str, list[float]]:
        histories: dict[str, list[float]] = {}
        for c in companies:
            rows = list(
                db.execute(
                    select(PriceTick.price)
                    .where(PriceTick.ticker == c.ticker)
                    .order_by(PriceTick.tick_id.desc())
                    .limit(TIMESFM_CONTEXT)
                ).scalars()
            )
            rows.reverse()
            histories[c.ticker] = rows if rows else [c.anchor_price]
        return histories

    @staticmethod
    def _economy_digest(snapshot: dict[str, Any]) -> str:
        sectors = "; ".join(
            f"{s['sector']} {s['avg_change_pct']:+.1f}% (sent {s['avg_sentiment']:+.2f})"
            for s in snapshot["sectors"]
        )
        gainers = ", ".join(
            f"{g['ticker']} {g['change_pct']:+.1f}%" for g in snapshot["biggest_gainers"]
        )
        losers = ", ".join(
            f"{g['ticker']} {g['change_pct']:+.1f}%" for g in snapshot["biggest_losers"]
        )
        return (
            f"Stress index {snapshot['system_stress_index']:.2f}. "
            f"Bankruptcies: {snapshot['bankrupt_count']}. Sectors: {sectors}. "
            f"Gainers: {gainers or 'none'}. Losers: {losers or 'none'}."
        )
