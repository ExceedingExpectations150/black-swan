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


# --------------------------------------------------------------------------
# DailyReporter — end-of-day wire report from a quantitative digest
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyDigest:
    """Quantitative end-of-day rollup; the caller (tick engine) builds it."""

    day_index: int
    tick_id: int
    index_change_pct: float
    top_gainer_ticker: str
    top_gainer_pct: float
    top_loser_ticker: str
    top_loser_pct: float
    total_volume: int
    stress_index: float
    bankrupt_count: int
    headline: str


def render_factual(digest: DailyDigest) -> str:
    """Automated wire report — the LLM-down path, written like journalism.

    Every figure comes from the digest's real numbers (never invented);
    lede structure and vocabulary vary deterministically by day so
    consecutive reports read as reporting, not a filled-in form. This is
    the same approach real wire services use for automated market briefs.
    """
    chg = digest.index_change_pct
    mag = abs(chg)
    day = digest.day_index
    variant = day % 3

    if mag < 0.15:
        severity = "drifted"
    elif mag < 1.0:
        severity = "slipped" if chg < 0 else "edged higher"
    elif mag < 3.0:
        severity = "fell sharply" if chg < 0 else "rallied"
    else:
        severity = "was routed" if chg < 0 else "surged"

    if variant == 0:
        lede = (
            f"Markets {severity} on Day {day}, with the composite index "
            f"closing {chg:+.2f}%."
        )
    elif variant == 1:
        lede = (
            f"The composite index {severity} {mag:.2f}% by the Day {day} "
            f"close{' as sellers kept control of the tape' if chg < -1.0 else ''}."
        )
    else:
        lede = (
            f"Day {day} closed with the index at {chg:+.2f}% — a session that "
            f"{'extended the slide' if chg < 0 else 'clawed back ground'}."
        )

    spread = digest.top_gainer_pct - digest.top_loser_pct
    if variant == 0:
        movers = (
            f"{digest.top_gainer_ticker} held up best at {digest.top_gainer_pct:+.2f}% "
            f"while {digest.top_loser_ticker} bore the brunt at "
            f"{digest.top_loser_pct:+.2f}%, a {spread:.2f}-point dispersion across the tape."
        )
    else:
        movers = (
            f"At the extremes, {digest.top_loser_ticker} lost "
            f"{abs(digest.top_loser_pct):.2f}% against {digest.top_gainer_ticker}'s "
            f"{digest.top_gainer_pct:+.2f}% — dispersion of {spread:.2f} points."
        )

    if digest.stress_index >= 0.6:
        stress_read = "systemic stress gauges running hot"
    elif digest.stress_index >= 0.3:
        stress_read = "stress gauges elevated but contained"
    else:
        stress_read = "stress gauges subdued"
    tape = (
        f"Turnover reached {digest.total_volume:,} shares with {stress_read} "
        f"({digest.stress_index:.2f})"
        + (
            f" and {digest.bankrupt_count} names now in bankruptcy."
            if digest.bankrupt_count
            else "."
        )
    )

    sentences = [lede, movers, tape]
    if digest.headline:
        sentences.append(
            f'Desks continue to trade around the standing shock: "{digest.headline}".'
        )
    return " ".join(sentences)


def render_factual_company_post(
    ticker: str,
    name: str,
    sector: str,
    change_pct: float,
    price: float,
    tick_id: int,
    headline: str,
) -> str:
    """One wire-style company snippet from real numbers (news-desk fallback).

    Varies phrasing deterministically by (ticker, tick) so the feed reads
    like coverage rather than one repeated sentence. Data-only; no invention.
    """
    seed = (sum(ord(c) for c in ticker) + tick_id) % 4
    direction = "down" if change_pct < 0 else "up"
    mag = abs(change_pct)
    if seed == 0:
        body = (
            f"{name} ({ticker}) trades {direction} {mag:.2f}% at ${price:,.2f} "
            f"as {sector} names react to the tape."
        )
    elif seed == 1:
        body = (
            f"{ticker} marks ${price:,.2f}, {change_pct:+.2f}% on the session — "
            f"one of the more active {sector} prints."
        )
    elif seed == 2:
        body = (
            f"Order flow keeps {name} {direction} {mag:.2f}% at ${price:,.2f}; "
            f"{sector} desks watching the level."
        )
    else:
        body = (
            f"{ticker} changes hands at ${price:,.2f} ({change_pct:+.2f}%), "
            f"tracking the broader {sector} move."
        )
    if headline and mag >= 2.0:
        body += f' Traders tie the move to the standing shock: "{headline[:60]}".'
    return body


class DailyReporter:
    """Financial wire-service reporter writing the end-of-day market report.

    Raises RuntimeError only when the router does; the caller falls back to
    render_factual(digest) — this class never synthesizes a report itself.
    """

    def __init__(self, router: GeminiModelRouter) -> None:
        self.router = router

    def _build_prompt(self, digest: DailyDigest) -> str:
        return (
            "You are a financial wire-service reporter (Reuters/Bloomberg style) "
            "writing the end-of-day market report for a simulated exchange.\n\n"
            "Today's verified closing numbers — the ONLY figures that exist:\n"
            f"- Trading day: {digest.day_index} (tick {digest.tick_id})\n"
            f"- Market index change: {digest.index_change_pct:+.2f}%\n"
            f"- Top gainer: {digest.top_gainer_ticker} ({digest.top_gainer_pct:+.2f}%)\n"
            f"- Top loser: {digest.top_loser_ticker} ({digest.top_loser_pct:+.2f}%)\n"
            f"- Total volume: {digest.total_volume:,} shares\n"
            f"- System stress index: {digest.stress_index:.2f}\n"
            f"- Bankruptcies on record: {digest.bankrupt_count}\n"
            f"- Driving headline: {digest.headline}\n\n"
            "Write a 3-5 sentence end-of-day market report grounded ONLY in the "
            "numbers above. Do NOT invent any figure, ticker, or statistic that "
            "is not listed. Output plain text as a single paragraph: no markdown, "
            "no headings, no bullet points, no preamble."
        )

    async def report(self, http: aiohttp.ClientSession, digest: DailyDigest) -> str:
        """One router call; returns the report paragraph (may be empty)."""
        raw: str = await self.router.prompt_cohort(http, self._build_prompt(digest))
        return _extract_last_paragraph(raw)
