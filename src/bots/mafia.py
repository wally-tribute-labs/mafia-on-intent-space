"""MafiaBot — adds coven-space awareness on top of BotAgent.

Mafia behave like villagers in the day phase. At night, they act in the
coven subspace (`sessions["mafia"]`):

  - If no teammate has proposed a target this phase yet, post coven-target.
  - If a teammate already proposed (and we haven't confirmed), post a
    coven-confirm OR a competing coven-target depending on what the LLM
    decides.

Teammates are remembered from the reveal payload (if and when one arrives
in the coven space; the host typically seeds the coven with a private
membership message so each Mafia knows who else is on the team).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .base import BotAgent, ROLE_SUBSPACE_KEY
from .context import already_acted_this_phase

logger = logging.getLogger(__name__)


class MafiaBot(BotAgent):
    role: str = "mafia"

    def __post_init__(self) -> None:  # type: ignore[override]
        super().__post_init__()
        if "mafia" not in self.sessions:
            raise ValueError("MafiaBot requires sessions['mafia'] (coven subspace)")
        self._teammates: set[str] = set()
        # Host principals discovered from coven-membership payloads; used to
        # exclude the host from sender-inference fallback.
        self._known_host_pids: set[str] = set()

    # ------------------------------------------------------------------
    # Action gating
    # ------------------------------------------------------------------
    def should_act(self, phase: Optional[dict], scans: dict[str, list[dict]] | None = None) -> Optional[str]:
        """Same gating as BotAgent, plus refresh teammates from coven scans."""
        scans = scans or {}
        self._refresh_teammates(scans.get(ROLE_SUBSPACE_KEY["mafia"], []))
        return super().should_act(phase, scans)

    def _refresh_teammates(self, coven_msgs: list[dict]) -> None:
        """Resolve teammate set from coven-space messages.

        Resolution order (canonical → fallbacks):

        1. `kind=coven-membership` from the host (preferred): payload carries
           `members: [principal_id, ...]` listing exactly the mafia. Host's
           own principal is identified by `host` (string field) and excluded.
        2. `kind=reveal` (game-end): role_assignments map; pull all principals
           with role=="mafia". Useful for spectator/post-mortem reasoning.
        3. Sender inference (last-resort fallback): anyone who has posted in
           the coven (other than known host principals) is treated as a
           teammate. We track host principals from any coven-membership we've
           seen so we don't wrongly mark the host as a teammate.
        """
        for m in coven_msgs:
            payload = m.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            kind = payload.get("kind")
            if kind == "coven-membership":
                members = payload.get("members") or []
                if isinstance(members, list):
                    for pid in members:
                        if isinstance(pid, str) and pid != self.principal_id:
                            self._teammates.add(pid)
                host_pid = payload.get("host")
                if isinstance(host_pid, str):
                    self._known_host_pids.add(host_pid)
            elif kind == "reveal":
                role_assignments = payload.get("role_assignments") or {}
                if isinstance(role_assignments, dict):
                    for pid, role in role_assignments.items():
                        if role == "mafia" and pid != self.principal_id:
                            self._teammates.add(pid)

        # Fallback: sender inference, EXCLUDING any known host principals.
        for m in coven_msgs:
            sender = m.get("senderId")
            if not isinstance(sender, str):
                continue
            if sender == self.principal_id or sender in self._known_host_pids:
                continue
            self._teammates.add(sender)

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------
    def decide(self, action_kind: str, scans: dict[str, list[dict]]) -> dict | None:
        """Augment the persona prompt with teammate identities for coven actions."""
        if action_kind == "coven-target":
            persona_for_call = dict(self.persona)
            tm = ", ".join(sorted(self._teammates)) or "(none discovered yet)"
            base_overlay = (
                persona_for_call.get("system_overlay")
                or persona_for_call.get("overlay")
                or ""
            )
            persona_for_call["system_overlay"] = (
                f"{base_overlay}\n\nKnown Mafia teammates: {tm}.\n"
                "If a teammate's coven-target this phase is acceptable, output "
                "{\"kind\":\"coven-confirm\",\"target\":\"<their target>\"}."
            )
            saved = self.persona
            self.persona = persona_for_call
            try:
                return super().decide(action_kind, scans)
            finally:
                self.persona = saved
        return super().decide(action_kind, scans)


__all__ = ["MafiaBot"]
