"""Persona loader.

Reads src/bots/personas.json. Each entry has the schema described in
docs/AGENT_DESIGN.md §Decision policy controls plus a system_overlay
string from §Per-persona overlays.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

_PERSONAS_PATH = Path(__file__).parent / "personas.json"

_REQUIRED_KEYS = {
    "name",
    "system_overlay",
    "speaking_frequency",
    "vote_threshold",
    "id_vote_propensity",
    "temperature",
    "scan_interval_s",
}


def load_personas(path: Path | None = None) -> dict[str, dict[str, Any]]:
    raw = json.loads((path or _PERSONAS_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"personas.json must be a dict at top level; got {type(raw).__name__}")
    out: dict[str, dict[str, Any]] = {}
    for key, persona in raw.items():
        if not isinstance(persona, dict):
            raise ValueError(f"persona {key!r} must be a dict")
        missing = _REQUIRED_KEYS - persona.keys()
        if missing:
            raise ValueError(f"persona {key!r} missing keys: {sorted(missing)}")
        out[key] = persona
    return out


def random_persona(rng: random.Random | None = None) -> dict[str, Any]:
    rng = rng or random.Random()
    personas = load_personas()
    return rng.choice(list(personas.values()))


__all__ = ["load_personas", "random_persona"]
