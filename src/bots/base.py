"""BotAgent — the scan-decide-emit loop.

Each bot holds one or more `Session` objects (the spacebase1 SDK session,
duck-typed here). `sessions["game"]` is required; role-specific subspace
sessions go under their role key (e.g. `sessions["mafia"]`).

The loop is intentionally simple. All real state lives in the protocol log.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from src.game.schemas import (
    AccusationPayload,
    CovenConfirmPayload,
    CovenTargetPayload,
    DefensePayload,
    IdVotePayload,
    InvestigatePayload,
    ObservationPayload,
    ProtectPayload,
    SubstrateGuessPayload,
    VotePayload,
)

from .context import (
    already_acted_this_phase,
    current_phase,
    extract_deaths,
    extract_living_players,
)
from .llm import LLMClient, LLMError
from .prompts import build_system_prompt, build_user_message

logger = logging.getLogger(__name__)


ROLE_SUBSPACE_KEY: dict[str, str] = {
    "mafia": "mafia",
    "detective": "detective",
    "doctor": "doctor",
}

# Map role + phase suffix → action_kind for night phases.
NIGHT_ACTION_BY_ROLE: dict[str, tuple[str, str]] = {
    "mafia": ("-mafia", "coven-target"),
    "detective": ("-detective", "investigate"),
    "doctor": ("-doctor", "protect"),
}


@dataclass
class BotAgent:
    role: str
    persona: dict[str, Any]
    principal_id: str
    sessions: dict[str, Any]
    llm: LLMClient | None = None
    substrate: str = "agent"
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    agent_name: str = "bot"
    rng: random.Random = field(default_factory=random.Random)
    sleep_fn: Any = field(default=time.sleep)
    _iter: int = 0
    _all_principals: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if "game" not in self.sessions:
            raise ValueError("BotAgent requires sessions['game']")
        if self.llm is None:
            self.llm = LLMClient()
        self.persona = dict(self.persona)
        self.persona.setdefault("principal_id", self.principal_id)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self.log_dir / f"{self.agent_name}.ndjson"

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------
    def scan_all(self) -> dict[str, list[dict]]:
        """Full-scan every session this bot owns. Returns {space_key: messages}."""
        out: dict[str, list[dict]] = {}
        for key, session in self.sessions.items():
            space_id = getattr(session, "current_space_id", None)
            if not space_id:
                logger.debug("session %s has no bound space yet", key)
                out[key] = []
                continue
            try:
                result = session.scan_full(space_id)
            except Exception as exc:  # transient transport
                logger.warning("scan_full(%s) failed: %s", key, exc)
                out[key] = []
                continue
            out[key] = list(result.get("messages", []))
        self._refresh_principals(out.get("game", []))
        return out

    def _refresh_principals(self, game_messages: list[dict]) -> None:
        for m in game_messages:
            payload = m.get("payload") or {}
            if isinstance(payload, dict) and payload.get("kind") == "lobby-join":
                pid = payload.get("principal_id")
                if isinstance(pid, str):
                    self._all_principals.add(pid)
            sender = m.get("senderId")
            if isinstance(sender, str):
                self._all_principals.add(sender)

    # ------------------------------------------------------------------
    # Phase derivation
    # ------------------------------------------------------------------
    def _latest_phase(self, scans: dict[str, list[dict]]) -> Optional[dict]:
        """Latest unmatched phase-start across every space this bot can see.

        Night phases are posted in the role subspace (coven / detective-
        notebook / doctor-notebook), not in the public game space — so a
        bot that only consults `scans["game"]` would never see them and
        would skip its night action. Walk every scan and return the
        latest phase-start INTENT by timestamp.
        """
        latest: Optional[dict] = None
        latest_ts = -1
        for msgs in scans.values():
            cand = current_phase(msgs)
            if cand is None:
                continue
            ts = cand.get("timestamp", 0) or 0
            if ts > latest_ts:
                latest = cand
                latest_ts = ts
        return latest

    # ------------------------------------------------------------------
    # Action gating
    # ------------------------------------------------------------------
    def should_act(self, phase: Optional[dict], scans: dict[str, list[dict]] | None = None) -> Optional[str]:
        if phase is None:
            return None
        scans = scans or {}
        game_msgs = scans.get("game", [])
        p = (phase.get("payload") or {}).get("phase", "")

        if p.endswith("-discussion"):
            if already_acted_this_phase(game_msgs, phase, self.principal_id, "discussion-utterance"):
                return None
            freq = float(self.persona.get("speaking_frequency", 0.5))
            if self.rng.random() > freq:
                return None
            return "discussion-utterance"

        if p.endswith("-vote"):
            if already_acted_this_phase(game_msgs, phase, self.principal_id, "vote"):
                return None
            return "vote"

        for role, (suffix, action_kind) in NIGHT_ACTION_BY_ROLE.items():
            if not (p.startswith("night-") and suffix in p):
                continue
            if self.role != role:
                return None
            subspace = scans.get(ROLE_SUBSPACE_KEY[role], [])
            if already_acted_this_phase(subspace, phase, self.principal_id, action_kind):
                return None
            return action_kind

        return None

    # ------------------------------------------------------------------
    # LLM decision
    # ------------------------------------------------------------------
    def decide(self, action_kind: str, scans: dict[str, list[dict]]) -> dict | None:
        phase = self._latest_phase(scans)
        if phase is None:
            return None

        living = extract_living_players(scans.get("game", []), self._all_principals)
        deaths = extract_deaths(scans.get("game", []))

        private_key = ROLE_SUBSPACE_KEY.get(self.role)
        private_msgs = scans.get(private_key, []) if private_key else []

        system = build_system_prompt(self.role, self.substrate, action_kind, self.persona)
        user = build_user_message(
            phase=phase,
            public_messages=scans.get("game", []),
            private_messages=private_msgs,
            action_kind=action_kind,
            living_players=living,
            dead_players=deaths,
        )

        temp = float(self.persona.get("temperature", 0.8))
        t0 = time.time()
        try:
            assert self.llm is not None
            raw = self.llm.complete(system=system, user=user, max_tokens=512, temperature=temp)
        except LLMError as exc:
            self._log_iter(phase, action_kind, scanned=len(scans.get("game", [])), error=str(exc))
            return None
        latency_ms = int((time.time() - t0) * 1000)

        decision = _parse_json_response(raw)
        if decision is None:
            self._log_iter(phase, action_kind, scanned=len(scans.get("game", [])),
                           error="json-parse-failed", llm_latency_ms=latency_ms)
            return None
        decision["_phase"] = phase
        decision["_action_kind"] = action_kind
        decision["_latency_ms"] = latency_ms
        return decision

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------
    def emit(self, decision: dict) -> dict | None:
        action_kind = decision.get("_action_kind")
        phase = decision.get("_phase") or {}
        phase_name = (phase.get("payload") or {}).get("phase")

        try:
            payload, target_session_key = self._build_payload(action_kind, decision, phase_name)
        except (ValidationError, ValueError) as exc:
            logger.warning("payload validation failed for %s: %s", action_kind, exc)
            self._log_iter(phase, action_kind, scanned=0, error=f"validation: {exc}")
            return None
        if payload is None:
            return None

        session = self.sessions.get(target_session_key) or self.sessions["game"]
        intent = session.intent(
            content=payload.get("content") or "",
            payload=payload,
        )
        try:
            session.post(intent, step=f"bot.{action_kind}")
        except Exception as exc:
            logger.warning("post failed: %s", exc)
            self._log_iter(phase, action_kind, scanned=0, error=f"post: {exc}",
                           llm_latency_ms=decision.get("_latency_ms"))
            return None

        self._log_iter(
            phase,
            action_kind,
            scanned=0,
            post_id=intent.get("intentId"),
            llm_latency_ms=decision.get("_latency_ms"),
            target=(payload or {}).get("target"),
            reasoning=(payload or {}).get("reasoning"),
            content=(payload or {}).get("content"),
        )
        return intent

    def _build_payload(
        self,
        action_kind: str | None,
        decision: dict,
        phase_name: str | None,
    ) -> tuple[dict | None, str]:
        """Translate LLM decision dict into a validated payload + target session key."""
        target_key = "game"

        if action_kind == "discussion-utterance":
            kind = decision.get("kind", "observation")
            text = decision.get("text") or ""
            target = decision.get("target")
            if kind == "accusation" and target:
                payload = AccusationPayload(
                    target=target, claim=text, content=text, phase=phase_name,
                ).model_dump(exclude_none=True)
            elif kind == "defense":
                payload = DefensePayload(
                    claim=text, content=text, phase=phase_name,
                ).model_dump(exclude_none=True)
            else:
                payload = ObservationPayload(content=text, phase=phase_name).model_dump(exclude_none=True)
            return payload, target_key

        if action_kind == "vote":
            target = decision.get("target")
            if not target:
                return None, target_key
            kind = decision.get("kind", "vote")
            if kind == "id-vote":
                claimed = decision.get("claimed_substrate", "agent")
                payload = IdVotePayload(
                    target=target,
                    claimed_substrate=claimed,
                    content=decision.get("rationale"),
                    phase=phase_name,
                ).model_dump(exclude_none=True)
            else:
                payload = VotePayload(
                    target=target,
                    content=decision.get("rationale"),
                    phase=phase_name,
                ).model_dump(exclude_none=True)
            return payload, target_key

        if action_kind == "coven-target":
            target = decision.get("target")
            if not target:
                return None, "mafia"
            kind = decision.get("kind", "coven-target")
            if kind == "coven-confirm":
                payload = CovenConfirmPayload(
                    content=decision.get("reasoning"), phase=phase_name,
                ).model_dump(exclude_none=True)
            else:
                payload = CovenTargetPayload(
                    target=target,
                    reasoning=decision.get("reasoning"),
                    content=decision.get("reasoning"),
                    phase=phase_name,
                ).model_dump(exclude_none=True)
            return payload, "mafia"

        if action_kind == "investigate":
            target = decision.get("target")
            if not target:
                return None, "detective"
            payload = InvestigatePayload(target=target, phase=phase_name).model_dump(exclude_none=True)
            return payload, "detective"

        if action_kind == "protect":
            target = decision.get("target")
            if not target:
                return None, "doctor"
            payload = ProtectPayload(
                target=target,
                self_protect=(target == self.principal_id),
                phase=phase_name,
            ).model_dump(exclude_none=True)
            return payload, "doctor"

        return None, target_key

    # ------------------------------------------------------------------
    # Post-game substrate guesses
    # ------------------------------------------------------------------
    def post_substrate_guesses(self, reveal: dict | list[dict] | None) -> list[dict]:
        """Issue one substrate-guess INTENT per other living principal.

        Accepts the reveal payload (with role/substrate assignments) or just
        the full game-message list. Uses the LLM to decide guesses.
        """
        scans = self.scan_all()
        game_msgs = scans.get("game", [])
        targets = [p for p in self._all_principals if p != self.principal_id]
        if not targets:
            return []

        phase = current_phase(game_msgs) or {}
        system = build_system_prompt(self.role, self.substrate, "substrate-guess", self.persona)
        reveal_blob = json.dumps(reveal or {}, default=str)[:2000]
        user = build_user_message(
            phase=phase,
            public_messages=game_msgs,
            private_messages=[{"payload": {"kind": "reveal", "content": reveal_blob}, "senderId": "host", "type": "COMPLETE"}],
            action_kind="substrate-guess",
            living_players=self._all_principals,
            dead_players=extract_deaths(game_msgs),
        ) + f"\n\nTargets: {', '.join(sorted(targets))}"

        try:
            assert self.llm is not None
            raw = self.llm.complete(system=system, user=user, max_tokens=1024, temperature=0.5)
        except LLMError as exc:
            logger.warning("substrate-guess LLM call failed: %s", exc)
            return []

        guesses = _parse_json_response(raw)
        if not isinstance(guesses, list):
            logger.warning("substrate-guess output was not a list: %r", str(guesses)[:200])
            return []

        session = self.sessions["game"]
        emitted: list[dict] = []
        for g in guesses:
            try:
                payload = SubstrateGuessPayload(
                    target=g["target_principal"],
                    guess=g["guess"],
                    confidence=float(g.get("confidence", 0.5)),
                    reasoning=g.get("reasoning"),
                ).model_dump(exclude_none=True)
            except (KeyError, ValidationError, TypeError, ValueError) as exc:
                logger.warning("skip bad substrate-guess entry %r: %s", g, exc)
                continue
            intent = session.intent(content=payload.get("reasoning") or "", payload=payload)
            try:
                session.post(intent, step="bot.substrate-guess")
                emitted.append(intent)
            except Exception as exc:
                logger.warning("substrate-guess post failed: %s", exc)
        return emitted

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run_loop(self) -> None:
        interval = float(self.persona.get("scan_interval_s", 1.0))
        while True:
            self._iter += 1
            scans = self.scan_all()
            game_msgs = scans.get("game", [])

            game_end = next(
                (m for m in game_msgs
                 if (m.get("payload") or {}).get("kind") == "game-end"),
                None,
            )
            if game_end is not None:
                reveal = next(
                    (m for m in game_msgs
                     if (m.get("payload") or {}).get("kind") == "reveal"),
                    None,
                )
                self.post_substrate_guesses((reveal or {}).get("payload"))
                self._log_iter(None, "game-end", scanned=len(game_msgs))
                return

            phase = self._latest_phase(scans)
            action_kind = self.should_act(phase, scans)
            if action_kind:
                decision = self.decide(action_kind, scans)
                if decision is not None:
                    self.emit(decision)
            else:
                self._log_iter(phase, None, scanned=len(game_msgs))
            self.sleep_fn(interval)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def _log_iter(
        self,
        phase: dict | None,
        action: str | None,
        *,
        scanned: int,
        post_id: str | None = None,
        llm_latency_ms: int | None = None,
        error: str | None = None,
        target: str | None = None,
        reasoning: str | None = None,
        content: str | None = None,
    ) -> None:
        entry = {
            "ts": time.time(),
            "iter": self._iter,
            "phase": ((phase or {}).get("payload") or {}).get("phase"),
            "scanned_count": scanned,
            "action": action,
            "llm_latency_ms": llm_latency_ms,
            "post_id": post_id,
            "error": error,
            "target": target,
            "reasoning": reasoning,
            "content": content,
        }
        try:
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({k: v for k, v in entry.items() if v is not None}) + "\n")
        except OSError as exc:
            logger.debug("log write failed: %s", exc)


# ----------------------------------------------------------------------
# JSON parsing helpers
# ----------------------------------------------------------------------
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _parse_json_response(raw: str) -> Any:
    """Best-effort JSON extraction from an LLM string response."""
    if raw is None:
        return None
    text = raw.strip()
    # Strip code fences if present.
    m = _JSON_BLOCK_RE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: find the first JSON-ish opener (whichever comes first in text).
    candidates = []
    for opener, closer in (("{", "}"), ("[", "]")):
        idx = text.find(opener)
        if idx != -1:
            candidates.append((idx, opener, closer))
    candidates.sort()
    for _, opener, closer in candidates:
        start = text.find(opener)
        end = text.rfind(closer)
        if end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


__all__ = ["BotAgent", "LLMClient"]
