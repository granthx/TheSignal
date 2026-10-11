"""Optional LLM access. A minimal Anthropic Messages API client over plain HTTPS (no SDK required)
plus tolerant JSON extraction. Agents only ever depend on ``.enabled`` and ``.complete()``, so swapping
the provider (OpenAI, Gemini, a local model) means writing one small class here."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from .config import Settings

log = logging.getLogger("signal.llm")
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class LLMError(Exception):
    pass


class NullLLM:
    enabled = False

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        raise LLMError("no LLM configured")


class AnthropicLLM:
    enabled = True

    def __init__(self, api_key: str, model: str, timeout: float = 120.0, session: requests.Session | None = None):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.session = session or requests.Session()

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        last_error = "unknown error"
        for attempt in range(3):
            try:
                resp = self.session.post(API_URL, headers=headers, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code in (429, 500, 502, 503, 529):
                    last_error = f"HTTP {resp.status_code}"
                elif resp.status_code >= 400:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                else:
                    data = resp.json()
                    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
                    if not text.strip():
                        raise LLMError("empty completion")
                    return text
            time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"gave up after retries ({last_error})")


def make_llm(settings: Settings):
    if settings.llm_enabled:
        return AnthropicLLM(settings.anthropic_api_key, settings.anthropic_model)
    return NullLLM()


def extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model reply, tolerating code fences and chatter."""
    text = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise LLMError("no JSON in model reply")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned malformed JSON: {exc}") from exc
    return obj
