"""OpenAI-compatible LLM client with token accounting and key rotation using requests."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests
from agentsmith.models import LLMResponse


def approximate_tokens(text: str) -> int:
    """Small deterministic token estimate used when provider usage is missing."""
    if not text:
        return 0
    return max(1, int(len(text) / 4))


@dataclass
class LLMConfig:
    provider_url: str
    model_name: str
    api_keys: list[str]
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout_seconds: float = 60.0
    max_retries: int = 0


class LLMClient:
    """Minimal OpenAI-compatible chat completions client using requests."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._key_index = 0

    def _endpoint(self) -> str:
        base = self.config.provider_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _next_key(self) -> str | None:
        if not self.config.api_keys:
            return None
        key = self.config.api_keys[self._key_index % len(self.config.api_keys)]
        self._key_index += 1
        return key

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        """Generate a chat completion and normalize usage data."""
        if not self.config.provider_url or not self.config.model_name:
            raise RuntimeError(
                "provider_url and model_name are required for LLM calls"
            )
        if not self.config.api_keys:
            raise RuntimeError("No API key found. Set OPENROUTER_API_KEY e.g.")
        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stop": ["<end_code>"],
        }
        endpoint = self._endpoint()
        retries = 0
        last_error: Exception | None = None
        total_attempts = 1 + max(0, self.config.max_retries)
        for attempt in range(total_attempts):
            api_key = self._next_key()
            headers = {
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "agent-smith/0.1",
            }
            started = time.perf_counter()
            try:
                response = requests.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                )
                if response.status_code != 200:
                    exc = requests.exceptions.HTTPError(response=response)
                    raise exc

                latency_ms = (time.perf_counter() - started) * 1000
                parsed = response.json()  # Native JSON parsing

                message = parsed["choices"][0].get("message", {}) or {}
                text = message.get("content") or message.get("reasoning") or ""
                if not isinstance(text, str):
                    text = json.dumps(text, ensure_ascii=False)

                usage = parsed.get("usage") or {}
                prompt_tokens = int(
                    usage.get(
                        "prompt_tokens",
                        approximate_tokens(
                            json.dumps(messages, ensure_ascii=False)
                        ),
                    )
                )
                completion_tokens = int(
                    usage.get("completion_tokens", approximate_tokens(text))
                )
                return LLMResponse(
                    text=text,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    latency_ms=latency_ms,
                    retries=retries,
                )

            except requests.exceptions.HTTPError as exc:
                last_error = exc
                status_code = exc.response.status_code

                retryable = status_code in {
                    408,
                    409,
                    425,
                    429,
                    500,
                    502,
                    503,
                    504,
                }
                if not retryable or attempt + 1 >= total_attempts:
                    break

                sleep_for: float | None = None
                retry_after = exc.response.headers.get("Retry-After")
                if retry_after:
                    try:
                        sleep_for = float(retry_after)
                    except ValueError:
                        sleep_for = None

                if sleep_for is None:
                    try:
                        err_body = exc.response.json()
                        meta = (err_body.get("error") or {}).get(
                            "metadata"
                        ) or {}
                        sleep_for = (
                            float(meta.get("retry_after_seconds") or 0) or None
                        )
                    except Exception:
                        sleep_for = None

                retries += 1
                time.sleep(
                    min(15.0, max(sleep_for or 0, 0.5 * (attempt + 1) ** 2))
                )
                continue

            except (
                requests.exceptions.RequestException,
                ValueError,
                KeyError,
            ) as exc:
                last_error = exc
                if attempt + 1 >= total_attempts:
                    break

            retries += 1
            time.sleep(min(2.0, 0.25 * (attempt + 1)))

        raise RuntimeError(
            f"LLM request failed after {retries + 1} attempt(s): {last_error}"
        )
