"""DailyReporter: factual template correctness and router-path parsing.

Digest math is NOT tested here — the integrator owns digest building. What
matters: render_factual interpolates the digest's exact numbers with the
right direction word, and DailyReporter parses the router's last paragraph
while a raising router lets the caller fall back to the factual report.

Run: python tests/test_daily_reporter.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_agents import DailyDigest, DailyReporter, render_factual

REPORTER_REPLY: str = (
    "Let me review the closing numbers first. The index gain looks broad-based "
    "and volume is healthy.\n\n"
    "Markets closed higher on day 3 as the index advanced 1.25 percent on "
    "volume of 48,200 shares. NVDA paced the gainers while JPM lagged, and "
    "with the stress index at 0.31 the tape looked orderly into the close."
)

EXPECTED_PARAGRAPH: str = (
    "Markets closed higher on day 3 as the index advanced 1.25 percent on "
    "volume of 48,200 shares. NVDA paced the gainers while JPM lagged, and "
    "with the stress index at 0.31 the tape looked orderly into the close."
)


class FakeRouter:
    """Injected stand-in for GeminiModelRouter — canned reply or a hard raise."""

    def __init__(self, reply: str | None = None) -> None:
        self.reply = reply

    async def prompt_cohort(self, session, prompt_text: str) -> str:
        if self.reply is None:
            raise RuntimeError("Gemini API error HTTP 429 (simulated)")
        return self.reply


def _digest(index_change_pct: float) -> DailyDigest:
    return DailyDigest(
        day_index=3,
        tick_id=90,
        index_change_pct=index_change_pct,
        top_gainer_ticker="NVDA",
        top_gainer_pct=6.42,
        top_loser_ticker="JPM",
        top_loser_pct=-3.17,
        total_volume=48_200,
        stress_index=0.31,
        bankrupt_count=2,
        headline="Global chip demand surges on new AI datacenter orders",
    )


# The wire prose varies vocabulary by day; direction is asserted against the
# word SETS, not one hardcoded verb.
_UP_WORDS = ("edged higher", "rallied", "surged", "clawed back", "+1.25%")
_DOWN_WORDS = ("slipped", "fell sharply", "was routed", "extended the slide", "-2.50%")


def test_render_factual_positive_day():
    report = render_factual(_digest(1.25))
    assert any(w in report for w in _UP_WORDS), report
    assert not any(w in report for w in ("fell sharply", "was routed", "slipped")), report
    assert "1.25%" in report, report
    assert "NVDA" in report and "+6.42%" in report, report
    assert "JPM" in report and "-3.17%" in report, report
    assert "48,200" in report, report
    assert "0.31" in report, report
    assert "2 names now in bankruptcy" in report, report
    assert "Global chip demand surges on new AI datacenter orders" in report, report


def test_render_factual_negative_day():
    report = render_factual(_digest(-2.5))
    assert any(w in report for w in _DOWN_WORDS), report
    assert not any(w in report for w in ("rallied", "surged", "edged higher")), report
    # The index change appears with an explicit sign or as an unsigned
    # magnitude next to a downward verb — either way 2.50 must be real.
    assert "2.50%" in report, report


def test_report_parses_last_paragraph():
    reporter = DailyReporter(FakeRouter(REPORTER_REPLY))
    paragraph = asyncio.run(reporter.report(None, _digest(1.25)))
    assert paragraph == EXPECTED_PARAGRAPH, paragraph


def test_caller_fallback_yields_factual_report():
    """The integrator's pattern: try the LLM, fall back to render_factual."""
    reporter = DailyReporter(FakeRouter(reply=None))
    digest = _digest(-2.5)
    try:
        report = asyncio.run(reporter.report(None, digest))
    except RuntimeError:
        report = render_factual(digest)
    assert report == render_factual(digest), report


if __name__ == "__main__":
    test_render_factual_positive_day()
    test_render_factual_negative_day()
    test_report_parses_last_paragraph()
    test_caller_fallback_yields_factual_report()
    print("ALL DAILY REPORTER TESTS PASSED")
