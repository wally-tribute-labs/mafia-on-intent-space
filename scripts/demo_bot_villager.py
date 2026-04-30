#!/usr/bin/env python3
"""Tiny demo: instantiate a BotAgent against a stub session and run one
scan-decide-emit cycle for a hand-rolled day-1-discussion phase.

NOT for production. Useful as a smoke check that the bot package wires
together correctly when the real spacebase1 host isn't running.

Usage:
    ANTHROPIC_API_KEY=sk-... python scripts/demo_bot_villager.py
    # or skip the LLM entirely:
    DEMO_FAKE_LLM=1 python scripts/demo_bot_villager.py
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.bots.base import BotAgent  # noqa: E402
from src.bots.llm import LLMClient  # noqa: E402


class _FakeLLM:
    def complete(self, system, user, max_tokens=512, temperature=0.8):
        return json.dumps({"kind": "observation", "text": "ok let me think...", "target": None})


class _StubSession:
    def __init__(self, space_id, msgs):
        self.current_space_id = space_id
        self.messages = list(msgs)
        self.posted = []
        self._n = 0

    def scan_full(self, space_id):
        return {"messages": list(self.messages), "latestSeq": len(self.messages)}

    def intent(self, *, content, payload, parent_id=None, intent_id=None):
        self._n += 1
        return {
            "type": "INTENT",
            "intentId": intent_id or f"intent-demo-{self._n}",
            "parentId": parent_id or self.current_space_id,
            "senderId": "bot-principal-demo",
            "timestamp": 1000 + self._n,
            "payload": payload,
        }

    def post(self, message, *, step=None, **_):
        self.posted.append(message)
        self.messages.append(message)
        print(f"[posted] step={step} payload={json.dumps(message['payload'])[:160]}")
        return message


def main() -> int:
    phase_msg = {
        "type": "INTENT",
        "intentId": "intent-phase-demo",
        "parentId": "space-game-demo",
        "senderId": "host-demo",
        "timestamp": 100,
        "payload": {"kind": "phase-start", "phase": "day-1-discussion", "deadline_ms": 60_100},
    }
    join_msgs = [
        {"type": "PROMISE", "intentId": "intent-lobby", "promiseId": f"pj-{i}",
         "parentId": "space-game-demo", "senderId": pid, "timestamp": 50 + i,
         "payload": {"kind": "lobby-join", "principal_id": pid, "agent_label": pid}}
        for i, pid in enumerate(["bot-principal-demo", "p2", "p3", "p4"])
    ]
    game = _StubSession("space-game-demo", join_msgs + [phase_msg])

    persona = {
        "overlay": "PERSONA: \"skeptic\"",
        "speaking_frequency": 1.0,
        "vote_threshold": 0.5,
        "id_vote_propensity": 0.0,
        "temperature": 0.7,
        "scan_interval_s": 0.0,
    }
    llm: object
    if os.environ.get("DEMO_FAKE_LLM"):
        llm = _FakeLLM()
    else:
        llm = LLMClient()

    bot = BotAgent(
        role="villager",
        persona=persona,
        principal_id="bot-principal-demo",
        sessions={"game": game},
        llm=llm,  # type: ignore[arg-type]
        agent_name="demo-villager",
        log_dir=ROOT / "logs",
        rng=random.Random(0),
    )

    scans = bot.scan_all()
    action = bot.should_act(__import__("src.bots.context", fromlist=["current_phase"]).current_phase(scans["game"]), scans)
    print(f"action_kind={action}")
    if not action:
        return 0
    decision = bot.decide(action, scans)
    print(f"decision={json.dumps({k: v for k, v in (decision or {}).items() if not k.startswith('_')})}")
    if decision:
        bot.emit(decision)
    print(f"posted_count={len(game.posted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
