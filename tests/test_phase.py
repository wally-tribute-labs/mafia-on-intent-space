"""Tests for src.game.phase.current_phase.

Fixtures use the NESTED SDK shape that `scan_full` returns: `type`,
`intentId` / `promiseId`, `parentId`, `senderId`, `timestamp`, `payload`.
Phase-end → phase-start linkage is via `payload.ends_phase_intent_id`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.game.phase import current_phase  # noqa: E402


def _start(intent_id: str, phase: str, ts: int = 0) -> dict:
    return {
        "type": "INTENT",
        "intentId": intent_id,
        "parentId": "space-game",
        "senderId": "host",
        "timestamp": ts,
        "payload": {"kind": "phase-start", "phase": phase, "deadline_ms": ts + 60_000},
    }


def _end(start_intent_id: str, phase: str, ts: int = 0, next_phase: str | None = None) -> dict:
    return {
        "type": "COMPLETE",
        "promiseId": f"promise-{start_intent_id}",
        "parentId": "space-game",
        "senderId": "host",
        "timestamp": ts,
        "payload": {
            "kind": "phase-end",
            "ends_phase_intent_id": start_intent_id,
            "next_phase": next_phase,
        },
    }


def test_no_messages_returns_none():
    assert current_phase([]) is None


def test_no_phase_starts_returns_none():
    msgs = [
        {
            "type": "INTENT",
            "intentId": "i-1",
            "senderId": "p1",
            "timestamp": 5,
            "payload": {"kind": "vote", "target": "p2"},
        },
    ]
    assert current_phase(msgs) is None


def test_single_unended_phase_start_returned():
    s = _start("intent-p1", "day-1-discussion", ts=10)
    out = current_phase([s])
    assert out is s
    assert out["intentId"] == "intent-p1"


def test_phase_start_followed_by_phase_end_returns_none():
    s = _start("intent-p1", "day-1-discussion", ts=10)
    e = _end("intent-p1", "day-1-discussion", ts=20, next_phase="day-1-vote")
    assert current_phase([s, e]) is None


def test_returns_latest_unended_among_multiple():
    s1 = _start("intent-p1", "day-1-discussion", ts=10)
    e1 = _end("intent-p1", "day-1-discussion", ts=15)
    s2 = _start("intent-p2", "day-1-vote", ts=20)
    out = current_phase([s1, e1, s2])
    assert out is s2
    assert out["intentId"] == "intent-p2"


def test_intervening_messages_ignored():
    s = _start("intent-p1", "day-1-vote", ts=10)
    msgs = [
        s,
        {
            "type": "INTENT",
            "intentId": "i-v1",
            "senderId": "p1",
            "timestamp": 11,
            "payload": {"kind": "vote", "target": "p2"},
        },
        {
            "type": "INTENT",
            "intentId": "i-v2",
            "senderId": "p3",
            "timestamp": 12,
            "payload": {"kind": "vote", "target": "p2"},
        },
    ]
    assert current_phase(msgs) is s


def test_complete_for_unrelated_intent_does_not_close_phase():
    s = _start("intent-p1", "day-1-discussion", ts=10)
    unrelated_end = _end("intent-OTHER", "day-1-discussion", ts=15)
    assert current_phase([s, unrelated_end]) is s


def test_out_of_order_end_before_later_start_resolves_correctly():
    s1 = _start("intent-p1", "day-1-discussion", ts=10)
    s2 = _start("intent-p2", "day-1-vote", ts=20)
    e1 = _end("intent-p1", "day-1-discussion", ts=25)
    out = current_phase([s1, s2, e1])
    assert out is s2
