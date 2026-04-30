"""Round-trip tests: build → model_dump() → parse_payload() → equality.

Each concrete payload subclass gets one happy-path test. We also assert
parse_payload routes to the right class and rejects unknown kinds.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.game.schemas import (  # noqa: E402
    AccusationPayload,
    BasePayload,
    CovenConfirmPayload,
    CovenMembershipPayload,
    CovenTargetPayload,
    DeathPayload,
    DefensePayload,
    GameEndPayload,
    GameInitPayload,
    IdVotePayload,
    InvestigatePayload,
    InvestigateResultPayload,
    LobbyJoinPayload,
    LobbyOpenPayload,
    ObservationPayload,
    PhaseEndPayload,
    PhaseStartPayload,
    ProtectPayload,
    RevealPayload,
    RoleCardPayload,
    SharedSpaceInvitationPayload,
    SmokeTestPayload,
    SubstrateGuessPayload,
    SummaryPayload,
    VotePayload,
    parse_payload,
)


def _roundtrip(model: BasePayload) -> None:
    dumped = model.model_dump()
    parsed = parse_payload(dumped)
    assert type(parsed) is type(model)
    assert parsed == model
    assert parsed.model_dump() == dumped


def test_lobby_open():
    _roundtrip(LobbyOpenPayload(seats=7, opens_at="2026-04-30T18:00:00Z", deadline_ms=60000))


def test_lobby_join():
    _roundtrip(LobbyJoinPayload(principal_id="principal-1", agent_label="Alice"))


def test_game_init():
    _roundtrip(
        GameInitPayload(
            game_id="game-1234",
            players=[f"principal-{i}" for i in range(7)],
            role_commitments=[f"hash-r-{i}" for i in range(7)],
            substrate_commitments=[f"hash-s-{i}" for i in range(7)],
        )
    )


def test_phase_start():
    _roundtrip(
        PhaseStartPayload(
            phase="day-1-discussion",
            deadline_ms=60000,
            living=[f"principal-{i}" for i in range(7)],
            deaths=None,
        )
    )


def test_phase_end():
    _roundtrip(
        PhaseEndPayload(
            ends_phase_intent_id="intent-phase-1",
            next_phase="day-1-vote",
            tally={"principal-3": 2, "principal-7": 1},
            lynched="principal-3",
            id_votes=[
                {"target": "principal-3", "claimed": "agent", "actual": "agent", "correct": True}
            ],
            substrate_locked=["principal-3"],
        )
    )


def test_accusation():
    _roundtrip(AccusationPayload(target="principal-3", claim="suspiciously quiet"))


def test_defense():
    _roundtrip(DefensePayload(claim="I was scanning, not silent"))


def test_observation():
    _roundtrip(ObservationPayload(content="player 4 hesitated for a long time"))


def test_vote():
    _roundtrip(VotePayload(target="principal-3"))


def test_id_vote():
    _roundtrip(IdVotePayload(target="principal-3", claimed_substrate="agent"))


def test_coven_target():
    _roundtrip(CovenTargetPayload(target="principal-5", reasoning="loud villager"))


def test_coven_confirm():
    _roundtrip(CovenConfirmPayload(content="agree"))


def test_investigate():
    _roundtrip(InvestigatePayload(target="principal-2"))


def test_investigate_result():
    _roundtrip(
        InvestigateResultPayload(target="principal-2", role="mafia", substrate="agent")
    )


def test_protect():
    _roundtrip(ProtectPayload(target="principal-1", self_protect=False))


def test_death():
    _roundtrip(DeathPayload(target="principal-5", cause="mafia"))


def test_game_end():
    _roundtrip(GameEndPayload(winner="villagers"))


def test_reveal():
    _roundtrip(
        RevealPayload(
            role_assignments={"principal-0": "mafia", "principal-1": "villager"},
            substrate_assignments={"principal-0": "agent", "principal-1": "human"},
            salts={"principal-0": "deadbeef", "principal-1": "cafebabe"},
        )
    )


def test_substrate_guess():
    _roundtrip(
        SubstrateGuessPayload(
            target="principal-2",
            guess="agent",
            confidence=0.9,
            reasoning="symmetric reasoning",
        )
    )


def test_summary():
    _roundtrip(
        SummaryPayload(
            game_id="game-1234",
            summary_text="Villagers won on day 3",
            winners="villagers",
            mvp="principal-1",
        )
    )


def test_smoke_test():
    _roundtrip(SmokeTestPayload(content="ping"))


def test_shared_space_invitation():
    _roundtrip(
        SharedSpaceInvitationPayload(
            content="join coven",
            data={"space_id": "space-coven-1234"},
        )
    )


def test_role_card():
    _roundtrip(
        RoleCardPayload(
            content="role card",
            game_id="space-game-1",
            role="mafia",
            substrate="agent",
            salt_hex="ab" * 16,
            role_commitment="cd" * 32,
            substrate_commitment="ef" * 32,
            principal_id="prn-player-1",
        )
    )


def test_coven_membership():
    _roundtrip(
        CovenMembershipPayload(
            content="coven members",
            game_id="space-game-1",
            members=["prn-mafia-a", "prn-mafia-b"],
            host="prn-host",
        )
    )


def test_parse_payload_unknown_kind():
    with pytest.raises(ValueError, match="unknown payload kind"):
        parse_payload({"kind": "not-a-real-kind"})


def test_parse_payload_missing_kind():
    with pytest.raises(ValueError, match="missing required 'kind'"):
        parse_payload({"content": "hi"})


def test_parse_payload_dispatches_correct_class():
    raw = {"kind": "vote", "target": "principal-3"}
    parsed = parse_payload(raw)
    assert isinstance(parsed, VotePayload)
    assert parsed.target == "principal-3"
