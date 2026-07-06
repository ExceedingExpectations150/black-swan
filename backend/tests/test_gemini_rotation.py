"""Live-wire test for GeminiModelRouter's 429 rotation ladder.

Spins up a real local aiohttp server that returns 429 twice, then 200, and
asserts the router rotated keys, fell back from gemma-2-27b-it to
gemini-1.5-flash, and returned the final text. No network, no mocks of the
router itself — the actual request/retry code path runs end to end.

Run: python tests/test_gemini_rotation.py
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
from ai_clients import FALLBACK_MODEL, PRIMARY_MODEL, GeminiModelRouter

SEEN: list[dict[str, str]] = []


async def fake_generate_content(request: web.Request) -> web.Response:
    SEEN.append(
        {
            "model": request.match_info["model"],
            "key": request.headers.get("x-goog-api-key", ""),
        }
    )
    if len(SEEN) <= 2:
        return web.Response(status=429, text='{"error": {"code": 429}}')
    body: dict[str, Any] = {
        "candidates": [{"content": {"parts": [{"text": "HOLD: uncertainty too high"}]}}]
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
    ai_clients.BACKOFF_BASE_SECONDS = 0.01

    router = GeminiModelRouter()
    assert router.active_model == PRIMARY_MODEL

    async with aiohttp.ClientSession() as session:
        text = await router.prompt_cohort(session, "Market crashed 30%. BUY, SELL or HOLD?")

    assert text == "HOLD: uncertainty too high", f"unexpected text: {text!r}"
    assert len(SEEN) == 3, f"expected 3 requests, saw {len(SEEN)}"
    assert SEEN[0] == {"model": PRIMARY_MODEL, "key": "test-key-primary"}
    assert SEEN[1] == {"model": FALLBACK_MODEL, "key": "test-key-backup"}
    assert SEEN[2] == {"model": FALLBACK_MODEL, "key": "test-key-primary"}

    # Non-429 errors must raise hard, immediately.
    SEEN.clear()

    async def fail_500(request: web.Request) -> web.Response:
        SEEN.append({})
        return web.Response(status=500, text="boom")

    app2 = web.Application()
    app2.router.add_post("/v1beta/models/{model}:generateContent", fail_500)
    runner2 = web.AppRunner(app2)
    await runner2.setup()
    site2 = web.TCPSite(runner2, "127.0.0.1", 0)
    await site2.start()
    port2 = runner2.addresses[0][1]
    ai_clients.GEMINI_ENDPOINT_TEMPLATE = (
        f"http://127.0.0.1:{port2}/v1beta/models/{{model}}:generateContent"
    )

    router2 = GeminiModelRouter()
    try:
        async with aiohttp.ClientSession() as session:
            await router2.prompt_cohort(session, "ping")
    except RuntimeError as exc:
        assert "HTTP 500" in str(exc), str(exc)
        assert len(SEEN) == 1, "500 must not be retried"
    else:
        raise AssertionError("HTTP 500 did not raise")

    await runner.cleanup()
    await runner2.cleanup()
    print("PASS: 429 rotation (key+model+backoff), success parse, hard-fail on 500")


if __name__ == "__main__":
    asyncio.run(main())
