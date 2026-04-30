"""Tests for the BotAgent scan-decide-emit loop.

Uses a synthetic `Session` mock and a stub `LLMClient` so no network or
SDK is touched. Phase messages are constructed by hand to exercise the
gating logic in BotAgent.should_act().
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.bots.base import BotAgent, _parse_json_response  # noqa: E402
from src.bots.context import (  # noqa: E402
    already_acted_this_phase,
    current_phase,
    extract_living_players,
    format_message,
)
from src.bots.prompts import build_system_prompt, build_user_message  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: stub Session + stub LLM
# ---------------------------------------------------------------------------


class StubSession:
    """Duck-typed stand-in for HttpSpaceToolSession."""

    def __init__(self, space_id: str, messages: list[dict] | None = None):
        self.current_space_id = space_id
        self.messages = list(messages or [])
        self.posted: list[dict] = []
        self._counter = 0

    def scan_full(self, space_id: str) -> dict:
        assert space_id == self.current_space_id
        return {"messages": list(self.messages), "latestSeq": len(self.messages)}

    def intent(self, *, content: str, payload: dict, parent_id: str | None = None, intent_id: str | None = None) -> dict:
        self._counter += 1
        return {
            "type": "INTENT",
            "intentId": intent_id or f"intent-test-{self._counter}",
            "parentId": parent_id or self.current_space_id,
            "senderId": "bot-principal",
            "timestamp": 1_000 + self._counter,
            "payload": payload,
        }

    def post(self, message: dict, *, step: str | None = None, **_: object) -> dict:
        self.posted.append(message)
        # Simulate the message being visible on subsequent scans.
        self.messages.append(message)
        return message


class StubLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, max_tokens: int = 512, temperature: float = 0.8) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise RuntimeError("StubLLM exhausted")
        return self.responses.pop(0)


def _phase_start(intent_id: str = "intent-phase-1", phase: str = "day-1-discussion", ts: int = 100) -> dict:
    return {
        "type": "INTENT",
        "intentId": intent_id,
        "parentId": "space-game",
        "senderId": "host",
        "timestamp": ts,
        "payload": {"kind": "phase-start", "phase": phase, "deadline_ms": ts + 60_000},
    }


def _make_bot(*, role: str = "villager",
              persona_overrides: dict | None = None,
              game_msgs: list[dict] | None = None,
              llm_responses: list[str] | None = None,
              extra_sessions: dict[str, StubSession] | None = None,
              tmp_path: Path | None = None,
              speaking_frequency: float = 1.0) -> tuple[BotAgent, StubSession, StubLLM]:
    persona = {
        "overlay": "PERSONA: \"test\"",
        "speaking_frequency": speaking_frequency,
        "vote_threshold": 0.5,
        "id_vote_propensity": 0.0,
        "temperature": 0.5,
        "scan_interval_s": 0.0,
    }
    if persona_overrides:
        persona.update(persona_overrides)

    game = StubSession("space-game", messages=game_msgs)
    sessions = {"game": game}
    if extra_sessions:
        sessions.update(extra_sessions)

    llm = StubLLM(llm_responses or [])
    bot = BotAgent(
        role=role,
        persona=persona,
        principal_id="bot-principal",
        sessions=sessions,
        llm=llm,
        substrate="agent",
        log_dir=tmp_path or Path("logs"),
        agent_name="test-bot",
        rng=random.Random(0),
        sleep_fn=lambda *_: None,
    )
    bot._all_principals.update({"bot-principal", "p2", "p3"})
    return bot, game, llm


# ---------------------------------------------------------------------------
# context.py
# ---------------------------------------------------------------------------


def test_format_message_one_line_with_text():
    msg = {
        "type": "INTENT",
        "intentId": "intent-1",
        "senderId": "p2",
        "timestamp": 7,
        "payload": {"kind": "accusation", "target": "p3", "content": "you're sus"},
    }
    line = format_message(msg)
    assert "\n" not in line
    assert "kind=accusation" in line
    assert "target=p3" in line
    assert "you're sus" in line


def test_current_phase_skips_completed():
    p1 = _phase_start("intent-p1", "day-1-discussion", ts=10)
    p2 = _phase_start("intent-p2", "day-1-vote", ts=20)
    complete_p1 = {
        "type": "COMPLETE",
        "promiseId": "promise-p1",
        "parentId": "space-game",
        "senderId": "host",
        "timestamp": 15,
        "payload": {"kind": "phase-end", "ends_phase_intent_id": "intent-p1"},
    }
    msgs = [p1, complete_p1, p2]
    assert current_phase(msgs)["intentId"] == "intent-p2"


def test_extract_living_players_removes_dead_not_saved():
    msgs = [
        {"type": "INTENT", "senderId": "host", "payload": {"kind": "death", "target": "p2", "cause": "lynch"}},
        {"type": "INTENT", "senderId": "host", "payload": {"kind": "death", "target": "p3", "cause": "saved"}},
    ]
    living = extract_living_players(msgs, {"p1", "p2", "p3"})
    assert living == {"p1", "p3"}


def test_already_acted_this_phase_vote_double():
    phase = _phase_start("intent-p1", "day-1-vote", ts=100)
    msgs = [
        phase,
        {"type": "INTENT", "intentId": "i-vote", "senderId": "bot-principal",
         "timestamp": 110, "payload": {"kind": "vote", "target": "p2"}},
    ]
    assert already_acted_this_phase(msgs, phase, "bot-principal", "vote") is True
    assert already_acted_this_phase(msgs, phase, "p2", "vote") is False


def test_already_acted_ignores_messages_before_phase():
    phase = _phase_start("intent-p2", "day-1-vote", ts=200)
    msgs = [
        {"type": "INTENT", "intentId": "old-vote", "senderId": "bot-principal",
         "timestamp": 50, "payload": {"kind": "vote", "target": "p2"}},
        phase,
    ]
    assert already_acted_this_phase(msgs, phase, "bot-principal", "vote") is False


# ---------------------------------------------------------------------------
# prompts.py
# ---------------------------------------------------------------------------


def test_build_system_prompt_contains_role_and_action():
    persona = {"overlay": "PERSONA: \"skeptic\"", "principal_id": "bot-principal"}
    prompt = build_system_prompt("mafia", "agent", "vote", persona)
    assert "Your role: mafia" in prompt
    assert "PERSONA: \"skeptic\"" in prompt
    assert "casting your vote" in prompt
    assert "JSON only" in prompt


def test_build_user_message_truncates_to_30():
    msgs = [
        {"type": "INTENT", "senderId": f"p{i}", "timestamp": i,
         "payload": {"kind": "observation", "content": f"msg-{i}"}}
        for i in range(60)
    ]
    out = build_user_message(
        phase=_phase_start(),
        public_messages=msgs,
        private_messages=None,
        action_kind="discussion-utterance",
        living_players={"p1"},
    )
    assert "msg-59" in out
    assert "msg-30" in out
    assert "msg-29" not in out
    assert "Action requested" in out


# ---------------------------------------------------------------------------
# BotAgent gating
# ---------------------------------------------------------------------------


def test_should_act_returns_discussion_when_phase_open(tmp_path):
    phase = _phase_start("intent-p1", "day-1-discussion", ts=100)
    bot, _, _ = _make_bot(game_msgs=[phase], tmp_path=tmp_path, speaking_frequency=1.0)
    assert bot.should_act(phase, {"game": [phase]}) == "discussion-utterance"


def test_should_act_skips_discussion_if_already_spoke(tmp_path):
    phase = _phase_start("intent-p1", "day-1-discussion", ts=100)
    msgs = [
        phase,
        {"type": "INTENT", "intentId": "i-obs", "senderId": "bot-principal",
         "timestamp": 110, "payload": {"kind": "observation", "content": "hi"}},
    ]
    bot, _, _ = _make_bot(game_msgs=msgs, tmp_path=tmp_path, speaking_frequency=1.0)
    assert bot.should_act(phase, {"game": msgs}) is None


def test_should_act_speaking_frequency_zero_skips(tmp_path):
    phase = _phase_start("intent-p1", "day-1-discussion", ts=100)
    bot, _, _ = _make_bot(game_msgs=[phase], tmp_path=tmp_path, speaking_frequency=0.0)
    assert bot.should_act(phase, {"game": [phase]}) is None


def test_should_act_vote_once_only(tmp_path):
    phase = _phase_start("intent-p1", "day-1-vote", ts=100)
    msgs = [
        phase,
        {"type": "INTENT", "intentId": "i-v", "senderId": "bot-principal",
         "timestamp": 110, "payload": {"kind": "vote", "target": "p2"}},
    ]
    bot, _, _ = _make_bot(game_msgs=msgs, tmp_path=tmp_path)
    assert bot.should_act(phase, {"game": msgs}) is None


def test_should_act_night_mafia_only_for_mafia_role(tmp_path):
    phase = _phase_start("intent-p1", "night-1-mafia", ts=100)
    coven = StubSession("space-coven", messages=[phase])
    bot, _, _ = _make_bot(role="mafia", game_msgs=[phase], tmp_path=tmp_path,
                          extra_sessions={"mafia": coven})
    assert bot.should_act(phase, {"game": [phase], "mafia": [phase]}) == "coven-target"

    bot2, _, _ = _make_bot(role="villager", game_msgs=[phase], tmp_path=tmp_path)
    assert bot2.should_act(phase, {"game": [phase]}) is None


def test_should_act_returns_none_when_no_phase(tmp_path):
    bot, _, _ = _make_bot(tmp_path=tmp_path)
    assert bot.should_act(None, {}) is None


# ---------------------------------------------------------------------------
# decide + emit + scan_all
# ---------------------------------------------------------------------------


def test_scan_decide_emit_discussion_utterance(tmp_path):
    phase = _phase_start("intent-p1", "day-1-discussion", ts=100)
    llm_response = json.dumps({"kind": "accusation", "text": "p2 was quiet", "target": "p2"})
    bot, game, llm = _make_bot(
        game_msgs=[phase],
        tmp_path=tmp_path,
        llm_responses=[llm_response],
    )

    scans = bot.scan_all()
    assert "game" in scans

    action = bot.should_act(phase, scans)
    assert action == "discussion-utterance"

    decision = bot.decide(action, scans)
    assert decision is not None
    assert decision["kind"] == "accusation"

    emitted = bot.emit(decision)
    assert emitted is not None
    assert game.posted, "expected at least one posted message"
    posted_payload = game.posted[-1]["payload"]
    assert posted_payload["kind"] == "accusation"
    assert posted_payload["target"] == "p2"
    # Used the LLM exactly once.
    assert len(llm.calls) == 1


def test_emit_vote_validates_payload(tmp_path):
    phase = _phase_start("intent-p1", "day-1-vote", ts=100)
    bot, game, _ = _make_bot(game_msgs=[phase], tmp_path=tmp_path)
    decision = {
        "_action_kind": "vote",
        "_phase": phase,
        "kind": "vote",
        "target": "p2",
        "rationale": "they avoided answering",
    }
    bot.emit(decision)
    assert game.posted[-1]["payload"]["kind"] == "vote"
    assert game.posted[-1]["payload"]["target"] == "p2"


def test_emit_drops_vote_with_missing_target(tmp_path):
    phase = _phase_start("intent-p1", "day-1-vote", ts=100)
    bot, game, _ = _make_bot(game_msgs=[phase], tmp_path=tmp_path)
    decision = {"_action_kind": "vote", "_phase": phase, "kind": "vote"}
    assert bot.emit(decision) is None
    assert game.posted == []


def test_emit_coven_target_routes_to_mafia_session(tmp_path):
    phase = _phase_start("intent-p1", "night-1-mafia", ts=100)
    coven = StubSession("space-coven")
    bot, game, _ = _make_bot(
        role="mafia",
        game_msgs=[phase],
        tmp_path=tmp_path,
        extra_sessions={"mafia": coven},
    )
    decision = {
        "_action_kind": "coven-target",
        "_phase": phase,
        "kind": "coven-target",
        "target": "p3",
        "reasoning": "loud and confident",
    }
    bot.emit(decision)
    assert coven.posted, "coven-target should post to coven session"
    assert game.posted == []
    assert coven.posted[-1]["payload"]["kind"] == "coven-target"


def test_decide_returns_none_on_llm_error(tmp_path):
    from src.bots.llm import LLMError

    class BoomLLM:
        def complete(self, *a, **k):
            raise LLMError("boom")

    phase = _phase_start("intent-p1", "day-1-discussion", ts=100)
    bot, _, _ = _make_bot(game_msgs=[phase], tmp_path=tmp_path)
    bot.llm = BoomLLM()
    assert bot.decide("discussion-utterance", {"game": [phase]}) is None


def test_decide_returns_none_on_unparseable_json(tmp_path):
    phase = _phase_start("intent-p1", "day-1-discussion", ts=100)
    bot, _, _ = _make_bot(game_msgs=[phase], tmp_path=tmp_path,
                          llm_responses=["not json at all"])
    assert bot.decide("discussion-utterance", {"game": [phase]}) is None


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------


def test_parse_json_response_handles_code_fence():
    raw = "Here you go:\n```json\n{\"kind\":\"vote\",\"target\":\"p2\"}\n```\n"
    assert _parse_json_response(raw) == {"kind": "vote", "target": "p2"}


def test_parse_json_response_handles_plain_object():
    assert _parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_json_response_handles_array_with_prose():
    raw = "Sure! [\n  {\"target_principal\": \"p2\", \"guess\": \"agent\", \"confidence\": 0.9}\n]"
    parsed = _parse_json_response(raw)
    assert isinstance(parsed, list) and parsed[0]["guess"] == "agent"


def test_parse_json_response_returns_none_on_garbage():
    assert _parse_json_response("definitely not json") is None
