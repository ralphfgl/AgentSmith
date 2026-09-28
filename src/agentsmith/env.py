"""Environment and .env loading utilities (Simplified version)."""

from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv


def load_env_file(path: str | None = None) -> None:
    """Load KEY=VALUE pairs from a .env file using python-dotenv.

    If path is None, it automatically climbs the directory tree
    to find the nearest .env file.
    """
    if path:
        env_path = Path(path)
        if not env_path.exists():
            raise FileNotFoundError(f".env file not found: {env_path}")
        load_dotenv(dotenv_path=env_path)
    else:
        # load_dotenv() naturally climbs parent directories automatically
        load_dotenv(search_path=Path.cwd())


def split_env_tokens(value: str | None) -> list[str]:
    """Split API key env values that may be comma or newline separated."""
    if not value:
        return []
    tokens: list[str] = []
    # Replace common separators with commas, then split
    for chunk in value.replace("\n", ",").replace(":", ",").split(","):
        token = chunk.strip()
        if token:
            tokens.append(token)
    return tokens


def api_keys_for_provider(provider_url: str) -> list[str]:
    """Return explicit API tokens matching the target provider keyword."""
    lower_url = provider_url.lower()
    env_names: list[str] = []

    # Map keywords cleanly to their explicit environment variable names
    if "openrouter" in lower_url:
        env_names.append("OPENROUTER_API_KEY")
    elif "groq" in lower_url:
        env_names.append("GROQ_API_KEY")
    elif "together" in lower_url:
        env_names.append("TOGETHER_API_KEY")
    elif "mistral" in lower_url:
        env_names.append("MISTRAL_API_KEY")
    elif "fireworks" in lower_url:
        env_names.append("FIREWORKS_API_KEY")
    elif "cohere" in lower_url:
        env_names.append("COHERE_API_KEY")
    elif "google" in lower_url or "generativelanguage" in lower_url:
        env_names.append("GOOGLE_API_KEY")
    elif "openai" in lower_url:
        env_names.append("OPENAI_API_KEY")

    seen: set[str] = set()
    keys: list[str] = []

    for env_name in env_names:
        for key in split_env_tokens(os.environ.get(env_name)):
            if key not in seen:
                seen.add(key)
                keys.append(key)

    return keys
