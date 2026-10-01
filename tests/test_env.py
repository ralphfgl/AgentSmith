import os
import pytest

from agentsmith.env import (
    split_env_tokens,
    load_env_file,
    api_keys_for_provider,
)


# 1. Parameterized Test for split_env_tokens
# This runs the test function 4 separate times with each input/expected pair!
@pytest.mark.parametrize(
    "test_input, expected",
    [
        ("a,b,c", ["a", "b", "c"]),
        ("a\nb:c", ["a", "b", "c"]),
        (None, []),
        ("  x ,, y ", ["x", "y"]),
    ],
)
def test_split_env_tokens(test_input, expected):
    assert split_env_tokens(test_input) == expected


# 2 & 3. Combined Logic (File & Providers)
def test_load_env_file_and_provider_keys(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.test"
    env_file.write_text("GROQ_API_KEY=key1,key2\n")

    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    load_env_file(str(env_file))

    assert os.environ["GROQ_API_KEY"] == "key1,key2"
    assert api_keys_for_provider("https://groq.com") == ["key1", "key2"]
    assert api_keys_for_provider("https://openrouter.ai") == []


# 4. Error Handling
def test_load_env_file_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_env_file(".env.nope")
