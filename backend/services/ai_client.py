"""Shared Gemini client helper."""
from google import genai

_client = None


def get_gemini_client():
    global _client
    if _client is None:
        from backend.services.config import load_config

        config = load_config()
        api_key = config.get("gemini_api_key")
        if api_key:
            _client = genai.Client(api_key=api_key)
        else:
            # No key configured (e.g. fresh production deploy). Return a lazy
            # proxy that only raises if a caller actually tries to use it,
            # so module import time never crashes the whole app.
            class _LazyClient:
                def __getattr__(self, name):
                    raise RuntimeError(
                        "Gemini API key not configured. Set GEMINI_API_KEY env var "
                        "or add gemini_api_key in config.json."
                    )

            _client = _LazyClient()
    return _client

