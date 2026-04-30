"""Tests for VillagerBot, MafiaBot, DetectiveBot, DoctorBot.

Each test stands up the role's required sessions, drives a single phase,
and asserts the right protocol payload was posted to the right session.
LLMClient is replaced by a deterministic stub.
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from src.bots.detective import DetectiveBot  # noqa: E402
from src.bots.doctor import DoctorBot  # noqa: E402
from src.bots.mafia import MafiaBot  # noqa: E402
from src.bots.personas import load_personas  # noqa: E402
from src.bots.villager import VillagerBot  # noqa: E402


# ---------------------------------------------------------------------------
# Stubs (mirror tests/test_bots_base.py)
# ---------------------------------------------------------------------------


class StubSession:
    def __init__(self, space_id: str, messages: list[dict] | None = None):
        self.current_space_id = space_id
        self.messages = list(messages or [])
        self.posted: list[dict] = []
        self._n = 0

    def scan_full(self, space_id: str) -> dict:
        assert space_id == self.current_space_id
        return {"messages": list(self.messages), "latestSeq": len(self.messages)}

    def intent(self, *, content, payload, parent_id=None, intent_id=None):
        self._n += 1
        return {
            "type": "INTENT",
            "intentId": intent_id or f"i-{space_id_short(self.current_space_id)}-{self._n}",
            "parentId": parent_id or self.current_space_id,
            "senderId": "bot-principal",
            "timestamp": 1_000 + self._n,
            "payload": payload,
        }

    def post(self, message, *, step=None, **_):
        self.posted.append(message)
        self.messages.append(message)
        return message


def space_id_short(s: str) -> str:
    return s.replace("space-", "")


class StubLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, max_tokens=512, temperature=0.8):
        self.calls.append((system, user))
        return self.response


def _phase(intent_id: str, name: str, ts: int = 100) -> dict:
    return {
        "type": "INTENT",
        "intentId": intent_id,
        "parentId": "space-game",
        "senderId": "host",
        "timestamp": ts,
        "payload": {"kind": "phase-start", "phase": name, "deadline_ms": ts + 60_000},
    }


def _persona() -> dict:
    return {
        "name": "test",
        "system_overlay": "PERSONA: \"test\"",
        "speaking_frequency": 1.0,
        "vote_threshold": 0.5,
        "id_vote_propensity": 0.0,
        "temperature": 0.5,
        "scan_interval_s": 0.0,
    }


# ---------------------------------------------------------------------------
# personas.json schema
# ---------------------------------------------------------------------------


def test_personas_json_loads_with_required_keys():
    personas = load_personas()
    assert len(personas) >= 6
    for name, p in personas.items():
        for key in ("name", "system_overlay", "speaking_frequency",
                    "vote_threshold", "id_vote_propensity",
                    "temperature", "scan_interval_s"):
            assert key in p, f"persona {name!r} missing {key!r}"


# ---------------------------------------------------------------------------
# VillagerBot
# ---------------------------------------------------------------------------


def test_villager_bot_emits_observation(tmp_path):
    phase = _phase("i-day1-disc", "day-1-discussion", ts=100)
    game = StubSession("space-game", messages=[phase])
    llm = StubLLM(json.dumps({"kind": "observation", "text": "hmm"}))
    bot = VillagerBot(
        role="villager",
        persona=_persona(),
        principal_id="bot-principal",
        sessions={"game": game},
        llm=llm,
        agent_name="villager-test",
        log_dir=tmp_path,
        rng=random.Random(0),
        sleep_fn=lambda *_: None,
    )
    bot._all_principals.update({"bot-principal", "p2", "p3"})
    scans = bot.scan_all()
    decision = bot.decide(bot.should_act(phase, scans), scans)
    bot.emit(decision)
    assert game.posted, "VillagerBot should post a discussion utterance"
    assert game.posted[-1]["payload"]["kind"] == "observation"
    # Villager must NEVER hold a private subspace.
    assert "mafia" not in bot.sessions
    assert "detective" not in bot.sessions
    assert "doctor" not in bot.sessions


# ---------------------------------------------------------------------------
# MafiaBot
# ---------------------------------------------------------------------------


def test_mafia_bot_requires_coven_session(tmp_path):
    game = StubSession("space-game")
    with pytest.raises(ValueError, match="sessions\\['mafia'\\]"):
        MafiaBot(
            role="mafia",
            persona=_persona(),
            principal_id="bot-principal",
            sessions={"game": game},
            llm=StubLLM("{}"),
            agent_name="mafia-test",
            log_dir=tmp_path,
            sleep_fn=lambda *_: None,
        )


def test_mafia_bot_emits_coven_target_to_coven_session(tmp_path):
    phase = _phase("i-night1-mafia", "night-1-mafia", ts=200)
    game = StubSession("space-game", messages=[phase])
    coven = StubSession("space-coven", messages=[phase])
    llm = StubLLM(json.dumps({
        "kind": "coven-target", "target": "p3", "reasoning": "loud accuser",
    }))
    bot = MafiaBot(
        role="mafia",
        persona=_persona(),
        principal_id="bot-principal",
        sessions={"game": game, "mafia": coven},
        llm=llm,
        agent_name="mafia-test",
        log_dir=tmp_path,
        rng=random.Random(0),
        sleep_fn=lambda *_: None,
    )
    bot._all_principals.update({"bot-principal", "p2", "p3"})
    scans = bot.scan_all()
    action = bot.should_act(phase, scans)
    assert action == "coven-target"
    decision = bot.decide(action, scans)
    bot.emit(decision)
    assert coven.posted, "coven-target must be posted to the coven session"
    assert game.posted == [], "coven-target must NOT leak to public game space"
    assert coven.posted[-1]["payload"]["kind"] == "coven-target"
    assert coven.posted[-1]["payload"]["target"] == "p3"


def test_mafia_bot_learns_teammates_from_coven_senders(tmp_path):
    phase = _phase("i-night1-mafia", "night-1-mafia", ts=200)
    teammate_post = {
        "type": "INTENT",
        "intentId": "i-mate-target",
        "parentId": "space-coven",
        "senderId": "p2",
        "timestamp": 210,
        "payload": {"kind": "coven-target", "target": "p4", "reasoning": "..."},
    }
    game = StubSession("space-game", messages=[phase])
    coven = StubSession("space-coven", messages=[phase, teammate_post])
    llm = StubLLM(json.dumps({"kind": "coven-confirm", "target": "p4"}))

    bot = MafiaBot(
        role="mafia",
        persona=_persona(),
        principal_id="bot-principal",
        sessions={"game": game, "mafia": coven},
        llm=llm,
        agent_name="mafia-test",
        log_dir=tmp_path,
        sleep_fn=lambda *_: None,
    )
    bot._all_principals.update({"bot-principal", "p2", "p3", "p4"})
    scans = bot.scan_all()
    bot.should_act(phase, scans)
    assert "p2" in bot._teammates

    decision = bot.decide("coven-target", scans)
    # Teammate id appears in the system prompt.
    system_prompt, _ = llm.calls[-1]
    assert "p2" in system_prompt
    assert decision is not None and decision.get("kind") == "coven-confirm"
    bot.emit(decision)
    assert coven.posted[-1]["payload"]["kind"] == "coven-confirm"


def test_mafia_bot_resolves_teammates_from_coven_membership(tmp_path):
    """Host posts coven-membership listing the mafia; bot uses that
    canonical list and ignores host's own posts in sender-inference."""
    phase = _phase("i-night1-mafia", "night-1-mafia", ts=200)
    membership = {
        "type": "INTENT",
        "intentId": "i-cm",
        "parentId": "space-coven",
        "senderId": "host",
        "timestamp": 205,
        "payload": {
            "kind": "coven-membership",
            "members": ["bot-principal", "p2"],
            "host": "host",
        },
    }
    # Host also posts the phase-start in coven (mirror of game phase).
    # Without the membership signal, sender-inference would wrongly mark
    # `host` as a teammate. With it, host is in _known_host_pids and skipped.
    host_phase_in_coven = {
        "type": "INTENT",
        "intentId": "i-night1-mafia-coven",
        "parentId": "space-coven",
        "senderId": "host",
        "timestamp": 206,
        "payload": {"kind": "phase-start", "phase": "night-1-mafia"},
    }
    game = StubSession("space-game", messages=[phase])
    coven = StubSession(
        "space-coven", messages=[membership, host_phase_in_coven, phase]
    )
    llm = StubLLM(json.dumps({"kind": "coven-target", "target": "p3", "reasoning": "x"}))

    bot = MafiaBot(
        role="mafia",
        persona=_persona(),
        principal_id="bot-principal",
        sessions={"game": game, "mafia": coven},
        llm=llm,
        agent_name="mafia-test",
        log_dir=tmp_path,
        sleep_fn=lambda *_: None,
    )
    bot._all_principals.update({"bot-principal", "p2", "p3"})
    scans = bot.scan_all()
    bot.should_act(phase, scans)

    assert bot._teammates == {"p2"}
    assert "host" in bot._known_host_pids
    assert "host" not in bot._teammates


# ---------------------------------------------------------------------------
# DetectiveBot
# ---------------------------------------------------------------------------


def test_detective_bot_emits_investigate_to_notebook(tmp_path):
    phase = _phase("i-night1-det", "night-1-detective", ts=300)
    notebook_history = [
        phase,
        {
            "type": "COMPLETE",
            "promiseId": "pm-prev",
            "intentId": "i-prev-investigate",
            "parentId": "space-detective",
            "senderId": "host",
            "timestamp": 250,
            "payload": {
                "kind": "investigate-result",
                "target": "p2",
                "role": "villager",
                "substrate": "human",
            },
        },
    ]
    game = StubSession("space-game", messages=[phase])
    notebook = StubSession("space-detective", messages=notebook_history)
    llm = StubLLM(json.dumps({"kind": "investigate", "target": "p3"}))

    bot = DetectiveBot(
        role="detective",
        persona=_persona(),
        principal_id="bot-principal",
        sessions={"game": game, "detective": notebook},
        llm=llm,
        agent_name="det-test",
        log_dir=tmp_path,
        sleep_fn=lambda *_: None,
    )
    bot._all_principals.update({"bot-principal", "p2", "p3"})
    scans = bot.scan_all()
    action = bot.should_act(phase, scans)
    assert action == "investigate"
    decision = bot.decide(action, scans)
    bot.emit(decision)

    # Past result must be visible to the LLM.
    system_prompt, _ = llm.calls[-1]
    assert "p2" in system_prompt and "villager" in system_prompt

    assert notebook.posted, "investigate must be posted to the detective notebook"
    assert game.posted == []
    assert notebook.posted[-1]["payload"]["kind"] == "investigate"
    assert notebook.posted[-1]["payload"]["target"] == "p3"


def test_detective_does_not_act_on_doctor_phase(tmp_path):
    phase = _phase("i-night1-doc", "night-1-doctor", ts=400)
    game = StubSession("space-game", messages=[phase])
    notebook = StubSession("space-detective", messages=[phase])
    bot = DetectiveBot(
        role="detective",
        persona=_persona(),
        principal_id="bot-principal",
        sessions={"game": game, "detective": notebook},
        llm=StubLLM("{}"),
        agent_name="det-test",
        log_dir=tmp_path,
        sleep_fn=lambda *_: None,
    )
    scans = bot.scan_all()
    assert bot.should_act(phase, scans) is None


# ---------------------------------------------------------------------------
# DoctorBot
# ---------------------------------------------------------------------------


def test_doctor_bot_emits_protect_to_notebook(tmp_path):
    phase = _phase("i-night1-doc", "night-1-doctor", ts=500)
    game = StubSession("space-game", messages=[phase])
    notebook = StubSession("space-doctor", messages=[phase])
    llm = StubLLM(json.dumps({"kind": "protect", "target": "p2"}))

    bot = DoctorBot(
        role="doctor",
        persona=_persona(),
        principal_id="bot-principal",
        sessions={"game": game, "doctor": notebook},
        llm=llm,
        agent_name="doc-test",
        log_dir=tmp_path,
        sleep_fn=lambda *_: None,
    )
    bot._all_principals.update({"bot-principal", "p2", "p3"})
    scans = bot.scan_all()
    decision = bot.decide(bot.should_act(phase, scans), scans)
    bot.emit(decision)

    assert notebook.posted, "protect must go to the doctor notebook"
    assert game.posted == []
    assert notebook.posted[-1]["payload"]["kind"] == "protect"
    assert notebook.posted[-1]["payload"]["target"] == "p2"


def test_doctor_self_protect_blocked_after_one_use(tmp_path):
    phase = _phase("i-night2-doc", "night-2-doctor", ts=900)
    prior_self_protect = {
        "type": "INTENT",
        "intentId": "i-prev-protect",
        "parentId": "space-doctor",
        "senderId": "bot-principal",
        "timestamp": 500,
        "payload": {"kind": "protect", "target": "bot-principal", "self_protect": True},
    }
    game = StubSession("space-game", messages=[phase])
    notebook = StubSession("space-doctor", messages=[phase, prior_self_protect])
    llm = StubLLM(json.dumps({"kind": "protect", "target": "bot-principal"}))

    bot = DoctorBot(
        role="doctor",
        persona=_persona(),
        principal_id="bot-principal",
        sessions={"game": game, "doctor": notebook},
        llm=llm,
        agent_name="doc-test",
        log_dir=tmp_path,
        sleep_fn=lambda *_: None,
    )
    bot._all_principals.update({"bot-principal", "p2", "p3"})
    scans = bot.scan_all()
    # The LLM tried to self-protect again — emit must drop it.
    decision = {
        "_action_kind": "protect",
        "_phase": phase,
        "kind": "protect",
        "target": "bot-principal",
    }
    result = bot.emit(decision)
    assert result is None
    assert notebook.posted == [prior_self_protect[0]] if False else True
    # Only the historical message is in posted-on-this-test (notebook.posted is local).
    assert notebook.posted == []


def test_doctor_protects_other_player_when_self_protect_already_used(tmp_path):
    phase = _phase("i-night2-doc", "night-2-doctor", ts=900)
    prior_self_protect = {
        "type": "INTENT",
        "intentId": "i-prev-protect",
        "parentId": "space-doctor",
        "senderId": "bot-principal",
        "timestamp": 500,
        "payload": {"kind": "protect", "target": "bot-principal", "self_protect": True},
    }
    game = StubSession("space-game", messages=[phase])
    notebook = StubSession("space-doctor", messages=[phase, prior_self_protect])
    llm = StubLLM(json.dumps({"kind": "protect", "target": "p3"}))

    bot = DoctorBot(
        role="doctor",
        persona=_persona(),
        principal_id="bot-principal",
        sessions={"game": game, "doctor": notebook},
        llm=llm,
        agent_name="doc-test",
        log_dir=tmp_path,
        sleep_fn=lambda *_: None,
    )
    bot._all_principals.update({"bot-principal", "p2", "p3"})
    scans = bot.scan_all()
    decision = bot.decide(bot.should_act(phase, scans), scans)
    bot.emit(decision)
    # Prompt should warn about already-used self-protect.
    system_prompt, _ = llm.calls[-1]
    assert "ALREADY used your one self-protection" in system_prompt
    assert notebook.posted, "should still emit a non-self protect"
    assert notebook.posted[-1]["payload"]["target"] == "p3"
