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
# Broad market hit applied to every sector for any active event (a Black Swan is
# risk-off by default); keyword rules add sector-specific pain on top.
BROAD_HEURISTIC_HIT: float = -0.05

# keyword -> {sector-substring: extra impact}. Sector-substrings are matched
# case-insensitively against each company's sector label.
_HEURISTIC_RULES: list[tuple[tuple[str, ...], dict[str, float]]] = [
    (("debt", "default", "bank", "credit", "bond", "sovereign", "liquidity"),
     {"financ": -0.14, "real estate": -0.10, "consumer": -0.04}),
    (("oil", "energy", "opec", "war", "invasion", "sanction", "pipeline"),
     {"energy": -0.02, "airl": -0.14, "transport": -0.10, "industrial": -0.06}),
    (("pandemic", "virus", "outbreak", "lockdown", "quarantine"),
     {"travel": -0.16, "airl": -0.16, "consumer": -0.10, "health": 0.04, "tech": 0.02}),
    (("chip", "semiconductor", "export ban", "tariff", "trade war"),
     {"tech": -0.12, "semic": -0.14, "automotive": -0.08, "industrial": -0.05}),
    (("rate", "inflation", "hike", "fed", "central bank"),
     {"tech": -0.09, "real estate": -0.11, "financ": 0.03, "consumer": -0.05}),
    (("cyber", "hack", "breach", "outage"),
     {"tech": -0.10, "financ": -0.06}),
]

_ANALYST_PROMPT = """You are a sell-side macro strategist. A Black Swan event has just hit the market:

"{event}"

Estimate the expected NEAR-TERM price impact of this event on each of these market sectors, as a decimal fraction of price (e.g. -0.08 means down 8%, 0.03 means up 3%). Most Black Swans are risk-off, so most sectors should be negative, but some may be defensive or benefit. Base it on real economic transmission (who is exposed, who is a haven).

SECTORS: {sectors}

Reply with ONLY a strict JSON object mapping each sector name exactly as given to its impact fraction, nothing else:
{{"Sector A": -0.08, "Sector B": -0.03}}"""


def _clamp(value: float) -> float:
    return max(-IMPACT_CLAMP, min(IMPACT_CLAMP, value))


def heuristic_sector_impact(event: str, sectors: list[str]) -> dict[str, float]:
    """Keyword fallback: broad risk-off hit plus sector-specific rules."""
    text = event.lower()
    impact: dict[str, float] = {s: BROAD_HEURISTIC_HIT for s in sectors}
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
