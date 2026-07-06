"""Smoke test for TimesFMForecaster against the real checkpoint.

Downloads google/timesfm-2.5-200m-pytorch from Hugging Face on first run
(cached afterwards), then forecasts from a synthetic price walk and from a
short 5-point history to exercise the left-padding rule.

Run: python tests/test_timesfm_forecast.py
"""

from __future__ import annotations

import math
import os
import sys
import time

os.environ.setdefault("GEMINI_API_KEY_PRIMARY", "unused")
os.environ.setdefault("GEMINI_API_KEY_BACKUP", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_clients import TimesFMForecaster


def main() -> None:
    t0 = time.time()
    forecaster = TimesFMForecaster()
    print(f"model loaded on {forecaster.device} in {time.time() - t0:.1f}s")

    # 600-point gentle sine-drift walk around 100 — plausible clearing prices.
    history: list[float] = [
        100.0 + 5.0 * math.sin(i / 25.0) + i * 0.01 for i in range(600)
    ]
    t1 = time.time()
    nxt = forecaster.forecast_next_tick(history)
    print(f"forecast(600 pts) = {nxt:.4f} in {time.time() - t1:.2f}s")
    assert math.isfinite(nxt), "forecast must be finite"
    assert 50.0 < nxt < 200.0, f"forecast {nxt} wildly outside plausible band"

    # Short history: 5 points -> left-padded with the initial price to 32.
    short = [100.0, 101.0, 102.0, 101.5, 102.5]
    nxt_short = forecaster.forecast_next_tick(short)
    print(f"forecast(5 pts, padded) = {nxt_short:.4f}")
    assert math.isfinite(nxt_short)
    assert 50.0 < nxt_short < 200.0

    try:
        forecaster.forecast_next_tick([])
    except ValueError:
        pass
    else:
        raise AssertionError("empty history must raise ValueError")

    print("PASS: checkpoint load, 600-pt forecast, padded 5-pt forecast, empty guard")


if __name__ == "__main__":
    main()
