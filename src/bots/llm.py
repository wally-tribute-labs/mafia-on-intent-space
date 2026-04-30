"""Minimal LLM client wrapper.

Default provider is Anthropic Claude Haiku 3.5 over raw httpx (no SDK to
keep deps minimal). OpenAI provider is selected via `LLM_PROVIDER=openai`.

Exposes a single .complete(system, user, max_tokens, temperature) -> str
contract so swapping providers is trivial.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 512,
        temperature: float = 0.8,
    ) -> str: ...


class LLMError(RuntimeError):
    pass


class AnthropicProvider:
    """Claude Haiku 3.5 via raw HTTPS — no anthropic-py dependency."""

    DEFAULT_MODEL = "claude-3-5-haiku-latest"
    URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 30.0):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY not set")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", self.DEFAULT_MODEL)
        self.timeout = timeout

    def complete(self, *, system: str, user: str, max_tokens: int = 512, temperature: float = 0.8) -> str:
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }
        resp = httpx.post(self.URL, headers=headers, json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise LLMError(f"anthropic {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected anthropic response shape: {json.dumps(data)[:400]}") from exc


class OpenAIProvider:
    """OpenAI chat completions via raw HTTPS."""

    DEFAULT_MODEL = "gpt-4o-mini"
    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 30.0):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise LLMError("OPENAI_API_KEY not set")
        self.model = model or os.environ.get("OPENAI_MODEL", self.DEFAULT_MODEL)
        self.timeout = timeout

    def complete(self, *, system: str, user: str, max_tokens: int = 512, temperature: float = 0.8) -> str:
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = httpx.post(self.URL, headers=headers, json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise LLMError(f"openai {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected openai response shape: {json.dumps(data)[:400]}") from exc


def _build_default_provider() -> LLMProvider:
    name = (os.environ.get("LLM_PROVIDER") or "anthropic").lower()
    if name == "openai":
        return OpenAIProvider()
    if name == "anthropic":
        return AnthropicProvider()
    raise LLMError(f"unknown LLM_PROVIDER: {name!r}")


class LLMClient:
    """Public facade with one retry on transient failure."""

    def __init__(self, provider: LLMProvider | None = None, *, max_retries: int = 1, backoff_s: float = 1.5):
        self.provider = provider or _build_default_provider()
        self.max_retries = max_retries
        self.backoff_s = backoff_s

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 512,
        temperature: float = 0.8,
    ) -> str:
        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self.max_retries:
            try:
                return self.provider.complete(
                    system=system, user=user, max_tokens=max_tokens, temperature=temperature
                )
            except Exception as exc:  # network or LLMError
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                wait = self.backoff_s * (2 ** attempt)
                logger.warning("LLM call failed (attempt %d): %s — retry in %.1fs", attempt + 1, exc, wait)
                time.sleep(wait)
                attempt += 1
        assert last_exc is not None
        raise LLMError(f"LLM call failed after {self.max_retries + 1} attempts: {last_exc}") from last_exc


__all__ = ["LLMClient", "LLMError", "LLMProvider", "AnthropicProvider", "OpenAIProvider"]
