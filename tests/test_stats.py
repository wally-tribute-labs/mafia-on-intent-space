"""Tests for src.game.stats."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.game.stats import compute_principal_stats, leaderboard  # noqa: E402


def _guess(sender: str, target: str, guess: str, game_id: str) -> dict:
    return {
        "type": "ASSESS",
        "intentId": f"i-guess-{sender}-{target}-{game_id}",
        "senderId": sender,
        "parentId": f"lobby-join-{target}",
        "timestamp": 0,
        "payload": {
            "kind": "substrate-guess",
            "target": target,
            "guess": guess,
            "confidence": 0.8,
            "reasoning": "...",
            "game_id": game_id,
        },
    }


def _reveal(game_id: str, assignments: dict[str, str]) -> dict:
    return {
        "type": "INTENT",
        "intentId": f"i-reveal-{game_id}",
        "senderId": "host",
        "timestamp": 0,
        "payload": {
            "kind": "reveal",
            "game_id": game_id,
            "substrate_assignments": assignments,
        },
    }


def test_empty_input_yields_zeroed_stats():
    out = compute_principal_stats([], "p1")
    assert out["detection_accuracy"] == 0.0
    assert out["stealth_score"] == 0.0
    assert out["games_played"] == 0
    assert out["detection_total"] == 0
    assert out["stealth_total"] == 0


def test_single_game_perfect_detection():
    msgs = [
        _reveal("g1", {"p2": "agent", "p3": "human"}),
        _guess("p1", "p2", "agent", "g1"),
        _guess("p1", "p3", "human", "g1"),
    ]
    out = compute_principal_stats(msgs, "p1")
    assert out["detection_accuracy"] == 1.0
    assert out["detection_total"] == 2
    assert out["detection_correct"] == 2
    assert out["games_played"] == 1


def test_single_game_perfect_stealth():
    msgs = [
        _reveal("g1", {"p2": "agent"}),
        _guess("p1", "p2", "human", "g1"),
        _guess("p3", "p2", "human", "g1"),
    ]
    out = compute_principal_stats(msgs, "p2")
    assert out["stealth_score"] == 1.0
    assert out["stealth_total"] == 2
    assert out["stealth_wrong"] == 2


def test_unverifiable_guess_ignored_when_no_reveal():
    msgs = [_guess("p1", "p2", "agent", "g1")]
    out = compute_principal_stats(msgs, "p1")
    assert out["detection_total"] == 0
    assert out["detection_accuracy"] == 0.0
    assert out["games_played"] == 1


def test_multi_game_with_conflicting_assesses():
    msgs = [
        _reveal("g1", {"p2": "agent", "p3": "human"}),
        _reveal("g2", {"p2": "human", "p3": "agent"}),
        _guess("p1", "p2", "agent", "g1"),
        _guess("p1", "p2", "agent", "g2"),
        _guess("p1", "p3", "agent", "g1"),
        _guess("p1", "p3", "agent", "g2"),
    ]
    out = compute_principal_stats(msgs, "p1")
    assert out["detection_total"] == 4
    assert out["detection_correct"] == 2
    assert out["detection_accuracy"] == 0.5
    assert out["games_played"] == 2


def test_principal_in_reveal_counts_as_game_played():
    msgs = [_reveal("g1", {"p1": "human", "p2": "agent"})]
    out = compute_principal_stats(msgs, "p1")
    assert out["games_played"] == 1


def test_leaderboard_sorts_by_combined_score():
    msgs = [
        _reveal("g1", {"p1": "agent", "p2": "agent", "p3": "human"}),
        _guess("p1", "p2", "agent", "g1"),
        _guess("p1", "p3", "human", "g1"),
        _guess("p2", "p3", "agent", "g1"),
        _guess("p3", "p1", "human", "g1"),
    ]
    board = leaderboard(msgs)
    assert {r["principal"] for r in board} == {"p1", "p2", "p3"}
    p1_row = next(r for r in board if r["principal"] == "p1")
    assert p1_row["detection"] == 1.0
    assert p1_row["stealth"] == 1.0
    assert board[0]["principal"] == "p1"
    for r in board:
        assert set(r) == {"principal", "stealth", "detection", "games"}


def test_leaderboard_empty_input():
    assert leaderboard([]) == []
