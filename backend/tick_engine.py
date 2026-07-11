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
import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_clients import GeminiModelRouter, TimesFMForecaster
from behavioral_agents import build_behavioral_orders
from database import SessionLocal
from event_analyst import EventImpactAnalyst
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
from social_agents import (
    CompanyPRContext,
    DailyDigest,
    DailyReporter,
    MacroAnalyst,
    NewsPublisher,
    PostDraft,
    render_factual,
    render_factual_company_post,
)

logger = logging.getLogger("chaosnet.tick")

# Cohorts are batched per Gemini call. Free-tier flash allows ~5
# requests/min, so a tick's LLM footprint must stay small: with the default
# batch of 50, a 150-cohort swarm costs 3 swarm calls + 1 PR-desk call per
# tick. Tune via BLACKSWAN_SWARM_BATCH alongside BLACKSWAN_RETAIL_COHORTS.
COHORT_BATCH_SIZE: int = max(1, int(os.getenv("BLACKSWAN_SWARM_BATCH", "50")))
RETAIL_MAX_ORDER_FRACTION: float = 0.80
# Calibrated for the conviction ladder below (larger fractions oversize the
# quant flow once five funds ladder their limits across the gap).
INSTITUTIONAL_CASH_FRACTION_PER_TICKER: float = 0.02
INSTITUTIONAL_INVENTORY_FRACTION: float = 0.05
SOCIAL_DIGEST_POSTS: int = 8
SENTIMENT_CARRYOVER: float = 0.7
ANALYST_EVERY_N_TICKS: int = 5
# LLM call budget. Free-tier flash allows ~10 requests/min, but an unpaced
# tick loop fires several calls per tick and instantly 429-storms, forcing
# every news/report path onto its fallback. Pace like a real newsroom:
# the news desk publishes every N ticks, and the LLM swarm voices ONE
# rotating cohort batch every M ticks (heuristic strategies carry the rest
# of the crowd in between). Tune per key quota.
NEWS_DESK_EVERY_N_TICKS: int = max(1, int(os.getenv("BLACKSWAN_NEWS_EVERY_N", "2")))
SWARM_LLM_EVERY_N_TICKS: int = max(1, int(os.getenv("BLACKSWAN_SWARM_EVERY_N", "4")))
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
# prices only through agent beliefs (sentiment + fear) and repriced
# fundamentals (event analyst -> conditioned forecasts), never through a
# multiplier on the clearing price.
#
# Black Swan transmission. An active event is NOT applied to the price; it is
# applied to agent DECISIONS. EVENT_FEAR_INTENSITY feeds the behavioral
# crowd's fear term (-> sell tilt); EVENT_SENTIMENT_HIT drives a per-tick
# negative sentiment impulse (bad news) that the crowd and the economy panel
# both read. The crash then emerges from the resulting order flow.
EVENT_FEAR_INTENSITY: float = 1.0
EVENT_SENTIMENT_HIT: float = 0.03
EVENT_SENTIMENT_DECAY: float = 0.9
# A Black Swan's panic is an IMPULSE that fades, not a permanent force.
# Without decay the crowd sells every tick forever and the market spirals to
# ~zero. Fear decays from the full hit toward a small residual over
# EVENT_DECAY_TICKS, so the market drops sharply, finds a floor near the
# impaired fundamental, and can recover — while the (persistent) analyst
# impact on the forecast keeps fundamentals repriced.
EVENT_DECAY_TICKS: float = 12.0
EVENT_RESIDUAL_FRAC: float = 0.15
# Institutional (TimesFM) conviction ladder: the 5 quant funds disperse their
# limit prices between the current price and the forecast, so the smart-money
# book actually crosses the behavioral crowd instead of stacking one-sided.
INSTITUTIONAL_CONVICTIONS: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.25)
# Ignore forecasts within this fraction of the current price (no edge, no order).
QUANT_DEADBAND: float = 0.001
# Weight of the event-repriced fundamental (anchor x (1 + impact)) in the
# quant forecast. High enough that the quant funds DEFEND that fundamental as
# a floor instead of chasing a falling price down forever.
FUNDAMENTAL_BLEND: float = 0.6

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
        self.daily_reporter = DailyReporter(router)
        self.event_analyst = EventImpactAnalyst(router)
        # Event-conditioned forecast pipeline state. The slow refresh loop
        # REBINDS these dicts (never mutates in place) so the tick loop can
        # snapshot them without locks.
        self.forecast_cache: dict[str, float] = {}
        self.event_impact: dict[str, float] = {}
        self.event_impact_source: str = ""
        self._analyzed_event: str = ""
        self._event_key: str = ""
        self._event_start_tick: int | None = None

    async def execute_simulation_tick(
        self,
        tick_id: int,
        active_event: str,
        sim_time: datetime | None = None,
        ticks_per_day: int | None = None,
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
            holdings, holding_rows = self._load_holdings(db, agents)
            recent_posts: list[SocialPost] = list(
                db.execute(
                    select(SocialPost).order_by(SocialPost.tick_id.desc()).limit(SOCIAL_DIGEST_POSTS)
                ).scalars()
            )
            histories = self._load_price_histories(db, companies)

            # Black Swan transmission (beliefs, never price): a decaying
            # panic intensity drives both the sentiment impulse and the
            # behavioral fear term, so the crash is an impulse that finds a
            # floor instead of a permanent slide.
            event_intensity = self._event_intensity(active_event, tick_id)
            self._apply_event_sentiment(companies, event_intensity)

            async with aiohttp.ClientSession() as http:
                # 3 & 4. News desk and LLM swarm run on paced cadences (see
                # the LLM call budget note above); the heuristic crowd trades
                # every tick regardless. TimesFM does NOT run in-tick: the
                # slow refresh loop maintains forecast_cache off-thread and
                # the tick reads a snapshot (empty until the first refresh
                # lands — the quant funds simply sit out those first seconds).
                async def _no_posts() -> list[SocialPost]:
                    return []

                async def _no_swarm() -> tuple[list[OrderBook], set[str]]:
                    return [], set()

                news_task = (
                    self._run_news_desk(db, http, companies, active_event, tick_id)
                    if tick_id % NEWS_DESK_EVERY_N_TICKS == 0
                    else _no_posts()
                )
                swarm_task = (
                    self._run_swarm(
                        http, companies, retail, holdings, recent_posts, active_event, tick_id
                    )
                    if tick_id % SWARM_LLM_EVERY_N_TICKS == 0
                    else _no_swarm()
                )

                posts, swarm_result = await asyncio.gather(news_task, swarm_task)
                swarm_orders, llm_decided = swarm_result
                forecasts = dict(self.forecast_cache)

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
                    event_intensity=event_intensity,
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
                    db, tick_id, companies, orders, agents, holdings, holding_rows
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

                # 7b. Daily news reporter: at each simulated-day boundary,
                # digest the day's REAL numbers and publish an end-of-day
                # report to the feed (LLM prose, deterministic factual
                # fallback — never invented figures).
                if ticks_per_day and tick_id % ticks_per_day == 0:
                    report_post = await self._publish_daily_report(
                        db, http, tick_id, ticks_per_day, companies, snapshot, active_event
                    )
                    if report_post is not None:
                        events.append(
                            _event("social_post", tick_id, _post_payload(report_post))
                        )

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
            drafts = []
        except RuntimeError as exc:
            logger.warning("News desk call failed (tick %d): %s", tick_id, exc)
            drafts = []

        if not drafts:
            # LLM unavailable: the wire still publishes — factual snippets
            # for the session's biggest movers, built from real numbers only.
            movers = sorted(contexts, key=lambda c: abs(c.change_pct), reverse=True)[:3]
            drafts = [
                PostDraft(
                    ticker=c.ticker,
                    content=render_factual_company_post(
                        c.ticker, c.name, c.sector, c.change_pct, c.price, tick_id, headline
                    ),
                    sentiment=max(-1.0, min(1.0, c.change_pct / 5.0)),
                    stance="neutral",
                )
                for c in movers
            ]

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
        if not batches:
            return [], set()
        # ONE rotating batch per swarm cycle: each call voices a different
        # cohort group (quota discipline), while the heuristic layer trades
        # for everyone the LLM didn't decide for this tick.
        idx = (tick_id // SWARM_LLM_EVERY_N_TICKS) % len(batches)
        built, decided = await run_batch(batches[idx], idx * COHORT_BATCH_SIZE)
        return built, decided

    # ------------------------------------------------------------------ #
    # Daily news reporter                                                 #
    # ------------------------------------------------------------------ #

    async def _publish_daily_report(
        self,
        db: Session,
        http: aiohttp.ClientSession,
        tick_id: int,
        ticks_per_day: int,
        companies: list[Company],
        snapshot: dict[str, Any],
        active_event: str,
    ) -> SocialPost | None:
        """End-of-day market report from the day's REAL numbers.

        Day window is (tick_id - ticks_per_day, tick_id]. Day-open prices come
        from the CompanySnapshot rows at the previous day boundary (anchor
        price on day one). LLM prose via the router; on any failure the
        deterministic factual template renders the same numbers instead.
        """
        day_index = tick_id // ticks_per_day
        day_start_tick = tick_id - ticks_per_day

        opens: dict[str, float] = {}
        if day_start_tick > 0:
            rows = db.execute(
                select(CompanySnapshot).where(CompanySnapshot.tick_id == day_start_tick)
            ).scalars()
            opens = {r.ticker: r.current_price for r in rows}
        changes: list[tuple[str, float]] = []
        for c in companies:
            open_price = opens.get(c.ticker) or c.anchor_price
            if open_price and open_price > 0:
                changes.append((c.ticker, (c.current_price - open_price) / open_price * 100.0))
        if not changes:
            return None
        changes.sort(key=lambda t: t[1])
        top_loser, top_gainer = changes[0], changes[-1]
        index_change = sum(pct for _, pct in changes) / len(changes)
        total_volume = int(
            db.execute(
                select(func.coalesce(func.sum(PriceTick.volume), 0)).where(
                    PriceTick.tick_id > day_start_tick, PriceTick.tick_id <= tick_id
                )
            ).scalar_one()
        )

        digest = DailyDigest(
            day_index=day_index,
            tick_id=tick_id,
            index_change_pct=round(index_change, 3),
            top_gainer_ticker=top_gainer[0],
            top_gainer_pct=round(top_gainer[1], 3),
            top_loser_ticker=top_loser[0],
            top_loser_pct=round(top_loser[1], 3),
            total_volume=total_volume,
            stress_index=float(snapshot.get("system_stress_index", 0.0)),
            bankrupt_count=int(snapshot.get("bankrupt_count", 0)),
            headline=active_event,
        )
        try:
            content = await asyncio.wait_for(
                self.daily_reporter.report(http, digest), timeout=20.0
            )
        except (RuntimeError, asyncio.TimeoutError) as exc:
            logger.warning("daily reporter LLM failed (day %d): %s", day_index, exc)
            content = render_factual(digest)
        if not content.strip():
            # An LLM "success" with empty/unparseable prose must never
            # publish a blank wire post.
            content = render_factual(digest)

        post = SocialPost(
            post_id=str(uuid.uuid4()),
            tick_id=tick_id,
            author_type=AuthorType.ANALYST,
            author_ticker=None,
            author_display="Market Wire",
            handle="@DailyBrief",
            content=content,
            sentiment=max(-1.0, min(1.0, index_change / 10.0)),
            likes=120,
            reposts=24,
            ts=getattr(self, "_sim_time", None) or datetime.now(timezone.utc),
        )
        db.add(post)
        return post

    # ------------------------------------------------------------------ #
    # Quant funds                                                         #
    # ------------------------------------------------------------------ #

    def _forecast_all(self, histories: dict[str, list[float]]) -> dict[str, float]:
        """One batched TimesFM pass (kept for tests and the refresh path)."""
        return self.forecaster.forecast_batch(histories)

    def refresh_forecasts(self) -> None:
        """Recompute the event-conditioned forecast (read-only, off-thread).

        Called by the controller in a worker thread every few seconds. Runs one
        batched TimesFM forecast, then conditions each ticker on the analyst's
        event impact so the quant funds trade a prediction that accounts for
        the Black Swan. Never writes the DB, so it can't contend with the tick
        loop's single-writer session.
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
        FIXED repriced level the quant funds trade toward. Blending it in
        (rather than haircutting the trailing forecast, which just chases a
        falling price down) gives the market a floor at the justified
        fundamental instead of a bottomless slide to zero.
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
        self._event_key = ""
        self._event_start_tick = None

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
        # Supersede guard: if two analyses are in flight (event A then B),
        # only the most recently REQUESTED one may commit its result —
        # otherwise a slow A could overwrite B's impact map.
        self._pending_event = event
        with self.session_factory() as db:
            companies = list(db.execute(select(Company)).scalars())
        sectors = sorted({c.sector for c in companies})
        try:
            async with aiohttp.ClientSession() as http:
                sector_impact, source = await self.event_analyst.analyze(http, event, sectors)
        except Exception as exc:  # never let analysis crash the refresh loop
            logger.warning("event analyst failed for %r: %s", event, exc)
            return
        if getattr(self, "_pending_event", event) != event:
            return  # a newer event superseded this analysis mid-flight
        self.event_impact = {
            c.ticker: sector_impact.get(c.sector, 0.0) for c in companies
        }
        self.event_impact_source = source
        self._analyzed_event = event
        logger.info(
            "event impact (%s) for %r across %d sectors", source, event, len(sector_impact)
        )

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
        if intensity <= 0.0:
            return
        for company in companies:
            bias = (sum(ord(ch) for ch in company.ticker) % 100) / 100.0  # 0..1
            impulse = -EVENT_SENTIMENT_HIT * (0.5 + bias) * intensity
            company.sentiment = max(
                -1.0, min(1.0, company.sentiment * EVENT_SENTIMENT_DECAY + impulse)
            )

    def _build_institutional_orders(
        self,
        tick_id: int,
        institutional: list[AgentState],
        holdings: dict[str, dict[str, int]],
        forecasts: dict[str, float],
        companies: list[Company],
    ) -> list[OrderBook]:
        """TimesFM quant funds as dispersed liquidity providers.

        Each of the 5 funds treats its forecast as fair value and posts a
        limit order PARTWAY there (scaled by its conviction on the ladder), so
        the smart-money book spans the current-price -> forecast range and
        actually crosses the behavioral crowd instead of stacking one-sided.
        When panic drops the price below the forecast the funds BUY the dip;
        when it runs above, they SELL — real, model-driven price discovery.
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
        holding_rows: dict[tuple[str, str], AgentHolding],
    ) -> list[dict[str, Any]]:
        by_agent = {a.agent_id: a for a in agents}
        price_updates: list[dict[str, Any]] = []

        # Group once instead of rescanning the full order list per company
        # (O(companies x orders) with ~700 orders/tick and growing with
        # BLACKSWAN_RETAIL_COHORTS).
        orders_by_ticker: dict[str, list[OrderBook]] = {}
        for o in orders:
            orders_by_ticker.setdefault(o.ticker, []).append(o)

        for company in companies:
            book = orders_by_ticker.get(company.ticker, [])
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
        self._flush_holdings(db, holdings, holding_rows)
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
    ) -> tuple[dict[str, dict[str, int]], dict[tuple[str, str], AgentHolding]]:
        """One full read of agent_holdings, reused for the end-of-tick flush.

        Returns (quantities, row map) so _flush_holdings never re-scans the
        table — with 155 agents x 51 tickers that second scan was ~7.9k ORM
        rows materialized every tick for nothing.
        """
        holdings: dict[str, dict[str, int]] = {a.agent_id: {} for a in agents}
        rows: dict[tuple[str, str], AgentHolding] = {}
        for row in db.execute(select(AgentHolding)).scalars():
            rows[(row.agent_id, row.ticker)] = row
            if row.agent_id in holdings:
                holdings[row.agent_id][row.ticker] = row.quantity
        return holdings, rows

    def _flush_holdings(
        self,
        db: Session,
        holdings: dict[str, dict[str, int]],
        rows: dict[tuple[str, str], AgentHolding],
    ) -> None:
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
        # Single batched query instead of one per ticker (51 round trips per
        # tick). Every ticker gets one PriceTick per tick, so a tick_id
        # window bounds each ticker's series to <= TIMESFM_CONTEXT points.
        latest = db.scalar(select(func.max(PriceTick.tick_id))) or 0
        rows = db.execute(
            select(PriceTick.ticker, PriceTick.price)
            .where(PriceTick.tick_id > latest - TIMESFM_CONTEXT)
            .order_by(PriceTick.tick_id)
        ).all()
        histories: dict[str, list[float]] = {c.ticker: [] for c in companies}
        for ticker, price in rows:
            series = histories.get(ticker)
            if series is not None:
                series.append(price)
        for c in companies:
            if not histories[c.ticker]:
                histories[c.ticker] = [c.anchor_price]
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
