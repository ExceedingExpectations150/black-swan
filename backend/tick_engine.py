"""Simulation tick orchestrator for ChaosNet "Black Swan".

Multi-ticker orchestration (full per-ticker CDA — the lighter beta-derived
fallback was NOT chosen; every company clears through the real matching
engine each tick). Canonical per-tick sequence:

  1. tick_start
  2. macro news (headline passed in from the controller)
  3. Corporate PR agents post -> per-company sentiment nudges
  4. behavioral crowd -> per-ticker limit orders. The always-on LocalBehavioralEngine
     provides the guaranteed retail order flow (no API); when Gemini quota is
     available the Gemma swarm's decisions override it per agent.
  5. quant funds (TimesFM per ticker) -> dispersed limit orders that provide
     real liquidity toward each forecast (dip-buying when price < forecast)
  6. CDA match per ticker -> price_ticks, settlement, bankruptcies
  7. economy analysis (+ Macro Analyst every N ticks)
  8. return the ordered WebSocket event list for broadcast

Price discovery is 100% order-flow driven: the clearing price moves only
because agents actually trade through the matching engine. There is NO scripted
price path. A Black Swan event is transmitted as fear (into behavioral expected
returns) and as a negative sentiment impulse — agents react, and the crash
emerges from the resulting sell flow. TimesFM runs locally (free); Gemini is
best-effort narrative + optional retail override.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_clients import GeminiModelRouter, TimesFMForecaster
from behavioral_engine import LocalBehavioralEngine
from database import SessionLocal
from event_analyst import EventImpactAnalyst
from sim_time import sim_clock
from economy import compute_economy_snapshot, persist_economy_snapshot
from matching_engine import ClearedTransaction, MatchingEngine
from models import (
    AgentHolding,
    AgentState,
    AgentType,
    AuthorType,
    Company,
    OrderBook,
    OrderStatus,
    OrderType,
    PriceTick,
    SocialPost,
    WorldState,
)
from social_agents import CompanyPRContext, CorporatePRDesk, MacroAnalyst, PostDraft

logger = logging.getLogger("chaosnet.tick")

# All cohorts in ONE Gemini call per tick. Free-tier flash allows ~5
# requests/min, so a tick's LLM footprint must stay tiny: this makes it
# 1 PR-desk call + 1 swarm call. Flash handles all 50 cohorts in a single
# JSON array well within the context window.
COHORT_BATCH_SIZE: int = 50
RETAIL_MAX_ORDER_FRACTION: float = 0.20
INSTITUTIONAL_CASH_FRACTION_PER_TICKER: float = 0.02
INSTITUTIONAL_INVENTORY_FRACTION: float = 0.05
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
# Black Swan transmission. An active event is NOT applied to the price; it is
# applied to agent DECISIONS. EVENT_FEAR_INTENSITY feeds the behavioral crowd's
# expected-return fear term (-> sell tilt); EVENT_SENTIMENT_HIT drives a
# per-tick negative sentiment impulse (bad news) that the crowd and the economy
# panel both read. The crash then emerges from the resulting order flow.
EVENT_FEAR_INTENSITY: float = 1.0
EVENT_SENTIMENT_HIT: float = 0.03
EVENT_SENTIMENT_DECAY: float = 0.9
# A Black Swan's panic is an IMPULSE that fades, not a permanent force. Without
# decay the crowd sells every tick forever and the market spirals to ~zero (the
# "-99%" bug). Fear decays from the full hit toward a small residual over
# EVENT_DECAY_TICKS, so the market drops sharply, finds a floor near the
# impaired fundamental, and can recover — while the (persistent) analyst impact
# on the forecast keeps fundamentals repriced.
EVENT_DECAY_TICKS: float = 12.0
EVENT_RESIDUAL_FRAC: float = 0.15
# Institutional (TimesFM) conviction ladder: the 5 quant funds disperse their
# limit prices between the current price and the forecast, so the smart-money
# book actually crosses the behavioral crowd instead of stacking one-sided.
INSTITUTIONAL_CONVICTIONS: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.25)
# Ignore forecasts within this fraction of the current price (no edge, no order).
QUANT_DEADBAND: float = 0.001
# Weight of the event-repriced fundamental (anchor x (1 + impact)) in the quant
# forecast. High enough that the quant funds DEFEND that fundamental as a floor
# instead of chasing a falling price down forever (the perpetual-ratchet bug).
FUNDAMENTAL_BLEND: float = 0.6

SWARM_PROMPT_TEMPLATE: str = """You are a JSON API simulating {n} retail trading cohorts in a multi-stock market. You never explain; you only output JSON.

MACRO NEWS: {headline}

MARKET SNAPSHOT (ticker | price | change vs real anchor | crowd sentiment -1..1):
{market_block}

RECENT SOCIAL FEED:
{social_block}

COHORTS (index | risk tolerance 0=cautious..1=aggressive | cash | holdings):
{cohort_block}

For EACH cohort decide zero or more limit orders consistent with its risk
profile, cash, and holdings. Aggressive cohorts chase momentum and rumors;
cautious ones de-risk. A cohort may do nothing (empty orders list).
Do not think out loud. Your ENTIRE reply must be only a strict JSON array
starting with the character [ and nothing else, exactly this shape:
[{{"cohort": 0, "orders": [{{"ticker": "AAPL", "action": "BUY", "qty": 10, "limit_price": 232.5}}]}}]"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        self.pr_desk = CorporatePRDesk(router)
        self.analyst = MacroAnalyst(router)
        self.behavioral = LocalBehavioralEngine()
        self.event_analyst = EventImpactAnalyst(router)
        # Latest event-conditioned TimesFM forecasts, refreshed off-thread by the
        # controller so the ~10 s CPU inference never blocks the real-time tick
        # loop. Reassigned atomically; the tick reads a snapshot. Empty until the
        # first refresh.
        self.forecast_cache: dict[str, float] = {}
        # Per-ticker event impact fraction from the analyst agent (see
        # refresh_event_impact); applied to the raw TimesFM forecast so the
        # quant funds trade a prediction that accounts for the Black Swan.
        self.event_impact: dict[str, float] = {}
        self.event_impact_source: str = ""
        self._analyzed_event: str = ""
        # Tracks how long the current event has been active, to decay its panic.
        self._event_key: str = ""
        self._event_start_tick: int | None = None

    async def execute_simulation_tick(
        self, tick_id: int, active_event: str
    ) -> list[dict[str, Any]]:
        """Run one tick; returns the ordered event list for /ws broadcast.

        Real-time by construction: only fast, local work runs here (behavioral
        crowd, cached TimesFM forecast, CDA, local event-aware social). The
        ~10 s TimesFM inference runs off-thread and lands in self.forecast_cache;
        this tick just reads a snapshot of it. No blocking network calls.
        """
        clock = sim_clock(tick_id)
        events: list[dict[str, Any]] = [_event("tick_start", tick_id, clock)]
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
            histories = self._load_price_histories(db, companies)
            event_intensity = self._event_intensity(active_event, tick_id)
            rng = random.Random(tick_id * 1_000_003 + 1)

            # Black Swan transmission: bad news -> crowd sentiment (agents then
            # trade on it), scaled by the decaying panic. Not applied to price.
            if event_intensity > 0:
                self._apply_event_sentiment(companies, event_intensity)

            # Retail crowd (local, always-on) + quant funds from the latest
            # background TimesFM forecast snapshot.
            local_intents = self.behavioral.generate(
                tick_id, companies, retail, holdings, histories, event_intensity, rng
            )
            known = {c.ticker for c in companies}
            forecasts = dict(self.forecast_cache)  # snapshot; empty before first refresh
            orders = self._materialize_retail(
                local_intents, retail, holdings, known, tick_id
            ) + self._build_institutional_orders(
                tick_id, institutional, holdings, forecasts, companies
            )

            # CDA per ticker (the one and only price setter).
            price_updates = self._clear_markets(
                db, tick_id, companies, orders, agents, holdings
            )
            events.append(_event("price_update", tick_id, {"prices": price_updates}))

            snapshot = compute_economy_snapshot(db, tick_id)
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
                )
            )
            db.commit()

        events.append(_event("tick_end", tick_id, clock))
        return events

    def refresh_forecasts(self) -> None:
        """Recompute the event-conditioned forecast (read-only, off-thread).

        Called by the controller in a worker thread every few seconds. Runs one
        batched TimesFM forecast, then conditions each ticker on the analyst's
        event impact — final = TimesFM x (1 + impact) — so the quant funds trade
        a prediction that accounts for the Black Swan. Never writes the DB, so
        it can't contend with the tick loop's single-writer session.
        """
        with self.session_factory() as db:
            companies = list(
                db.execute(select(Company).where(Company.is_bankrupt.is_(False))).scalars()
            )
            histories = self._load_price_histories(db, companies)
        raw = self.forecaster.forecast_batch(histories)
        anchors = {c.ticker: c.anchor_price for c in companies}
        self.forecast_cache = self._condition_forecast(raw, anchors, self.event_impact)

    @staticmethod
    def _condition_forecast(
        raw: dict[str, float], anchors: dict[str, float], impact: dict[str, float]
    ) -> dict[str, float]:
        """Blend the trailing TimesFM forecast with the event-repriced fundamental.

        For a name the analyst flagged, fair value is anchor x (1 + impact) — a
        FIXED repriced level the quant funds trade toward. Blending it in (rather
        than haircutting the trailing forecast, which just chases a falling price
        down) gives the market a floor at the justified fundamental instead of a
        bottomless slide to zero.
        """
        conditioned: dict[str, float] = {}
        for ticker, raw_val in raw.items():
            imp = impact.get(ticker, 0.0)
            if imp and ticker in anchors:
                fundamental = anchors[ticker] * (1.0 + imp)
                conditioned[ticker] = (
                    FUNDAMENTAL_BLEND * fundamental + (1.0 - FUNDAMENTAL_BLEND) * raw_val
                )
            else:
                conditioned[ticker] = raw_val
        return conditioned

    def reset_ai_state(self) -> None:
        """Clear cached forecasts and event analysis (used on world reset)."""
        self.forecast_cache = {}
        self.event_impact = {}
        self.event_impact_source = ""
        self._analyzed_event = ""

    async def refresh_event_impact(self, active_event: str) -> None:
        """Update the per-ticker event impact when the active event changes.

        Runs the LLM analyst agent (with a keyword fallback) once per distinct
        event, mapping its per-sector verdict onto every company. Best-effort:
        a failure leaves the last impact in place. Clearing the event clears
        the impact, so the quant forecast reverts to pure TimesFM (recovery).
        """
        event = active_event.strip()
        if event == self._analyzed_event:
            return
        if not event:
            self.event_impact = {}
            self.event_impact_source = ""
            self._analyzed_event = ""
            return
        with self.session_factory() as db:
            companies = list(db.execute(select(Company)).scalars())
        sectors = sorted({c.sector for c in companies})
        try:
            async with aiohttp.ClientSession() as http:
                sector_impact, source = await self.event_analyst.analyze(http, event, sectors)
        except Exception as exc:  # never let analysis crash the refresh loop
            logger.warning("event analyst failed for %r: %s", event, exc)
            return
        self.event_impact = {
            c.ticker: sector_impact.get(c.sector, 0.0) for c in companies
        }
        self.event_impact_source = source
        self._analyzed_event = event
        logger.info(
            "event impact (%s) for %r across %d sectors", source, event, len(sector_impact)
        )

    # ------------------------------------------------------------------ #
    # PR desk                                                             #
    # ------------------------------------------------------------------ #

    async def _run_pr_desk(
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
            drafts: list[PostDraft] = await self.pr_desk.generate_posts(
                http, contexts, headline or "No major macro news."
            )
        except RuntimeError as exc:
            logger.warning("PR desk call failed (tick %d): %s", tick_id, exc)
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
                author_type=AuthorType.COMPANY,
                author_ticker=d.ticker,
                author_display=company.name,
                handle="@" + re.sub(r"[^a-z0-9]", "", company.name.lower())[:15],
                content=d.content,
                sentiment=d.sentiment,
                likes=int(150 * abs(d.sentiment)) + 25,
                reposts=(int(150 * abs(d.sentiment)) + 25) // 6,
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
    ) -> dict[str, list[dict[str, Any]]]:
        """Best-effort Gemma cohort decisions, keyed by agent_id (empty on 429).

        Returns raw order intents ({"ticker","action","qty","limit_price"});
        the shared _materialize_retail applies cash/holding caps. An empty dict
        means the local behavioral engine's flow stands unopposed this tick.
        """
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
        async def run_batch(batch: list[AgentState], base: int) -> dict[str, list[dict[str, Any]]]:
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
                raw = await self.router.prompt_cohort(http, prompt)
            except RuntimeError as exc:
                logger.warning("swarm batch @%d failed (tick %d): %s", base, tick_id, exc)
                return {}
            decisions = parse_swarm_reply(raw, len(batch))
            if not decisions:
                logger.warning(
                    "swarm batch @%d (tick %d): reply yielded no decisions: %.150s",
                    base,
                    tick_id,
                    raw,
                )
            # Only agents the model actually decided for; absent = HOLD, which
            # leaves that agent's local behavioral order flow in place.
            return {batch[idx].agent_id: orders for idx, orders in decisions.items()}

        batches = [
            retail[i : i + COHORT_BATCH_SIZE] for i in range(0, len(retail), COHORT_BATCH_SIZE)
        ]
        results = await asyncio.gather(
            *(run_batch(b, i * COHORT_BATCH_SIZE) for i, b in enumerate(batches))
        )
        merged: dict[str, list[dict[str, Any]]] = {}
        for sub in results:
            merged.update(sub)
        return merged

    def _materialize_retail(
        self,
        intents_by_agent: dict[str, list[dict[str, Any]]],
        retail: list[AgentState],
        holdings: dict[str, dict[str, int]],
        known: set[str],
        tick_id: int,
    ) -> list[OrderBook]:
        """Turn merged retail intents into OrderBook rows under cash/holding caps.

        Shared by the local behavioral engine and the Gemma swarm so both obey
        the same affordability rules: a BUY is capped at RETAIL_MAX_ORDER_FRACTION
        of cash, a SELL at shares actually held. Unknown/bankrupt tickers drop.
        """
        by_id = {a.agent_id: a for a in retail}
        built: list[OrderBook] = []
        for agent_id, intents in intents_by_agent.items():
            agent = by_id.get(agent_id)
            if agent is None:
                continue
            for o in intents:
                try:
                    ticker = str(o["ticker"]).upper()
                    action = str(o["action"]).upper()
                    limit_price = float(o["limit_price"])
                    qty = int(o["qty"])
                except (KeyError, TypeError, ValueError):
                    continue
                if ticker not in known or limit_price <= 0 or qty <= 0:
                    continue
                if action == "BUY":
                    affordable = int(agent.cash_balance * RETAIL_MAX_ORDER_FRACTION / limit_price)
                    qty = min(qty, affordable)
                    side = OrderType.BUY
                elif action == "SELL":
                    qty = min(qty, holdings.get(agent_id, {}).get(ticker, 0))
                    side = OrderType.SELL
                else:
                    continue
                if qty <= 0:
                    continue
                built.append(
                    OrderBook(
                        order_id=str(uuid.uuid4()),
                        tick_id=tick_id,
                        agent_id=agent_id,
                        ticker=ticker,
                        order_type=side,
                        quantity=qty,
                        limit_price=limit_price,
                        status=OrderStatus.PENDING,
                    )
                )
        return built

    # ------------------------------------------------------------------ #
    # Quant funds                                                         #
    # ------------------------------------------------------------------ #

    def _forecast_all(self, histories: dict[str, list[float]]) -> dict[str, float]:
        # One batched TimesFM call for the whole market (see forecast_batch);
        # per-ticker looping was ~30 s/tick on CPU.
        return self.forecaster.forecast_batch(histories)

    def _build_institutional_orders(
        self,
        tick_id: int,
        institutional: list[AgentState],
        holdings: dict[str, dict[str, int]],
        forecasts: dict[str, float],
        companies: list[Company],
    ) -> list[OrderBook]:
        """TimesFM quant funds as dispersed liquidity providers.

        Each of the 5 funds treats its forecast as fair value and posts a limit
        order PARTWAY there (scaled by its conviction on the ladder), so the
        smart-money book spans the current-price -> forecast range and actually
        crosses the behavioral crowd instead of stacking one-sided. When panic
        drops the price below the (history-based) forecast the funds BUY the
        dip; when it runs above, they SELL — real, model-driven price discovery.
        """
        orders: list[OrderBook] = []
        by_ticker = {c.ticker: c for c in companies}
        for i, agent in enumerate(institutional):
            conviction = INSTITUTIONAL_CONVICTIONS[i % len(INSTITUTIONAL_CONVICTIONS)]
            for ticker, prediction in forecasts.items():
                company = by_ticker.get(ticker)
                if company is None or company.current_price <= 0:
                    continue
                gap = (prediction - company.current_price) / company.current_price
                if abs(gap) < QUANT_DEADBAND:
                    continue
                limit_price = round(
                    max(0.01, company.current_price * (1.0 + gap * conviction)), 2
                )
                if gap > 0:
                    side = OrderType.BUY
                    qty = int(
                        agent.cash_balance
                        * INSTITUTIONAL_CASH_FRACTION_PER_TICKER
                        * conviction
                        / limit_price
                    )
                else:
                    side = OrderType.SELL
                    held = holdings.get(agent.agent_id, {}).get(ticker, 0)
                    qty = int(held * INSTITUTIONAL_INVENTORY_FRACTION * (0.5 + conviction))
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
                        limit_price=limit_price,
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

            # The clearing price IS the price. No overlay, no scripted path —
            # it moved only because these orders actually traded.
            company.current_price = clearing_price
            db.add(
                PriceTick(
                    tick_id=tick_id, ticker=company.ticker, price=clearing_price, volume=volume
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

    def _event_intensity(self, active_event: str, tick_id: int) -> float:
        """Decaying panic intensity for the active event (0.0 when none).

        Peaks at EVENT_FEAR_INTENSITY when the event first hits, then decays
        toward a small residual over EVENT_DECAY_TICKS. This is what makes the
        crash an impulse that stabilizes instead of a permanent slide to zero.
        """
        event = active_event.strip()
        if not event:
            self._event_key = ""
            self._event_start_tick = None
            return 0.0
        if event != self._event_key:
            self._event_key = event
            self._event_start_tick = tick_id
        start = self._event_start_tick if self._event_start_tick is not None else tick_id
        elapsed = max(0, tick_id - start)
        decay = EVENT_RESIDUAL_FRAC + (1.0 - EVENT_RESIDUAL_FRAC) * math.exp(
            -elapsed / EVENT_DECAY_TICKS
        )
        return EVENT_FEAR_INTENSITY * decay

    @staticmethod
    def _apply_event_sentiment(companies: list[Company], intensity: float) -> None:
        """Transmit an active Black Swan as a negative sentiment impulse.

        This does NOT move price — it moves crowd sentiment (bad news), which
        the behavioral engine then trades on. The hit is heterogeneous per
        company (seeded from the ticker) and scaled by the decaying panic
        `intensity`, so as the shock fades sentiment mean-reverts toward zero.
        """
        for company in companies:
            bias = (sum(ord(ch) for ch in company.ticker) % 100) / 100.0  # 0..1
            impulse = -EVENT_SENTIMENT_HIT * (0.5 + bias) * intensity
            company.sentiment = max(
                -1.0, min(1.0, company.sentiment * EVENT_SENTIMENT_DECAY + impulse)
            )

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
