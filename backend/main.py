"""FastAPI entrypoint for ChaosNet "Black Swan" (PRD.md Phase 3).

POST /api/start  — boots the AI layers (first call loads TimesFM) and starts
                   the 3-second simulation loop.
POST /api/stop   — halts the loop.
WS   /ws         — live telemetry: every tick's payload is broadcast to all
                   connected dashboard clients.

Run: uvicorn main:app --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select

from database import SessionLocal, init_db, seed_initial_market_state
from models import WorldState

logger = logging.getLogger("chaosnet.main")

TICK_INTERVAL_SECONDS: float = 3.0
INITIAL_PRICE: float = 100.0
CONTEXT_TICKS: int = 512


class ConnectionManager:
    """Tracks live WebSocket clients and fans telemetry out to all of them."""

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
    """Owns the tick loop, market state, and lazily-built AI engines."""

    def __init__(self) -> None:
        self.manager = ConnectionManager()
        self.tick_engine: Any = None
        self.task: asyncio.Task[None] | None = None
        self.active_event: str = ""
        self.current_price: float = INITIAL_PRICE
        self.price_history: list[float] = [INITIAL_PRICE]
        self.next_tick_id: int = 1
        self.last_telemetry: dict[str, Any] | None = None

    @property
    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def load_market_state(self) -> None:
        """Resume price series and tick counter from the database."""
        with SessionLocal() as db:
            rows = list(
                db.execute(
                    select(WorldState.tick_id, WorldState.current_price)
                    .order_by(WorldState.tick_id.desc())
                    .limit(CONTEXT_TICKS)
                ).all()
            )
        if rows:
            rows.reverse()
            self.price_history = [price for _, price in rows]
            self.current_price = self.price_history[-1]
            self.next_tick_id = rows[-1][0] + 1
        else:
            self.current_price = INITIAL_PRICE
            self.price_history = [INITIAL_PRICE]
            self.next_tick_id = 1

    def build_engines(self) -> None:
        """Construct the AI layers. Hard-errors without keys/torch by design."""
        if self.tick_engine is not None:
            return
        # Imported here so the API process can boot (and /api/stop, /ws work)
        # before the heavy TimesFM checkpoint load happens on first /api/start.
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
        try:
            await self._run_loop_inner()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # loop must die loudly, not silently
            logger.exception("Simulation loop crashed on tick %d", self.next_tick_id)
            await self.manager.broadcast(
                {"error": f"simulation halted: {exc}", "tick_id": self.next_tick_id}
            )

    async def _run_loop_inner(self) -> None:
        while True:
            telemetry = await self.tick_engine.execute_simulation_tick(
                tick_id=self.next_tick_id,
                active_event=self.active_event,
                current_price=self.current_price,
                price_history=self.price_history,
            )
            self.next_tick_id += 1
            self.current_price = telemetry["clearing_price"]
            self.price_history.append(self.current_price)
            self.price_history = self.price_history[-CONTEXT_TICKS:]
            self.last_telemetry = telemetry
            await self.manager.broadcast(telemetry)
            await asyncio.sleep(TICK_INTERVAL_SECONDS)


controller = SimulationController()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    created = seed_initial_market_state()
    if created:
        logger.info("Seeded %d agents into an empty market.", created)
    controller.load_market_state()
    yield
    if controller.is_running and controller.task is not None:
        controller.task.cancel()


app = FastAPI(title="ChaosNet: Black Swan Market Twin", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5055", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class EventPayload(BaseModel):
    headline: str


@app.post("/api/start")
async def start_simulation() -> dict[str, Any]:
    if controller.is_running:
        return {"status": "already_running", "next_tick": controller.next_tick_id}
    try:
        controller.build_engines()
    except Exception as exc:
        # Surface the hard error (missing keys, broken torch, ...) as a real
        # HTTP response — raw exceptions skip CORSMiddleware and reach the
        # browser as an opaque "Failed to fetch".
        logger.exception("AI layer startup failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    controller.load_market_state()
    controller.task = asyncio.create_task(controller.run_loop())
    return {
        "status": "started",
        "tick_interval_seconds": TICK_INTERVAL_SECONDS,
        "next_tick": controller.next_tick_id,
        "current_price": controller.current_price,
    }


@app.post("/api/stop")
async def stop_simulation() -> dict[str, Any]:
    if not controller.is_running or controller.task is None:
        return {"status": "not_running"}
    controller.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await controller.task
    controller.task = None
    return {"status": "stopped", "last_tick": controller.next_tick_id - 1}


@app.post("/api/event")
async def inject_event(payload: EventPayload) -> dict[str, Any]:
    """Set the active Black Swan headline fed into every cohort prompt."""
    controller.active_event = payload.headline.strip()
    return {"status": "event_set", "headline": controller.active_event}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await controller.manager.connect(websocket)
    if controller.last_telemetry is not None:
        await websocket.send_json(controller.last_telemetry)
    try:
        while True:
            # Dashboard clients don't send commands; this keeps the socket
            # open and lets disconnects surface promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        controller.manager.disconnect(websocket)
