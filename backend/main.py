"""FastAPI entrypoint for ChaosNet "Black Swan" — multi-ticker world edition.

REST: /api/state (hydration), /api/companies[/{ticker}[/prices]],
/api/social, /api/economy, plus the simulation controls
/api/start, /api/stop, /api/event.

WebSocket /ws: every message uses the shared envelope
{ "type": "<event>", "tick_id": 0, "ts": "...", "payload": {} } —
this shape is a frozen cross-team contract with the frontend.

Run: uvicorn main:app --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import aiohttp

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from database import SessionLocal, init_db, seed_initial_market_state, Base, engine
from economy import compute_economy_snapshot, snapshot_from_row
from models import Company, EconomySnapshot, PriceTick, SocialPost, WorldState, CompanySnapshot
from trader_agent import (
    ChatMessage,
    SeniorTraderAgent,
    build_market_brief,
    compute_ticker_stats,
    render_offline_brief,
)

logger = logging.getLogger("chaosnet.main")

TICK_INTERVAL_SECONDS: float = 3.0
# Cadence of the slow AI-refresh loop (event analyst + batched TimesFM).
FORECAST_REFRESH_SECONDS: float = 8.0
# Defaults for a bare POST /api/start (no payload): a bounded, well-formed
# run rather than an unbounded 1-tick/day drift.
DEFAULT_DURATION_DAYS: int = 30
DEFAULT_TICKS_PER_DAY: int = 4


def _epoch(dt: datetime) -> int:
    """Datetime -> epoch seconds; SQLite returns naive datetimes, treat as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _company_payload(company: Company) -> dict[str, Any]:
    change_pct = (
        (company.current_price - company.anchor_price) / company.anchor_price * 100.0
        if company.anchor_price
        else 0.0
    )
    return {
        "ticker": company.ticker,
        "name": company.name,
        "sector": company.sector,
        "country": company.country,
        "city": company.city,
        "lat": company.lat,
        "lon": company.lon,
        "description": company.description,
        "shares_outstanding": company.shares_outstanding,
        "anchor_price": company.anchor_price,
        "current_price": company.current_price,
        "change_pct": round(change_pct, 4),
        "sentiment": round(company.sentiment, 4),
        "volatility": round(company.volatility, 6),
        "market_cap": company.current_price * company.shares_outstanding,
        "is_bankrupt": company.is_bankrupt,
    }


def _post_payload(post: SocialPost) -> dict[str, Any]:
    return {
        "post_id": post.post_id,
        "tick_id": post.tick_id,
        "ts": post.ts.isoformat() if post.ts else None,
        "author_type": post.author_type.value,
        "author_ticker": post.author_ticker,
        "author_display": post.author_display,
        "handle": post.handle,
        "content": post.content,
        "sentiment": post.sentiment,
        "likes": post.likes,
        "reposts": post.reposts,
    }


def _latest_economy(db: Any) -> dict[str, Any]:
    row = db.execute(
        select(EconomySnapshot).order_by(EconomySnapshot.tick_id.desc()).limit(1)
    ).scalar_one_or_none()
    if row is not None:
        return snapshot_from_row(row)
    latest_tick = db.execute(select(func.max(WorldState.tick_id))).scalar_one() or 0
    return compute_economy_snapshot(db, latest_tick)


# CORSMiddleware does NOT cover the WebSocket handshake, so /ws enforces the
# same origin allowlist itself. Cap concurrent sockets so a connection flood
# can't blow up the per-tick broadcast fan-out.
MAX_WS_CLIENTS = 64


class RateLimiter:
    """Per-IP sliding-window limiter (dependency-free).

    Guards the endpoints that hit the paid Gemini API (/api/event, /api/chat)
    so a trivial curl loop from one host can't exhaust quota or run up cost.
    """

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window
        hits = [t for t in self._hits.get(key, []) if t > cutoff]
        if len(hits) >= self.max_calls:
            raise HTTPException(status_code=429, detail="rate limit exceeded; slow down")
        hits.append(now)
        self._hits[key] = hits


_llm_rate_limiter = RateLimiter(max_calls=20, window_seconds=60.0)


# Only trust X-Forwarded-For when the app is actually behind a proxy that
# sets it (opt-in). Trusting it unconditionally lets any client spoof a fresh
# IP per request and bypass the limiter; ignoring it entirely collapses to one
# global bucket behind a proxy. TRUST_PROXY=1 in the proxied deployment.
_TRUST_PROXY = os.getenv("TRUST_PROXY", "").strip() in ("1", "true", "yes")


def _client_ip(request: Request) -> str:
    """Per-client key for rate limiting. Uses the direct peer by default
    (un-spoofable on a localhost/demo deploy); reads the first X-Forwarded-For
    hop only when TRUST_PROXY is set (behind Render/Cloudflare per DEPLOY.md)."""
    if _TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_llm(request: Request) -> None:
    _llm_rate_limiter.check(_client_ip(request))


class ConnectionManager:
    """Tracks live WebSocket clients and fans envelope events out to all."""

    def __init__(self) -> None:
        self._clients: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> bool:
        """Accept only same-origin sockets, up to the connection cap.

        Returns False (and closes with a policy code) when the origin is not
        allowed or the cap is reached; the caller then bails out.
        """
        origin = websocket.headers.get("origin")
        # A same-origin browser always sends Origin. Accept a missing Origin
        # (native ws tools) ONLY when the whole allowlist is localhost — in a
        # public deployment an Origin-less client would otherwise bypass the
        # allowlist entirely and scrape/flood the feed from anywhere.
        allowed = (
            origin in ALLOWED_ORIGINS if origin is not None else _LOCALHOST_ONLY
        )
        if not allowed:
            await websocket.close(code=1008)
            return False
        if len(self._clients) >= MAX_WS_CLIENTS:
            await websocket.close(code=1013)  # try again later
            return False
        await websocket.accept()
        self._clients.append(websocket)
        return True

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._clients:
            self._clients.remove(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        # Serialize ONCE per event (send_json would re-dump the same dict for
        # every client) and fan out concurrently instead of serially.
        text = json.dumps(payload)
        clients = list(self._clients)
        results = await asyncio.gather(
            *(client.send_text(text) for client in clients),
            return_exceptions=True,
        )
        for client, result in zip(clients, results):
            if isinstance(result, BaseException):
                self.disconnect(client)


class SimulationController:
    """Owns the tick loop, the active macro event, and the lazy AI engines."""

    def __init__(self) -> None:
        self.manager = ConnectionManager()
        self.tick_engine: Any = None
        self.task: asyncio.Task[None] | None = None
        self.forecast_task: asyncio.Task[None] | None = None
        self.chat_router: Any = None  # lazy, independent of /api/start
        self.active_event: str = ""
        self.next_tick_id: int = 1
        self.tick_interval_seconds: float = TICK_INTERVAL_SECONDS
        self.paused: bool = False
        self.target_tick: int | None = None
        self.max_ticks: int | None = None
        self.duration_days: int | None = None
        self.ticks_per_day: int | None = None
        # Simulated clock: each tick advances sim time by one trading step
        # (24h / ticks_per_day). Anchored when a fresh run starts.
        self.sim_start: datetime | None = None

    def sim_seconds_per_tick(self) -> float:
        return 86400.0 / float(self.ticks_per_day or 1)

    def sim_time_for(self, tick_id: int) -> datetime:
        base = self.sim_start or datetime.now(timezone.utc)
        return base + timedelta(seconds=(tick_id - 1) * self.sim_seconds_per_tick())

    def get_status_payload(self) -> dict[str, Any]:
        return {
            "is_running": self.is_running,
            "paused": self.paused,
            "tick_interval_seconds": self.tick_interval_seconds,
            "target_tick": self.target_tick,
            "next_tick": self.next_tick_id,
            "max_ticks": self.max_ticks,
            "duration_days": self.duration_days,
            "ticks_per_day": self.ticks_per_day,
            "sim_start": self.sim_start.isoformat() if self.sim_start else None,
            "sim_time": self.sim_time_for(self.next_tick_id).isoformat(),
            "sim_seconds_per_tick": self.sim_seconds_per_tick(),
        }

    @property
    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def load_tick_counter(self) -> None:
        with SessionLocal() as db:
            latest = db.execute(select(func.max(WorldState.tick_id))).scalar_one()
        self.next_tick_id = (latest or 0) + 1

    def reset_world(self, preserve_event: bool = False) -> int:
        """Wipe the database, reseed, and zero the controller counters.

        The caller must have stopped the run loop first. `preserve_event`
        keeps the armed Black Swan headline (used when a new run resets the
        world after the event was already posted).
        """
        # A cancelled asyncio.to_thread() does NOT stop its worker thread; a
        # refresh_forecasts() read can briefly hold the SQLite handle while
        # we wipe. Retry the drop instead of 500ing the reset.
        for attempt in range(3):
            try:
                Base.metadata.drop_all(bind=engine)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(0.5)
        init_db()
        created = seed_initial_market_state()
        logger.info("World reset. Seeded %d agents.", created)
        self.next_tick_id = 1
        if not preserve_event:
            self.active_event = ""
        self.paused = False
        self.target_tick = None
        self.max_ticks = None
        self.duration_days = None
        self.ticks_per_day = None
        self.sim_start = None
        self.tick_engine = None  # rebuild to clear any cached state
        return created

    def build_engines(self) -> None:
        """Construct the AI layers. Hard-errors without keys/torch by design."""
        if self.tick_engine is not None:
            return
        from ai_clients import GeminiModelRouter, TimesFMForecaster
        from matching_engine import MatchingEngine
        from tick_engine import TickEngine

        logger.info("Loading AI compute layers (TimesFM checkpoint + Gemini router)...")
        self.tick_engine = TickEngine(
            router=GeminiModelRouter(),
            forecaster=TimesFMForecaster(),
            matching_engine=MatchingEngine(),
        )

    async def run_loop(self) -> None:
        self.pausing_in_progress = False
        try:
            while True:
                if self.paused:
                    if self.pausing_in_progress:
                        self.pausing_in_progress = False
                        await self.manager.broadcast({
                            "type": "sim_status",
                            "tick_id": self.next_tick_id,
                            "ts": "",
                            "payload": self.get_status_payload()
                        })
                    await asyncio.sleep(0.5)
                    continue

                # `>` (not `>=`): target_tick is the LAST tick that should
                # run — pausing at >= skipped it (25-day runs did 24 ticks).
                if self.target_tick is not None and self.next_tick_id > self.target_tick:
                    self.paused = True
                    self.target_tick = None
                    await self.manager.broadcast({
                        "type": "sim_status",
                        "tick_id": self.next_tick_id,
                        "ts": "",
                        "payload": self.get_status_payload()
                    })
                    await asyncio.sleep(0.5)
                    continue

                events = await self.tick_engine.execute_simulation_tick(
                    self.next_tick_id,
                    self.active_event,
                    sim_time=self.sim_time_for(self.next_tick_id),
                    ticks_per_day=self.ticks_per_day,
                )
                self.next_tick_id += 1
                for event in events:
                    await self.manager.broadcast(event)
                await asyncio.sleep(self.tick_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the loop must die loudly, never silently
            logger.exception("Simulation loop crashed on tick %d", self.next_tick_id)
            await self.manager.broadcast(
                {
                    "type": "error",
                    "tick_id": self.next_tick_id,
                    "ts": "",
                    "payload": {"error": f"simulation halted: {exc}"},
                }
            )

    async def forecast_refresh_loop(self) -> None:
        """Slow AI loop: event analyst + batched TimesFM, off the tick path.

        Keeps `tick_engine.forecast_cache` fresh (event-conditioned) so the
        fast tick loop only ever snapshots a dict. Errors are logged and
        retried next cycle; they never take down the simulation.
        """
        try:
            while True:
                engine_ref = self.tick_engine
                if engine_ref is not None:
                    try:
                        await engine_ref.refresh_event_impact(self.active_event)
                        await asyncio.to_thread(engine_ref.refresh_forecasts)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning("forecast refresh failed: %s", exc)
                await asyncio.sleep(FORECAST_REFRESH_SECONDS)
        except asyncio.CancelledError:
            raise

    async def stop_ai_tasks(self) -> None:
        """Cancel the tick and refresh loops (idempotent)."""
        for attr in ("task", "forecast_task"):
            t: asyncio.Task[None] | None = getattr(self, attr)
            if t is not None:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
                setattr(self, attr, None)


controller = SimulationController()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Always wipe database on startup to ensure a fresh simulation run
    logger.info("Wiping database for fresh run...")
    Base.metadata.drop_all(bind=engine)
    init_db()
    created = seed_initial_market_state()
    if created:
        logger.info("Seeded %d agents into an empty market.", created)
    controller.load_tick_counter()
    yield
    await controller.stop_ai_tasks()


app = FastAPI(title="ChaosNet: Black Swan Market Twin", lifespan=lifespan)

# Allowed browser origins — comma-separated ALLOWED_ORIGINS in production
# (e.g. the deployed Vercel URL); defaults to local dev.
_origins_env = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:5055,http://localhost:3000"
)
ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()]
# True only when every allowed origin is loopback — gates the WS no-Origin
# exception so it applies in local dev but never in a public deployment.
_LOCALHOST_ONLY = bool(ALLOWED_ORIGINS) and all(
    ("localhost" in o or "127.0.0.1" in o) for o in ALLOWED_ORIGINS
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Bounded request models: caps prevent an unauthenticated caller from
# spinning unbounded compute (a 999999999-day run) or pushing oversized text
# into the paid LLM prompts. duration_days x ticks_per_day now bounds total
# ticks to a finite run even at MAX speed (speed=0). Pydantic rejects
# out-of-range input with a 422 before any work starts.
MAX_DURATION_DAYS = 365
MAX_TICKS_PER_DAY = 96
MAX_INTERVAL_SECONDS = 60.0


class EventPayload(BaseModel):
    headline: str = Field(max_length=500)

class StartPayload(BaseModel):
    speed: float = Field(ge=0.0, le=MAX_INTERVAL_SECONDS)
    duration_days: int = Field(ge=1, le=MAX_DURATION_DAYS)
    ticks_per_day: int = Field(ge=1, le=MAX_TICKS_PER_DAY)

class SpeedPayload(BaseModel):
    interval: float = Field(ge=0.0, le=MAX_INTERVAL_SECONDS)

class DurationPayload(BaseModel):
    days: int = Field(ge=1, le=MAX_DURATION_DAYS)

class ChatMessagePayload(BaseModel):
    role: str = Field(max_length=16)
    content: str = Field(max_length=2000)

class ChatRequest(BaseModel):
    messages: list[ChatMessagePayload] = Field(max_length=50)

class GeminiKeyPayload(BaseModel):
    primary: str = Field(min_length=10, max_length=200)
    backup: str | None = Field(default=None, max_length=200)


# --------------------------------------------------------------------- #
# Config: compute device + runtime API-key entry                        #
# --------------------------------------------------------------------- #

_compute_cache: dict[str, str] | None = None


def _compute_info() -> dict[str, str]:
    """Resolved inference device + torch backend, cached (torch import is heavy).

    `torch.cuda` is the accelerator API for both NVIDIA CUDA and AMD ROCm, so
    an AMD GPU under the ROCm wheel reports device='cuda', backend='ROCm/HIP'.
    """
    global _compute_cache
    if _compute_cache is None:
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
            backend = (
                "ROCm/HIP"
                if getattr(torch.version, "hip", None)
                else ("CUDA" if getattr(torch.version, "cuda", None) else "CPU")
            )
        except Exception:
            device, backend = "unknown", "unknown"
        _compute_cache = {"device": device, "compute_backend": backend}
    return _compute_cache


def _llm_active() -> bool:
    router = getattr(controller.tick_engine, "router", None) if controller.tick_engine else None
    if router is not None:
        return bool(getattr(router, "is_active", False))
    return bool(os.getenv("GEMINI_API_KEY_PRIMARY"))


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    """Compute device + whether LLM prose is active (drives the setup console)."""
    return {"llm_active": _llm_active(), **_compute_info()}


@app.post("/api/config/gemini")
async def set_gemini_key(payload: GeminiKeyPayload, request: Request) -> dict[str, Any]:
    """Add a Gemini key at RUNTIME — enables LLM news/PR/desk prose with no
    restart. The key is injected into any live routers and set in the process
    env for future ones; it is never logged, echoed, or persisted to disk."""
    _rate_limit_llm(request)
    keys = [payload.primary.strip()]
    if payload.backup and payload.backup.strip():
        keys.append(payload.backup.strip())
    os.environ["GEMINI_API_KEY_PRIMARY"] = keys[0]
    os.environ["GEMINI_API_KEY_BACKUP"] = keys[1] if len(keys) > 1 else keys[0]
    live = getattr(controller.tick_engine, "router", None) if controller.tick_engine else None
    # chat_router is often the SAME object as tick_engine.router — dedup by
    # identity so the count reflects distinct routers actually updated.
    routers = {id(r): r for r in (live, controller.chat_router) if r is not None}
    updated = 0
    for router in routers.values():
        if hasattr(router, "set_keys"):
            router.set_keys(keys)
            updated += 1
    logger.info("Gemini key set at runtime — %d live router(s) updated; LLM prose enabled", updated)
    return {"status": "ok", "llm_active": True, "live_routers_updated": updated}


# --------------------------------------------------------------------- #
# Simulation controls                                                    #
# --------------------------------------------------------------------- #


@app.post("/api/start")
async def start_simulation(payload: StartPayload | None = None) -> dict[str, Any]:
    if controller.is_running:
        if not controller.paused:
            # Actively ticking — don't stomp a live run.
            return {"status": "already_running", "payload": controller.get_status_payload()}
        # Paused loop (a completed run parks here, task still alive in its
        # sleep loop). Starting again means "new simulation": stop the old
        # tasks so the fresh-start path below can reset the world. Resuming
        # a paused run is /api/resume, not /api/start.
        await controller.stop_ai_tasks()

    # A new simulation always begins from a clean world at tick 1. If a
    # previous run left ticks behind, wipe and reseed (keeping the armed
    # event, which the client posts before calling start).
    controller.load_tick_counter()
    world_was_reset = False
    if controller.next_tick_id > 1:
        await controller.stop_ai_tasks()  # refresh loop must not read mid-wipe
        controller.reset_world(preserve_event=True)
        world_was_reset = True

    if payload:
        controller.tick_interval_seconds = payload.speed
        controller.duration_days = payload.duration_days
        controller.ticks_per_day = payload.ticks_per_day
    else:
        # Bare start: a bounded, well-formed default run.
        controller.duration_days = controller.duration_days or DEFAULT_DURATION_DAYS
        controller.ticks_per_day = controller.ticks_per_day or DEFAULT_TICKS_PER_DAY
    controller.max_ticks = controller.duration_days * controller.ticks_per_day
    controller.target_tick = controller.next_tick_id + controller.max_ticks - 1

    controller.sim_start = datetime.now(timezone.utc)

    try:
        controller.build_engines()
    except Exception as exc:
        # Log the real cause server-side; return a generic message so the
        # client never sees internal paths / package internals from a failed
        # torch/timesfm/HF-checkpoint init.
        logger.exception("AI layer startup failed")
        raise HTTPException(
            status_code=500, detail="failed to initialize AI engines"
        ) from exc
    controller.paused = False
    # Refresh loop first so the forecast cache warms as early as possible.
    controller.forecast_task = asyncio.create_task(controller.forecast_refresh_loop())
    controller.task = asyncio.create_task(controller.run_loop())
    status = controller.get_status_payload()
    if world_was_reset:
        asyncio.create_task(controller.manager.broadcast({"type": "reset", "tick_id": 1, "ts": "", "payload": status}))
    asyncio.create_task(controller.manager.broadcast({"type": "sim_status", "tick_id": controller.next_tick_id, "ts": "", "payload": status}))
    return {
        "status": "started",
        "payload": status,
    }


@app.post("/api/stop")
async def stop_simulation() -> dict[str, Any]:
    was_running = controller.is_running and controller.task is not None
    # Always tear down BOTH loops (idempotent). If the tick loop crashed and
    # returned, is_running is False but the independent forecast_refresh_loop
    # can still be alive — gating on is_running would orphan it forever.
    await controller.stop_ai_tasks()
    if not was_running:
        return {"status": "not_running", "payload": controller.get_status_payload()}
    status = controller.get_status_payload()
    asyncio.create_task(controller.manager.broadcast({"type": "sim_status", "tick_id": controller.next_tick_id, "ts": "", "payload": status}))
    return {"status": "stopped", "payload": status}


@app.post("/api/reset")
async def reset_simulation() -> dict[str, Any]:
    """Wipe the entire simulation database and restart from tick 1."""
    # Both loops must stop BEFORE the wipe — the refresh worker thread must
    # not read tables while they drop. reset_world() nulls tick_engine,
    # which destroys the forecast/event caches with it.
    await controller.stop_ai_tasks()

    controller.reset_world(preserve_event=False)
    # Reset always lands on an IDLE world at tick 0. Nothing auto-restarts:
    # the setup console owns launching runs (a page refresh resets to state
    # zero and must not spawn an unconfigured background run).

    status = controller.get_status_payload()
    asyncio.create_task(controller.manager.broadcast({
        "type": "reset", 
        "tick_id": 1, 
        "ts": "", 
        "payload": status
    }))
    return {"status": "reset", "payload": status}


@app.post("/api/event")
async def inject_event(payload: EventPayload, request: Request) -> dict[str, Any]:
    """Set the active Black Swan headline fed into every cohort prompt."""
    _rate_limit_llm(request)
    controller.active_event = payload.headline.strip()
    # Kick the analyst immediately so the per-sector impact lands now
    # instead of waiting for the next refresh cycle.
    if controller.tick_engine is not None:
        asyncio.create_task(
            controller.tick_engine.refresh_event_impact(controller.active_event)
        )
    return {"status": "event_set", "headline": controller.active_event}

@app.post("/api/pause")
async def pause_simulation() -> dict[str, Any]:
    if controller.is_running and not controller.paused:
        controller.pausing_in_progress = True
    controller.paused = True
    payload = controller.get_status_payload()
    payload["pausing_in_progress"] = getattr(controller, "pausing_in_progress", False)
    asyncio.create_task(controller.manager.broadcast({"type": "sim_status", "tick_id": controller.next_tick_id, "ts": "", "payload": payload}))
    return payload

@app.post("/api/resume")
async def resume_simulation() -> dict[str, Any]:
    if not controller.is_running:
        raise HTTPException(status_code=400, detail="Simulation not started")
    controller.paused = False
    payload = controller.get_status_payload()
    asyncio.create_task(controller.manager.broadcast({"type": "sim_status", "tick_id": controller.next_tick_id, "ts": "", "payload": payload}))
    return payload

@app.post("/api/speed")
async def set_speed(payload: SpeedPayload) -> dict[str, Any]:
    controller.tick_interval_seconds = max(0.0, payload.interval)
    status = controller.get_status_payload()
    asyncio.create_task(controller.manager.broadcast({"type": "sim_status", "tick_id": controller.next_tick_id, "ts": "", "payload": status}))
    return status

@app.post("/api/duration")
async def set_duration(payload: DurationPayload) -> dict[str, Any]:
    """Set the run's TOTAL length in simulated days (not additional days).

    Runs always begin at tick 1 under the state-zero flow, so the absolute
    target is days * ticks_per_day. Shrinking below the current tick pauses
    the run on its next iteration — that is the intended "cut it short".
    """
    if controller.ticks_per_day is None:
        raise HTTPException(status_code=400, detail="Configure a run via /api/start first.")
    if payload.days <= 0:
        controller.target_tick = None
        controller.max_ticks = None
        controller.duration_days = None
    else:
        controller.duration_days = payload.days
        controller.max_ticks = payload.days * controller.ticks_per_day
        controller.target_tick = controller.max_ticks
    status = controller.get_status_payload()
    asyncio.create_task(controller.manager.broadcast({"type": "sim_status", "tick_id": controller.next_tick_id, "ts": "", "payload": status}))
    return status

@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    return controller.get_status_payload()


# --------------------------------------------------------------------- #
# World data (shared contract with the frontend)                         #
# --------------------------------------------------------------------- #


@app.get("/api/state")
async def get_state() -> dict[str, Any]:
    with SessionLocal() as db:
        companies = list(db.execute(select(Company)).scalars())
        posts = list(
            db.execute(select(SocialPost).order_by(SocialPost.tick_id.desc()).limit(50)).scalars()
        )
        economy = _latest_economy(db)
        latest_tick = db.execute(select(func.max(WorldState.tick_id))).scalar_one() or 0
    return {
        "companies": [_company_payload(c) for c in companies],
        "economy": economy,
        "social": [_post_payload(p) for p in posts],
        "tick_id": latest_tick,
    }


@app.get("/api/history/{tick_id}")
async def get_history(tick_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        companies = list(db.execute(select(Company)).scalars())
        company_dict = {c.ticker: c for c in companies}
        
        snapshots = list(db.execute(
            select(CompanySnapshot).where(CompanySnapshot.tick_id == tick_id)
        ).scalars())
        
        if not snapshots:
            raise HTTPException(status_code=404, detail=f"No snapshot for tick {tick_id}")
            
        merged_companies = []
        for snap in snapshots:
            c = company_dict.get(snap.ticker)
            if c:
                payload = _company_payload(c)
                payload.update({
                    "current_price": snap.current_price,
                    "sentiment": snap.sentiment,
                    "volatility": snap.volatility,
                    "is_bankrupt": snap.is_bankrupt,
                    "market_cap": snap.current_price * c.shares_outstanding,
                    "change_pct": round((snap.current_price - c.anchor_price) / c.anchor_price * 100.0, 4) if c.anchor_price else 0.0,
                })
                merged_companies.append(payload)

        row = db.execute(
            select(EconomySnapshot).where(EconomySnapshot.tick_id == tick_id)
        ).scalar_one_or_none()
        economy = snapshot_from_row(row) if row else None

        posts = list(
            db.execute(
                select(SocialPost)
                .where(SocialPost.tick_id <= tick_id)
                .order_by(SocialPost.tick_id.desc())
                .limit(50)
            ).scalars()
        )

    return {
        "companies": merged_companies,
        "economy": economy,
        "social": [_post_payload(p) for p in posts],
        "tick_id": tick_id,
    }


@app.get("/api/companies")
async def get_companies() -> dict[str, Any]:
    with SessionLocal() as db:
        companies = list(db.execute(select(Company)).scalars())
    return {"companies": [_company_payload(c) for c in companies]}


@app.get("/api/companies/{ticker}")
async def get_company(ticker: str) -> dict[str, Any]:
    with SessionLocal() as db:
        company = db.get(Company, ticker.upper())
        if company is None:
            raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")
        posts = list(
            db.execute(
                select(SocialPost)
                .where(SocialPost.author_ticker == company.ticker)
                .order_by(SocialPost.tick_id.desc())
                .limit(20)
            ).scalars()
        )
        series = list(
            db.execute(
                select(PriceTick)
                .where(PriceTick.ticker == company.ticker)
                .order_by(PriceTick.tick_id.desc())
                .limit(512)
            ).scalars()
        )
    payload = _company_payload(company)
    payload["recent_posts"] = [_post_payload(p) for p in posts]
    payload["price_series"] = [
        {"t": _epoch(row.ts), "tick": row.tick_id, "price": row.price, "volume": row.volume}
        for row in reversed(series)
    ]
    return payload


@app.get("/api/companies/{ticker}/prices")
async def get_company_prices(ticker: str, limit: int = 512) -> dict[str, Any]:
    limit = max(1, min(limit, 2048))
    with SessionLocal() as db:
        company = db.get(Company, ticker.upper())
        if company is None:
            raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")
        series = list(
            db.execute(
                select(PriceTick)
                .where(PriceTick.ticker == company.ticker)
                .order_by(PriceTick.tick_id.desc())
                .limit(limit)
            ).scalars()
        )
    return {
        "ticker": company.ticker,
        "prices": [
            {
                # t is simulated time (epoch seconds) so charts get a real
                # time axis; tick keeps the ordinal for tick-based views.
                "t": _epoch(row.ts),
                "tick": row.tick_id,
                "price": row.price,
                "volume": row.volume,
            }
            for row in reversed(series)
        ],
    }


@app.get("/api/social")
async def get_social(limit: int = 50, ticker: str | None = None) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    with SessionLocal() as db:
        query = select(SocialPost).order_by(SocialPost.tick_id.desc()).limit(limit)
        if ticker:
            query = query.where(SocialPost.author_ticker == ticker.upper())
        posts = list(db.execute(query).scalars())
    return {"posts": [_post_payload(p) for p in posts]}


@app.get("/api/economy")
async def get_economy() -> dict[str, Any]:
    with SessionLocal() as db:
        return _latest_economy(db)


@app.post("/api/chat")
async def chat_with_desk(payload: ChatRequest, request: Request) -> dict[str, Any]:
    """Senior-trader desk chat, grounded in the live market's real numbers.

    Works with or without a running simulation and with or without LLM
    quota: the statistical brief (momentum, volatility, forecast deltas) is
    always computed from real data, and if the desk model is unreachable the
    reply IS that brief — never fabricated prose.
    """
    _rate_limit_llm(request)
    if not payload.messages or payload.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="messages must end with a user turn")

    with SessionLocal() as db:
        companies = list(
            db.execute(select(Company).where(Company.is_bankrupt.is_(False))).scalars()
        )
        info = {
            c.ticker: {"current_price": c.current_price, "anchor_price": c.anchor_price}
            for c in companies
        }
        histories: dict[str, list[float]] = {c.ticker: [] for c in companies}
        rows = db.execute(
            select(PriceTick.ticker, PriceTick.price)
            .order_by(PriceTick.tick_id.desc())
            .limit(60 * max(1, len(companies)))
        ).all()
        for ticker, price in reversed(rows):
            series = histories.get(ticker)
            if series is not None:
                series.append(price)
        econ = db.execute(
            select(EconomySnapshot).order_by(EconomySnapshot.tick_id.desc()).limit(1)
        ).scalar_one_or_none()

    stress = econ.system_stress_index if econ else 0.0
    bankrupt = econ.bankrupt_count if econ else 0
    forecasts = dict(controller.tick_engine.forecast_cache) if controller.tick_engine else {}
    stats = compute_ticker_stats(histories, info, forecasts)
    brief = build_market_brief(stats, stress, bankrupt)

    # Lazy router: reuse the engine's when a run is live, otherwise build a
    # standalone one; if keys are missing, fall back to the offline brief.
    if controller.chat_router is None:
        if controller.tick_engine is not None:
            controller.chat_router = controller.tick_engine.router
        else:
            try:
                from ai_clients import GeminiModelRouter

                controller.chat_router = GeminiModelRouter()
            except Exception as exc:
                logger.warning("chat router unavailable: %s", exc)
    if controller.chat_router is None:
        return {"reply": render_offline_brief(brief), "source": "offline"}

    desk = SeniorTraderAgent(controller.chat_router)
    messages = [ChatMessage(role=m.role, content=m.content) for m in payload.messages]
    async with aiohttp.ClientSession() as http:
        reply, source = await desk.answer(http, messages, brief)
    return {"reply": reply, "source": source}


@app.get("/api/indices")
async def get_indices_endpoint() -> dict[str, Any]:
    """Real-market index strip (cached). Empty list if the provider is down."""
    from market_data import get_indices

    return {"indices": await asyncio.to_thread(get_indices)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    if not await controller.manager.connect(websocket):
        return  # origin rejected or connection cap reached
    try:
        while True:
            # Dashboard clients don't send commands; this keeps the socket
            # open and lets disconnects surface promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        controller.manager.disconnect(websocket)
