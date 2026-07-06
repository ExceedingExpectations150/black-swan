"""Live-wire test for CorporatePRDesk and MacroAnalyst parsing.

Spins up a real local aiohttp server that answers like Gemma 4 does in the
wild — reasoning preamble first, JSON afterwards — and asserts the tolerant
parsers extract, validate, clamp, and drop exactly as specified. The real
GeminiModelRouter request path runs end to end against the fake endpoint.

Run: python tests/test_social_agents.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

os.environ.setdefault("GEMINI_API_KEY_PRIMARY", "test-key-primary")
os.environ.setdefault("GEMINI_API_KEY_BACKUP", "test-key-backup")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import web

import ai_clients
from ai_clients import GeminiModelRouter
from social_agents import CompanyPRContext, CorporatePRDesk, MacroAnalyst, PostDraft

PR_REPLY: str = (
    "Okay, let me think through each company's situation. Apple is up on strong "
    "earnings so its PR desk will lean celebratory [confidence: high]. Tesla is "
    "down hard and will get defensive. JPMorgan will deflect toward stability.\n\n"
    "Here is the JSON array:\n"
    "[\n"
    '  {"ticker": "AAPL", "content": "Another record quarter for the Apple community. '
    'Grateful to our customers worldwide.", "sentiment": 0.6, "stance": "hype"},\n'
    '  {"ticker": "TSLA", "content": "Volatility is noise. Our production lines and '
    'mission have never been stronger.", "sentiment": 1.7, "stance": "defensive"},\n'
    '  {"ticker": "ZZZZ", "content": "We remain committed to excellence.", '
    '"sentiment": 0.1, "stance": "neutral"},\n'
    '  {"ticker": "JPM", "content": "Markets move; our fortress balance sheet does not. '
    'Client assets remain fully protected.", "sentiment": -0.2, "stance": "confident"}\n'
    "]"
)

ANALYST_REPLY: str = (
    "Let me reason about the digest first. Stress is elevated, financials are "
    "leading the decline, and two bankruptcies suggest contagion risk.\n\n"
    "Systemic stress climbed sharply this session as financials led broad sector "
    "declines. Two bankruptcies confirmed that margin pressure is now translating "
    "into real failures. Liquidity remains the key variable to watch, and until "
    "funding markets stabilize, rallies should be treated as tactical rather than "
    "durable."
)

EXPECTED_DRAFTS: list[PostDraft] = [
    PostDraft(
        ticker="AAPL",
        content=(
            "Another record quarter for the Apple community. "
            "Grateful to our customers worldwide."
        ),
        sentiment=0.6,
        stance="hype",
    ),
    PostDraft(
        ticker="TSLA",
        content=(
            "Volatility is noise. Our production lines and "
            "mission have never been stronger."
        ),
        sentiment=1.0,  # clamped from 1.7
        stance="defensive",
    ),
    PostDraft(
        ticker="JPM",
        content=(
            "Markets move; our fortress balance sheet does not. "
            "Client assets remain fully protected."
        ),
        sentiment=-0.2,
        stance="neutral",  # "confident" is not a valid stance
    ),
]

EXPECTED_PARAGRAPH: str = (
    "Systemic stress climbed sharply this session as financials led broad sector "
    "declines. Two bankruptcies confirmed that margin pressure is now translating "
    "into real failures. Liquidity remains the key variable to watch, and until "
    "funding markets stabilize, rallies should be treated as tactical rather than "
    "durable."
)

REQUEST_COUNT: list[int] = [0]


async def fake_generate_content(request: web.Request) -> web.Response:
    REQUEST_COUNT[0] += 1
    payload: dict[str, Any] = await request.json()
    prompt: str = payload["contents"][0]["parts"][0]["text"]
    reply = ANALYST_REPLY if "financial market analyst" in prompt else PR_REPLY
    body: dict[str, Any] = {
        "candidates": [{"content": {"parts": [{"text": reply}]}}]
    }
    return web.json_response(body)


async def main() -> None:
    app = web.Application()
    app.router.add_post("/v1beta/models/{model}:generateContent", fake_generate_content)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    ai_clients.GEMINI_ENDPOINT_TEMPLATE = (
        f"http://127.0.0.1:{port}/v1beta/models/{{model}}:generateContent"
    )

    router = GeminiModelRouter()
    desk = CorporatePRDesk(router)
    analyst = MacroAnalyst(router)

    companies = [
        CompanyPRContext(
            ticker="AAPL", name="Apple Inc.", sector="Technology",
            price=232.15, change_pct=2.1, sentiment=0.4,
            competitor_note="Samsung shipping a rival headset next month.",
        ),
        CompanyPRContext(
            ticker="TSLA", name="Tesla Inc.", sector="Automotive",
            price=241.30, change_pct=-6.4, sentiment=-0.5,
            competitor_note="BYD undercutting on price across Europe.",
        ),
        CompanyPRContext(
            ticker="JPM", name="JPMorgan Chase", sector="Financials",
            price=198.70, change_pct=-1.0, sentiment=-0.1,
            competitor_note="Goldman Sachs flagged rising credit losses.",
        ),
    ]

    async with aiohttp.ClientSession() as session:
        drafts = await desk.generate_posts(
            session, companies, "Global banking system collapse triggers worldwide margin calls"
        )
        paragraph = await analyst.narrate(
            session, "stress=0.82; sectors: financials -8.4%, tech -2.1%; "
            "movers: JPM -12%, AAPL +2%; bankruptcies: 2"
        )

    assert drafts == EXPECTED_DRAFTS, f"unexpected drafts:\n{drafts!r}"
    assert paragraph == EXPECTED_PARAGRAPH, f"unexpected paragraph:\n{paragraph!r}"
    assert REQUEST_COUNT[0] == 2, f"expected 2 LLM calls (one per author), saw {REQUEST_COUNT[0]}"

    await runner.cleanup()
    print(
        "PASS: batched PR parse (preamble tolerated, sentiment 1.7 clamped to 1.0, "
        "unknown ticker ZZZZ dropped, invalid stance -> neutral), "
        "analyst last-paragraph extraction, exactly 2 router calls"
    )


if __name__ == "__main__":
    asyncio.run(main())
