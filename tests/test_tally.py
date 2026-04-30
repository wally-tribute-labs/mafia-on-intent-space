"""Tests for src.game.tally.

Fixtures use the NESTED SDK shape: sender lives in `senderId`, payload
fields under `payload`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.game.tally import (  # noqa: E402
    compute_tally,
    resolve_id_votes,
    resolve_lynch,
)


PHASE_START = {
    "type": "INTENT",
    "intentId": "intent-phase-vote",
    "parentId": "space-game",
    "senderId": "host",
    "timestamp": 100,
    "payload": {"kind": "phase-start", "phase": "day-1-vote"},
}


def _vote(sender: str, target: str, ts: int, phase: str = "day-1-vote") -> dict:
    return {
        "type": "INTENT",
        "intentId": f"i-vote-{sender}-{ts}",
        "parentId": "intent-phase-vote",
        "senderId": sender,
        "timestamp": ts,
        "payload": {"kind": "vote", "target": target, "phase": phase},
    }


def _id_vote(sender: str, target: str, claimed: str, ts: int) -> dict:
    return {
        "type": "INTENT",
        "intentId": f"i-idvote-{sender}-{ts}",
        "parentId": "intent-phase-vote",
        "senderId": sender,
        "timestamp": ts,
        "payload": {
            "kind": "id-vote",
            "target": target,
            "claimed_substrate": claimed,
            "phase": "day-1-vote",
        },
    }


# ---------------------------------------------------------------------------
# compute_tally
# ---------------------------------------------------------------------------


def test_compute_tally_happy_path():
    votes = [
        _vote("p1", "p3", ts=110),
        _vote("p2", "p3", ts=111),
        _vote("p4", "p7", ts=112),
    ]
    assert compute_tally(PHASE_START, votes) == {"p3": 2, "p7": 1}


def test_compute_tally_no_votes():
    assert compute_tally(PHASE_START, []) == {}


def test_compute_tally_first_vote_per_sender_wins():
    votes = [
        _vote("p1", "p3", ts=110),
        _vote("p1", "p7", ts=111),
        _vote("p2", "p3", ts=112),
    ]
    assert compute_tally(PHASE_START, votes) == {"p3": 2}


def test_compute_tally_ignores_non_vote_kinds():
    msgs = [
        _vote("p1", "p3", ts=110),
        {
            "type": "INTENT",
            "intentId": "i-acc",
            "senderId": "p2",
            "timestamp": 111,
            "payload": {"kind": "accusation", "target": "p3", "claim": "..."},
        },
        _id_vote("p2", "p3", "agent", ts=112),
    ]
    assert compute_tally(PHASE_START, msgs) == {"p3": 1}


def test_compute_tally_filters_by_phase_when_present():
    votes = [
        _vote("p1", "p3", ts=110, phase="day-1-vote"),
        _vote("p2", "p3", ts=120, phase="day-2-vote"),
    ]
    assert compute_tally(PHASE_START, votes) == {"p3": 1}


# ---------------------------------------------------------------------------
# resolve_lynch
# ---------------------------------------------------------------------------


def test_resolve_lynch_happy_path():
    assert resolve_lynch({"p3": 2, "p7": 1}) == "p3"


def test_resolve_lynch_tie_returns_none():
    assert resolve_lynch({"p3": 2, "p7": 2}) is None


def test_resolve_lynch_empty_returns_none():
    assert resolve_lynch({}) is None


def test_resolve_lynch_single_entry():
    assert resolve_lynch({"p3": 1}) == "p3"


# ---------------------------------------------------------------------------
# resolve_id_votes
# ---------------------------------------------------------------------------


def test_resolve_id_votes_correct_locks_target():
    truth = {"p3": "agent", "p7": "human"}
    msgs = [_id_vote("p1", "p3", "agent", ts=110)]
    out = resolve_id_votes(msgs, truth)
    assert out == [
        {
            "voter": "p1",
            "target": "p3",
            "claimed": "agent",
            "actual": "agent",
            "correct": True,
            "locked": "p3",
        }
    ]


def test_resolve_id_votes_incorrect_locks_voter():
    truth = {"p3": "human"}
    msgs = [_id_vote("p1", "p3", "agent", ts=110)]
    out = resolve_id_votes(msgs, truth)
    assert len(out) == 1
    assert out[0]["correct"] is False
    assert out[0]["locked"] == "p1"
    assert out[0]["actual"] == "human"


def test_resolve_id_votes_first_per_voter_wins():
    truth = {"p3": "agent", "p7": "human"}
    msgs = [
        _id_vote("p1", "p3", "agent", ts=110),
        _id_vote("p1", "p7", "agent", ts=111),
    ]
    out = resolve_id_votes(msgs, truth)
    assert len(out) == 1
    assert out[0]["target"] == "p3"


def test_resolve_id_votes_ignores_non_id_votes():
    truth = {"p3": "agent"}
    msgs = [
        _vote("p1", "p3", ts=110),
        _id_vote("p2", "p3", "agent", ts=111),
    ]
    out = resolve_id_votes(msgs, truth)
    assert [r["voter"] for r in out] == ["p2"]


def test_resolve_id_votes_empty():
    assert resolve_id_votes([], {}) == []
