"""Tests for src.game.winner.compute_winner."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.game.winner import compute_winner  # noqa: E402


ROLES_7 = {
    "p1": "mafia",
    "p2": "mafia",
    "p3": "detective",
    "p4": "doctor",
    "p5": "villager",
    "p6": "villager",
    "p7": "villager",
}


def test_game_ongoing_at_start():
    assert compute_winner(set(ROLES_7), ROLES_7) is None


def test_villagers_win_when_all_mafia_dead():
    living = {"p3", "p4", "p5", "p6", "p7"}
    assert compute_winner(living, ROLES_7) == "villagers"


def test_mafia_win_when_at_majority():
    living = {"p1", "p2", "p5"}
    assert compute_winner(living, ROLES_7) == "mafia"


def test_mafia_win_at_strict_parity():
    living = {"p1", "p2", "p5", "p6"}
    assert compute_winner(living, ROLES_7) == "mafia"


def test_villagers_still_winning_with_one_mafia_outnumbered():
    living = {"p1", "p3", "p4", "p5"}
    assert compute_winner(living, ROLES_7) is None


def test_draw_when_no_one_living():
    assert compute_winner(set(), ROLES_7) == "draw"


def test_unknown_principal_treated_as_non_mafia():
    living = {"p1", "p99"}
    assert compute_winner(living, ROLES_7) == "mafia"


def test_only_villagers_left_returns_villagers():
    living = {"p5", "p6"}
    assert compute_winner(living, ROLES_7) == "villagers"
