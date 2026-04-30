"""Resolve a single night's mafia kill against the doctor's protect.

Per GAME_RULES.md §Night rules — Resolution order:
  1. Doctor's `protect` target
  2. Mafia's `coven-target` (with majority mafia agreement)
  3. If doctor target == mafia target → `cause=saved`, no death
  4. Otherwise → victim dies (`cause=mafia`)

Edge case (§Edge cases): "Mafia disagree on target → take first INTENT
with PROMISE (consensus order); if none, no kill."

Inputs are PROJECTED dicts — not raw SDK messages. The host loop is
expected to project coven-target INTENTs (with a `confirmed` flag derived
from matching `coven-confirm` PROMISEs) before calling this function.
That keeps this module a pure resolver and avoids re-encoding the SDK
shape for each rule.
"""

from __future__ import annotations

from typing import Optional


def _chosen_target(coven_targets: list[dict]) -> Optional[str]:
    """First coven-target marked `confirmed=True`, else None.

    Each item is a dict with at least `target` and `confirmed` keys.
    Items missing `target` are skipped; non-bool `confirmed` is treated
    as falsy.
    """
    for entry in coven_targets:
        target = entry.get("target")
        confirmed = entry.get("confirmed")
        if isinstance(target, str) and confirmed is True:
            return target
    return None


def resolve_night(
    coven_targets: list[dict],
    doctor_protect: dict | None,
) -> dict:
    """Resolve the night's death (or save) and return a result dict.

    Returns `{killed: principal_id | None, cause: "mafia" | "saved" | "none"}`:
    - `cause="none"` and `killed=None` if no confirmed mafia target.
    - `cause="saved"` and `killed=None` if doctor's target equals the
      confirmed mafia target.
    - `cause="mafia"` and `killed=<target>` otherwise.
    """
    chosen = _chosen_target(coven_targets)
    if chosen is None:
        return {"killed": None, "cause": "none"}

    doctor_target = None
    if isinstance(doctor_protect, dict):
        candidate = doctor_protect.get("target")
        if isinstance(candidate, str):
            doctor_target = candidate

    if doctor_target == chosen:
        return {"killed": None, "cause": "saved"}
    return {"killed": chosen, "cause": "mafia"}
