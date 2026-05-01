"""Day/night phase loop for the host.

Per docs/GAME_RULES.md §Phase sequence + §Phase durations:

  Day-N Discussion → Day-N Vote (tally inline in phase-end)
  → Night-N Mafia + Night-N Detective + Night-N Doctor (in subspaces)
  → resolve kill+protect → morning announcement (deaths)
  → check winner; loop or post game-end + reveal

Phase durations:
  - Day Discussion: 60s
  - Day Vote:       30s
  - Night phases:   30s each
  - Tally / Morning: instant (carried by the phase-end of the prior phase
                              and a `death` INTENT in the game space)

All durations are scaled by `state.multiplier`.

Phase-end → phase-start linkage is via
`payload.ends_phase_intent_id` (per A1 reconciliation).

The host posts COMPLETE on the phase-start INTENT for phase-end.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from src.game.night_resolve import resolve_night
from src.game.tally import compute_tally, resolve_id_votes, resolve_lynch
from src.game.winner import compute_winner

from ._validate import validate
from .start import GameState

logger = logging.getLogger(__name__)


# Default phase durations in seconds (pre-multiplier).
DAY_DISCUSSION_S = 60.0
DAY_VOTE_S = 30.0
NIGHT_S = 30.0


def _payload(m: dict) -> dict:
    p = m.get("payload")
    return p if isinstance(p, dict) else {}


def _post_phase_start(
    session,
    *,
    space_id: str,
    phase: str,
    deadline_ms: int,
    living: list[str] | None,
    deaths: list[dict] | None,
) -> str:
    payload = validate(
        {
            "kind": "phase-start",
            "content": f"phase-start: {phase}",
            "phase": phase,
            "deadline_ms": deadline_ms,
            "living": living,
            "deaths": deaths,
        }
    )
    intent = session.intent(
        content=payload.get("content") or phase,
        parent_id=space_id,
        payload=payload,
    )
    posted = session.post_and_confirm(
        intent, step=f"host.phase-start.{phase}", confirm_space_id=space_id
    )
    intent_id = posted.get("intentId") or intent.get("intentId")
    if not isinstance(intent_id, str):
        raise RuntimeError(f"phase-start did not return intentId: {posted!r}")
    return intent_id


def _post_phase_end(
    session,
    *,
    space_id: str,
    phase_intent_id: str,
    next_phase: str | None,
    promise_id: str | None = None,
    extras: dict | None = None,
) -> dict:
    """Post the phase-end COMPLETE.

    The protocol calls for COMPLETE on a PROMISE; in our pattern the
    "phase" PROMISE is the host's own self-promise to advance phases.
    Since we're not always issuing a separate PROMISE, we use the
    COMPLETE builder with the phase intent's id as `promise_id` (the
    SDK only requires a string here — it doesn't check semantics).
    """
    payload_dict: dict = {
        "kind": "phase-end",
        "content": f"phase-end: {phase_intent_id}",
        "ends_phase_intent_id": phase_intent_id,
        "next_phase": next_phase,
    }
    if extras:
        payload_dict.update(extras)
    payload = validate(payload_dict)
    complete = session.complete(
        promise_id=promise_id or phase_intent_id,
        parent_id=space_id,
        summary=f"phase-end {phase_intent_id}",
        payload=payload,
    )
    posted = session.post_and_confirm(
        complete, step="host.phase-end", confirm_space_id=space_id
    )
    return posted


def _post_death(
    session,
    *,
    space_id: str,
    target: str,
    cause: str,
) -> dict:
    payload = validate(
        {
            "kind": "death",
            "content": f"{target} dies ({cause})",
            "target": target,
            "cause": cause,
        }
    )
    intent = session.intent(
        content=payload["content"],
        parent_id=space_id,
        payload=payload,
    )
    return session.post_and_confirm(
        intent, step=f"host.death.{target[-8:]}", confirm_space_id=space_id
    )


def _post_game_end(
    session,
    *,
    space_id: str,
    winner: str,
) -> dict:
    payload = validate(
        {
            "kind": "game-end",
            "content": f"Game over — {winner} win",
            "winner": winner,
        }
    )
    intent = session.intent(
        content=payload["content"],
        parent_id=space_id,
        payload=payload,
    )
    return session.post_and_confirm(
        intent, step="host.game-end", confirm_space_id=space_id
    )


def _post_reveal(
    session,
    *,
    state: GameState,
) -> dict:
    payload = validate(
        {
            "kind": "reveal",
            "content": "Reveal: roles, substrates, and salts.",
            "role_assignments": dict(state.role_truth),
            "substrate_assignments": dict(state.substrate_truth),
            "salts": dict(state.salts),
        }
    )
    complete = session.complete(
        promise_id=state.game_init_intent_id,
        parent_id=state.game_space_id,
        summary="reveal",
        payload=payload,
    )
    return session.post_and_confirm(
        complete, step="host.reveal", confirm_space_id=state.game_space_id
    )


def _post_summary_to_commons(
    session,
    *,
    state: GameState,
    winner: str,
    duration_ms: int,
    final_living: list[str],
) -> dict:
    """Mirror reveal into commons for cross-game reputation (per A3 spec).

    `parent_id` references the lobby-open INTENT in commons. If
    `state.lobby_intent_id` is None the summary is parented to "commons"
    as a fallback (still indexable by spectator scans).
    """
    payload = validate(
        {
            "kind": "summary",
            "content": f"Game {state.game_space_id}: {winner} win",
            "summary_text": f"Game {state.game_space_id} ended. Winner: {winner}.",
            "winners": winner,
            "game_id": state.game_space_id,
            "data": {
                "winner": winner,
                "role_assignments": dict(state.role_truth),
                "substrate_assignments": dict(state.substrate_truth),
                "duration_ms": duration_ms,
                "final_living": list(final_living),
            },
        }
    )
    parent = state.lobby_intent_id or "commons"
    intent = session.intent(
        content=payload["content"],
        parent_id=parent,
        payload=payload,
    )
    # The summary INTENT lives in `parent`'s interior (lobby-open intent
    # space when present, top-level commons otherwise). Confirming via a
    # commons scan would never see it — post_and_confirm matches by
    # parentId+intentId in the scanned interior.
    return session.post_and_confirm(
        intent, step="host.summary", confirm_space_id=parent
    )


def _connect_to_space(session, space_id: str, state: GameState | None = None) -> None:
    """Switch the session's active station to `space_id`.

    Resolution order:
      1. If `state.subspace_access` has materials for `space_id`, call the
         real `session.connect_to(...)`. This is the live path.
      2. Otherwise, if the session exposes `set_current_space(space_id)`,
         call it. This is the test-stub path.
      3. Otherwise, no-op (best-effort; the session was presumably already
         on the right station, or the caller doesn't care).
    """
    access = None
    if state is not None and isinstance(state.subspace_access, dict):
        access = state.subspace_access.get(space_id)
    connect_to = getattr(session, "connect_to", None)
    if access is not None and callable(connect_to):
        # Each station has its own audience-scoped principal_id (commons
        # has commons-principal, home has home-principal, each shared
        # space has its own). Use the access entry's principal_id when
        # present; only fall back to host_principal_id for legacy
        # entries that didn't capture one.
        sender = access.get("principal_id") or (
            state.host_principal_id if state is not None else None
        )
        try:
            connect_to(
                endpoint=access["endpoint"],
                station_token=access["station_token"],
                audience=access["audience"],
                sender_id=sender,
            )
            logger.info("_connect_to_space switched -> %s (audience=%s sender=%s)",
                        space_id, access["audience"], sender)
        except Exception as exc:
            logger.warning("connect_to %s failed: %s", space_id, exc)
        return
    logger.info("_connect_to_space: no access for %s; subspace_access keys=%s",
                space_id,
                sorted(state.subspace_access.keys()) if state is not None else "no-state")
    setter = getattr(session, "set_current_space", None)
    if callable(setter):
        setter(space_id)
        return


def _coven_targets_projection(messages: list[dict]) -> list[dict]:
    """Project coven-target INTENTs + matching coven-confirm PROMISEs.

    Each output dict is `{target, sender, intent_id, confirmed}` where
    `confirmed=True` if any coven-confirm PROMISE references the
    coven-target's `intentId`.
    """
    targets: list[dict] = []
    confirms_for: set[str] = set()
    for m in messages:
        if m.get("type") != "PROMISE":
            continue
        if _payload(m).get("kind") != "coven-confirm":
            continue
        intent_id = m.get("intentId")
        if isinstance(intent_id, str):
            confirms_for.add(intent_id)
    for m in messages:
        if m.get("type") != "INTENT":
            continue
        payload = _payload(m)
        if payload.get("kind") != "coven-target":
            continue
        intent_id = m.get("intentId")
        target = payload.get("target")
        if not isinstance(intent_id, str) or not isinstance(target, str):
            continue
        targets.append(
            {
                "target": target,
                "sender": m.get("senderId"),
                "intent_id": intent_id,
                "confirmed": intent_id in confirms_for,
            }
        )
    return targets


def _doctor_protect_projection(messages: list[dict]) -> dict | None:
    for m in messages:
        if m.get("type") != "INTENT":
            continue
        payload = _payload(m)
        if payload.get("kind") != "protect":
            continue
        target = payload.get("target")
        if isinstance(target, str):
            return {"target": target, "sender": m.get("senderId")}
    return None


def run_game_loop(
    session,
    state: GameState,
    *,
    max_days: int = 7,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> dict:
    """Run the day/night loop until a winner emerges.

    Returns `{winner, days_played, final_living, duration_ms}`.
    """
    started_at = now()
    living = set(state.players)
    days_played = 0

    if not state.players:
        winner = "draw"
        if state.game_init_intent_id and state.game_space_id != state.host_home_space_id:
            _post_game_end(session, space_id=state.game_space_id, winner=winner)
        duration_ms = int((now() - started_at) * 1000)
        _connect_to_space(session, "commons", state)
        _post_summary_to_commons(
            session,
            state=state,
            winner=winner,
            duration_ms=duration_ms,
            final_living=[],
        )
        return {
            "winner": winner,
            "days_played": 0,
            "final_living": [],
            "duration_ms": duration_ms,
        }

    while days_played < max_days:
        days_played += 1

        # ---- Day-N Discussion ----
        _connect_to_space(session, state.game_space_id, state)
        discussion_phase = f"day-{days_played}-discussion"
        discussion_dur = DAY_DISCUSSION_S * state.multiplier
        deadline_ms = int(discussion_dur * 1000)
        disc_id = _post_phase_start(
            session,
            space_id=state.game_space_id,
            phase=discussion_phase,
            deadline_ms=deadline_ms,
            living=sorted(living),
            deaths=None,
        )
        sleep(discussion_dur)
        _post_phase_end(
            session,
            space_id=state.game_space_id,
            phase_intent_id=disc_id,
            next_phase=f"day-{days_played}-vote",
        )

        # ---- Day-N Vote ----
        vote_phase = f"day-{days_played}-vote"
        vote_dur = DAY_VOTE_S * state.multiplier
        deadline_ms = int(vote_dur * 1000)
        vote_phase_intent = {
            "type": "INTENT",
            "intentId": "",  # filled by post
            "payload": {"kind": "phase-start", "phase": vote_phase},
        }
        vote_id = _post_phase_start(
            session,
            space_id=state.game_space_id,
            phase=vote_phase,
            deadline_ms=deadline_ms,
            living=sorted(living),
            deaths=None,
        )
        vote_phase_intent["intentId"] = vote_id
        sleep(vote_dur)

        scan = session.scan_full(state.game_space_id).get("messages", [])
        tally = compute_tally(vote_phase_intent, scan)
        lynched = resolve_lynch(tally)
        id_results = resolve_id_votes(scan, state.substrate_truth)
        substrate_locked = sorted({r["locked"] for r in id_results})

        _post_phase_end(
            session,
            space_id=state.game_space_id,
            phase_intent_id=vote_id,
            next_phase=f"night-{days_played}-mafia",
            extras={
                "tally": tally,
                "lynched": lynched,
                "id_votes": [
                    {k: v for k, v in r.items() if k != "actual"}
                    for r in id_results
                ],
                "substrate_locked": substrate_locked,
            },
        )

        if lynched and lynched in living:
            _post_death(
                session,
                space_id=state.game_space_id,
                target=lynched,
                cause="lynch",
            )
            living.discard(lynched)
            outcome = compute_winner(living, state.role_truth)
            if outcome is not None:
                duration_ms = int((now() - started_at) * 1000)
                _finalize(session, state, outcome, days_played, sorted(living), duration_ms)
                return {
                    "winner": outcome,
                    "days_played": days_played,
                    "final_living": sorted(living),
                    "duration_ms": duration_ms,
                }

        # ---- Night-N (mafia / detective / doctor in their subspaces) ----
        night_dur = NIGHT_S * state.multiplier
        deadline_ms = int(night_dur * 1000)

        if state.coven_id:
            _connect_to_space(session, state.coven_id, state)
            _post_phase_start(
                session,
                space_id=state.coven_id,
                phase=f"night-{days_played}-mafia",
                deadline_ms=deadline_ms,
                living=sorted(living),
                deaths=None,
            )
        if state.detective_id:
            _connect_to_space(session, state.detective_id, state)
            _post_phase_start(
                session,
                space_id=state.detective_id,
                phase=f"night-{days_played}-detective",
                deadline_ms=deadline_ms,
                living=sorted(living),
                deaths=None,
            )
        if state.doctor_id:
            _connect_to_space(session, state.doctor_id, state)
            _post_phase_start(
                session,
                space_id=state.doctor_id,
                phase=f"night-{days_played}-doctor",
                deadline_ms=deadline_ms,
                living=sorted(living),
                deaths=None,
            )

        sleep(night_dur)

        coven_targets: list[dict] = []
        doctor_protect: dict | None = None
        if state.coven_id:
            _connect_to_space(session, state.coven_id, state)
            coven_msgs = session.scan_full(state.coven_id).get("messages", [])
            coven_targets = _coven_targets_projection(coven_msgs)
        if state.doctor_id:
            _connect_to_space(session, state.doctor_id, state)
            doc_msgs = session.scan_full(state.doctor_id).get("messages", [])
            doctor_protect = _doctor_protect_projection(doc_msgs)

        outcome_dict = resolve_night(coven_targets, doctor_protect)

        # Post phase-end COMPLETE on each night phase-start IS not strictly
        # needed for state derivation (the morning death announcement marks
        # the night's resolution). For now we skip explicit night phase-end
        # COMPLETEs and announce in the game space.

        _connect_to_space(session, state.game_space_id, state)
        if outcome_dict["cause"] == "mafia" and outcome_dict["killed"]:
            _post_death(
                session,
                space_id=state.game_space_id,
                target=outcome_dict["killed"],
                cause="mafia",
            )
            living.discard(outcome_dict["killed"])
        elif outcome_dict["cause"] == "saved" and outcome_dict.get("killed") is None:
            target_text = "(target saved)"
            payload = validate(
                {
                    "kind": "death",
                    "content": f"Mafia attempt — {target_text}",
                    "target": state.players[0] if state.players else "unknown",
                    "cause": "saved",
                }
            )
            intent = session.intent(
                content=payload["content"],
                parent_id=state.game_space_id,
                payload=payload,
            )
            session.post_and_confirm(
                intent, step="host.death.saved", confirm_space_id=state.game_space_id
            )

        outcome = compute_winner(living, state.role_truth)
        if outcome is not None:
            duration_ms = int((now() - started_at) * 1000)
            _finalize(session, state, outcome, days_played, sorted(living), duration_ms)
            return {
                "winner": outcome,
                "days_played": days_played,
                "final_living": sorted(living),
                "duration_ms": duration_ms,
            }

    duration_ms = int((now() - started_at) * 1000)
    _finalize(session, state, "draw", days_played, sorted(living), duration_ms)
    return {
        "winner": "draw",
        "days_played": days_played,
        "final_living": sorted(living),
        "duration_ms": duration_ms,
    }


def _finalize(
    session,
    state: GameState,
    winner: str,
    days_played: int,
    final_living: list[str],
    duration_ms: int,
) -> None:
    """Post game-end + reveal in game space, then summary in commons."""
    _connect_to_space(session, state.game_space_id, state)
    _post_game_end(session, space_id=state.game_space_id, winner=winner)
    if state.game_init_intent_id:
        _post_reveal(session, state=state)
    _connect_to_space(session, "commons", state)
    _post_summary_to_commons(
        session,
        state=state,
        winner=winner,
        duration_ms=duration_ms,
        final_living=final_living,
    )
