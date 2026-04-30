"""Test that the host posts a summary INTENT to commons after game-end.

This is the reputation-mirroring contract that src/game/stats.py reads.
We exercise the zero-player short-circuit path because it requires no
phase progression — the loop posts game-end + summary immediately.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.host.phase_loop import _connect_to_space, run_game_loop  # noqa: E402
from src.host.start import GameState  # noqa: E402


HOST_PRINCIPAL = "host-principal"
HOST_HOME = "space-host-home"
GAME_SPACE = "space-game-1"


class MockSession:
    """Captures every post; supports the host's complete + intent builders."""

    def __init__(self) -> None:
        self.current_space_id = HOST_HOME
        self.posted: list[dict] = []
        self._counter = 0

    def identity(self) -> dict:
        return {"principalId": HOST_PRINCIPAL, "currentSpaceId": HOST_HOME}

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

    def complete(self, *, promise_id: str, parent_id: str, summary: str,
                 payload: dict | None = None) -> dict:
        self._counter += 1
        return {
            "type": "COMPLETE",
            "promiseId": promise_id,
            "parentId": parent_id,
            "senderId": HOST_PRINCIPAL,
            "summary": summary,
            "timestamp": self._counter,
            "payload": payload or {},
        }

    def post_and_confirm(self, message: dict, *, step: str | None = None,
                         confirm_space_id: str | None = None) -> dict:
        self.posted.append({"message": message, "step": step,
                            "confirm_space_id": confirm_space_id})
        return message

    def scan_full(self, space_id: str) -> dict:
        return {"messages": [], "latestSeq": 0}

    def set_current_space(self, space_id: str) -> None:
        self.current_space_id = space_id


def _empty_state(lobby_intent_id: str = "intent-lobby-1") -> GameState:
    return GameState(
        host_principal_id=HOST_PRINCIPAL,
        host_home_space_id=HOST_HOME,
        game_space_id=HOST_HOME,  # zero-player short-circuit path
        coven_id=None,
        detective_id=None,
        doctor_id=None,
        role_truth={},
        substrate_truth={},
        salts={},
        role_commitments={},
        substrate_commitments={},
        players=[],
        player_cards=[],
        game_init_intent_id="",
        lobby_intent_id=lobby_intent_id,
        multiplier=1.0,
    )


class ConnectTrackingSession:
    """Stub that records connect_to + set_current_space invocations."""

    def __init__(self) -> None:
        self.current_space_id = None
        self.connect_to_calls: list[dict] = []
        self.set_calls: list[str] = []

    def connect_to(self, *, endpoint, station_token, audience, sender_id):
        self.connect_to_calls.append({
            "endpoint": endpoint, "station_token": station_token,
            "audience": audience, "sender_id": sender_id,
        })
        self.current_space_id = audience.split("/")[-1]

    def set_current_space(self, space_id: str) -> None:
        self.set_calls.append(space_id)
        self.current_space_id = space_id


def test_connect_to_space_uses_real_connect_when_access_present():
    session = ConnectTrackingSession()
    state = _empty_state()
    state.subspace_access = {
        "space-coven-xyz": {
            "endpoint": "https://example.test/coven/itp",
            "station_token": "tok-coven",
            "audience": "intent-space://test/space/space-coven-xyz",
        },
    }

    _connect_to_space(session, "space-coven-xyz", state)

    assert len(session.connect_to_calls) == 1
    call = session.connect_to_calls[0]
    assert call["endpoint"] == "https://example.test/coven/itp"
    assert call["station_token"] == "tok-coven"
    assert call["sender_id"] == HOST_PRINCIPAL
    # Test stub fallback must NOT be used when real connect path fired.
    assert session.set_calls == []


def test_connect_to_space_falls_back_to_set_current_space_for_unknown():
    session = ConnectTrackingSession()
    state = _empty_state()
    # subspace_access is empty by default — no entry for "commons".

    _connect_to_space(session, "commons", state)

    assert session.connect_to_calls == []
    assert session.set_calls == ["commons"]
    assert session.current_space_id == "commons"


def test_summary_intent_posted_to_commons_after_game_end():
    session = MockSession()
    state = _empty_state(lobby_intent_id="intent-lobby-1")

    outcome = run_game_loop(session, state, sleep=lambda *_: None,
                            now=lambda: 0.0)
    assert outcome["winner"] == "draw"

    summary_steps = [p for p in session.posted if p["step"] == "host.summary"]
    assert len(summary_steps) == 1, f"expected exactly one summary; got {summary_steps}"
    summary = summary_steps[0]
    assert summary["confirm_space_id"] == "commons"

    msg = summary["message"]
    assert msg["type"] == "INTENT"
    assert msg["parentId"] == "intent-lobby-1"

    payload = msg["payload"]
    assert payload["kind"] == "summary"
    assert payload["winners"] == "draw"
    assert payload["game_id"] == HOST_HOME
    data = payload["data"]
    assert data["winner"] == "draw"
    assert data["role_assignments"] == {}
    assert data["substrate_assignments"] == {}
    assert data["final_living"] == []
    assert "duration_ms" in data
