"""Vote tallying and lynch / id-vote resolution.

Inputs are NESTED SDK-shaped messages (same shape as src/game/phase.py):
sender lives in `senderId`; `target` / `claimed_substrate` / `phase` live
in `payload`.

Per GAME_RULES.md §Day Vote rules: each living player posts at most one
`kind=vote` and at most one `kind=id-vote` per day. A second submission
from the same sender in the same phase is ignored — first one wins by
scan order.

Per PROTOCOL_MAPPING.md §Vote: tally is computed from the public log; any
agent can independently reproduce it. Host's phase-end is verifiable, not
authoritative.
"""

from __future__ import annotations

from typing import Optional


def _payload(m: dict) -> dict:
    p = m.get("payload")
    return p if isinstance(p, dict) else {}


def compute_tally(
    phase_start_intent: dict,
    vote_messages: list[dict],
) -> dict[str, int]:
    """Count first vote per sender for the given phase.

    `phase_start_intent` is the nested phase-start message (full SDK shape).
    Iterates `vote_messages` in scan order. Skips messages whose payload
    `kind` is not "vote" and (when both carry a phase) whose payload phase
    doesn't match `phase_start_intent.payload.phase`. Only the first vote
    per sender counts; later duplicates are ignored.
    """
    phase = _payload(phase_start_intent).get("phase") if phase_start_intent else None
    seen_senders: set[str] = set()
    counts: dict[str, int] = {}
    for m in vote_messages:
        payload = _payload(m)
        if payload.get("kind") != "vote":
            continue
        msg_phase = payload.get("phase")
        if phase is not None and msg_phase is not None and msg_phase != phase:
            continue
        sender = m.get("senderId")
        target = payload.get("target")
        if sender is None or target is None:
            continue
        if sender in seen_senders:
            continue
        seen_senders.add(sender)
        counts[target] = counts.get(target, 0) + 1
    return counts


def resolve_lynch(tally: dict[str, int]) -> Optional[str]:
    """Return the unique argmax of `tally`, or None on tie / empty.

    Per GAME_RULES.md: "Ties: no lynch."
    """
    if not tally:
        return None
    max_count = max(tally.values())
    if max_count <= 0:
        return None
    leaders = [t for t, c in tally.items() if c == max_count]
    if len(leaders) != 1:
        return None
    return leaders[0]


def resolve_id_votes(
    id_vote_messages: list[dict],
    truth: dict[str, str],
) -> list[dict]:
    """Resolve id-votes against the privately-known substrate map.

    Per GAME_RULES.md §Identification Vote:
    - Correct guess → target's substrate is locked publicly.
    - Incorrect guess → voter's own substrate is locked.

    Only the first id-vote per voter counts (matches Day Vote dedup rule).
    Returns one dict per resolved id-vote with keys
    {voter, target, claimed, actual, correct, locked}.
    """
    seen_voters: set[str] = set()
    results: list[dict] = []
    for m in id_vote_messages:
        payload = _payload(m)
        if payload.get("kind") != "id-vote":
            continue
        voter = m.get("senderId")
        target = payload.get("target")
        claimed = payload.get("claimed_substrate")
        if voter is None or target is None or claimed is None:
            continue
        if voter in seen_voters:
            continue
        seen_voters.add(voter)
        actual = truth.get(target)
        correct = claimed == actual
        locked = target if correct else voter
        results.append(
            {
                "voter": voter,
                "target": target,
                "claimed": claimed,
                "actual": actual,
                "correct": correct,
                "locked": locked,
            }
        )
    return results
