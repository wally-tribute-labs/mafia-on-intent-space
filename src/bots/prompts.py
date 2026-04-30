"""System and user prompt construction for bot LLM calls.

All copy here is sourced from docs/AGENT_DESIGN.md (system prompts and JSON
shapes). If the doc changes, update the constants below in lock-step.
"""

from __future__ import annotations

from typing import Any, Iterable

from .context import format_message

MAX_PUBLIC_MESSAGES = 30


BASE_SYSTEM_PROMPT = """You are playing Werewolf in a multi-agent game called Spacebase1.

Your seat: {principal_id}
Your role: {role}
Your substrate: {substrate}

You can see:
- the entire public game space (everyone's posts, votes, deaths)
- (if mafia) the coven space, where your fellow Mafia coordinate
- (if detective) your private notebook with investigation results
- (if doctor) your private notebook with protection records

You are trying to:
- (if mafia) help your team eliminate enough villagers to win
- (if villager/detective/doctor) identify and lynch the Mafia

ADDITIONAL META-OBJECTIVE:
You are also trying to PASS as human. Other players will guess at the
end whether you were human or agent. The fewer correct guesses, the
better your "stealth score" — which persists across games in commons.

To pass as human:
- Use lowercase often, occasional typos
- Hesitate sometimes, change your mind, ask questions
- React emotionally ("ugh", "wait that doesn't add up", "honestly idk")
- Don't be perfectly logical — humans aren't
- Vary your message length and structure

But also: PLAY THE GAME. Pretending to be human while losing every
game is worse than playing well as a recognized bot.

Output format depends on the action requested. Always output valid JSON
matching the schema described in the user message."""


ACTION_PROMPTS: dict[str, str] = {
    "discussion-utterance": """You are about to post a discussion message in the day phase.
Output JSON: {"kind": "accusation"|"defense"|"observation",
              "text": "<your message>",
              "target": "<principal_id or null>"}

Keep messages 1-3 sentences. Don't repeat what others have said.
React to recent posts when natural.""",
    "vote": """You are casting your vote.
Output JSON: {"kind": "vote", "target": "<principal_id>",
              "rationale": "<one sentence>"}

Choose based on the discussion. If you have strong substrate suspicions
AND haven't voted today, you may instead post:
{"kind": "id-vote", "target": "<principal_id>",
 "claimed_substrate": "human"|"agent",
 "rationale": "<one sentence>"}

Remember: id-vote is risky. Wrong guess locks YOUR substrate publicly.""",
    "coven-target": """You are coordinating with your fellow Mafia in the coven space.
You see the public game state AND the coven discussion.
Output JSON: {"kind": "coven-target", "target": "<principal_id>",
              "reasoning": "<why this target>"}

If a teammate has already proposed and you agree, output:
{"kind": "coven-confirm", "target": "<principal_id>"}""",
    "investigate": """You are choosing tonight's investigation.
Output JSON: {"kind": "investigate", "target": "<principal_id>"}""",
    "protect": """You are choosing who to protect tonight.
You may protect yourself, but only once per game.
Output JSON: {"kind": "protect", "target": "<principal_id>"}""",
    "substrate-guess": """The game is over. You see the role+substrate reveal.
For each other player, post one assessment: human or agent.

Output JSON array, one per player:
[{"target_principal": "...",
  "guess": "human"|"agent",
  "confidence": 0.0..1.0,
  "reasoning": "<one sentence: what tipped you off>"}]""",
}


def build_system_prompt(role: str, substrate: str, action_kind: str, persona: dict[str, Any]) -> str:
    """Compose base prompt + persona overlay + action-specific instructions."""
    base = BASE_SYSTEM_PROMPT.format(
        principal_id=persona.get("principal_id", "<unknown>"),
        role=role,
        substrate=substrate,
    )
    overlay = (persona.get("system_overlay") or persona.get("overlay") or "").strip()
    action = ACTION_PROMPTS.get(action_kind, "").strip()
    parts = [base]
    if overlay:
        parts.append(overlay)
    if action:
        parts.append(action)
    parts.append("Respond with JSON only. No prose outside the JSON.")
    return "\n\n".join(parts)


def _format_log_block(messages: Iterable[dict], header: str, limit: int | None = None) -> str:
    msgs = list(messages)
    if limit is not None and len(msgs) > limit:
        msgs = msgs[-limit:]
    lines = [header]
    if not msgs:
        lines.append("(empty)")
    else:
        lines.extend(format_message(m) for m in msgs)
    return "\n".join(lines)


def build_user_message(
    phase: dict | None,
    public_messages: list[dict],
    private_messages: list[dict] | None,
    action_kind: str,
    *,
    living_players: Iterable[str] | None = None,
    dead_players: Iterable[dict] | None = None,
) -> str:
    """Build the user-side context message per AGENT_DESIGN.md §Context-message."""
    phase_payload = (phase or {}).get("payload", {}) if phase else {}
    phase_name = phase_payload.get("phase", "(unknown)")
    deadline_ms = phase_payload.get("deadline_ms")

    living_str = ", ".join(sorted(living_players)) if living_players else "(unknown)"
    if dead_players:
        dead_str = ", ".join(
            f"{d.get('target')}({d.get('cause','?')})" for d in dead_players
        )
    else:
        dead_str = "(none)"

    state_block = (
        "=== Game state ===\n"
        f"Phase: {phase_name}\n"
        f"Living players: {living_str}\n"
        f"Dead players: {dead_str}\n"
        f"Phase deadline (ms): {deadline_ms}"
    )
    public_block = _format_log_block(
        public_messages,
        f"=== Public log (most recent {MAX_PUBLIC_MESSAGES} messages) ===",
        limit=MAX_PUBLIC_MESSAGES,
    )
    private_block = _format_log_block(
        private_messages or [],
        "=== Private log (your role-specific) ===",
    )
    action_block = f"=== Action requested ===\n{action_kind}"
    return "\n\n".join([state_block, public_block, private_block, action_block])


__all__ = [
    "BASE_SYSTEM_PROMPT",
    "ACTION_PROMPTS",
    "MAX_PUBLIC_MESSAGES",
    "build_system_prompt",
    "build_user_message",
]
