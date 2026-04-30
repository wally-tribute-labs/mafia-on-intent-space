"""Payload validation helper.

Per the A3 constraint: "All payloads validated against src/game/schemas.py
before posting." We use `parse_payload` to dispatch by `kind` and re-emit
the model_dump (which strips defaults to None when configured). Returning
the dumped dict — not the pydantic model — keeps SDK calls duck-typed.
"""

from __future__ import annotations

from src.game.schemas import parse_payload


def validate(payload: dict) -> dict:
    """Round-trip a payload through its pydantic schema.

    Raises ValueError on unknown `kind`, pydantic ValidationError on
    schema violations. Returns a fresh dict suitable for SDK `post()`.
    """
    model = parse_payload(payload)
    return model.model_dump()
