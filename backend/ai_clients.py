"""AI compute layers for ChaosNet "Black Swan" (PRD.md Sections 2 and 4).

Two real inference paths — no mocks, no stubs, no dummy data (PRD rule 4.1):
- GeminiModelRouter: Google Generative Language API via aiohttp, with
  HTTP 429 key rotation, model fallback, and exponential backoff (rule 4.2).
- TimesFMForecaster: Google's timesfm foundation model running locally on
  PyTorch, fed the last 512 clearing prices with left-padding (rule 4.3).

Missing dependencies or credentials raise hard errors by design.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Final

import aiohttp
import numpy as np
from dotenv import load_dotenv

load_dotenv()

GEMINI_ENDPOINT_TEMPLATE: Final[str] = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
# PRD.md names gemma-2-27b-it / gemini-1.5-flash; both were retired from the
# Generative Language API (verified via ListModels 2026-07-06). The PRD's
# gemma-2-27b-it and gemini-1.5-flash are retired. Live diagnosis showed the
# served Gemma models cannot produce structured output: gemma-4-31b-it 500s,
# and gemma-4-26b-a4b-it returns only chain-of-thought and never emits the
# requested JSON. gemini-2.5-flash follows JSON-only instructions cleanly, so
# it is primary; the 429 engine rotates keys and scales down to a lighter
# flash. The "Gemma cohort" concept is preserved; only the served model differs.
PRIMARY_MODEL: Final[str] = "gemma-4-31b-it"
FALLBACK_MODEL: Final[str] = "gemma-4-26b-it"
# Retry/backoff kept short: under free-tier quota the LLM cohort/PR calls
# 429 every tick, and long backoff stalls the whole simulation. The quant
# (TimesFM) path is local and unaffected, so a fast fail keeps ticks flowing.
MAX_RETRIES: Final[int] = 1
BACKOFF_BASE_SECONDS: Final[float] = 0.25
# Flash models can also reason before answering; keep the cap generous so the
# final JSON is never truncated (observed live 2026-07-06).
MAX_OUTPUT_TOKENS: Final[int] = 8192


class GeminiModelRouter:
    """Async Gemini/Gemma client with dynamic HTTP 429 rate-limit handling.

    On 429: rotates between the primary and backup API key, switches from
    gemma-2-27b-it to gemini-1.5-flash, applies exponential backoff, and
    retries up to MAX_RETRIES times before raising.
    """

    def __init__(self) -> None:
        primary: str | None = os.getenv("GEMINI_API_KEY_PRIMARY")
        backup: str | None = os.getenv("GEMINI_API_KEY_BACKUP")
        if not primary or not backup:
            raise RuntimeError(
                "GEMINI_API_KEY_PRIMARY and GEMINI_API_KEY_BACKUP must be set in .env — "
                "behavioral cohorts have no mock fallback by design (PRD rule 4.1)."
            )
        self._keys: list[str] = [primary, backup]
        self._key_index: int = 0
        self._model: str = PRIMARY_MODEL

    @property
    def active_model(self) -> str:
        return self._model

    @property
    def active_key_index(self) -> int:
        return self._key_index

    def _rotate_on_rate_limit(self) -> None:
        self._key_index = (self._key_index + 1) % len(self._keys)
        self._model = FALLBACK_MODEL

    def _endpoint(self) -> str:
        return GEMINI_ENDPOINT_TEMPLATE.format(model=self._model)

    async def prompt_cohort(self, session: aiohttp.ClientSession, prompt_text: str) -> str:
        """Send one behavioral prompt and return the model's text response."""
        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS},
        }
        last_status: int = 0

        for attempt in range(1 + MAX_RETRIES):
            headers: dict[str, str] = {
                "x-goog-api-key": self._keys[self._key_index],
                "Content-Type": "application/json",
            }
            async with session.post(self._endpoint(), json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data: dict[str, Any] = await resp.json()
                    try:
                        text: str = data["candidates"][0]["content"]["parts"][0]["text"]
                    except (KeyError, IndexError, TypeError) as exc:
                        raise RuntimeError(
                            f"Unexpected Gemini response shape from {self._model}: {data!r}"
                        ) from exc
                    return text

                last_status = resp.status
                body: str = await resp.text()

            if last_status >= 429 and attempt < MAX_RETRIES:
                self._rotate_on_rate_limit()
                await asyncio.sleep(BACKOFF_BASE_SECONDS * (2**attempt))
                continue

            raise RuntimeError(
                f"Gemini API error HTTP {last_status} on {self._model} "
                f"(attempt {attempt + 1}/{1 + MAX_RETRIES}): {body[:500]}"
            )

        raise RuntimeError(
            f"Gemini API still rate-limited (HTTP {last_status}) after {MAX_RETRIES} retries."
        )


class TimesFMForecaster:
    """Local TimesFM point forecaster for the institutional quant agents.

    Runs Google's TimesFM foundation model on PyTorch with a 512-tick context
    window and 32-tick horizon, on CUDA when available (device selection is
    automatic inside the timesfm torch module).

    Checkpoint note: PRD.md names `google/timesfm-2.0-500m`, which only the
    timesfm 1.x package (Python <= 3.11) can load. This machine runs Python
    3.12, where the installed timesfm 2.0.2 package supports exactly one
    PyTorch checkpoint: `google/timesfm-2.5-200m-pytorch`. Same architecture
    family, newer revision — the PRD's context/horizon/padding rules are
    preserved unchanged.
    """

    CONTEXT_LEN: Final[int] = 512
    HORIZON_LEN: Final[int] = 32
    MIN_CONTEXT: Final[int] = 32
    CHECKPOINT_REPO: Final[str] = "google/timesfm-2.5-200m-pytorch"

    def __init__(self) -> None:
        # Imported here, not at module top, so the Gemma cohort layer stays
        # usable if the torch runtime is broken. Nothing is caught: a missing
        # or unloadable torch/timesfm still raises a hard error on first use
        # (PRD rule: no mock fallbacks).
        import torch
        import timesfm

        self.device: str = "cuda" if torch.cuda.is_available() else "cpu"
        # torch_compile=False: inductor/triton are not available on Windows.
        self.tfm: timesfm.TimesFM_2p5_200M_torch = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
            self.CHECKPOINT_REPO, torch_compile=False
        )
        self.tfm.compile(
            timesfm.ForecastConfig(
                max_context=self.CONTEXT_LEN,
                max_horizon=self.HORIZON_LEN,
                normalize_inputs=True,
                per_core_batch_size=1,
                infer_is_positive=True,
                fix_quantile_crossing=True,
            )
        )

    def forecast_next_tick(self, price_history: list[float]) -> float:
        """Forecast the next clearing price from recent market history.

        Feeds the last CONTEXT_LEN prices; histories shorter than MIN_CONTEXT
        are left-padded with the initial price (PRD rule 4.3). Returns the
        first point of the horizon forecast.
        """
        if not price_history:
            raise ValueError("price_history must contain at least one clearing price.")

        window: list[float] = price_history[-self.CONTEXT_LEN :]
        if len(window) < self.MIN_CONTEXT:
            window = [window[0]] * (self.MIN_CONTEXT - len(window)) + window

        context: np.ndarray = np.asarray(window, dtype=np.float32)
        point_forecast, _ = self.tfm.forecast(horizon=self.HORIZON_LEN, inputs=[context])
        return float(point_forecast[0][0])
