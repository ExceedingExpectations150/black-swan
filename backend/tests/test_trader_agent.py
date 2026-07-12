"""Trader agent: stat math, brief ranking, offline brief, and LLM/offline routing.

Momentum and volatility are checked against a hand-crafted price series with
explicitly listed tick-over-tick returns. The router is a FakeRouter injected
into SeniorTraderAgent — never a monkeypatched network.

Run: python tests/test_trader_agent.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trader_agent import (
    ChatMessage,
    SeniorTraderAgent,
    TickerStat,
    build_market_brief,
    compute_ticker_stats,
    render_offline_brief,
)

# 6 prices -> 5 tick-over-tick returns, listed by hand below.
HISTORY: list[float] = [100.0, 102.0, 101.0, 105.0, 110.0, 108.0]
HAND_RETURNS: list[float] = [
    2.0 / 100.0,    # 100 -> 102
    -1.0 / 102.0,   # 102 -> 101
    4.0 / 101.0,    # 101 -> 105
    5.0 / 105.0,    # 105 -> 110
    -2.0 / 110.0,   # 110 -> 108
]


class FakeRouter:
    """Injected stand-in for GeminiModelRouter — canned reply or a hard raise."""

    def __init__(self, reply: str | None = None) -> None:
        self.reply = reply

    async def prompt_cohort(self, session, prompt_text: str) -> str:
        if self.reply is None:
            raise RuntimeError("Gemini API error HTTP 429 (simulated)")
        return self.reply


def _approx(actual: float, expected: float, tol: float = 1e-9) -> bool:
    return abs(actual - expected) <= tol


def _stat(ticker: str, momentum_pct: float, volatility: float = 0.01) -> TickerStat:
    return TickerStat(
        ticker=ticker,
        last_price=100.0,
        change_pct=momentum_pct / 2,
        momentum_pct=momentum_pct,
        volatility=volatility,
        forecast=None,
        forecast_delta_pct=None,
    )


def test_compute_ticker_stats_math():
    stats = compute_ticker_stats(
        histories={"AAPL": HISTORY},
        companies={"AAPL": {"current_price": 108.0, "anchor_price": 100.0}},
        forecasts={"AAPL": 113.4},
    )
    assert len(stats) == 1
    s = stats[0]
    assert s.ticker == "AAPL"
    assert s.last_price == 108.0
    assert _approx(s.change_pct, 8.0), s.change_pct
    # Momentum over the last 5 points: [102, 101, 105, 110, 108].
    assert _approx(s.momentum_pct, (108.0 - 102.0) / 102.0 * 100), s.momentum_pct
    assert _approx(s.volatility, statistics.pstdev(HAND_RETURNS)), s.volatility
    assert s.forecast == 113.4
    # (113.4 - 108) / 108 * 100 = 5.0 exactly.
    assert _approx(s.forecast_delta_pct, 5.0), s.forecast_delta_pct


def test_compute_ticker_stats_short_histories_and_no_forecast():
    stats = compute_ticker_stats(
        histories={"ONE": [50.0], "TWO": [50.0, 55.0]},
        companies={
            "ONE": {"current_price": 50.0, "anchor_price": 50.0},
            "TWO": {"current_price": 55.0, "anchor_price": 50.0},
        },
        forecasts={},
    )
    by_ticker = {s.ticker: s for s in stats}
    assert by_ticker["ONE"].momentum_pct == 0.0  # <2 points
    assert by_ticker["ONE"].volatility == 0.0    # <3 points
    assert _approx(by_ticker["TWO"].momentum_pct, 10.0)
    assert by_ticker["TWO"].volatility == 0.0    # <3 points
    assert by_ticker["ONE"].forecast is None
    assert by_ticker["ONE"].forecast_delta_pct is None


def test_build_market_brief_top3_each_way():
    stats = [
        _stat("A", 5.0), _stat("B", -2.0), _stat("C", 9.0), _stat("D", 0.0),
        _stat("E", -7.0), _stat("F", 3.0), _stat("G", -1.0),
    ]
    brief = build_market_brief(stats, stress_index=0.42, bankrupt_count=1)
    assert [s.ticker for s in brief.gainers] == ["C", "A", "F"]
    assert [s.ticker for s in brief.losers] == ["E", "B", "G"]  # worst first
    assert brief.stress_index == 0.42
    assert brief.bankrupt_count == 1
    assert len(brief.stats) == 7


def test_render_offline_brief_focus_is_factual():
    # When the user names a ticker, the desk leads with it and cites ONLY its
    # real figures (price, anchor divergence, momentum, TimesFM forecast).
    stats = compute_ticker_stats(
        histories={"AAPL": HISTORY},
        companies={"AAPL": {"current_price": 108.0, "anchor_price": 100.0}},
        forecasts={"AAPL": 113.4},
    )
    brief = build_market_brief(stats, stress_index=0.42, bankrupt_count=1)
    text = render_offline_brief(brief, [ChatMessage(role="user", content="read on AAPL?")])
    assert "AAPL" in text, text
    assert "$108.00" in text, text            # last price
    assert "+8.00%" in text, text             # vs anchor
    assert "+5.88%" in text, text             # momentum over last 5 points
    assert "$113.40" in text and "+5.00%" in text, text   # TimesFM forecast
    assert "0.42" in text, text               # stress index
    assert "leans higher" in text, text       # positive momentum + forecast


def test_render_offline_brief_market_read_is_conversational():
    # With no named ticker, it gives a market-wide read — prose, not a dump.
    stats = compute_ticker_stats(
        histories={"AAPL": HISTORY},
        companies={"AAPL": {"current_price": 108.0, "anchor_price": 100.0}},
        forecasts={"AAPL": 113.4},
    )
    brief = build_market_brief(stats, stress_index=0.42, bankrupt_count=1)
    text = render_offline_brief(brief)
    assert "AAPL" in text and "+5.88%" in text, text
    assert "0.42" in text, text
    assert "\n" not in text, "should read as prose, not a line-by-line dump"


def test_answer_offline_on_router_failure():
    stats = compute_ticker_stats(
        histories={"AAPL": HISTORY},
        companies={"AAPL": {"current_price": 108.0, "anchor_price": 100.0}},
        forecasts={},
    )
    brief = build_market_brief(stats, stress_index=0.42, bankrupt_count=1)
    agent = SeniorTraderAgent(FakeRouter(reply=None))
    messages = [ChatMessage(role="user", content="What looks likely to rise?")]
    reply, source = asyncio.run(agent.answer(None, messages, brief))
    assert source == "offline", source
    assert reply == render_offline_brief(brief, messages), reply


def test_answer_llm_on_working_router():
    stats = compute_ticker_stats(
        histories={"AAPL": HISTORY},
        companies={"AAPL": {"current_price": 108.0, "anchor_price": 100.0}},
        forecasts={},
    )
    brief = build_market_brief(stats, stress_index=0.42, bankrupt_count=1)
    llm_reply = (
        "Reviewing the desk brief before answering.\n\n"
        "With AAPL momentum at +5.88% and stress contained at 0.42, odds "
        "modestly favor continued upside, though I would size accordingly."
    )
    agent = SeniorTraderAgent(FakeRouter(llm_reply))
    messages = [
        ChatMessage(role="user", content="How is AAPL positioned?"),
        ChatMessage(role="assistant", content="Momentum is positive."),
        ChatMessage(role="user", content="So what rises from here?"),
    ]
    reply, source = asyncio.run(agent.answer(None, messages, brief))
    assert source == "llm", source
    assert reply == (
        "With AAPL momentum at +5.88% and stress contained at 0.42, odds "
        "modestly favor continued upside, though I would size accordingly."
    ), reply


if __name__ == "__main__":
    test_compute_ticker_stats_math()
    test_compute_ticker_stats_short_histories_and_no_forecast()
    test_build_market_brief_top3_each_way()
    test_render_offline_brief_focus_is_factual()
    test_render_offline_brief_market_read_is_conversational()
    test_answer_offline_on_router_failure()
    test_answer_llm_on_working_router()
    print("ALL TRADER AGENT TESTS PASSED")
