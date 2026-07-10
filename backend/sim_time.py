"""Tick -> simulated market time for ChaosNet "Black Swan".

The world advances by ticks; this maps a tick to a simulated trading clock so
the UI can show a timeline and a session progress bar. One tick = one simulated
minute; each "day" is one 09:30->16:00 trading session (390 ticks), after which
the clock rolls to the next day. `session_pct` (0..1) drives the time bar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

SIM_EPOCH: datetime = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)  # Mon 09:30
SIM_SECONDS_PER_TICK: int = 60  # 1 tick = 1 simulated minute
SESSION_OPEN_MIN: int = 9 * 60 + 30   # 09:30
SESSION_CLOSE_MIN: int = 16 * 60      # 16:00
SESSION_LEN_MIN: int = SESSION_CLOSE_MIN - SESSION_OPEN_MIN  # 390


def sim_datetime(tick_id: int) -> datetime:
    """Continuous absolute simulated timestamp for a tick."""
    return SIM_EPOCH + timedelta(seconds=SIM_SECONDS_PER_TICK * max(0, tick_id))


def sim_clock(tick_id: int) -> dict[str, Any]:
    """Timeline payload: absolute time, a Day N · HH:MM label, session progress."""
    tick_id = max(0, tick_id)
    total_min = SIM_SECONDS_PER_TICK * tick_id // 60
    day = total_min // SESSION_LEN_MIN + 1
    into_session = total_min % SESSION_LEN_MIN
    clock_min = SESSION_OPEN_MIN + into_session
    hh, mm = divmod(clock_min, 60)
    return {
        "tick_id": tick_id,
        "sim_time": sim_datetime(tick_id).isoformat(),
        "sim_label": f"Day {day} · {hh:02d}:{mm:02d}",
        "session_pct": round(into_session / SESSION_LEN_MIN, 4),
    }
