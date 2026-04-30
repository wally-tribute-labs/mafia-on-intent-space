"""DetectiveBot — adds detective-notebook subspace handling.

The detective notebook is a private subspace shared with the host. Each
night the detective posts an `investigate` INTENT naming a target; the
host responds with a COMPLETE carrying `investigate-result` (target role
+ substrate). Past results inform future investigations and day-phase
accusations.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import BotAgent, ROLE_SUBSPACE_KEY

logger = logging.getLogger(__name__)


class DetectiveBot(BotAgent):
    role: str = "detective"

    def __post_init__(self) -> None:  # type: ignore[override]
        super().__post_init__()
        if "detective" not in self.sessions:
            raise ValueError("DetectiveBot requires sessions['detective'] (notebook subspace)")

    def _past_results(self, notebook_msgs: list[dict]) -> list[dict]:
        """Extract investigate-result COMPLETEs into a compact list."""
        out: list[dict] = []
        for m in notebook_msgs:
            payload = m.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if payload.get("kind") == "investigate-result":
                out.append({
                    "target": payload.get("target"),
                    "role": payload.get("role"),
                    "substrate": payload.get("substrate"),
                })
        return out

    def decide(self, action_kind: str, scans: dict[str, list[dict]]) -> dict | None:
        """Inject prior investigate-results into the persona overlay."""
        if action_kind == "investigate":
            notebook = scans.get(ROLE_SUBSPACE_KEY["detective"], [])
            results = self._past_results(notebook)
            persona_for_call = dict(self.persona)
            base_overlay = (
                persona_for_call.get("system_overlay")
                or persona_for_call.get("overlay")
                or ""
            )
            if results:
                lines = [f"- {r['target']}: role={r['role']} substrate={r['substrate']}"
                         for r in results]
                history = "Prior investigation results (do not re-investigate):\n" + "\n".join(lines)
            else:
                history = "Prior investigation results: (none)"
            persona_for_call["system_overlay"] = f"{base_overlay}\n\n{history}"
            saved = self.persona
            self.persona = persona_for_call
            try:
                return super().decide(action_kind, scans)
            finally:
                self.persona = saved
        return super().decide(action_kind, scans)


__all__ = ["DetectiveBot"]
