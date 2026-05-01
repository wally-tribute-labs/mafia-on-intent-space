"""Tests for src.host.lobby.run_lobby."""

from __future__ import annotations

import os
import sys
from typing import Callable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.host.lobby import COMMONS_SPACE_ID, run_lobby  # noqa: E402


class MockSession:
    """Minimal HttpSpaceToolSession stand-in for lobby tests."""

    def __init__(self, scan_sequence: list[list[dict]]) -> None:
        self._scan_sequence = list(scan_sequence)
        self.posted: list[dict] = []
        self._counter = 0
        self.current_space_id = COMMONS_SPACE_ID

    def intent(self, *, content: str, payload: dict, parent_id: str = COMMONS_SPACE_ID,
               intent_id: str | None = None) -> dict:
        self._counter += 1
        return {
            "type": "INTENT",
            "intentId": intent_id or f"intent-host-{self._counter}",
            "parentId": parent_id,
            "senderId": "host-principal",
            "timestamp": self._counter,
            "payload": payload,
        }

    def post_and_confirm(self, message: dict, *, step: str | None = None,
                         confirm_space_id: str | None = None) -> dict:
        self.posted.append({"message": message, "step": step, "confirm_space_id": confirm_space_id})
        return message

    def scan_full(self, space_id: str) -> dict:
        # run_lobby scans the lobby-open intent's interior (not commons)
        # to collect lobby-join PROMISEs, since they're posted nested
        # under that intent.
        if space_id == COMMONS_SPACE_ID:
            return {"messages": [], "latestSeq": 0}
        if self._scan_sequence:
            return {"messages": self._scan_sequence.pop(0), "latestSeq": 0}
        return {"messages": [], "latestSeq": 0}


def _join(sender: str, lobby_intent_id: str, agent_label: str = "test") -> dict:
    return {
        "type": "PROMISE",
        "promiseId": f"promise-{sender}",
        "intentId": lobby_intent_id,
        # PROMISEs are posted nested under the lobby-open intent, so the
        # parentId is the lobby-open intent's id (its interior space).
        "parentId": lobby_intent_id,
        "senderId": sender,
        "timestamp": 0,
        "payload": {
            "kind": "lobby-join",
            "principal_id": sender,
            "agent_label": agent_label,
        },
    }


def _now_factory(values: list[float]) -> Callable[[], float]:
    """Return a `now()` callable that walks `values` then sticks at the last."""
    state = {"i": 0}

    def now() -> float:
        i = state["i"]
        if i < len(values):
            state["i"] += 1
            return values[i]
        return values[-1]

    return now


def test_run_lobby_posts_lobby_open_intent_to_commons():
    session = MockSession(scan_sequence=[[]])  # one empty scan, then deadline
    now = _now_factory([0.0, 100.0])  # fast deadline trip

    result = run_lobby(session, player_count=0, deadline_s=1.0, now=now, sleep=lambda *_: None)

    assert len(session.posted) == 1
    posted = session.posted[0]
    assert posted["confirm_space_id"] == COMMONS_SPACE_ID
    assert posted["step"] == "host.lobby-open"
    payload = posted["message"]["payload"]
    assert payload["kind"] == "lobby-open"
    assert payload["seats"] == 0
    assert payload["deadline_ms"] == 1000
    assert result.players == []


def test_run_lobby_returns_when_count_met():
    lobby_intent = "intent-host-1"
    scan_first = [_join("p1", lobby_intent), _join("p2", lobby_intent)]
    session = MockSession(scan_sequence=[scan_first])
    now = _now_factory([0.0])

    result = run_lobby(
        session,
        player_count=2,
        deadline_s=60.0,
        now=now,
        sleep=lambda *_: None,
    )

    assert result.players == ["p1", "p2"]
    assert result.deadline_reached is False
    assert result.lobby_intent_id == lobby_intent


def test_run_lobby_returns_partial_at_deadline():
    lobby_intent = "intent-host-1"
    scan_first = [_join("p1", lobby_intent)]
    session = MockSession(scan_sequence=[scan_first, scan_first])
    now = _now_factory([0.0, 0.5, 100.0])

    result = run_lobby(
        session,
        player_count=7,
        deadline_s=1.0,
        now=now,
        sleep=lambda *_: None,
    )

    assert result.players == ["p1"]
    assert result.deadline_reached is True


def test_run_lobby_dedups_per_principal_first_promise_wins():
    lobby_intent = "intent-host-1"
    msgs = [
        _join("p1", lobby_intent, agent_label="first"),
        _join("p1", lobby_intent, agent_label="second"),
        _join("p2", lobby_intent),
    ]
    session = MockSession(scan_sequence=[msgs])
    now = _now_factory([0.0])

    result = run_lobby(
        session,
        player_count=2,
        deadline_s=60.0,
        now=now,
        sleep=lambda *_: None,
    )
    assert result.players == ["p1", "p2"]


def test_run_lobby_skips_promises_for_other_intents():
    lobby_intent = "intent-host-1"
    msgs = [
        _join("p1", "some-other-intent"),
        _join("p2", lobby_intent),
    ]
    session = MockSession(scan_sequence=[msgs])
    now = _now_factory([0.0])

    result = run_lobby(
        session,
        player_count=1,
        deadline_s=60.0,
        now=now,
        sleep=lambda *_: None,
    )
    assert result.players == ["p2"]


def test_run_lobby_skips_non_promise_messages():
    lobby_intent = "intent-host-1"
    msgs = [
        {"type": "INTENT", "intentId": lobby_intent, "senderId": "p3",
         "payload": {"kind": "lobby-join", "principal_id": "p3", "agent_label": "x"}},
        _join("p2", lobby_intent),
    ]
    session = MockSession(scan_sequence=[msgs])
    now = _now_factory([0.0])

    result = run_lobby(
        session,
        player_count=1,
        deadline_s=60.0,
        now=now,
        sleep=lambda *_: None,
    )
    assert result.players == ["p2"]


def test_run_lobby_negative_player_count_raises():
    session = MockSession(scan_sequence=[[]])
    try:
        run_lobby(session, player_count=-1, deadline_s=1.0, sleep=lambda *_: None)
    except ValueError as e:
        assert "non-negative" in str(e)
    else:
        raise AssertionError("expected ValueError")
