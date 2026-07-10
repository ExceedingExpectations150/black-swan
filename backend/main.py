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
import logging
import os
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select

from database import SessionLocal, init_db, seed_initial_market_state, Base, engine
from economy import compute_economy_snapshot, snapshot_from_row
from models import (
    Company,
    CompanySnapshot,
    EconomySnapshot,
    PriceTick,
    SocialPost,
    WorldState,
)
from sim_time import sim_clock

logger = logging.getLogger("chaosnet.main")

TICK_INTERVAL_SECONDS: float = 3.0
# How often the background thread recomputes the whole-market TimesFM forecast.
# Decoupled from the tick so the ~10 s CPU inference never stalls real-time play.
FORECAST_REFRESH_SECONDS: float = 8.0


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


class ConnectionManager:
    """Tracks live WebSocket clients and fans envelope events out to all."""

    def __init__(self) -> None:
        self._clients: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._clients:
            self._clients.remove(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for client in self._clients:
            try:
                await client.send_json(payload)
            except Exception:
                dead.append(client)
        for client in dead:
            self.disconnect(client)


class SimulationController:
    """Owns the tick loop, the active macro event, and the lazy AI engines."""

    def __init__(self) -> None:
        self.manager = ConnectionManager()
        self.tick_engine: Any = None
        self.task: asyncio.Task[None] | None = None
        self.forecast_task: asyncio.Task[None] | None = None
        self.active_event: str = ""
        self.next_tick_id: int = 1
        self.tick_interval_seconds: float = TICK_INTERVAL_SECONDS
        self.paused: bool = False
        self.target_tick: int | None = None
        self.max_ticks: int | None = None
        self.duration_days: int | None = None
        self.ticks_per_day: int | None = None

    def get_status_payload(self) -> dict[str, Any]:
        return {
            "is_running": self.is_running,
            "paused": self.paused,
            "tick_interval_seconds": self.tick_interval_seconds,
            "target_tick": self.target_tick,
            "next_tick": self.next_tick_id,
            "max_ticks": self.max_ticks,
            "duration_days": self.duration_days,
            "ticks_per_day": self.ticks_per_day
        }

    @property
    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def load_tick_counter(self) -> None:
        with SessionLocal() as db:
            latest = db.execute(select(func.max(WorldState.tick_id))).scalar_one()
        self.next_tick_id = (latest or 0) + 1

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

                if self.target_tick is not None and self.next_tick_id >= self.target_tick:
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
                    self.next_tick_id, self.active_event
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
        """Refresh the TimesFM forecast cache off the event loop, forever.

        The heavy inference runs in a worker thread so it never blocks the tick
        loop. Errors are logged and retried on the next cycle — a bad forecast
        refresh must not take down real-time play (the market keeps trading on
        the local crowd and the last good forecast).
        """
        try:
            while True:
                try:
                    # 1. Analyst agent maps the active event -> per-ticker impact
                    #    (only re-runs when the event changes). 2. TimesFM
                    #    forecast, conditioned on that impact, off-thread.
                    await self.tick_engine.refresh_event_impact(self.active_event)
                    await asyncio.to_thread(self.tick_engine.refresh_forecasts)
                except Exception:
                    logger.exception("forecast/impact refresh failed")
                await asyncio.sleep(FORECAST_REFRESH_SECONDS)
        except asyncio.CancelledError:
            raise


controller = SimulationController()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Fresh run on each launch (the setup modal expects tick 0), but reuse the
    # already-fetched real anchor prices — reset_world wipes only the dynamic
    # state and reseeds agents, so no market-data refetch is needed after the
    # first-ever seed. Avoids the drop_all + yfinance-on-every-boot fragility.
    init_db()
    created = seed_initial_market_state()
    if created:
        logger.info("Seeded %d agents into an empty market.", created)
    else:
        from database import reset_world

        reset_world()
        logger.info("Fresh run: reset dynamic state, kept real anchors.")
    controller.load_tick_counter()
    yield
    if controller.is_running and controller.task is not None:
        controller.task.cancel()
    if controller.forecast_task is not None:
        controller.forecast_task.cancel()


app = FastAPI(title="ChaosNet: Black Swan Market Twin", lifespan=lifespan)

# Allowed browser origins — comma-separated ALLOWED_ORIGINS in production
# (e.g. the deployed Vercel URL); defaults to local dev.
_origins_env = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:5055,http://localhost:3000"
)
ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EventPayload(BaseModel):
    headline: str

class StartPayload(BaseModel):
    speed: float
    duration_days: int
    ticks_per_day: int

class SpeedPayload(BaseModel):
    interval: float

class DurationPayload(BaseModel):
    ticks: int


# --------------------------------------------------------------------- #
# Simulation controls                                                    #
# --------------------------------------------------------------------- #


@app.post("/api/start")
async def start_simulation(payload: StartPayload | None = None) -> dict[str, Any]:
    if controller.is_running:
        return {"status": "already_running", "payload": controller.get_status_payload()}
        
    if payload:
        controller.tick_interval_seconds = payload.speed
        controller.duration_days = payload.duration_days
        controller.ticks_per_day = payload.ticks_per_day
        controller.max_ticks = payload.duration_days * payload.ticks_per_day
        controller.target_tick = controller.next_tick_id + controller.max_ticks - 1
        
    try:
        controller.build_engines()
    except Exception as exc:
        logger.exception("AI layer startup failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    controller.load_tick_counter()
    controller.paused = False
    # Background TimesFM refresher first so a forecast starts warming immediately,
    # then the real-time tick loop (which reads the forecast cache).
    controller.forecast_task = asyncio.create_task(controller.forecast_refresh_loop())
    controller.task = asyncio.create_task(controller.run_loop())
    status = controller.get_status_payload()
    asyncio.create_task(controller.manager.broadcast({"type": "sim_status", "tick_id": controller.next_tick_id, "ts": "", "payload": status}))
    return {
        "status": "started",
        "payload": status,
    }


@app.post("/api/stop")
async def stop_simulation() -> dict[str, Any]:
    if not controller.is_running or controller.task is None:
        return {"status": "not_running", "payload": controller.get_status_payload()}
    controller.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await controller.task
    controller.task = None
    if controller.forecast_task is not None:
        controller.forecast_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await controller.forecast_task
        controller.forecast_task = None
    status = controller.get_status_payload()
    asyncio.create_task(
        controller.manager.broadcast(
            {"type": "sim_status", "tick_id": controller.next_tick_id, "ts": "", "payload": status}
        )
    )
    return {"status": "stopped", "payload": status}


@app.post("/api/reset")
async def reset_simulation() -> dict[str, Any]:
    """Reseed a pristine world (fresh cash + holdings) and restart from tick 1.

    Uses reset_world (targeted wipe that keeps the real anchor prices, so no
    market-data fetch is needed), clears the analyst/forecast caches and all
    playback state, and — if the sim was running — restarts both loops.
    """
    was_running = controller.is_running
    for task_attr in ("task", "forecast_task"):
        task = getattr(controller, task_attr)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            setattr(controller, task_attr, None)

    from database import reset_world

    created = await asyncio.to_thread(reset_world)
    logger.info("World reset. Seeded %d agents.", created)

    controller.active_event = ""
    controller.next_tick_id = 1
    controller.paused = False
    controller.target_tick = None
    controller.max_ticks = None
    controller.duration_days = None
    controller.ticks_per_day = None
    if controller.tick_engine is not None:
        controller.tick_engine.reset_ai_state()
    controller.load_tick_counter()

    if was_running:
        controller.build_engines()
        controller.forecast_task = asyncio.create_task(controller.forecast_refresh_loop())
        controller.task = asyncio.create_task(controller.run_loop())

    status = controller.get_status_payload()
    asyncio.create_task(
        controller.manager.broadcast(
            {"type": "reset", "tick_id": 1, "ts": "", "payload": status}
        )
    )
    return {"status": "reset", "payload": status}


@app.post("/api/event")
async def inject_event(payload: EventPayload) -> dict[str, Any]:
    """Set the active Black Swan headline the analyst agent reasons about."""
    controller.active_event = payload.headline.strip()
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
    if payload.ticks <= 0:
        controller.target_tick = None
        controller.max_ticks = None
    else:
        controller.target_tick = controller.next_tick_id + payload.ticks
        controller.max_ticks = controller.next_tick_id + payload.ticks - 1
    status = controller.get_status_payload()
    asyncio.create_task(controller.manager.broadcast({"type": "sim_status", "tick_id": controller.next_tick_id, "ts": "", "payload": status}))
    return status

@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    return controller.get_status_payload()


@app.get("/api/analyst")
async def get_analyst() -> dict[str, Any]:
    """Current event-impact analysis feeding the TimesFM forecast."""
    engine = controller.tick_engine
    if engine is None:
        return {"event": controller.active_event, "source": "", "impact": {}}
    return {
        "event": controller.active_event,
        "source": engine.event_impact_source,
        "impact": engine.event_impact,
    }


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
        "clock": sim_clock(latest_tick),
        # Playback status so a fresh page load / reconnect knows the current
        # pause/speed/duration state (the timeline + controls read these).
        **controller.get_status_payload(),
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
        {"t": row.tick_id, "price": row.price, "volume": row.volume} for row in reversed(series)
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
            {"t": row.tick_id, "price": row.price, "volume": row.volume}
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


@app.get("/api/indices")
async def get_indices_endpoint() -> dict[str, Any]:
    """Real-market index strip (cached). Empty list if the provider is down."""
    from market_data import get_indices

    return {"indices": await asyncio.to_thread(get_indices)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await controller.manager.connect(websocket)
    try:
        while True:
            # Dashboard clients don't send commands; this keeps the socket
            # open and lets disconnects surface promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        controller.manager.disconnect(websocket)
