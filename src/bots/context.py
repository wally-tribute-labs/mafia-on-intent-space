"""Helpers that derive game state from raw protocol messages.

These are pure functions over the message log — no I/O, no SDK imports.
The canonical phase-derivation primitive lives in src/game/phase.py and
is re-exported here for the bots package's convenience.
"""

from __future__ import annotations

from typing import Iterable

from src.game.phase import current_phase


def format_message(msg: dict) -> str:
    """One-line, log-style rendering of a protocol message for LLM context."""
    payload = msg.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    kind = payload.get("kind", "?")
    sender = msg.get("senderId", "?")
    mtype = msg.get("type", "?")
    seq = msg.get("seq", msg.get("timestamp", "?"))
    msg_id = msg.get("intentId") or msg.get("promiseId") or "?"

    bits = [
        f"[seq={seq}",
        f"type={mtype}",
        f"sender={sender}",
        f"kind={kind}",
        f"id={msg_id}",
    ]
    target = payload.get("target")
    if target:
        bits.append(f"target={target}")
    head = " ".join(bits) + "]"

    text = payload.get("content") or payload.get("claim") or payload.get("text") or ""
    if isinstance(text, str) and text:
        text = text.replace("\n", " ").strip()
        if len(text) > 280:
            text = text[:277] + "..."
        return f"{head} {text}"
    return head


def extract_living_players(
    game_messages: list[dict],
    all_principals: Iterable[str],
) -> set[str]:
    """All known principals minus those declared dead via a `death` INTENT/COMPLETE."""
    dead: set[str] = set()
    for m in game_messages:
        payload = m.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") != "death":
            continue
        target = payload.get("target")
        cause = payload.get("cause")
        if isinstance(target, str) and cause and cause != "saved":
            dead.add(target)
    return {p for p in all_principals if p not in dead}


def extract_deaths(game_messages: list[dict]) -> list[dict]:
    """All death records seen so far, in log order."""
    out: list[dict] = []
    for m in game_messages:
        payload = m.get("payload") or {}
        if isinstance(payload, dict) and payload.get("kind") == "death":
            out.append({"target": payload.get("target"), "cause": payload.get("cause")})
    return out


def already_acted_this_phase(
    messages: list[dict],
    phase: dict,
    principal: str,
    action_kind: str,
) -> bool:
    """Did `principal` already post a message of `action_kind` since `phase` started?

    Uses phase['timestamp'] as the lower bound. action_kind matches one of:
      - "discussion-utterance" → any of accusation|defense|observation
      - "vote"                 → vote OR id-vote
      - "coven-target"         → coven-target OR coven-confirm
      - "investigate"          → investigate
      - "protect"              → protect
    """
    floor = phase.get("timestamp", 0) if phase else 0
    discussion_kinds = {"accusation", "defense", "observation"}
    vote_kinds = {"vote", "id-vote"}
    coven_kinds = {"coven-target", "coven-confirm"}
    target_kinds: set[str]
    if action_kind == "discussion-utterance":
        target_kinds = discussion_kinds
    elif action_kind == "vote":
        target_kinds = vote_kinds
    elif action_kind == "coven-target":
        target_kinds = coven_kinds
    else:
        target_kinds = {action_kind}

    for m in messages:
        if m.get("senderId") != principal:
            continue
        if (m.get("timestamp") or 0) < floor:
            continue
        payload = m.get("payload") or {}
        if isinstance(payload, dict) and payload.get("kind") in target_kinds:
            return True
    return False


__all__ = [
    "format_message",
    "current_phase",
    "extract_living_players",
    "extract_deaths",
    "already_acted_this_phase",
]
