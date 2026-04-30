"""Tests for src.game.night_resolve.resolve_night."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.game.night_resolve import resolve_night  # noqa: E402


def test_no_coven_targets_no_kill():
    assert resolve_night([], None) == {"killed": None, "cause": "none"}


def test_unconfirmed_coven_target_no_kill():
    coven = [{"target": "p3", "confirmed": False}]
    assert resolve_night(coven, None) == {"killed": None, "cause": "none"}


def test_confirmed_target_no_doctor_kills():
    coven = [{"target": "p3", "confirmed": True}]
    assert resolve_night(coven, None) == {"killed": "p3", "cause": "mafia"}


def test_doctor_protect_matches_chosen_target_saves():
    coven = [{"target": "p3", "confirmed": True}]
    doctor = {"target": "p3"}
    assert resolve_night(coven, doctor) == {"killed": None, "cause": "saved"}


def test_doctor_protect_mismatched_target_kill_proceeds():
    coven = [{"target": "p3", "confirmed": True}]
    doctor = {"target": "p7"}
    assert resolve_night(coven, doctor) == {"killed": "p3", "cause": "mafia"}


def test_doctor_acts_but_no_coven_target_no_kill():
    doctor = {"target": "p3"}
    assert resolve_night([], doctor) == {"killed": None, "cause": "none"}


def test_first_confirmed_wins_over_later_confirmed():
    coven = [
        {"target": "p3", "confirmed": False},
        {"target": "p5", "confirmed": True},
        {"target": "p7", "confirmed": True},
    ]
    assert resolve_night(coven, None) == {"killed": "p5", "cause": "mafia"}


def test_malformed_doctor_protect_treated_as_no_protection():
    coven = [{"target": "p3", "confirmed": True}]
    assert resolve_night(coven, {"target": None}) == {"killed": "p3", "cause": "mafia"}
    assert resolve_night(coven, {}) == {"killed": "p3", "cause": "mafia"}
