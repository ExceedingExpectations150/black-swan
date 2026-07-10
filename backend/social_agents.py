"""LLM-driven social-feed authors for ChaosNet "Black Swan".

Two authors, both speaking through the shared GeminiModelRouter (never a
second client — it already owns 429 key rotation, model fallback, backoff):
- CorporatePRDesk: every company's PR account writes one post per tick,
  batched into a SINGLE router call (free-tier quota is 5 req/min).
- MacroAnalyst: one paragraph of analyst commentary from a quantitative
  economy digest.

Parsing is defensive (Gemma 4 narrates reasoning before its JSON) but never
synthesizes content: an unusable reply yields an empty result, not fakes.
No DB access in this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Final

import aiohttp

from ai_clients import GeminiModelRouter

VALID_STANCES: Final[frozenset[str]] = frozenset({"hype", "defensive", "deflect", "neutral"})
MAX_POST_CHARS: Final[int] = 280


@dataclass(frozen=True)
class CompanyPRContext:
    """Everything a company's PR account knows about its own situation."""

    ticker: str
    name: str
    sector: str
    price: float
    change_pct: float
    sentiment: float
    competitor_note: str


@dataclass(frozen=True)
class PostDraft:
    """One validated social post ready for persistence by the caller."""

    ticker: str
    content: str
    sentiment: float
    stance: str


def _extract_json_array(raw: str) -> list[Any] | None:
    """Return the LAST JSON array found anywhere in the reply, else None.

    Tolerates reasoning preamble and markdown fences by attempting a real
    JSON decode at every '[' position instead of trusting a bracket regex.
    The last array wins because Gemma 4's chain-of-thought often contains
    draft/example arrays before the final answer (observed live 2026-07-06).
    """
    decoder = json.JSONDecoder()
    result: list[Any] | None = None
    for match in re.finditer(r"\[", raw):
        try:
            value, _ = decoder.raw_decode(raw, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            result = value
    return result


def _extract_last_paragraph(raw: str) -> str:
    """Strip markdown fences and return the last non-empty paragraph."""
    cleaned = re.sub(r"```[a-zA-Z0-9_-]*", "", raw)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned)]
    non_empty = [p for p in paragraphs if p]
    return non_empty[-1] if non_empty else ""


class NewsPublisher:
    """Writes one credible financial news blurb per company via a single batched call."""

    def __init__(self, router: GeminiModelRouter) -> None:
        self.router = router

    def _build_prompt(self, companies: list[CompanyPRContext], macro_headline: str) -> str:
        lines: list[str] = [
            "You are simulating a prestigious financial news desk (e.g., Reuters, Bloomberg) reporting live market updates.",
            "",
            f"Today's macro headline: {macro_headline}",
            "",
            "Companies:",
        ]
        for c in companies:
            lines.append(
                f"- {c.ticker} | {c.name} | sector: {c.sector} | "
                f"price: ${c.price:.2f} | change today: {c.change_pct:+.2f}% | "
                f"current public sentiment: {c.sentiment:+.2f} | "
                f"competitor note: {c.competitor_note}"
            )
        lines += [
            "",
            "For EACH company above, your news desk writes exactly ONE short breaking news snippet "
            "(under 280 characters) reporting on its situation and the macro headline in a credible, "
            "objective financial journalism voice. No corporate hype.",
            "",
            "Output ONLY a strict JSON array, no markdown fences, no commentary, "
            "with one object per company, exactly like:",
            '[{"ticker":"AAPL","content":"...","sentiment":0.3,"stance":"hype"}]',
            'where "sentiment" is a float in [-1,1] for the post\'s tone and '
            '"stance" is exactly one of "hype", "defensive", "deflect", "neutral".',
            "Do NOT think out loud, do NOT draft or revise, do NOT explain your "
            "choices. The very first character of your reply must be '[' and the "
            "last must be ']'.",
        ]
        return "\n".join(lines)

    async def generate_posts(
        self,
        session: aiohttp.ClientSession,
        companies: list[CompanyPRContext],
        macro_headline: str,
    ) -> list[PostDraft]:
        """One router call for ALL companies; returns only validated drafts."""
        if not companies:
            return []
        raw: str = await self.router.prompt_cohort(
            session, self._build_prompt(companies, macro_headline)
        )
        items = _extract_json_array(raw)
        if items is None:
            return []

        known_tickers: set[str] = {c.ticker for c in companies}
        drafts: list[PostDraft] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                ticker = str(item["ticker"]).strip().upper()
                content = str(item["content"]).strip()
                sentiment = float(item["sentiment"])
            except (KeyError, TypeError, ValueError):
                continue
            # Alphanumeric requirement rejects the "..." placeholder content
            # Gemma 4 puts in mid-reasoning scaffold arrays (observed live).
            if ticker not in known_tickers or not re.search(r"[A-Za-z0-9]", content):
                continue
            stance = str(item.get("stance", "")).strip().lower()
            if stance not in VALID_STANCES:
                stance = "neutral"
            drafts.append(
                PostDraft(
                    ticker=ticker,
                    content=content[:MAX_POST_CHARS],
                    sentiment=max(-1.0, min(1.0, sentiment)),
                    stance=stance,
                )
            )
        return drafts


class MacroAnalyst:
    """Turns a quantitative economy digest into one paragraph of commentary."""

    def __init__(self, router: GeminiModelRouter) -> None:
        self.router = router

    async def narrate(self, session: aiohttp.ClientSession, economy_digest: str) -> str:
        """One router call; returns a plain-text paragraph (may be empty)."""
        prompt = (
            "You are a veteran financial market analyst writing a live market note.\n"
            "Here is the current quantitative digest of the simulated economy "
            "(system stress, sector moves, biggest movers, bankruptcies):\n\n"
            f"{economy_digest}\n\n"
            "Write ONE plain-text paragraph (3-5 sentences) of macro commentary in "
            "a measured analyst voice. Output only the paragraph itself: no "
            "markdown, no headings, no bullet points, no preamble."
        )
        raw: str = await self.router.prompt_cohort(session, prompt)
        return _extract_last_paragraph(raw)
