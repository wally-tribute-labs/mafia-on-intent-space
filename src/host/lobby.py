"""Lobby formation in commons.

Per PROTOCOL_MAPPING.md §Lobby formation: host posts an `INTENT(kind=
lobby-open)` in commons; players reply with `PROMISE(kind=lobby-join)`
nested under that intent. Selection is first-N-by-sequence-number — no
central queue.

Server quirk: per docs/RISKS.md, the first INTENT to commons must use
`parent_id="commons"` literally (the SDK's `commons_space_id` may be
`None` after initial signup).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.game.schemas import LobbyJoinPayload

from ._validate import validate

logger = logging.getLogger(__name__)


COMMONS_SPACE_ID = "commons"


@dataclass
class LobbyResult:
    lobby_intent_id: str
    players: list[str]
    deadline_reached: bool


def _payload(m: dict) -> dict:
    p = m.get("payload")
    return p if isinstance(p, dict) else {}


def _collect_joins(messages: list[dict], lobby_intent_id: str) -> list[str]:
    """Walk lobby-interior messages and return principals that PROMISE'd to join.

    Returns the joiner's HOME principal (from `payload.principal_id`), NOT
    the PROMISE's senderId — agents can have a different commons-scoped
    principal vs home-scoped principal, and the steward needs the home
    one when adding the agent to a new shared space.

    First-PROMISE-per-(home-principal) wins; later duplicates are ignored.
    """
    seen: set[str] = set()
    joined: list[str] = []
    for m in messages:
        if m.get("type") != "PROMISE":
            continue
        if m.get("intentId") != lobby_intent_id:
            continue
        payload = _payload(m)
        if payload.get("kind") != "lobby-join":
            continue
        try:
            validated = LobbyJoinPayload.model_validate(payload)
        except Exception:
            logger.debug("Discarding malformed lobby-join: %r", payload)
            continue
        principal = validated.principal_id
        if not isinstance(principal, str) or principal in seen:
            continue
        seen.add(principal)
        joined.append(principal)
    return joined


def run_lobby(
    session,
    player_count: int,
    deadline_s: float,
    *,
    seats: int | None = None,
    poll_interval_s: float = 1.0,
    game_id: str | None = None,
    now: callable = time.time,
    sleep: callable = time.sleep,
) -> LobbyResult:
    """Open a lobby in commons and wait for `player_count` joins.

    Returns when `len(joined) >= player_count` OR the wall-clock deadline
    is reached. The host does not DECLINE late joiners here; that's a
    `phase-end`-style cleanup the caller can choose to do.

    `seats` defaults to `player_count` (visible in the lobby payload).
    """
    if player_count < 0:
        raise ValueError("player_count must be non-negative")

    seats_value = player_count if seats is None else seats
    deadline_ms = int(deadline_s * 1000)

    open_payload_in: dict = {
        "kind": "lobby-open",
        "content": f"Mafia game forming — {seats_value} seats, hidden substrate mix",
        "seats": seats_value,
        "deadline_ms": deadline_ms,
    }
    # Per-run tag so bots launched by this orchestrator can distinguish
    # this lobby-open from stale ones still visible in commons. Reusing
    # `game_id` (already on BasePayload) avoids a schema change.
    if isinstance(game_id, str) and game_id:
        open_payload_in["game_id"] = game_id
    open_payload = validate(open_payload_in)
    intent = session.intent(
        content=open_payload.get("content") or "lobby-open",
        parent_id=COMMONS_SPACE_ID,
        payload=open_payload,
    )
    posted = session.post_and_confirm(
        intent,
        step="host.lobby-open",
        confirm_space_id=COMMONS_SPACE_ID,
    )
    lobby_intent_id = posted.get("intentId") or intent.get("intentId")
    if not isinstance(lobby_intent_id, str):
        raise RuntimeError(f"lobby-open did not return an intentId; got {posted!r}")

    logger.info("lobby-open posted: intentId=%s seats=%d deadline_s=%.1f",
                lobby_intent_id, seats_value, deadline_s)

    deadline_t = now() + deadline_s
    joined: list[str] = []
    while True:
        # lobby-join PROMISEs are posted with parent_id=lobby_intent_id, so
        # they live inside the lobby-open INTENT's interior space, not in
        # commons proper. We scan that interior to collect joins.
        try:
            scan = session.scan_full(lobby_intent_id)
        except Exception as exc:
            raise RuntimeError(
                f"lobby interior scan_full failed during lobby: {exc}"
            ) from exc
        joined = _collect_joins(scan.get("messages", []), lobby_intent_id)
        if len(joined) >= player_count:
            logger.info("lobby filled: %d/%d", len(joined), player_count)
            return LobbyResult(
                lobby_intent_id=lobby_intent_id,
                players=sorted(joined),
                deadline_reached=False,
            )
        if now() >= deadline_t:
            logger.info(
                "lobby deadline reached with %d/%d players",
                len(joined),
                player_count,
            )
            return LobbyResult(
                lobby_intent_id=lobby_intent_id,
                players=sorted(joined),
                deadline_reached=True,
            )
        sleep(min(poll_interval_s, max(0.0, deadline_t - now())))
