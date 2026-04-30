"""VillagerBot — default BotAgent behavior, no role-specific overrides.

Villagers don't have a private subspace, don't act at night, and only need
the day-phase scan-decide-emit loop already implemented in BotAgent.
"""

from __future__ import annotations

from .base import BotAgent


class VillagerBot(BotAgent):
    """Pass-through subclass; reserved for future villager-only tweaks."""

    pass


__all__ = ["VillagerBot"]
