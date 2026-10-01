import os
import json
import time
import pytest
import requests
import requests_mock

from agentsmith.models import LLMResponse
from agentsmith.llm import LLMClient, LLMConfig, approximate_tokens


# ==========================================
# 1. Tests for approximate_tokens
# ==========================================
@pytest.mark.parametrize(
    "text, expected",
    [
        ("", 0),
        ("a", 1),  # floor of 1/4 is 0, but max(1, ...) forces 1
        ("abcd", 1),
        ("abcdefgh", 2),
    ],
)
def test_approximate_tokens(text, expected):
    assert approximate_tokens(text) == expected


# ==========================================
# 2. Tests for Setup & Key Rotation
# ==========================================
def test_client_endpoint_formatting():
    # It should correctly append /chat/completions if missing
    config1 = LLMConfig(
        provider_url="https://openai.com",
        model_name="gpt-4",
        api_keys=["key1"],
    )
    client1 = LLMClient(config1)
    assert client1._endpoint() == "https://openai.com/chat/completions"

    # It should preserve it if already present
    config2 = LLMConfig(
        provider_url="https://openai.com/chat/completions/",
        model_name="gpt-4",
        api_keys=["key1"],
    )
    client2 = LLMClient(config2)
    assert client2._endpoint() == "https://openai.com/chat/completions"


def test_client_key_rotation():
    config = LLMConfig(
        provider_url="http://test.ai",
        model_name="test",
        api_keys=["keyA", "keyB"],
    )
    client = LLMClient(config)

    assert client._next_key() == "keyA"
    assert client._next_key() == "keyB"
    # Loops back over the list automatically
    assert client._next_key() == "keyA"


def test_missing_config_raises():
    # Missing provider url or model name
    client = LLMClient(
        LLMConfig(provider_url="", model_name="gpt-4", api_keys=["key"])
    )
    with pytest.raises(
        RuntimeError, match="provider_url and model_name are required"
    ):
        client.complete([{"role": "user", "content": "hi"}])

    # Missing API Keys
    client = LLMClient(
        LLMConfig(
            provider_url="http://test.ai", model_name="gpt-4", api_keys=[]
        )
    )
    with pytest.raises(RuntimeError, match="No API key found"):
        client.complete([{"role": "user", "content": "hi"}])


# ==========================================
# 3. Successful Request & Token Accounting
# ==========================================
def test_complete_success_with_usage(requests_mock):
    config = LLMConfig(
        provider_url="https://test.ai",
        model_name="my-model",
        api_keys=["key1"],
    )
    client = LLMClient(config)

    mock_response = {
        "choices": [{"message": {"content": "Hello world"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    requests_mock.post(
        "https://test.ai/chat/completions", json=mock_response, status_code=200
    )

    res = client.complete([{"role": "user", "content": "hi"}])

    assert isinstance(res, LLMResponse)
    assert res.text == "Hello world"
    assert res.input_tokens == 10
    assert res.output_tokens == 5
    assert res.retries == 0


def test_complete_success_fallback_tokens(requests_mock):
    """When API returns no usage dict, fallback calculation must kick in."""
    config = LLMConfig(
        provider_url="https://test.ai",
        model_name="my-model",
        api_keys=["key1"],
    )
    client = LLMClient(config)

    # Missing the "usage" payload field completely
    mock_response = {"choices": [{"message": {"content": "Hello world"}}]}

    requests_mock.post(
        "https://test.ai/chat/completions", json=mock_response, status_code=200
    )

    res = client.complete([{"role": "user", "content": "hi"}])

    assert res.text == "Hello world"
    assert res.output_tokens == approximate_tokens("Hello world")
    assert res.input_tokens > 0  # Calculated via json.dumps(messages)


# ==========================================
# 4. Retries & Error Handling
# ==========================================
def test_complete_retry_then_success(requests_mock, monkeypatch):
    # Patch time.sleep so the test runs instantly instead of waiting
    monkeypatch.setattr(time, "sleep", lambda x: None)

    config = LLMConfig(
        provider_url="https://test.ai",
        model_name="m",
        api_keys=["key1", "key2"],
        max_retries=1,
    )
    client = LLMClient(config)

    # First attempt: 429 rate limit error, second attempt: 200 Success
    requests_mock.register_uri(
        "POST",
        "https://test.ai/chat/completions",
        [
            {"status_code": 429, "text": "Rate limit exceeded"},
            {
                "status_code": 200,
                "json": {
                    "choices": [
                        {"message": {"content": "Success after retry"}}
                    ]
                },
            },
        ],
    )

    res = client.complete([{"role": "user", "content": "hi"}])
    assert res.text == "Success after retry"
    assert res.retries == 1


def test_complete_fail_after_max_retries(requests_mock, monkeypatch):
    # Patch time.sleep so the test runs instantly instead of waiting
    monkeypatch.setattr(time, "sleep", lambda x: None)

    config = LLMConfig(
        provider_url="https://test.ai",
        model_name="m",
        api_keys=["key1"],
        max_retries=2,
    )
    client = LLMClient(config)

    # Continuous 500 server errors
    requests_mock.post("https://test.ai/chat/completions", status_code=500)

    with pytest.raises(RuntimeError, match="LLM request failed after"):
        client.complete([{"role": "user", "content": "hi"}])
