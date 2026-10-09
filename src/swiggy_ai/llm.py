"""The ONLY file that talks to OpenAI.

If the SDK changes, this is the one place to fix. Everything else calls
`complete_structured()` and gets back a validated Pydantic object plus token usage.
"""
import time
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

import openai
from pydantic import BaseModel

from swiggy_ai.config import get_settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised with a message that is safe to show to a user."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass
class LLMResult(Generic[T]):
    parsed: T
    usage: Usage


Tier = Literal["low", "medium", "high"]


def resolve_model(tier: Tier = "medium") -> str:
    """Pick the model for a job: OPENAI_MODEL_<TIER> if set, otherwise OPENAI_MODEL."""
    s = get_settings()
    model = {"low": s.openai_model_low, "medium": s.openai_model_medium,
             "high": s.openai_model_high}[tier] or s.openai_model
    if not model:
        raise LLMError(f"No model for tier '{tier}'. Set OPENAI_MODEL (or OPENAI_MODEL_"
                       f"{tier.upper()}) in .env.")
    return model


def _client() -> openai.OpenAI:
    s = get_settings()
    if not s.openai_api_key:
        raise LLMError("OPENAI_API_KEY is not set. Add it to .env.")
    return openai.OpenAI(api_key=s.openai_api_key, timeout=60, max_retries=0)


def complete_structured(
    *,
    instructions: str,
    user_input: str,
    schema: type[T],
    tier: Tier = "medium",
    temperature: float | None = 0.0,
    retries: int = 3,
) -> LLMResult[T]:
    """Ask the model for JSON that matches `schema`. Retries rate limits/timeouts with backoff."""
    client = _client()
    model = resolve_model(tier)
    kwargs: dict = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "text_format": schema,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature

    for attempt in range(retries + 1):
        try:
            resp = client.responses.parse(**kwargs)
            break
        except openai.AuthenticationError as e:
            raise LLMError("OpenAI rejected the API key. Check OPENAI_API_KEY in .env.") from e
        except openai.BadRequestError as e:
            # Some models do not accept `temperature`; retry once without it.
            if "temperature" in str(e).lower() and "temperature" in kwargs:
                kwargs.pop("temperature")
                continue
            raise LLMError(f"OpenAI rejected the request: {e}") from e
        except (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError) as e:
            if attempt == retries:
                raise LLMError(f"OpenAI is unavailable after {retries + 1} tries: {e}") from e
            time.sleep(2**attempt)  # 1s, 2s, 4s
    else:  # pragma: no cover
        raise LLMError("OpenAI call failed")

    if resp.output_parsed is None:
        raise LLMError("The model returned no structured output.")
    usage = Usage(
        input_tokens=getattr(resp.usage, "input_tokens", 0) or 0,
        output_tokens=getattr(resp.usage, "output_tokens", 0) or 0,
    )
    return LLMResult(parsed=resp.output_parsed, usage=usage)
