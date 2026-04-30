"""Phase derivation from intent-space message logs.

Inputs are NESTED SDK-shaped messages — exactly what `scan_full` returns::

    {
      "type": "INTENT" | "COMPLETE" | ...,
      "intentId": "...",        # present on INTENT
      "promiseId": "...",       # present on COMPLETE (NOT intentId)
      "parentId": "...",
      "senderId": "...",
      "timestamp": <int|str>,
      "payload": {"kind": "...", ...},
    }

Per ARCHITECTURE.md §Phase timing model: bots compute the current phase by
finding the latest unmatched `phase-start` (i.e. one with no corresponding
`phase-end`). No bot maintains a "phase" variable independently — phase is
*protocol-derived state*.

Phase-end → phase-start linkage uses an explicit reference in the payload
(`payload.ends_phase_intent_id`), not a header field, because COMPLETE
messages carry `promiseId` not `intentId` so header-level matching is
unreliable.
"""

from __future__ import annotations

from typing import Optional


def _payload(m: dict) -> dict:
    p = m.get("payload")
    return p if isinstance(p, dict) else {}


def current_phase(messages: list[dict]) -> Optional[dict]:
    """Return the latest phase-start INTENT with no matching phase-end.

    A phase-end COMPLETE matches a phase-start INTENT when
    `complete["payload"]["ends_phase_intent_id"] == start["intentId"]`.

    Returns the full nested phase-start dict, or None if no unmatched
    phase-start exists.
    """
    ended_ids: set[str] = set()
    for m in messages:
        if m.get("type") != "COMPLETE":
            continue
        payload = _payload(m)
        if payload.get("kind") != "phase-end":
            continue
        ref = payload.get("ends_phase_intent_id")
        if isinstance(ref, str):
            ended_ids.add(ref)

    latest: Optional[dict] = None
    for m in messages:
        if m.get("type") != "INTENT":
            continue
        if _payload(m).get("kind") != "phase-start":
            continue
        intent_id = m.get("intentId")
        if not isinstance(intent_id, str):
            continue
        if intent_id in ended_ids:
            continue
        latest = m
    return latest
