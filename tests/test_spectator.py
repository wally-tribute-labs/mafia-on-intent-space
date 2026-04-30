"""Tests for the spectator agent.

No network, no LLM — uses a stub session and the deterministic fallback
summary path so the spectator's correlation/posting logic can be exercised
in isolation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.spectator.main import (  # noqa: E402
    SPECTATOR_AUTHOR_TAG,
    build_summary_payload,
    collect_game_context,
    derive_fallback_summary,
    find_completion_signals,
    find_game_ends,
    find_lobby_open_for_game_end,
    find_lobby_open_for_trigger,
    post_summary,
    summarize_game,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class StubSession:
    def __init__(self, space_id: str = "commons"):
        self.current_space_id = space_id
        self.posted: list[dict] = []
        self._n = 0

    def intent(self, *, content, payload, parent_id=None, intent_id=None):
        self._n += 1
        return {
            "type": "INTENT",
            "intentId": intent_id or f"intent-spectator-{self._n}",
            "parentId": parent_id or self.current_space_id,
            "senderId": "spectator-principal",
            "timestamp": 9000 + self._n,
            "payload": payload,
            "_content": content,
        }

    def post(self, message, *, step=None, **_):
        self.posted.append({"step": step, "message": message})
        return message


def _intent(intent_id: str, parent_id: str, sender: str, kind: str, *, payload_extra: dict | None = None, ts: int = 0) -> dict:
    payload = {"kind": kind}
    if payload_extra:
        payload.update(payload_extra)
    return {
        "type": "INTENT",
        "intentId": intent_id,
        "parentId": parent_id,
        "senderId": sender,
        "timestamp": ts,
        "payload": payload,
    }


def _promise(promise_id: str, parent_id: str, sender: str, kind: str, *, payload_extra: dict | None = None, ts: int = 0) -> dict:
    payload = {"kind": kind}
    if payload_extra:
        payload.update(payload_extra)
    return {
        "type": "PROMISE",
        "promiseId": promise_id,
        "parentId": parent_id,
        "senderId": sender,
        "timestamp": ts,
        "payload": payload,
    }


def _build_commons_log() -> list[dict]:
    """A small commons trace covering one complete game."""
    lobby = _intent(
        "lobby-1", "commons", "host", "lobby-open",
        payload_extra={"seats": 7, "game_id": "g-1"}, ts=10,
    )
    joiners = [
        _promise(f"join-{i}", "lobby-1", f"p{i}", "lobby-join",
                 payload_extra={"principal_id": f"p{i}", "agent_label": f"player-{i}", "game_id": "g-1"},
                 ts=11 + i)
        for i in range(1, 4)
    ]
    reveal = _intent(
        "reveal-1", "lobby-1", "host", "reveal",
        payload_extra={
            "game_id": "g-1",
            "role_assignments": {"p1": "mafia", "p2": "villager", "p3": "doctor"},
            "substrate_assignments": {"p1": "agent", "p2": "human", "p3": "agent"},
            "salts": {"p1": "aa", "p2": "bb", "p3": "cc"},
        },
        ts=99,
    )
    guesses = [
        _intent(
            f"guess-{i}", "lobby-1", f"p{i}", "substrate-guess",
            payload_extra={"target": "p1", "guess": "agent", "confidence": 0.8, "game_id": "g-1"},
            ts=100 + i,
        )
        for i in range(2, 4)
    ]
    game_end = _intent(
        "ge-1", "lobby-1", "host", "game-end",
        payload_extra={"winner": "villagers", "game_id": "g-1"},
        ts=200,
    )
    return [lobby, *joiners, reveal, *guesses, game_end]


# ---------------------------------------------------------------------------
# find_game_ends
# ---------------------------------------------------------------------------


def test_find_game_ends_returns_only_game_end_intents_in_order():
    log = _build_commons_log()
    ends = find_game_ends(log)
    assert [m["intentId"] for m in ends] == ["ge-1"]


def test_find_game_ends_handles_empty_log():
    assert find_game_ends([]) == []


def test_find_game_ends_ignores_non_intent_types():
    msgs = [_promise("p", "commons", "host", "game-end")]
    assert find_game_ends(msgs) == []


# ---------------------------------------------------------------------------
# find_lobby_open_for_game_end
# ---------------------------------------------------------------------------


def _ge(log: list[dict]) -> dict:
    return next(m for m in log if m.get("intentId") == "ge-1")


def _lobby(log: list[dict]) -> dict:
    return next(m for m in log if m.get("intentId") == "lobby-1")


def test_find_lobby_open_via_parent_chain():
    log = _build_commons_log()
    ge = _ge(log)
    lobby = find_lobby_open_for_game_end(ge, log)
    assert lobby is not None
    assert lobby["intentId"] == "lobby-1"


def test_find_lobby_open_falls_back_to_game_id_match():
    # game-end posted at top level (parentId=commons) instead of under lobby
    lobby = _intent("lobby-2", "commons", "host", "lobby-open",
                    payload_extra={"seats": 7, "game_id": "g-2"})
    ge = _intent("ge-2", "commons", "host", "game-end",
                 payload_extra={"winner": "mafia", "game_id": "g-2"})
    found = find_lobby_open_for_game_end(ge, [lobby, ge])
    assert found is not None
    assert found["intentId"] == "lobby-2"


def test_find_lobby_open_returns_none_when_unknown():
    ge = _intent("ge-x", "commons", "host", "game-end",
                 payload_extra={"winner": "draw"})
    assert find_lobby_open_for_game_end(ge, [ge]) is None


# ---------------------------------------------------------------------------
# collect_game_context
# ---------------------------------------------------------------------------


def test_collect_game_context_groups_related_messages():
    log = _build_commons_log()
    ge = _ge(log)
    lobby = _lobby(log)
    ctx = collect_game_context(ge, lobby, log)
    assert ctx["lobby_open"] is lobby
    assert ctx["game_end"] is ge
    assert ctx["reveal"] is not None and ctx["reveal"]["intentId"] == "reveal-1"
    assert len(ctx["joiners"]) == 3
    assert len(ctx["substrate_guesses"]) == 2
    ids = {m.get("intentId") or m.get("promiseId") for m in ctx["messages"]}
    assert {"lobby-1", "reveal-1", "ge-1", "guess-2", "guess-3"}.issubset(ids)


# ---------------------------------------------------------------------------
# Fallback summarization (no LLM)
# ---------------------------------------------------------------------------


def test_derive_fallback_summary_reports_winner_and_counts():
    log = _build_commons_log()
    ge = _ge(log)
    lobby = _lobby(log)
    ctx = collect_game_context(ge, lobby, log)
    out = derive_fallback_summary(ctx)
    assert out["winners"] == "villagers"
    assert out["mvp"] is None
    assert "villagers" in out["summary_text"]
    assert "3 player" in out["summary_text"] or "3 player(s)" in out["summary_text"]


def test_summarize_game_uses_fallback_when_llm_is_none():
    log = _build_commons_log()
    ge = _ge(log)
    lobby = _lobby(log)
    ctx = collect_game_context(ge, lobby, log)
    out = summarize_game(ctx, llm=None)
    assert out["winners"] == "villagers"
    assert isinstance(out["summary_text"], str) and out["summary_text"]


# ---------------------------------------------------------------------------
# Payload + posting
# ---------------------------------------------------------------------------


def test_build_summary_payload_validates_against_schema():
    log = _build_commons_log()
    ge = _ge(log)
    lobby = _lobby(log)
    summary = {"summary_text": "Villagers prevailed.", "winners": "villagers", "mvp": None}
    payload = build_summary_payload(summary, game_end=ge, lobby_open=lobby)
    assert payload["kind"] == "summary"
    assert payload["summary_text"] == "Villagers prevailed."
    assert payload["winners"] == "villagers"
    assert payload["game_id"] == "g-1"
    assert payload["data"]["lobby_open_intent_id"] == "lobby-1"
    assert payload["data"]["game_end_intent_id"] == "ge-1"


def test_post_summary_uses_lobby_open_as_parent():
    log = _build_commons_log()
    ge = _ge(log)
    lobby = _lobby(log)
    payload = build_summary_payload(
        {"summary_text": "ok.", "winners": "villagers", "mvp": None},
        game_end=ge, lobby_open=lobby,
    )
    session = StubSession()
    posted = post_summary(session, summary_payload=payload, lobby_open=lobby)
    assert posted["parentId"] == "lobby-1"
    assert session.posted and session.posted[0]["step"] == "spectator.summary"
    assert session.posted[0]["message"]["payload"]["kind"] == "summary"


def test_post_summary_falls_back_to_commons_when_no_lobby_open():
    ge = _intent("ge-orphan", "commons", "host", "game-end",
                 payload_extra={"winner": "draw"})
    payload = build_summary_payload(
        {"summary_text": "no lobby found.", "winners": "draw", "mvp": None},
        game_end=ge, lobby_open=None,
    )
    session = StubSession()
    posted = post_summary(session, summary_payload=payload, lobby_open=None)
    assert posted["parentId"] == "commons"


# ---------------------------------------------------------------------------
# Host-summary trigger path (the live host posts kind=summary, not game-end)
# ---------------------------------------------------------------------------


def _host_summary_log() -> list[dict]:
    """Mirror what src/host/phase_loop._post_summary_to_commons emits."""
    lobby = _intent(
        "lobby-h", "commons", "host", "lobby-open",
        payload_extra={"seats": 7, "game_id": "g-h"},
    )
    joiners = [
        _promise(f"join-h-{i}", "lobby-h", f"p{i}", "lobby-join",
                 payload_extra={"principal_id": f"p{i}", "agent_label": f"player-{i}"},
                 ts=10 + i)
        for i in range(1, 4)
    ]
    host_summary = _intent(
        "host-sum-h", "lobby-h", "host", "summary",
        payload_extra={
            "summary_text": "Game g-h ended. Winner: villagers.",
            "winners": "villagers",
            "game_id": "g-h",
            "data": {
                "winner": "villagers",
                "role_assignments": {"p1": "mafia", "p2": "villager"},
                "substrate_assignments": {"p1": "agent", "p2": "human"},
                "duration_ms": 1200,
                "final_living": ["p2"],
            },
        },
        ts=200,
    )
    return [lobby, *joiners, host_summary]


def test_find_completion_signals_includes_foreign_summary():
    log = _host_summary_log()
    signals = find_completion_signals(log, my_principal_id="me")
    ids = [s.get("intentId") for s in signals]
    assert "host-sum-h" in ids


def test_find_completion_signals_skips_my_own_summary():
    log = _host_summary_log()
    log.append(_intent(
        "my-sum", "lobby-h", "me", "summary",
        payload_extra={
            "summary_text": "...", "winners": "villagers", "game_id": "g-h",
            "data": {"author": SPECTATOR_AUTHOR_TAG},
        },
        ts=300,
    ))
    signals = find_completion_signals(log, my_principal_id="me")
    ids = [s.get("intentId") for s in signals]
    assert "my-sum" not in ids
    assert "host-sum-h" in ids


def test_find_completion_signals_skips_other_spectator_summaries():
    log = _host_summary_log()
    log.append(_intent(
        "other-spec-sum", "lobby-h", "other-spectator", "summary",
        payload_extra={
            "summary_text": "...", "winners": "villagers", "game_id": "g-h",
            "data": {"author": SPECTATOR_AUTHOR_TAG},
        },
        ts=301,
    ))
    signals = find_completion_signals(log, my_principal_id="me")
    ids = [s.get("intentId") for s in signals]
    assert "other-spec-sum" not in ids


def test_collect_context_synthesizes_reveal_from_host_summary():
    log = _host_summary_log()
    trigger = next(m for m in log if m.get("intentId") == "host-sum-h")
    lobby = find_lobby_open_for_trigger(trigger, log)
    ctx = collect_game_context(trigger, lobby, log)
    assert ctx["reveal"] is not None
    rp = ctx["reveal"]["payload"]
    assert rp["kind"] == "reveal"
    assert rp["role_assignments"]["p1"] == "mafia"
    assert rp["substrate_assignments"]["p1"] == "agent"
    assert ctx["game_end"]["payload"].get("winner") == "villagers"


def test_full_loop_against_host_summary_uses_lobby_open_parent():
    log = _host_summary_log()
    trigger = next(m for m in log if m.get("intentId") == "host-sum-h")
    lobby = find_lobby_open_for_trigger(trigger, log)
    ctx = collect_game_context(trigger, lobby, log)
    summary = summarize_game(ctx, llm=None)
    payload = build_summary_payload(
        summary, game_end=ctx["game_end"], lobby_open=lobby, trigger=trigger,
    )
    assert payload["data"]["author"] == SPECTATOR_AUTHOR_TAG
    assert payload["data"]["trigger_intent_id"] == "host-sum-h"
    assert payload["data"]["trigger_kind"] == "summary"

    session = StubSession()
    posted = post_summary(session, summary_payload=payload, lobby_open=lobby)
    assert posted["parentId"] == "lobby-h"
