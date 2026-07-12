"""Event-impact analyst for ChaosNet "Black Swan".

An LLM agent reads the active Black Swan headline and estimates its near-term
price impact per market sector. That impact map is what conditions the TimesFM
forecast: TimesFM is univariate — it extrapolates price history and cannot know
a novel shock is coming — so the analyst injects the forward-looking, event-
driven adjustment. Final quant forecast = TimesFM point forecast x (1 + impact).

Primary path is the Gemini/Gemma model (the "agent"). If it is unreachable or
rate-limited, a transparent keyword heuristic keeps the quants event-aware so
the market still reacts. Impacts are decimal fractions (-0.08 = -8%).
"""

from __future__ import annotations

import asyncio
import json
import re

import aiohttp

from ai_clients import GeminiModelRouter

# The LLM agent is best-effort: if it does not answer within this budget, fall
# back to the keyword heuristic so the forecast refresh never stalls.
LLM_TIMEOUT_SECONDS: float = 10.0

# Impacts are clamped to a sane band so one bad parse can't blow up the market.
IMPACT_CLAMP: float = 0.35
# Broad market tilt applied to every sector, signed by the event's polarity:
# a risk-off shock pulls the whole market down, a positive catalyst lifts it.
BROAD_BEARISH_TILT: float = -0.05
BROAD_BULLISH_TILT: float = 0.04

# Polarity lexicons. The balance of these decides the broad market tilt, so a
# genuinely good headline ("stimulus package", "peace deal", "record earnings")
# is NOT forced risk-off — stocks can rally on it.
_BULLISH_WORDS: tuple[str, ...] = (
    "stimulus", "rate cut", "cut rates", "cuts rates", "easing", "surplus",
    "boom", "surge", "surges", "soar", "soars", "rally", "rallies", "rebound",
    "record", "beat", "beats", "breakthrough", "peace", "ceasefire", "truce",
    "deal", "agreement", "resolution", "recovery", "growth", "expansion",
    "upgrade", "approval", "approved", "cure", "vaccine", "discovery",
    "innovation", "windfall", "bailout succeeds", "all-time high", "optimism",
)
_BEARISH_WORDS: tuple[str, ...] = (
    "default", "crash", "collapse", "war", "invasion", "pandemic", "virus",
    "recession", "crisis", "hike", "hikes", "hack", "breach", "bankrupt",
    "sanction", "shortage", "ban", "tariff", "panic", "plunge", "slump",
    "downgrade", "contagion", "meltdown", "outage", "attack", "shock",
    "freeze", "halt", "quarantine", "lockdown", "fraud", "scandal",
)

# keyword -> {sector-substring: extra impact}. Both bearish and bullish rules;
# sector-substrings are matched case-insensitively against each sector label.
_HEURISTIC_RULES: list[tuple[tuple[str, ...], dict[str, float]]] = [
    # --- risk-off / sector-specific pain ---
    (("debt", "default", "bank run", "credit", "bond", "sovereign", "liquidity"),
     {"financ": -0.14, "real estate": -0.10, "consumer": -0.04}),
    (("oil shock", "opec", "war", "invasion", "sanction", "pipeline"),
     {"energy": 0.06, "airl": -0.14, "transport": -0.10, "industrial": -0.06}),
    (("pandemic", "virus", "outbreak", "lockdown", "quarantine"),
     {"travel": -0.16, "airl": -0.16, "consumer": -0.10, "health": 0.06, "tech": 0.03}),
    (("chip", "semiconductor", "export ban", "tariff", "trade war"),
     {"tech": -0.12, "semic": -0.14, "automotive": -0.08, "industrial": -0.05}),
    (("hike", "inflation", "central bank tightening"),
     {"tech": -0.09, "real estate": -0.11, "financ": 0.03, "consumer": -0.05}),
    (("cyber", "hack", "breach"),
     {"tech": -0.10, "financ": -0.06}),
    # --- positive catalysts / good news ---
    (("rate cut", "cut rates", "cuts rates", "easing", "stimulus"),
     {"tech": 0.07, "real estate": 0.09, "consumer": 0.05, "financ": 0.03, "industrial": 0.04}),
    (("breakthrough", "innovation", "ai boom", "productivity", "chip demand"),
     {"tech": 0.10, "semic": 0.10, "industrial": 0.04, "automotive": 0.04}),
    (("peace", "ceasefire", "truce", "trade deal", "agreement"),
     {"airl": 0.08, "travel": 0.08, "transport": 0.06, "industrial": 0.05, "energy": -0.03}),
    (("cure", "vaccine", "approval", "breakthrough treatment"),
     {"health": 0.12, "consumer": 0.03}),
    (("record earnings", "record profit", "strong demand", "boom", "surge in sales"),
     {"consumer": 0.06, "tech": 0.06, "financ": 0.05}),
]

_ANALYST_PROMPT = """You are a sell-side macro strategist. A market-moving event has just hit:

"{event}"

Estimate the expected NEAR-TERM price impact on each of these sectors, as a decimal fraction of price (e.g. -0.08 means down 8%, 0.03 means up 3%). Judge the DIRECTION from the event itself: a shock or crisis is risk-off (most sectors negative), but a positive catalyst (stimulus, rate cut, breakthrough, peace deal, record demand) should lift most sectors POSITIVE. Base it on real economic transmission (who is exposed, who benefits, who is a haven).

SECTORS: {sectors}

Reply with ONLY a strict JSON object mapping each sector name exactly as given to its impact fraction, nothing else:
{{"Sector A": -0.08, "Sector B": 0.03}}"""


def _clamp(value: float) -> float:
    return max(-IMPACT_CLAMP, min(IMPACT_CLAMP, value))


def _broad_tilt(text: str) -> float:
    """Signed market-wide tilt from the balance of bullish vs bearish words.

    Positive-leaning headlines lift the whole market; negative-leaning ones
    pull it down; a mixed/neutral headline gets no broad tilt (sector rules
    still apply). This is what lets good news rally stocks in keyless mode.
    """
    bull = sum(1 for w in _BULLISH_WORDS if w in text)
    bear = sum(1 for w in _BEARISH_WORDS if w in text)
    if bull > bear:
        return BROAD_BULLISH_TILT
    if bear > bull:
        return BROAD_BEARISH_TILT
    return 0.0


def heuristic_sector_impact(event: str, sectors: list[str]) -> dict[str, float]:
    """Keyword fallback: signed broad tilt plus sector-specific rules."""
    text = event.lower()
    tilt = _broad_tilt(text)
    impact: dict[str, float] = {s: tilt for s in sectors}
    for keywords, sector_deltas in _HEURISTIC_RULES:
        if any(k in text for k in keywords):
            for s in sectors:
                sl = s.lower()
                for frag, delta in sector_deltas.items():
                    if frag in sl:
                        impact[s] = _clamp(impact[s] + delta)
    return impact


class EventImpactAnalyst:
    """LLM agent that turns a Black Swan headline into a per-sector impact map."""

    def __init__(self, router: GeminiModelRouter) -> None:
        self.router = router

    async def analyze(
        self, http: aiohttp.ClientSession, event: str, sectors: list[str]
    ) -> tuple[dict[str, float], str]:
        """Return (sector_impact, source). source is "llm" or "heuristic".

        Tries the LLM agent first; on any failure falls back to the keyword
        heuristic so the quants are always event-aware.
        """
        try:
            raw = await asyncio.wait_for(
                self.router.prompt_cohort(
                    http, _ANALYST_PROMPT.format(event=event, sectors=json.dumps(sectors))
                ),
                timeout=LLM_TIMEOUT_SECONDS,
            )
            parsed = self._parse(raw, sectors)
            if parsed:
                return parsed, "llm"
        except Exception:
            pass
        return heuristic_sector_impact(event, sectors), "heuristic"

    @staticmethod
    def _parse(raw: str, sectors: list[str]) -> dict[str, float]:
        """Pull the JSON object out of the reply and keep known sectors only."""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {}
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
        known = {s.lower(): s for s in sectors}
        impact: dict[str, float] = {}
        for key, value in obj.items() if isinstance(obj, dict) else []:
            sector = known.get(str(key).lower())
            if sector is None:
                continue
            try:
                impact[sector] = _clamp(float(value))
            except (TypeError, ValueError):
                continue
        return impact
