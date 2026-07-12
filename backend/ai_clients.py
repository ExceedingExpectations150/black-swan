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
import logging
import os
from typing import Any, Final

import aiohttp
import numpy as np
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

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
# NOTE: these must match the comment above — gemma-4-31b-it 500s and
# gemma-4-26b-it 404s on the live API (verified 2026-07-11 in run logs),
# which silently killed every LLM feature (news desk, PR posts, swarm).
PRIMARY_MODEL: Final[str] = os.getenv("BLACKSWAN_LLM_PRIMARY", "gemini-2.5-flash")
FALLBACK_MODEL: Final[str] = os.getenv("BLACKSWAN_LLM_FALLBACK", "gemini-2.0-flash")
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
        # Keyless operation is allowed: the market must run for anyone who
        # clones the repo. Every LLM call site catches RuntimeError and falls
        # back to real-data paths (heuristic traders, keyword event analyst,
        # factual wire reports, offline desk briefs) — degradation, not mocks.
        self._keys: list[str] = [k for k in (primary, backup) if k]
        if not self._keys:
            logger.warning(
                "No GEMINI_API_KEY_PRIMARY/BACKUP set — LLM prose disabled; "
                "running on TimesFM + behavioral agents with factual fallbacks."
            )
        self._key_index: int = 0
        self._model: str = PRIMARY_MODEL

    @property
    def active_model(self) -> str:
        return self._model

    @property
    def active_key_index(self) -> int:
        return self._key_index

    @property
    def is_active(self) -> bool:
        """True once at least one API key is configured."""
        return bool(self._keys)

    def set_keys(self, keys: list[str]) -> None:
        """Inject API keys into a LIVE router (runtime key entry, no restart).

        Resets the rotation cursor and model so the next call starts on the
        primary key/model. The key value is never logged by this class.
        """
        self._keys = [k for k in keys if k]
        self._key_index = 0
        self._model = PRIMARY_MODEL

    def _rotate_on_rate_limit(self) -> None:
        self._key_index = (self._key_index + 1) % len(self._keys)
        self._model = FALLBACK_MODEL

    def _endpoint(self) -> str:
        return GEMINI_ENDPOINT_TEMPLATE.format(model=self._model)

    async def prompt_cohort(self, session: aiohttp.ClientSession, prompt_text: str) -> str:
        """Send one behavioral prompt and return the model's text response."""
        if not self._keys:
            raise RuntimeError("Gemini disabled: no API keys configured (keyless mode).")
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
            # Normalize EVERY network failure (DNS, connection refused, TLS,
            # server disconnect) to RuntimeError. Call sites catch RuntimeError
            # and degrade to factual fallbacks; a raw aiohttp.ClientError would
            # otherwise escape those handlers and crash the whole tick loop.
            try:
                async with session.post(
                    self._endpoint(), json=payload, headers=headers
                ) as resp:
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
            except aiohttp.ClientError as exc:
                raise RuntimeError(f"Gemini network error on {self._model}: {exc}") from exc

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
    window and 32-tick horizon. Device selection is device-agnostic: it uses
    the standard `torch.cuda` accelerator API, which the PyTorch ROCm build
    surfaces for AMD Instinct / Radeon GPUs — so on an AMD box with the ROCm
    wheel this runs on the AMD GPU with ZERO code changes (no CUDA-only
    kernels, no triton/inductor). On a CPU-only wheel (e.g. this Windows demo
    box, torch+cpu) it falls back to CPU. The resolved device is logged.

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
    # Micro-batch size: the whole market (~50 tickers) is forecast in ONE
    # batched call per refresh. per_core_batch_size=1 forced ~50 sequential
    # forward passes (~30 s on CPU); batching brings a refresh down to a few
    # seconds. Sized above the company count with headroom.
    BATCH_SIZE: Final[int] = 64

    def __init__(self) -> None:
        # Imported here, not at module top, so the Gemma cohort layer stays
        # usable if the torch runtime is broken. Nothing is caught: a missing
        # or unloadable torch/timesfm still raises a hard error on first use
        # (PRD rule: no mock fallbacks).
        import torch
        import timesfm

        # `torch.cuda` is the accelerator API for BOTH NVIDIA CUDA and AMD
        # ROCm builds; it returns True for an AMD GPU under the ROCm wheel.
        self.device: str = "cuda" if torch.cuda.is_available() else "cpu"
        backend = "ROCm/HIP" if getattr(torch.version, "hip", None) else (
            "CUDA" if getattr(torch.version, "cuda", None) else "CPU"
        )
        logger.info(
            "TimesFM device=%s (torch %s, backend=%s)", self.device, torch.__version__, backend
        )
        # torch_compile=False: inductor/triton are not available on Windows and
        # keeping it off avoids a triton dependency, aiding portability.
        self.tfm: timesfm.TimesFM_2p5_200M_torch = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
            self.CHECKPOINT_REPO, torch_compile=False
        )
        self.tfm.compile(
            timesfm.ForecastConfig(
                max_context=self.CONTEXT_LEN,
                max_horizon=self.HORIZON_LEN,
                normalize_inputs=True,
                per_core_batch_size=self.BATCH_SIZE,
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

        return self.forecast_batch({"_": price_history})["_"]

    def _to_context(self, price_history: list[float]) -> np.ndarray:
        """Last CONTEXT_LEN prices, left-padded to MIN_CONTEXT (PRD rule 4.3)."""
        window: list[float] = price_history[-self.CONTEXT_LEN :]
        if len(window) < self.MIN_CONTEXT:
            window = [window[0]] * (self.MIN_CONTEXT - len(window)) + window
        return np.asarray(window, dtype=np.float32)

    def forecast_batch(self, histories: dict[str, list[float]]) -> dict[str, float]:
        """Forecast the next clearing price for MANY series in ONE batched call.

        The whole market is forecast per refresh; batching (with a matching
        per_core_batch_size) turns ~50 sequential forward passes into one,
        which is what keeps the refresh loop fast. Empty histories are
        skipped; returns {ticker: next-tick point forecast}.
        """
        tickers: list[str] = []
        contexts: list[np.ndarray] = []
        for ticker, series in histories.items():
            if not series:
                continue
            tickers.append(ticker)
            contexts.append(self._to_context(series))
        if not contexts:
            return {}
        point_forecast, _ = self.tfm.forecast(horizon=self.HORIZON_LEN, inputs=contexts)
        return {ticker: float(point_forecast[i][0]) for i, ticker in enumerate(tickers)}
