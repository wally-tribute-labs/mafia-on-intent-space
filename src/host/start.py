"""Game start: roles, commitments, shared spaces, role-card delivery.

Per PROTOCOL_MAPPING.md §Role and substrate assignment + §Private faction
subspaces:
1. Host generates `(role_i, substrate_i, salt_i)` per player and computes
   commitments `sha256(value || salt)`.
2. Host requests the public game space from the steward (host's home
   space → steward COMPLETE returns access materials).
3. Host posts `game-init` INTENT in the game space carrying the
   commitments.
4. Host requests faction subspaces (coven for mafia; detective notebook;
   doctor notebook) — only those needed.
5. Host requests one private notebook per player (host + that player) and
   posts a role-card INTENT there carrying the player's `(role, salt,
   substrate, substrate_salt)`. The steward's auto-invitation places
   access materials in each player's home space.

Subspace access materials (endpoint, station_token, audience) returned by
the steward are captured into `GameState.subspace_access` keyed by the
shared space id. The phase loop uses these to call `session.connect_to(...)`
each time it switches between game / coven / detective / doctor spaces.
The host's own home-space materials are also captured (looked up from the
session's `known_stations()` registry) so the host can switch back to home
to post the final summary in commons.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from src.game.commit_reveal import commit, generate_salts

from ._validate import validate

logger = logging.getLogger(__name__)


# Role compositions per docs/GAME_RULES.md §Setup. Anything else falls
# back to "as many mafia as makes the count divide ~1/3, then 1 detective,
# 1 doctor (if 7+), rest villagers." We keep the canonical 5/7/9 explicit.
_ROLE_COMPOSITIONS: dict[int, list[str]] = {
    5: ["mafia", "detective", "villager", "villager", "villager"],
    7: ["mafia", "mafia", "detective", "doctor", "villager", "villager", "villager"],
    9: ["mafia", "mafia", "mafia", "detective", "doctor",
        "villager", "villager", "villager", "villager"],
}


def _compose_roles(n: int) -> list[str]:
    if n in _ROLE_COMPOSITIONS:
        return list(_ROLE_COMPOSITIONS[n])
    if n <= 0:
        return []
    mafia = max(1, n // 3)
    detectives = 1 if n >= 5 else 0
    doctors = 1 if n >= 7 else 0
    villagers = n - mafia - detectives - doctors
    if villagers < 0:
        mafia += villagers
        villagers = 0
    return (
        ["mafia"] * mafia
        + ["detective"] * detectives
        + ["doctor"] * doctors
        + ["villager"] * villagers
    )


@dataclass
class PlayerCard:
    principal_id: str
    role: str
    substrate: str
    salt_hex: str
    role_commitment: str
    substrate_commitment: str
    private_space_id: str | None = None


@dataclass
class GameState:
    """Everything the host needs to drive a game.

    `host_principal_id` is the host's principal; `host_home_space_id` is
    where the steward request was posted. `game_space_id` is the public
    game space; `coven_id`/`detective_id`/`doctor_id` are subspace ids
    (or None if that role isn't in this game).
    """

    host_principal_id: str
    host_home_space_id: str
    game_space_id: str
    coven_id: str | None
    detective_id: str | None
    doctor_id: str | None
    role_truth: dict[str, str]
    substrate_truth: dict[str, str]
    salts: dict[str, str]
    role_commitments: dict[str, str]
    substrate_commitments: dict[str, str]
    players: list[str]
    player_cards: list[PlayerCard]
    game_init_intent_id: str
    lobby_intent_id: str | None = None
    multiplier: float = 1.0
    extras: dict[str, Any] = field(default_factory=dict)
    # Access materials captured at provisioning time, keyed by space_id.
    # Each value is {"endpoint", "station_token", "audience"}. Used by
    # phase_loop._connect_to_space to switch the live session between
    # game/coven/detective/doctor/notebook spaces.
    subspace_access: dict[str, dict[str, str]] = field(default_factory=dict)


def _extract_access(payload: dict) -> dict[str, str] | None:
    """Pull (endpoint, station_token, audience, principal_id) out of a steward COMPLETE.

    Returns None if any required field is missing — the caller can decide
    whether to raise (live) or skip (test/empty). `principal_id` is optional
    (may be absent in some COMPLETE payloads or test fixtures); when present
    it's the audience-scoped principal the host should authenticate as.
    """
    endpoint = payload.get("itp_endpoint")
    token = payload.get("station_token")
    audience = payload.get("audience")
    if isinstance(endpoint, str) and isinstance(token, str) and isinstance(audience, str):
        out: dict[str, str] = {
            "endpoint": endpoint,
            "station_token": token,
            "audience": audience,
        }
        principal = payload.get("principal_id") or payload.get("requester_principal_id")
        if isinstance(principal, str):
            out["principal_id"] = principal
        return out
    return None


def _connect_with_access(session, access: dict[str, str], *, sender_id: str, purpose: str) -> None:
    """Switch the session's active station using captured access materials.

    Prefers `access["principal_id"]` (the audience-scoped principal embedded
    in the steward's COMPLETE) over the host's home principal — each shared
    space has its own per-audience principal and the token will only auth
    against that one. Falls back to `sender_id` for legacy access entries.
    """
    connect_to = getattr(session, "connect_to", None)
    if not callable(connect_to):
        return
    try:
        connect_to(
            endpoint=access["endpoint"],
            station_token=access["station_token"],
            audience=access["audience"],
            sender_id=access.get("principal_id") or sender_id,
        )
    except Exception as exc:
        raise RuntimeError(f"connect_to failed for {purpose}: {exc}") from exc


def _lookup_known_station(session, *, space_id: str | None = None,
                          audience_suffix: str | None = None) -> dict[str, str] | None:
    """Find an entry in session.known_stations() by space_id or audience suffix.

    Used to recover access materials for the host's own home space (bound at
    enrollment time) and for commons (bound at signup time). Returns None if
    no match or if the session lacks a known_stations() method.

    `audience_suffix` matches against the entry's `audience` (e.g.
    `"/space/commons"` to find the commons station). The endpoint URL is NOT
    matched directly because it varies in shape (`.../spaces/<id>/itp`) and
    the audience is the canonical identifier the server checks.
    """
    fetcher = getattr(session, "known_stations", None)
    if not callable(fetcher):
        return None
    try:
        stations = fetcher()
    except Exception:
        return None
    if not isinstance(stations, list):
        return None
    for entry in stations:
        if not isinstance(entry, dict):
            continue
        endpoint = entry.get("endpoint")
        token = entry.get("stationToken")
        audience = entry.get("audience")
        if not (isinstance(endpoint, str) and isinstance(token, str) and isinstance(audience, str)):
            continue
        matches_space = space_id is not None and entry.get("spaceId") == space_id
        matches_suffix = audience_suffix is not None and audience.endswith(audience_suffix)
        if matches_space or matches_suffix:
            out: dict[str, str] = {
                "endpoint": endpoint,
                "station_token": token,
                "audience": audience,
            }
            principal = entry.get("principalId")
            if isinstance(principal, str):
                out["principal_id"] = principal
            return out
    return None


def _request_shared_space(
    session,
    *,
    host_home_space_id: str,
    participant_principals: list[str],
    purpose: str,
    promise_wait_s: float,
    complete_wait_s: float,
) -> dict:
    """Run the steward provisioning dance for a shared space.

    Returns the steward's COMPLETE payload (carries `shared_space_id`,
    plus access materials).
    """
    intent = session.intent(
        content=f"Provision shared space: {purpose}",
        parent_id=host_home_space_id,
        payload={
            "requestedSpace": {
                "kind": "shared",
                "participant_principals": list(participant_principals),
            },
        },
    )
    try:
        posted = session.post_and_confirm(
            intent,
            step=f"host.provision.{purpose}",
            confirm_space_id=host_home_space_id,
        )
    except Exception as exc:
        raise RuntimeError(f"steward request failed for {purpose}: {exc}") from exc
    request_intent_id = posted.get("intentId") or intent.get("intentId")
    if not isinstance(request_intent_id, str):
        raise RuntimeError(f"provisioning intent missing intentId: {posted!r}")

    try:
        promise = session.wait_for_promise(
            request_intent_id, wait_seconds=promise_wait_s
        )
    except Exception as exc:
        raise RuntimeError(f"steward PROMISE timeout for {purpose}: {exc}") from exc
    promise_id = promise.get("promiseId")
    if not isinstance(promise_id, str):
        raise RuntimeError(f"provisioning PROMISE missing promiseId: {promise!r}")

    try:
        session.post_and_confirm(
            session.accept(promise_id=promise_id, parent_id=request_intent_id),
            step=f"host.accept.{purpose}",
            confirm_space_id=request_intent_id,
        )
    except Exception as exc:
        raise RuntimeError(f"steward ACCEPT failed for {purpose}: {exc}") from exc

    try:
        complete = session.wait_for_complete(
            request_intent_id,
            promise_id=promise_id,
            wait_seconds=complete_wait_s,
        )
    except Exception as exc:
        raise RuntimeError(f"steward COMPLETE timeout for {purpose}: {exc}") from exc
    payload = complete.get("payload") or {}
    if not isinstance(payload, dict) or "shared_space_id" not in payload:
        raise RuntimeError(f"steward COMPLETE missing shared_space_id: {complete!r}")
    return payload


def start_game(
    session,
    players: list[str],
    player_substrates: dict[str, str],
    multiplier: float,
    *,
    rng: random.Random | None = None,
    promise_wait_s: float = 30.0,
    complete_wait_s: float = 30.0,
    skip_role_card_delivery: bool = False,
) -> GameState:
    """Provision the game and return everything needed to drive the loop.

    Caller is responsible for ensuring `session` is connected to the
    host's home space (i.e. `current_space_id == host_home_space_id`).

    `player_substrates` maps principal_id -> "human"|"agent". Roles are
    randomly assigned from the canonical composition for the player
    count.

    `skip_role_card_delivery=True` is for tests — provisions only the
    public game space and skips faction + per-player notebooks.
    """
    if rng is None:
        rng = random.Random()

    identity = session.identity() or {}
    host_principal_id = identity.get("principalId") or identity.get("principal_id")
    host_home_space_id = (
        getattr(session, "current_space_id", None)
        or identity.get("currentSpaceId")
    )
    if not isinstance(host_principal_id, str) or not isinstance(host_home_space_id, str):
        raise RuntimeError(
            "start_game requires session.identity().principalId and "
            "session.current_space_id to be set"
        )

    if any(p not in player_substrates for p in players):
        missing = [p for p in players if p not in player_substrates]
        raise ValueError(f"player_substrates missing entries: {missing}")

    # ---- Roles + substrates + salts ----
    roles = _compose_roles(len(players))
    rng.shuffle(roles)

    salt_bytes = generate_salts(len(players))
    salts: dict[str, str] = {}
    role_commitments: dict[str, str] = {}
    substrate_commitments: dict[str, str] = {}
    role_truth: dict[str, str] = {}
    substrate_truth: dict[str, str] = {}
    cards: list[PlayerCard] = []
    for principal, role, salt in zip(players, roles, salt_bytes):
        salt_hex = salt.hex()
        substrate = player_substrates[principal]
        role_truth[principal] = role
        substrate_truth[principal] = substrate
        salts[principal] = salt_hex
        role_c = commit(role, salt)
        substrate_c = commit(substrate, salt)
        role_commitments[principal] = role_c
        substrate_commitments[principal] = substrate_c
        cards.append(
            PlayerCard(
                principal_id=principal,
                role=role,
                substrate=substrate,
                salt_hex=salt_hex,
                role_commitment=role_c,
                substrate_commitment=substrate_c,
            )
        )

    # ---- Capture access materials we already hold (home + commons) ----
    subspace_access: dict[str, dict[str, str]] = {}
    home_access = _lookup_known_station(session, space_id=host_home_space_id)
    if home_access is not None:
        subspace_access[host_home_space_id] = home_access
    commons_access = _lookup_known_station(session, audience_suffix="/space/commons")
    if commons_access is not None:
        subspace_access["commons"] = commons_access

    # ---- Public game space ----
    if players:
        game_payload = _request_shared_space(
            session,
            host_home_space_id=host_home_space_id,
            participant_principals=[host_principal_id, *players],
            purpose="game-space",
            promise_wait_s=promise_wait_s,
            complete_wait_s=complete_wait_s,
        )
        game_space_id = game_payload["shared_space_id"]
        game_access = _extract_access(game_payload)
        if game_access is not None:
            subspace_access[game_space_id] = game_access
            _connect_with_access(
                session, game_access,
                sender_id=host_principal_id,
                purpose="game-space",
            )
    else:
        game_space_id = host_home_space_id  # 0-player smoke; nothing real is posted

    # ---- game-init INTENT in game space ----
    init_payload = validate(
        {
            "kind": "game-init",
            "content": "Game init: roles and substrates committed.",
            "game_id": game_space_id,
            "players": list(players),
            "role_commitments": [role_commitments[p] for p in players],
            "substrate_commitments": [substrate_commitments[p] for p in players],
        }
    )
    game_init_intent_id = ""
    if players:
        init_intent = session.intent(
            content=init_payload.get("content") or "game-init",
            parent_id=game_space_id,
            payload=init_payload,
        )
        posted = session.post_and_confirm(
            init_intent, step="host.game-init", confirm_space_id=game_space_id
        )
        game_init_intent_id = posted.get("intentId") or init_intent.get("intentId") or ""

    # ---- Faction subspaces ----
    coven_id: str | None = None
    detective_id: str | None = None
    doctor_id: str | None = None
    if not skip_role_card_delivery and players:
        mafia = [p for p in players if role_truth[p] == "mafia"]
        detective = [p for p in players if role_truth[p] == "detective"]
        doctor = [p for p in players if role_truth[p] == "doctor"]

        if mafia:
            coven = _request_shared_space(
                session,
                host_home_space_id=host_home_space_id,
                participant_principals=[host_principal_id, *mafia],
                purpose="coven",
                promise_wait_s=promise_wait_s,
                complete_wait_s=complete_wait_s,
            )
            coven_id = coven["shared_space_id"]
            coven_access = _extract_access(coven)
            if coven_access is not None:
                subspace_access[coven_id] = coven_access
                _connect_with_access(
                    session, coven_access,
                    sender_id=host_principal_id,
                    purpose="coven",
                )
            # Seed the coven with the canonical mafia roster so MafiaBots
            # have a reliable teammate set from turn one (see
            # docs/PROTOCOL_MAPPING.md §Private faction subspaces and
            # src/bots/mafia.py::_refresh_teammates).
            membership_payload = validate(
                {
                    "kind": "coven-membership",
                    "content": "Coven members for this game.",
                    "game_id": game_space_id,
                    "members": list(mafia),
                    "host": host_principal_id,
                }
            )
            membership_intent = session.intent(
                content=membership_payload.get("content") or "coven-membership",
                parent_id=coven_id,
                payload=membership_payload,
            )
            try:
                session.post_and_confirm(
                    membership_intent,
                    step="host.coven-membership",
                    confirm_space_id=coven_id,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"coven-membership post failed: {exc}"
                ) from exc
        if detective:
            det = _request_shared_space(
                session,
                host_home_space_id=host_home_space_id,
                participant_principals=[host_principal_id, *detective],
                purpose="detective",
                promise_wait_s=promise_wait_s,
                complete_wait_s=complete_wait_s,
            )
            detective_id = det["shared_space_id"]
            det_access = _extract_access(det)
            if det_access is not None:
                subspace_access[detective_id] = det_access
                _connect_with_access(
                    session, det_access,
                    sender_id=host_principal_id,
                    purpose="detective",
                )
        if doctor:
            doc = _request_shared_space(
                session,
                host_home_space_id=host_home_space_id,
                participant_principals=[host_principal_id, *doctor],
                purpose="doctor",
                promise_wait_s=promise_wait_s,
                complete_wait_s=complete_wait_s,
            )
            doctor_id = doc["shared_space_id"]
            doc_access = _extract_access(doc)
            if doc_access is not None:
                subspace_access[doctor_id] = doc_access
                _connect_with_access(
                    session, doc_access,
                    sender_id=host_principal_id,
                    purpose="doctor",
                )

        # ---- Per-player private notebooks for role-card delivery ----
        for card in cards:
            try:
                note = _request_shared_space(
                    session,
                    host_home_space_id=host_home_space_id,
                    participant_principals=[host_principal_id, card.principal_id],
                    purpose=f"player-{card.principal_id[-12:]}",
                    promise_wait_s=promise_wait_s,
                    complete_wait_s=complete_wait_s,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"per-player notebook provisioning failed for {card.principal_id}: {exc}"
                ) from exc
            card.private_space_id = note["shared_space_id"]
            note_access = _extract_access(note)
            if note_access is not None:
                subspace_access[card.private_space_id] = note_access
                _connect_with_access(
                    session, note_access,
                    sender_id=host_principal_id,
                    purpose=f"player-notebook-{card.principal_id[-8:]}",
                )
            role_card_payload = validate(
                {
                    "kind": "role-card",
                    "content": "Your role-card. Verify against the public game-init commitments.",
                    "game_id": game_space_id,
                    "role": card.role,
                    "substrate": card.substrate,
                    "salt_hex": card.salt_hex,
                    "role_commitment": card.role_commitment,
                    "substrate_commitment": card.substrate_commitment,
                    "principal_id": card.principal_id,
                }
            )
            note_intent = session.intent(
                content=role_card_payload.get("content") or "role-card",
                parent_id=card.private_space_id,
                payload=role_card_payload,
            )
            try:
                session.post_and_confirm(
                    note_intent,
                    step=f"host.role-card.{card.principal_id[-8:]}",
                    confirm_space_id=card.private_space_id,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"role-card delivery failed for {card.principal_id}: {exc}"
                ) from exc

    return GameState(
        host_principal_id=host_principal_id,
        host_home_space_id=host_home_space_id,
        game_space_id=game_space_id,
        coven_id=coven_id,
        detective_id=detective_id,
        doctor_id=doctor_id,
        role_truth=role_truth,
        substrate_truth=substrate_truth,
        salts=salts,
        role_commitments=role_commitments,
        substrate_commitments=substrate_commitments,
        players=list(players),
        player_cards=cards,
        game_init_intent_id=game_init_intent_id,
        multiplier=multiplier,
        subspace_access=subspace_access,
    )
