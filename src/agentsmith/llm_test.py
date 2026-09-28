"""Provider-agnostic OpenAI-compatible LLM client with key rotation."""

from __future__ import annotations

import time
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from src.models import LLMResponse


def approximate_tokens(text: str) -> int:
    """Rough token estimate used when provider usage is unavailable."""
    return max(1, len(text) // 4) if text else 0


@dataclass
class LLMConfig:
    provider_url: str
    model_name: str
    api_keys: list[str]
    temperature: float = 0.1
    max_tokens: int = 900
    timeout_seconds: float = 60.0
    max_retries: int = 0


class KeyPool:
    """Simple round-robin API key pool."""

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("At least one API key is required")

        self._keys = keys
        self._index = 0

    def next(self) -> str:
        key = self._keys[self._index]
        self._index = (self._index + 1) % len(self._keys)
        return key


class LLMClient:
    """Minimal provider-agnostic client for OpenAI-compatible APIs."""

    def __init__(self, config: LLMConfig):
        if not config.provider_url:
            raise ValueError("provider_url is required")
        if not config.model_name:
            raise ValueError("model_name is required")

        self.config = config
        self._keys = KeyPool(config.api_keys)

    def _client(self) -> OpenAI:
        return OpenAI(
            api_key=self._keys.next(),
            base_url=self.config.provider_url.rstrip("/"),
            timeout=self.config.timeout_seconds,
            max_retries=0,
        )

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        """Generate a completion with provider-independent retry handling."""
        retries = 0
        last_error: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            started = time.perf_counter()
            try:
                response = self._client().chat.completions.create(
                    model=self.config.model_name,
                    messages=messages,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    stop=["<end_code>"],
                )
                latency_ms = (time.perf_counter() - started) * 1000
                text = response.choices[0].message.content or ""
                usage = response.usage
                return LLMResponse(
                    text=text,
                    input_tokens=(
                        usage.prompt_tokens
                        if usage and usage.prompt_tokens is not None
                        else approximate_tokens(str(messages))
                    ),
                    output_tokens=(
                        usage.completion_tokens
                        if usage and usage.completion_tokens is not None
                        else approximate_tokens(text)
                    ),
                    latency_ms=latency_ms,
                    retries=retries,
                )
            except APIStatusError as exc:
                last_error = exc
                if (
                    not self._should_retry(exc)
                    or attempt >= self.config.max_retries
                ):
                    break
                retries += 1
                time.sleep(self._retry_delay(attempt))
            except (APIConnectionError, APITimeoutError) as exc:
                last_error = exc

                if attempt >= self.config.max_retries:
                    break
                retries += 1
                time.sleep(self._retry_delay(attempt))
        raise RuntimeError(
            f"LLM request failed after {retries + 1} attempt(s): {last_error}"
        ) from last_error

    @staticmethod
    def _should_retry(exc: APIStatusError) -> bool:
        """Return whether an HTTP/API error is worth retrying."""
        return exc.status_code in {
            408,  # Request timeout
            409,  # Conflict
            425,  # Too early
            429,  # Rate limited
            500,
            502,
            503,
            504,
        }

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        """Small exponential backoff capped at two seconds."""
        return min(2.0, 0.25 * (attempt + 1))
