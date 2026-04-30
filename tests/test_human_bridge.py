"""FastAPI TestClient-based tests for the human bridge.

Validates:
  - GET /             renders without crashing across phases
  - POST /post        constructs the right payload and calls session.post
  - POST /rewrite_robot returns the LLM-rewritten text via the test hook

The session is a minimal stub matching the surface used by bridge.py
(intent / post / scan_full / identity). No network. No real SDK.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.human import llm as human_llm  # noqa: E402
from src.human.bridge import BridgeState, create_app  # noqa: E402


GAME_SPACE = "space-game-test"
PRIV_SPACE = "space-coven-test"


class FakeSession:
    """Minimal stand-in for HttpSpaceToolSession used by the bridge."""

    def __init__(self, public: list[dict] | None = None, private: list[dict] | None = None) -> None:
        self.public = public or []
        self.private = private or []
        self.posted: list[dict] = []
        self.principal_id = "principal-human-1"

    def scan_full(self, space_id: str) -> dict:
        if space_id == GAME_SPACE:
            return {"messages": list(self.public), "latestSeq": len(self.public)}
        if space_id == PRIV_SPACE:
            return {"messages": list(self.private), "latestSeq": len(self.private)}
        return {"messages": [], "latestSeq": 0}

    def identity(self) -> dict:
        return {"principalId": self.principal_id, "agentId": self.principal_id}

    def intent(self, content: str, *, parent_id: str, payload: dict | None = None) -> dict:
        return {
            "type": "INTENT",
            "intentId": "intent-test-1",
            "parentId": parent_id,
            "senderId": self.principal_id,
            "timestamp": 1,
            "payload": dict(payload or {}, content=content),
        }

    def post(self, message: dict, **_: Any) -> dict:
        self.posted.append(message)
        return message


def _phase_start_msg(phase: str = "day-1-discussion", living: list[str] | None = None) -> dict:
    return {
        "type": "INTENT",
        "intentId": "intent-phase-1",
        "parentId": GAME_SPACE,
        "senderId": "principal-host",
        "timestamp": 100,
        "payload": {
            "kind": "phase-start",
            "phase": phase,
            "deadline_ms": 999999999999,
            "living": living or ["principal-human-1", "principal-bot-1", "principal-bot-2"],
        },
    }


def _make_state(public: list[dict], private: list[dict] | None = None, role: str | None = "villager") -> tuple[FakeSession, BridgeState]:
    session = FakeSession(public=public, private=private or [])
    state = BridgeState(
        session=session,
        game_space_id=GAME_SPACE,
        private_space_id=PRIV_SPACE if private is not None else None,
        role=role,
    )
    state.scan_once()  # populate caches without starting a loop
    return session, state


def _client(state: BridgeState) -> TestClient:
    app = create_app(state, run_scan_loop=False)
    return TestClient(app)


# --- GET / -------------------------------------------------------------------

def test_get_index_discussion_renders_ok():
    _, state = _make_state([_phase_start_msg("day-1-discussion")])
    with _client(state) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "day-1-discussion" in r.text
    assert "PUBLIC FEED" in r.text or "public" in r.text.lower()
    assert "Send" in r.text  # discussion partial


def test_get_index_vote_phase_renders_radios():
    _, state = _make_state([_phase_start_msg("day-1-vote")])
    with _client(state) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "Submit vote" in r.text
    assert 'value="vote"' in r.text


def test_get_index_no_phase_yet():
    _, state = _make_state([])
    with _client(state) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "waiting for game-start" in r.text


def test_get_index_endgame_when_game_end_present():
    msgs = [
        _phase_start_msg("day-2-vote"),
        {
            "type": "INTENT",
            "intentId": "intent-end",
            "parentId": GAME_SPACE,
            "senderId": "principal-host",
            "timestamp": 200,
            "payload": {"kind": "game-end", "winner": "villagers"},
        },
    ]
    _, state = _make_state(msgs)
    with _client(state) as c:
        r = c.get("/")
    assert "Submit guess" in r.text
    assert 'name="action_kind" value="substrate-guess"' in r.text


def test_get_index_mafia_night_for_mafia_role():
    _, state = _make_state([_phase_start_msg("night-1-mafia")], role="mafia")
    with _client(state) as c:
        r = c.get("/")
    assert "Send to coven" in r.text


# --- /feed -------------------------------------------------------------------

def test_feed_public_returns_partial():
    msgs = [
        _phase_start_msg(),
        {
            "type": "INTENT",
            "intentId": "intent-acc-1",
            "parentId": GAME_SPACE,
            "senderId": "principal-bot-2",
            "timestamp": 110,
            "payload": {"kind": "accusation", "target": "principal-bot-1", "claim": "you flipped fast"},
        },
    ]
    _, state = _make_state(msgs)
    with _client(state) as c:
        r = c.get("/feed/public")
    assert r.status_code == 200
    assert "PUBLIC FEED" in r.text
    assert "you flipped fast" in r.text


def test_feed_private_204_when_no_private_space():
    _, state = _make_state([_phase_start_msg()])
    with _client(state) as c:
        r = c.get("/feed/private")
    assert r.status_code == 204


def test_feed_private_renders_when_bound():
    private = [
        {
            "type": "INTENT",
            "intentId": "intent-coven-1",
            "parentId": PRIV_SPACE,
            "senderId": "principal-bot-1",
            "timestamp": 50,
            "payload": {"kind": "coven-target", "target": "principal-bot-2", "content": "let's hit them"},
        }
    ]
    _, state = _make_state([_phase_start_msg("night-1-mafia")], private=private, role="mafia")
    with _client(state) as c:
        r = c.get("/feed/private")
    assert r.status_code == 200
    assert "PRIVATE FEED" in r.text
    assert "let&#39;s hit them" in r.text or "let's hit them" in r.text


# --- /post -------------------------------------------------------------------

def test_post_observation_calls_session_post_with_correct_payload():
    session, state = _make_state([_phase_start_msg("day-1-discussion")])
    with _client(state) as c:
        r = c.post(
            "/post",
            data={
                "action_kind": "observation",
                "text": "i think bot-1 is mafia",
                "target": "",
            },
        )
    assert r.status_code == 200
    assert "posted observation" in r.text
    assert len(session.posted) == 1
    msg = session.posted[0]
    assert msg["type"] == "INTENT"
    assert msg["parentId"] == GAME_SPACE
    payload = msg["payload"]
    assert payload["kind"] == "observation"
    assert payload["content"] == "i think bot-1 is mafia"
    assert payload["phase"] == "day-1-discussion"


def test_post_vote_includes_target_and_phase():
    session, state = _make_state([_phase_start_msg("day-1-vote")])
    with _client(state) as c:
        r = c.post(
            "/post",
            data={
                "action_kind": "vote",
                "target": "principal-bot-1",
                "text": "voted hardest after misdirect",
            },
        )
    assert r.status_code == 200
    assert len(session.posted) == 1
    payload = session.posted[0]["payload"]
    assert payload["kind"] == "vote"
    assert payload["target"] == "principal-bot-1"
    assert payload["phase"] == "day-1-vote"
    assert payload["content"] == "voted hardest after misdirect"


def test_post_id_vote_requires_substrate():
    _, state = _make_state([_phase_start_msg("day-1-vote")])
    with _client(state) as c:
        r = c.post(
            "/post",
            data={"action_kind": "id-vote", "target": "principal-bot-1", "claimed_substrate": ""},
        )
    assert r.status_code == 400


def test_post_coven_target_routes_to_private_space_when_bound():
    session, state = _make_state(
        [_phase_start_msg("night-1-mafia")],
        private=[],
        role="mafia",
    )
    with _client(state) as c:
        r = c.post(
            "/post",
            data={"action_kind": "coven-target", "target": "principal-bot-2", "text": "soft target"},
        )
    assert r.status_code == 200
    assert len(session.posted) == 1
    assert session.posted[0]["parentId"] == PRIV_SPACE


def test_post_unknown_action_kind_400():
    _, state = _make_state([_phase_start_msg()])
    with _client(state) as c:
        r = c.post("/post", data={"action_kind": "telepathy"})
    assert r.status_code == 400


# --- /rewrite_robot ----------------------------------------------------------

def test_rewrite_robot_uses_hook_and_returns_html():
    captured: list[tuple[str, str]] = []

    def fake_complete(system: str, user: str) -> str:
        captured.append((system, user))
        return "I would like to formally submit the following observation."

    human_llm.set_complete_hook(fake_complete)
    try:
        _, state = _make_state([_phase_start_msg("day-1-discussion")])
        with _client(state) as c:
            r = c.post("/rewrite_robot", data={"text": "ugh idk player3 is sus"})
        assert r.status_code == 200
        assert "Posting as" in r.text
        assert "I would like to formally submit" in r.text
        assert len(captured) == 1
        assert "Rewrite the user" in captured[0][0]
        assert captured[0][1] == "ugh idk player3 is sus"
    finally:
        human_llm.set_complete_hook(None)


def test_rewrite_robot_falls_back_to_stub_when_no_hook_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    human_llm.set_complete_hook(None)
    _, state = _make_state([_phase_start_msg("day-1-discussion")])
    with _client(state) as c:
        r = c.post("/rewrite_robot", data={"text": "u sure?"})
    assert r.status_code == 200
    assert "Posting as" in r.text
    # stub prepends a formalising preamble
    assert "I would like to note" in r.text
