"""Tests for src.host.start.start_game.

The MockSession captures every post and simulates the steward dance:
each provisioning request returns a deterministic shared_space_id.
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.host.start import start_game  # noqa: E402
from src.game.commit_reveal import verify  # noqa: E402


HOST_PRINCIPAL = "host-principal"
HOST_HOME = "space-host-home"


class MockSession:
    """Stub HttpSpaceToolSession that simulates steward provisioning."""

    def __init__(self) -> None:
        self.current_space_id = HOST_HOME
        self.posted: list[dict] = []
        self._counter = 0
        self._space_counter = 0

    # ---- identity ----
    def identity(self) -> dict:
        return {"principalId": HOST_PRINCIPAL, "currentSpaceId": HOST_HOME}

    # ---- builders ----
    def intent(self, *, content: str, payload: dict, parent_id: str | None = None,
               intent_id: str | None = None) -> dict:
        self._counter += 1
        return {
            "type": "INTENT",
            "intentId": intent_id or f"intent-host-{self._counter}",
            "parentId": parent_id or self.current_space_id,
            "senderId": HOST_PRINCIPAL,
            "timestamp": self._counter,
            "payload": payload,
        }

    def accept(self, *, promise_id: str, parent_id: str) -> dict:
        self._counter += 1
        return {
            "type": "ACCEPT",
            "intentId": f"accept-{self._counter}",
            "parentId": parent_id,
            "promiseId": promise_id,
            "senderId": HOST_PRINCIPAL,
            "timestamp": self._counter,
        }

    # ---- send ----
    def post_and_confirm(self, message: dict, *, step: str | None = None,
                         confirm_space_id: str | None = None) -> dict:
        self.posted.append({"message": message, "step": step, "confirm_space_id": confirm_space_id})
        return message

    # ---- steward dance helpers ----
    def wait_for_promise(self, intent_id: str, *, wait_seconds: float) -> dict:
        return {
            "type": "PROMISE",
            "intentId": intent_id,
            "promiseId": f"promise-{intent_id}",
            "senderId": "steward",
            "timestamp": 0,
        }

    def wait_for_complete(self, intent_id: str, *, promise_id: str,
                          wait_seconds: float) -> dict:
        self._space_counter += 1
        space_id = f"space-shared-{self._space_counter}"
        return {
            "type": "COMPLETE",
            "promiseId": promise_id,
            "intentId": intent_id,
            "senderId": "steward",
            "timestamp": 0,
            "payload": {
                "shared_space_id": space_id,
                "itp_endpoint": f"https://example.test/{space_id}/itp",
                "station_token": "tok-" + space_id,
                "audience": f"intent-space://test/space/{space_id}",
            },
        }

    def connect_to(self, *, endpoint: str, station_token: str, audience: str,
                   sender_id: str) -> None:
        self.current_space_id = audience.split("/")[-1]


def _payloads_by_step(session: MockSession, step: str) -> list[dict]:
    return [p["message"]["payload"] for p in session.posted if p["step"] == step]


def test_start_game_zero_players_short_circuits():
    session = MockSession()
    state = start_game(
        session,
        players=[],
        player_substrates={},
        multiplier=1.0,
        rng=random.Random(0),
    )
    assert state.players == []
    assert state.role_truth == {}
    assert state.substrate_truth == {}
    assert session.posted == []


def test_start_game_provisions_game_space_and_posts_game_init():
    session = MockSession()
    players = [f"principal-{i}" for i in range(7)]
    substrates = {p: ("human" if i < 2 else "agent") for i, p in enumerate(players)}

    state = start_game(
        session,
        players=players,
        player_substrates=substrates,
        multiplier=1.0,
        rng=random.Random(0),
    )

    assert state.host_principal_id == HOST_PRINCIPAL
    assert state.host_home_space_id == HOST_HOME
    assert state.game_space_id.startswith("space-shared-")
    assert sorted(state.role_truth.keys()) == sorted(players)
    assert sorted(state.substrate_truth.keys()) == sorted(players)
    assert state.substrate_truth == substrates

    role_counts: dict[str, int] = {}
    for r in state.role_truth.values():
        role_counts[r] = role_counts.get(r, 0) + 1
    assert role_counts == {"mafia": 2, "detective": 1, "doctor": 1, "villager": 3}

    init_payloads = _payloads_by_step(session, "host.game-init")
    assert len(init_payloads) == 1
    init = init_payloads[0]
    assert init["kind"] == "game-init"
    assert init["players"] == players
    assert len(init["role_commitments"]) == 7
    assert len(init["substrate_commitments"]) == 7

    expected_role_commits = [state.role_commitments[p] for p in players]
    expected_sub_commits = [state.substrate_commitments[p] for p in players]
    assert init["role_commitments"] == expected_role_commits
    assert init["substrate_commitments"] == expected_sub_commits


def test_start_game_provisions_faction_subspaces_and_player_notebooks():
    session = MockSession()
    players = [f"principal-{i}" for i in range(7)]
    substrates = {p: "agent" for p in players}

    state = start_game(
        session,
        players=players,
        player_substrates=substrates,
        multiplier=1.0,
        rng=random.Random(0),
    )

    purposes = [p["step"].split(".", 2)[-1] for p in session.posted
                if p["step"] and p["step"].startswith("host.provision.")]
    assert "game-space" in purposes
    assert "coven" in purposes
    assert "detective" in purposes
    assert "doctor" in purposes
    assert state.coven_id is not None
    assert state.detective_id is not None
    assert state.doctor_id is not None

    role_card_steps = [p for p in session.posted
                       if p["step"] and p["step"].startswith("host.role-card.")]
    assert len(role_card_steps) == 7
    for entry in role_card_steps:
        payload = entry["message"]["payload"]
        assert payload["kind"] == "role-card"
        assert {"role", "substrate", "salt_hex", "role_commitment",
                "substrate_commitment", "principal_id"} <= set(payload)
        assert verify(payload["role"], payload["salt_hex"], payload["role_commitment"]) is True
        assert verify(payload["substrate"], payload["salt_hex"], payload["substrate_commitment"]) is True


def test_start_game_posts_coven_membership_after_provisioning():
    session = MockSession()
    players = [f"principal-{i}" for i in range(7)]
    substrates = {p: "agent" for p in players}

    state = start_game(
        session,
        players=players,
        player_substrates=substrates,
        multiplier=1.0,
        rng=random.Random(0),
    )

    membership = [p for p in session.posted if p["step"] == "host.coven-membership"]
    assert len(membership) == 1, "exactly one coven-membership post expected"
    msg = membership[0]
    assert msg["confirm_space_id"] == state.coven_id
    payload = msg["message"]["payload"]
    assert payload["kind"] == "coven-membership"
    assert payload["host"] == HOST_PRINCIPAL
    expected_mafia = [p for p in players if state.role_truth[p] == "mafia"]
    assert sorted(payload["members"]) == sorted(expected_mafia)
    # Host must NOT appear in the members list (only the actual mafia).
    assert HOST_PRINCIPAL not in payload["members"]


def test_start_game_captures_subspace_access_materials():
    session = MockSession()
    players = [f"principal-{i}" for i in range(7)]
    substrates = {p: "agent" for p in players}

    state = start_game(
        session,
        players=players,
        player_substrates=substrates,
        multiplier=1.0,
        rng=random.Random(0),
    )

    # Game + coven + detective + doctor + 7 player notebooks should all be
    # present in subspace_access; each entry has the three required fields.
    assert state.game_space_id in state.subspace_access
    assert state.coven_id in state.subspace_access
    assert state.detective_id in state.subspace_access
    assert state.doctor_id in state.subspace_access
    for card in state.player_cards:
        assert card.private_space_id in state.subspace_access
    for access in state.subspace_access.values():
        assert {"endpoint", "station_token", "audience"} == set(access)


def test_start_game_skip_role_card_delivery():
    session = MockSession()
    players = [f"principal-{i}" for i in range(5)]
    substrates = {p: "agent" for p in players}

    state = start_game(
        session,
        players=players,
        player_substrates=substrates,
        multiplier=1.0,
        rng=random.Random(0),
        skip_role_card_delivery=True,
    )

    role_card_steps = [p for p in session.posted
                       if p["step"] and p["step"].startswith("host.role-card.")]
    assert role_card_steps == []
    assert state.coven_id is None
    assert state.detective_id is None
    assert state.doctor_id is None


def test_start_game_missing_substrate_raises():
    session = MockSession()
    players = ["p1", "p2"]
    try:
        start_game(
            session,
            players=players,
            player_substrates={"p1": "human"},
            multiplier=1.0,
            rng=random.Random(0),
        )
    except ValueError as e:
        assert "p2" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_start_game_commitments_verify_against_per_player_salts():
    session = MockSession()
    players = [f"principal-{i}" for i in range(7)]
    substrates = {p: ("human" if i % 2 == 0 else "agent") for i, p in enumerate(players)}

    state = start_game(
        session,
        players=players,
        player_substrates=substrates,
        multiplier=1.0,
        rng=random.Random(42),
        skip_role_card_delivery=True,
    )

    for p in players:
        assert verify(state.role_truth[p], state.salts[p], state.role_commitments[p])
        assert verify(state.substrate_truth[p], state.salts[p], state.substrate_commitments[p])
