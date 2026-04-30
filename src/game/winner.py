"""Win-condition derivation from the public log.

Per GAME_RULES.md §Game-end conditions:
- Villagers win when all Mafia are dead.
- Mafia win when `living_mafia >= living_non_mafia`.

Edge case (per GAME_RULES.md §Edge cases): "All players die in one night →
draw." We treat empty living set with no mafia as a draw rather than a
villager win.
"""

from __future__ import annotations

from typing import Optional


def compute_winner(
    living_principals: set[str],
    role_truth: dict[str, str],
) -> Optional[str]:
    """Return "villagers" / "mafia" / "draw", or None if game is ongoing.

    `role_truth` maps principal_id -> role string ("mafia" / "detective" /
    "doctor" / "villager"). Only "mafia" is special-cased; everyone else is
    counted as non-mafia.
    """
    living_mafia = sum(
        1 for p in living_principals if role_truth.get(p) == "mafia"
    )
    living_non_mafia = len(living_principals) - living_mafia

    if living_mafia == 0 and living_non_mafia == 0:
        return "draw"
    if living_mafia == 0:
        return "villagers"
    if living_mafia >= living_non_mafia:
        return "mafia"
    return None
