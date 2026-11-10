"""
Claude client - the ONLY module that calls the Anthropic SDK.

What it adds on top of a plain SDK call:

* Fallback ladder  - each task has an ordered list of models (config/llm.yaml).
  Rate limits, timeouts, 5xx errors and refusals move to the next model.
  A bad request (our bug) is raised immediately - retrying will not fix it.
* Structured output - `client.messages.parse(output_format=<PydanticModel>)`,
  so the response is validated JSON or an exception, never loose text.
* Refusals          - `stop_reason == "refusal"` is checked BEFORE reading output.
* Prompt caching    - system blocks come from prompts.cached_system(), which
  places one cache breakpoint after the frozen prefix (instructions + ICP).
* Cost              - computed from `usage` (uncached, cache-write, cache-read,
  output tokens) and returned so the caller records an llm_call row.

No sampling parameters (temperature / top_p / top_k) are sent: the SDK's API
version (2026-10-21) rejects them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from app.core.config import get_settings, load_yaml

T = TypeVar("T", bound=BaseModel)


class AIUnavailable(Exception):
    """Every model in the ladder failed or refused. Callers degrade (e.g. row -> HOLD)."""


@dataclass
class ClaudeResult:
    output: BaseModel
    model: str
    tokens_in: int
    tokens_cached: int
    tokens_out: int
    cost_micro_usd: int
    latency_ms: int
    fallback_used: bool
    attempts: int


@lru_cache
def _client() -> anthropic.Anthropic:
    """
    The SDK only reads ANTHROPIC_API_KEY from the PROCESS environment, but our settings
    are loaded from backend/.env. Pass the key explicitly so a key in .env always works;
    fall back to the SDK's own lookup (env var / `ant auth login` profile) if unset.
    Two SDK-level retries per model.
    """
    key = get_settings().anthropic_api_key
    if key:
        return anthropic.Anthropic(api_key=key, max_retries=2, timeout=60.0)
    return anthropic.Anthropic(max_retries=2, timeout=60.0)


def _cost_micro_usd(model: str, usage) -> tuple[int, int, int, int]:
    """Return (cost_micro_usd, uncached_in, cached_in, out) from an SDK usage object."""
    prices = load_yaml("llm")["prices"][model]
    uncached = usage.input_tokens or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    out = usage.output_tokens or 0
    usd = (
        uncached * prices["input"]
        + cache_write * prices["input"] * 1.25          # cache writes cost 1.25x input
        + cache_read * prices["cache_read"]
        + out * prices["output"]
    ) / 1_000_000
    return int(usd * 1_000_000), uncached + cache_write, cache_read, out


def run_task(task: str, *, system: list[dict], user: str, output_model: type[T]) -> ClaudeResult:
    """Run one AI task through its fallback ladder. Raises AIUnavailable if all fail."""
    cfg = load_yaml("llm")["tasks"][task]
    ladder: list[str] = cfg["ladder"]
    last_error: Exception | None = None
    started = time.monotonic()

    for attempt, model in enumerate(ladder):
        try:
            response = _client().messages.parse(
                model=model,
                max_tokens=cfg["max_tokens"],
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_model,
            )
        except (anthropic.BadRequestError, anthropic.AuthenticationError,
                anthropic.PermissionDeniedError):
            raise                                          # config/bug - another model won't fix it
        except (anthropic.RateLimitError, anthropic.APIConnectionError,
                anthropic.APITimeoutError, anthropic.InternalServerError,
                anthropic.APIStatusError) as exc:
            last_error = exc                               # try the next model
            continue

        if response.stop_reason == "refusal" or response.parsed_output is None:
            last_error = AIUnavailable(f"{model} refused or returned no output")
            continue

        cost, tin, tcached, tout = _cost_micro_usd(model, response.usage)
        return ClaudeResult(
            output=response.parsed_output, model=model, tokens_in=tin, tokens_cached=tcached,
            tokens_out=tout, cost_micro_usd=cost,
            latency_ms=int((time.monotonic() - started) * 1000),
            fallback_used=attempt > 0, attempts=attempt + 1,
        )

    raise AIUnavailable(f"All models failed for task '{task}': {last_error}")
