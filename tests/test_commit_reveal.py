"""Tests for src.game.commit_reveal."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.game.commit_reveal import commit, generate_salts, verify  # noqa: E402


def test_generate_salts_returns_n_unique_16_byte_values():
    salts = generate_salts(7)
    assert len(salts) == 7
    assert all(isinstance(s, bytes) and len(s) == 16 for s in salts)
    assert len({bytes(s) for s in salts}) == 7


def test_generate_salts_zero():
    assert generate_salts(0) == []


def test_commit_is_deterministic_for_same_value_and_salt():
    salt = b"\x00" * 16
    assert commit("mafia", salt) == commit("mafia", salt)


def test_commit_changes_when_salt_changes():
    assert commit("mafia", b"\x00" * 16) != commit("mafia", b"\x01" * 16)


def test_commit_changes_when_value_changes():
    salt = b"\x00" * 16
    assert commit("mafia", salt) != commit("villager", salt)


def test_round_trip_verify_passes():
    salt = generate_salts(1)[0]
    c = commit("detective", salt)
    assert verify("detective", salt.hex(), c) is True


def test_verify_detects_value_tampering():
    salt = generate_salts(1)[0]
    c = commit("mafia", salt)
    assert verify("villager", salt.hex(), c) is False


def test_verify_detects_salt_tampering():
    salt = generate_salts(1)[0]
    c = commit("mafia", salt)
    bad_salt = bytes(b ^ 0x01 for b in salt)
    assert verify("mafia", bad_salt.hex(), c) is False


def test_verify_detects_commitment_tampering():
    salt = generate_salts(1)[0]
    c = commit("mafia", salt)
    flipped = c[:-1] + ("0" if c[-1] != "0" else "1")
    assert verify("mafia", salt.hex(), flipped) is False


def test_verify_returns_false_on_invalid_hex():
    salt = generate_salts(1)[0]
    c = commit("mafia", salt)
    assert verify("mafia", "not-hex!", c) is False


def test_full_seven_seat_commit_reveal_round_trip():
    roles = ["mafia", "mafia", "detective", "doctor", "villager", "villager", "villager"]
    salts = generate_salts(len(roles))
    commits = [commit(r, s) for r, s in zip(roles, salts)]
    for r, s, c in zip(roles, salts, commits):
        assert verify(r, s.hex(), c) is True
    assert verify(roles[0], salts[1].hex(), commits[0]) is False
