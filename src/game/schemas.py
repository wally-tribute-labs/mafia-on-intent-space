"""Pydantic v2 payload schemas for Mafia-on-intent-space.

Every message body posted into the intent space carries one of these payloads
as JSON. `kind` is the dispatcher: bot scan loops route by `payload.kind`.

Field choices come from docs/PROTOCOL_MAPPING.md (taxonomy + lobby/game flow)
and docs/GAME_RULES.md (kind-specific schemas). This module is pure schema —
no SDK imports, no protocol I/O.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal[
    "lobby-open",
    "lobby-join",
    "game-init",
    "phase-start",
    "phase-end",
    "accusation",
    "defense",
    "observation",
    "vote",
    "id-vote",
    "coven-membership",
    "coven-target",
    "coven-confirm",
    "investigate",
    "investigate-result",
    "protect",
    "death",
    "game-end",
    "reveal",
    "substrate-guess",
    "summary",
    "smoke-test",
    "shared-space-invitation",
    "role-card",
]

Substrate = Literal["human", "agent"]
Role = Literal["mafia", "detective", "doctor", "villager"]
Winner = Literal["villagers", "mafia", "draw"]
DeathCause = Literal["lynch", "mafia", "saved"]


class BasePayload(BaseModel):
    """Fields shared by every payload (see ARCHITECTURE.md §Data shape)."""

    model_config = ConfigDict(extra="forbid")

    kind: Kind
    content: str | None = None
    game_id: str | None = None
    phase: str | None = None
    timestamp_iso: str | None = None
    data: dict[str, Any] | None = None


class LobbyOpenPayload(BasePayload):
    kind: Literal["lobby-open"] = "lobby-open"
    seats: int = Field(...)
    opens_at: str | None = None
    deadline_ms: int | None = None


class LobbyJoinPayload(BasePayload):
    kind: Literal["lobby-join"] = "lobby-join"
    principal_id: str = Field(...)
    agent_label: str = Field(...)
    # Substrate the joiner *claims* at lobby time. Truth is reaffirmed
    # at game-end via substrate-guess. Optional so legacy lobby-joins
    # (without a substrate hint) still validate.
    agent_substrate: Substrate | None = None


class GameInitPayload(BasePayload):
    kind: Literal["game-init"] = "game-init"
    game_id: str = Field(...)
    players: list[str] = Field(...)
    role_commitments: list[str] = Field(...)
    substrate_commitments: list[str] = Field(...)


class PhaseStartPayload(BasePayload):
    kind: Literal["phase-start"] = "phase-start"
    phase: str = Field(...)
    deadline_ms: int | None = None
    living: list[str] | None = None
    deaths: list[dict[str, Any]] | None = None


class PhaseEndPayload(BasePayload):
    kind: Literal["phase-end"] = "phase-end"
    ends_phase_intent_id: str = Field(...)
    next_phase: str | None = None
    tally: dict[str, int] | None = None
    lynched: str | None = None
    killed: str | None = None
    chosen_target: str | None = None
    id_votes: list[dict[str, Any]] | None = None
    substrate_locked: list[str] | None = None


class AccusationPayload(BasePayload):
    kind: Literal["accusation"] = "accusation"
    target: str = Field(...)
    claim: str = Field(...)


class DefensePayload(BasePayload):
    kind: Literal["defense"] = "defense"
    claim: str = Field(...)


class ObservationPayload(BasePayload):
    """Free-form observation/commentary INTENT.

    The taxonomy table in ARCHITECTURE.md does not formally define this kind;
    it appears in the kind list as a generic discussion utterance with no
    required structured fields beyond BasePayload.content.
    """

    kind: Literal["observation"] = "observation"


class VotePayload(BasePayload):
    kind: Literal["vote"] = "vote"
    target: str = Field(...)


class IdVotePayload(BasePayload):
    kind: Literal["id-vote"] = "id-vote"
    target: str = Field(...)
    claimed_substrate: Substrate = Field(...)


class CovenMembershipPayload(BasePayload):
    """Posted by the host in the coven subspace at coven creation.

    Lists the mafia principals so MafiaBot can resolve teammates without
    relying on sender inference (which would otherwise wrongly attribute
    host phase-start INTENTs to a teammate).
    """

    kind: Literal["coven-membership"] = "coven-membership"
    members: list[str] = Field(...)
    host: str | None = None


class CovenTargetPayload(BasePayload):
    kind: Literal["coven-target"] = "coven-target"
    target: str = Field(...)
    reasoning: str | None = None


class CovenConfirmPayload(BasePayload):
    kind: Literal["coven-confirm"] = "coven-confirm"


class InvestigatePayload(BasePayload):
    kind: Literal["investigate"] = "investigate"
    target: str = Field(...)


class InvestigateResultPayload(BasePayload):
    kind: Literal["investigate-result"] = "investigate-result"
    target: str = Field(...)
    role: Role = Field(...)
    substrate: Substrate = Field(...)


class ProtectPayload(BasePayload):
    kind: Literal["protect"] = "protect"
    target: str = Field(...)
    self_protect: bool | None = None


class DeathPayload(BasePayload):
    kind: Literal["death"] = "death"
    target: str = Field(...)
    cause: DeathCause = Field(...)


class GameEndPayload(BasePayload):
    kind: Literal["game-end"] = "game-end"
    winner: Winner = Field(...)


class RevealPayload(BasePayload):
    kind: Literal["reveal"] = "reveal"
    role_assignments: dict[str, Role] = Field(...)
    substrate_assignments: dict[str, Substrate] = Field(...)
    salts: dict[str, str] = Field(...)


class SubstrateGuessPayload(BasePayload):
    kind: Literal["substrate-guess"] = "substrate-guess"
    target: str = Field(...)
    guess: Substrate = Field(...)
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str | None = None


class SummaryPayload(BasePayload):
    kind: Literal["summary"] = "summary"
    summary_text: str = Field(...)
    winners: str = Field(...)
    mvp: str | None = None


class SmokeTestPayload(BasePayload):
    """Sentinel payload used by scripts/smoke_test.py to verify connectivity.

    Not formally specified in PROTOCOL_MAPPING.md; carries only BasePayload.
    """

    kind: Literal["smoke-test"] = "smoke-test"


class RoleCardPayload(BasePayload):
    """Posted by the host in each player's private notebook subspace.

    Carries the player's role + substrate plus the salt used to compute the
    public commitments in `game-init`. The player can locally verify
    `sha256(role||salt) == role_commitment` and likewise for substrate.
    """

    kind: Literal["role-card"] = "role-card"
    role: Role = Field(...)
    substrate: Substrate = Field(...)
    salt_hex: str = Field(...)
    role_commitment: str = Field(...)
    substrate_commitment: str = Field(...)
    principal_id: str = Field(...)


class SharedSpaceInvitationPayload(BasePayload):
    """Out-of-band invitation marker.

    Not formally specified in PROTOCOL_MAPPING.md; the steward issues real
    shared-space access via the spacebase1 provisioning flow. This payload is
    a hint/announcement carrying only BasePayload fields.
    """

    kind: Literal["shared-space-invitation"] = "shared-space-invitation"


_REGISTRY: dict[str, type[BasePayload]] = {
    "lobby-open": LobbyOpenPayload,
    "lobby-join": LobbyJoinPayload,
    "game-init": GameInitPayload,
    "phase-start": PhaseStartPayload,
    "phase-end": PhaseEndPayload,
    "accusation": AccusationPayload,
    "defense": DefensePayload,
    "observation": ObservationPayload,
    "vote": VotePayload,
    "id-vote": IdVotePayload,
    "coven-membership": CovenMembershipPayload,
    "coven-target": CovenTargetPayload,
    "coven-confirm": CovenConfirmPayload,
    "investigate": InvestigatePayload,
    "investigate-result": InvestigateResultPayload,
    "protect": ProtectPayload,
    "death": DeathPayload,
    "game-end": GameEndPayload,
    "reveal": RevealPayload,
    "substrate-guess": SubstrateGuessPayload,
    "summary": SummaryPayload,
    "smoke-test": SmokeTestPayload,
    "shared-space-invitation": SharedSpaceInvitationPayload,
    "role-card": RoleCardPayload,
}


def parse_payload(raw: dict[str, Any]) -> BasePayload:
    """Dispatch a raw dict to the concrete payload subclass keyed by `kind`.

    Raises ValueError if `kind` is missing or unknown; pydantic ValidationError
    if the dict doesn't match the chosen subclass schema.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"parse_payload expected dict, got {type(raw).__name__}")
    kind = raw.get("kind")
    if kind is None:
        raise ValueError("payload is missing required 'kind' field")
    cls = _REGISTRY.get(kind)
    if cls is None:
        raise ValueError(f"unknown payload kind: {kind!r}")
    return cls.model_validate(raw)


__all__ = [
    "Kind",
    "Substrate",
    "Role",
    "Winner",
    "DeathCause",
    "BasePayload",
    "LobbyOpenPayload",
    "LobbyJoinPayload",
    "GameInitPayload",
    "PhaseStartPayload",
    "PhaseEndPayload",
    "AccusationPayload",
    "DefensePayload",
    "ObservationPayload",
    "VotePayload",
    "IdVotePayload",
    "CovenMembershipPayload",
    "CovenTargetPayload",
    "CovenConfirmPayload",
    "InvestigatePayload",
    "InvestigateResultPayload",
    "ProtectPayload",
    "DeathPayload",
    "GameEndPayload",
    "RevealPayload",
    "SubstrateGuessPayload",
    "SummaryPayload",
    "SmokeTestPayload",
    "SharedSpaceInvitationPayload",
    "RoleCardPayload",
    "parse_payload",
]
