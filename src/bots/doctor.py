"""DoctorBot — adds doctor-notebook subspace handling.

The doctor protects one player per night via a `protect` INTENT in their
private notebook. Self-protection is allowed only ONCE per game (per
docs/GAME_RULES.md §Doctor). We enforce that by scanning the notebook for
prior `protect` INTENTs whose target equals our own principal_id.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.game.schemas import ProtectPayload

from .base import BotAgent, ROLE_SUBSPACE_KEY

logger = logging.getLogger(__name__)


class DoctorBot(BotAgent):
    role: str = "doctor"

    def __post_init__(self) -> None:  # type: ignore[override]
        super().__post_init__()
        if "doctor" not in self.sessions:
            raise ValueError("DoctorBot requires sessions['doctor'] (notebook subspace)")

    def _self_protect_used(self, notebook_msgs: list[dict]) -> bool:
        for m in notebook_msgs:
            if m.get("senderId") != self.principal_id:
                continue
            payload = m.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if payload.get("kind") != "protect":
                continue
            if payload.get("target") == self.principal_id or payload.get("self_protect") is True:
                return True
        return False

    def decide(self, action_kind: str, scans: dict[str, list[dict]]) -> dict | None:
        if action_kind == "protect":
            notebook = scans.get(ROLE_SUBSPACE_KEY["doctor"], [])
            persona_for_call = dict(self.persona)
            base_overlay = (
                persona_for_call.get("system_overlay")
                or persona_for_call.get("overlay")
                or ""
            )
            if self._self_protect_used(notebook):
                hint = "You have ALREADY used your one self-protection this game. Do NOT protect yourself."
            else:
                hint = "You may protect yourself, but only ONCE per game. Use it wisely."
            persona_for_call["system_overlay"] = f"{base_overlay}\n\n{hint}"
            saved = self.persona
            self.persona = persona_for_call
            try:
                return super().decide(action_kind, scans)
            finally:
                self.persona = saved
        return super().decide(action_kind, scans)

    def emit(self, decision: dict) -> dict | None:
        """Drop a self-protect if the one-shot has already been used."""
        if decision.get("_action_kind") == "protect":
            target = decision.get("target")
            notebook = []
            doctor_session = self.sessions.get("doctor")
            if doctor_session is not None:
                space_id = getattr(doctor_session, "current_space_id", None)
                if space_id:
                    try:
                        notebook = doctor_session.scan_full(space_id).get("messages", [])
                    except Exception as exc:
                        logger.debug("doctor notebook re-scan failed: %s", exc)
            if target == self.principal_id and self._self_protect_used(notebook):
                logger.info("doctor self-protect blocked (already used)")
                self._log_iter(decision.get("_phase"), "protect", scanned=0,
                               error="self-protect already used")
                return None
            # Validate now so a downstream payload error doesn't go silent.
            try:
                ProtectPayload(
                    target=target or "",
                    self_protect=(target == self.principal_id),
                ).model_dump(exclude_none=True)
            except Exception as exc:
                logger.warning("invalid protect target: %s", exc)
                return None
        return super().emit(decision)


__all__ = ["DoctorBot"]
