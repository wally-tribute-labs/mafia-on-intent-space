"""LLM provider abstraction for the human bridge.

Mirrors the structure intended for src/bots/llm.py (separate package per
parallelization plan): a thin `complete(system, user, ...)` function that
hides the provider behind environment variables. Anthropic Claude Haiku is
the default; falls back to a deterministic stub when no key is present so
the bridge boots without credentials (useful for demos and tests).

`robot_mode_rewrite` implements the per-message rewrite described in
docs/AGENT_DESIGN.md §Robot Mode.
"""

from __future__ import annotations

import os
from typing import Callable, Optional


_ROBOT_SYSTEM = (
    "Rewrite the user's text as if it were posted by an LLM bot. "
    "Make it sound robotic, formal, and overly logical. Use full sentences, "
    "proper punctuation, no contractions, no slang, no emoji. Keep the "
    "intended meaning intact. Length similar to original."
)


def _stub_complete(system: str, user: str, **_: object) -> str:
    """Deterministic offline fallback used when no LLM provider is configured."""
    text = user.strip()
    if not text:
        return text
    text = text.replace("'", "").replace("’", "")
    if not text.endswith((".", "?", "!")):
        text = text + "."
    return "I would like to note the following observation. " + text[:1].upper() + text[1:]


def _anthropic_complete(system: str, user: str, *, model: str, max_tokens: int, temperature: float) -> str:
    import anthropic  # imported lazily so dev installs without it still work

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=temperature,
    )
    return response.content[0].text.strip()


def complete(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    max_tokens: int = 256,
    temperature: float = 0.5,
) -> str:
    """Call the configured LLM. Falls back to a stub when offline.

    Provider selection is by env: ANTHROPIC_API_KEY → Anthropic; otherwise stub.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _anthropic_complete(
            system,
            user,
            model=model or "claude-haiku-3-5",
            max_tokens=max_tokens,
            temperature=temperature,
        )
    return _stub_complete(system, user)


# Tests inject via this module-level hook to avoid network and avoid having to
# patch `complete` in two places (it is also used directly in unit tests).
_complete_hook: Optional[Callable[[str, str], str]] = None


def set_complete_hook(hook: Optional[Callable[[str, str], str]]) -> None:
    """Override the LLM call (test seam). Pass None to clear."""
    global _complete_hook
    _complete_hook = hook


def robot_mode_rewrite(text: str) -> str:
    """Rewrite a human message so it reads like an LLM bot post.

    See docs/AGENT_DESIGN.md §Robot Mode for the prompt rationale.
    """
    if _complete_hook is not None:
        return _complete_hook(_ROBOT_SYSTEM, text).strip()
    return complete(_ROBOT_SYSTEM, text, max_tokens=256, temperature=0.5).strip()


__all__ = ["complete", "robot_mode_rewrite", "set_complete_hook"]
