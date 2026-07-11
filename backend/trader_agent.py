"""Senior trader chat agent for ChaosNet "Black Swan".

Answers user questions about likely rises/falls through the shared
GeminiModelRouter, grounded in a quantitative MarketBrief computed here from
price histories, company state, and TimesFM forecasts. When the router fails
or times out, the agent degrades to a deterministic statistical brief built
from the same real numbers — factual, never invented.

Stateless, no DB access: the caller supplies histories/companies/forecasts
and persists whatever it wants.
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass
from typing import Final

import aiohttp

from ai_clients import GeminiModelRouter
from social_agents import _extract_last_paragraph

# Points of price history used for the momentum read.
MOMENTUM_WINDOW: Final[int] = 5

# Minimum price points before volatility is meaningful (matches economy.py).
MIN_VOLATILITY_POINTS: Final[int] = 3

# Entries in each movers list of the brief.
MOVERS_COUNT: Final[int] = 3

# Conversation turns injected into the prompt.
CONTEXT_MESSAGES: Final[int] = 8

# Hard ceiling on one LLM answer before degrading to the offline brief.
ANSWER_TIMEOUT_SECONDS: Final[float] = 20.0

OFFLINE_NOTE: Final[str] = "Desk model offline — raw statistical read:"


@dataclass(frozen=True)
class ChatMessage:
    """One turn of the trader chat ("user" or "assistant")."""

    role: str
    content: str


@dataclass(frozen=True)
class TickerStat:
    """Quantitative read on one ticker; forecast fields are None without TimesFM."""

    ticker: str
    last_price: float
    change_pct: float
    momentum_pct: float
    volatility: float
    forecast: float | None
    forecast_delta_pct: float | None


@dataclass(frozen=True)
class MarketBrief:
    """Everything the trader agent is allowed to cite."""

    stats: tuple[TickerStat, ...]
    gainers: tuple[TickerStat, ...]
    losers: tuple[TickerStat, ...]
    stress_index: float
    bankrupt_count: int


def compute_ticker_stats(
    histories: dict[str, list[float]],
    companies: dict[str, dict],
    forecasts: dict[str, float],
) -> list[TickerStat]:
    """Build per-ticker stats from real market state.

    momentum_pct: percent change across the last MOMENTUM_WINDOW price points
    (0.0 with fewer than 2 points). volatility: population stdev of
    tick-over-tick returns (0.0 with fewer than MIN_VOLATILITY_POINTS points).
    change_pct is the divergence of current_price from anchor_price.
    """
    stats: list[TickerStat] = []
    for ticker, company in companies.items():
        last_price = float(company["current_price"])
        anchor_price = float(company["anchor_price"])
        change_pct = (
            (last_price - anchor_price) / anchor_price * 100 if anchor_price else 0.0
        )

        history = histories.get(ticker, [])
        window = history[-MOMENTUM_WINDOW:]
        if len(window) >= 2 and window[0]:
            momentum_pct = (window[-1] - window[0]) / window[0] * 100
        else:
            momentum_pct = 0.0

        if len(history) >= MIN_VOLATILITY_POINTS:
            returns = [
                (after - before) / before
                for before, after in zip(history, history[1:])
                if before
            ]
            volatility = statistics.pstdev(returns) if returns else 0.0
        else:
            volatility = 0.0

        forecast = forecasts.get(ticker)
        if forecast is not None and last_price:
            forecast_delta_pct: float | None = (forecast - last_price) / last_price * 100
        else:
            forecast_delta_pct = None

        stats.append(
            TickerStat(
                ticker=ticker,
                last_price=last_price,
                change_pct=change_pct,
                momentum_pct=momentum_pct,
                volatility=volatility,
                forecast=forecast,
                forecast_delta_pct=forecast_delta_pct,
            )
        )
    return stats


def build_market_brief(
    stats: list[TickerStat], stress_index: float, bankrupt_count: int
) -> MarketBrief:
    """Rank momentum both ways and freeze the citable market picture."""
    by_momentum = sorted(stats, key=lambda s: s.momentum_pct, reverse=True)
    return MarketBrief(
        stats=tuple(stats),
        gainers=tuple(by_momentum[:MOVERS_COUNT]),
        losers=tuple(reversed(by_momentum[-MOVERS_COUNT:])) if stats else (),
        stress_index=stress_index,
        bankrupt_count=bankrupt_count,
    )


def _brief_lines(brief: MarketBrief) -> list[str]:
    """The brief's numbers as plain lines — shared by prompt and offline paths."""
    lines: list[str] = [
        f"System stress index: {brief.stress_index:.2f} | "
        f"bankruptcies: {brief.bankrupt_count}",
        "Momentum leaders:",
    ]
    for s in brief.gainers:
        lines.append(
            f"- {s.ticker}: last ${s.last_price:.2f}, momentum {s.momentum_pct:+.2f}%, "
            f"vs anchor {s.change_pct:+.2f}%"
        )
    lines.append("Momentum laggards:")
    for s in brief.losers:
        lines.append(
            f"- {s.ticker}: last ${s.last_price:.2f}, momentum {s.momentum_pct:+.2f}%, "
            f"vs anchor {s.change_pct:+.2f}%"
        )
    most_volatile = sorted(brief.stats, key=lambda s: s.volatility, reverse=True)
    lines.append("Most volatile:")
    for s in most_volatile[:MOVERS_COUNT]:
        lines.append(f"- {s.ticker}: volatility {s.volatility:.4f}")
    forecasted = [s for s in brief.stats if s.forecast_delta_pct is not None]
    if forecasted:
        lines.append("Model forecasts (TimesFM, next tick vs last price):")
        for s in forecasted:
            lines.append(
                f"- {s.ticker}: forecast ${s.forecast:.2f} ({s.forecast_delta_pct:+.2f}%)"
            )
    return lines


def render_offline_brief(brief: MarketBrief) -> str:
    """Deterministic statistical brief — the LLM-down path. Real numbers only."""
    return "\n".join([OFFLINE_NOTE, *_brief_lines(brief)])


class SeniorTraderAgent:
    """Senior desk trader persona answering chat questions over a MarketBrief."""

    def __init__(self, router: GeminiModelRouter) -> None:
        self.router = router

    def _build_prompt(self, messages: list[ChatMessage], brief: MarketBrief) -> str:
        lines: list[str] = [
            "You are a senior desk trader at an institutional trading desk, "
            "answering a colleague's questions about this simulated market.",
            "",
            "Current desk brief — the ONLY numbers that exist:",
            *_brief_lines(brief),
            "",
            "Conversation so far (answer the LAST user message in context):",
        ]
        for message in messages[-CONTEXT_MESSAGES:]:
            speaker = "User" if message.role == "user" else "Trader"
            lines.append(f"{speaker}: {message.content}")
        lines += [
            "",
            "Give a measured, probabilistic read on what is likely to rise or "
            "fall and why, in a calm professional trader voice. Cite ONLY numbers "
            "present in the desk brief above — never invent a figure, ticker, or "
            "statistic. Frame views as probabilities, not certainties. Output "
            "plain text as a single paragraph: no markdown, no headings, no "
            "bullet points, no preamble.",
        ]
        return "\n".join(lines)

    async def answer(
        self,
        http: aiohttp.ClientSession,
        messages: list[ChatMessage],
        brief: MarketBrief,
    ) -> tuple[str, str]:
        """Return (reply, source): source is "llm", or "offline" on any failure."""
        try:
            raw: str = await asyncio.wait_for(
                self.router.prompt_cohort(http, self._build_prompt(messages, brief)),
                timeout=ANSWER_TIMEOUT_SECONDS,
            )
        except Exception:
            return render_offline_brief(brief), "offline"
        reply = _extract_last_paragraph(raw)
        if not reply:
            return render_offline_brief(brief), "offline"
        return reply, "llm"
