"""Cross-game reputation derived from public commons messages.

Per PROTOCOL_MAPPING.md §Substrate guesses: every player publicly assesses
every other player's substrate after game-end. The append-only commons log
accumulates all guesses across all games, and any agent can scan commons
to compute reputation:

- detection_accuracy = correct_count(my guesses) / total
- stealth_score      = wrong_count(guesses about me) / total

To verify correctness without trusting the assessor, this module pairs
each `substrate-guess` against a `reveal` posted into commons for the
same `game_id`. (The host or a spectator is expected to mirror the
in-game-space reveal into commons; alternatively, if commons stays in
sync via PROMISE/COMPLETE chains, the same data is reachable.)

Inputs are the NESTED SDK shape used elsewhere: `senderId` on the
envelope, payload fields under `payload`. A guess message is identified
by `payload.kind == "substrate-guess"` and must carry
`{target, guess, game_id}`. A reveal is identified by `payload.kind ==
"reveal"` and must carry `{game_id, substrate_assignments: {pid: sub}}`.
"""

from __future__ import annotations


def _payload(m: dict) -> dict:
    p = m.get("payload")
    return p if isinstance(p, dict) else {}


def _truth_by_game(commons_messages: list[dict]) -> dict[str, dict[str, str]]:
    """Aggregate {game_id: {principal_id: substrate}} from reveal messages.

    Later reveals for the same game_id overwrite earlier ones, but in
    practice each game posts exactly one reveal.
    """
    out: dict[str, dict[str, str]] = {}
    for m in commons_messages:
        payload = _payload(m)
        if payload.get("kind") != "reveal":
            continue
        game_id = payload.get("game_id") or m.get("game_id")
        assignments = payload.get("substrate_assignments")
        if not isinstance(game_id, str) or not isinstance(assignments, dict):
            continue
        out[game_id] = {
            k: v for k, v in assignments.items()
            if isinstance(k, str) and isinstance(v, str)
        }
    return out


def _is_guess(m: dict) -> bool:
    return _payload(m).get("kind") == "substrate-guess"


def _judged(guess_msg: dict, truth: dict[str, dict[str, str]]) -> tuple[bool, bool]:
    """Return (judged, correct).

    `judged=False` means the guess can't be verified (no reveal for that
    game, or target not in reveal). Such guesses are excluded from both
    detection and stealth ratios.
    """
    payload = _payload(guess_msg)
    game_id = payload.get("game_id")
    target = payload.get("target")
    guess = payload.get("guess")
    if not isinstance(game_id, str) or not isinstance(target, str) or not isinstance(guess, str):
        return False, False
    actual = truth.get(game_id, {}).get(target)
    if actual is None:
        return False, False
    return True, guess == actual


def compute_principal_stats(
    commons_messages: list[dict],
    principal_id: str,
) -> dict:
    """Compute reputation for one principal.

    Returns:
      detection_accuracy: float in [0, 1] — fraction of this principal's
        verifiable substrate-guesses that were correct.
      stealth_score: float in [0, 1] — fraction of verifiable guesses
        about this principal that were wrong (i.e. they stayed hidden).
      games_played: int — distinct game_ids in which this principal
        appears in a reveal's substrate_assignments OR posted a guess.
      detection_total / detection_correct: raw counters.
      stealth_total / stealth_wrong: raw counters.
    """
    truth = _truth_by_game(commons_messages)

    detection_total = 0
    detection_correct = 0
    stealth_total = 0
    stealth_wrong = 0
    games: set[str] = set()

    for m in commons_messages:
        if not _is_guess(m):
            continue
        payload = _payload(m)
        game_id = payload.get("game_id")
        if isinstance(game_id, str):
            sender = m.get("senderId")
            target = payload.get("target")
            if sender == principal_id or target == principal_id:
                games.add(game_id)

        sender = m.get("senderId")
        if sender == principal_id:
            judged, correct = _judged(m, truth)
            if judged:
                detection_total += 1
                if correct:
                    detection_correct += 1

        target = payload.get("target")
        if target == principal_id:
            judged, correct = _judged(m, truth)
            if judged:
                stealth_total += 1
                if not correct:
                    stealth_wrong += 1

    for game_id, assignments in truth.items():
        if principal_id in assignments:
            games.add(game_id)

    return {
        "detection_accuracy": detection_correct / detection_total if detection_total else 0.0,
        "stealth_score": stealth_wrong / stealth_total if stealth_total else 0.0,
        "games_played": len(games),
        "detection_total": detection_total,
        "detection_correct": detection_correct,
        "stealth_total": stealth_total,
        "stealth_wrong": stealth_wrong,
    }


def _all_principals(commons_messages: list[dict]) -> set[str]:
    """Every principal that appears as guess sender, guess target, or in a reveal."""
    found: set[str] = set()
    for m in commons_messages:
        payload = _payload(m)
        kind = payload.get("kind")
        if kind == "substrate-guess":
            sender = m.get("senderId")
            target = payload.get("target")
            if isinstance(sender, str):
                found.add(sender)
            if isinstance(target, str):
                found.add(target)
        elif kind == "reveal":
            assignments = payload.get("substrate_assignments")
            if isinstance(assignments, dict):
                found.update(k for k in assignments if isinstance(k, str))
    return found


def leaderboard(commons_messages: list[dict]) -> list[dict]:
    """Return principals sorted by combined reputation score.

    Score = (detection_accuracy + stealth_score) / 2. Tiebreak by
    games_played descending, then principal_id ascending for determinism.
    """
    rows: list[dict] = []
    for principal in _all_principals(commons_messages):
        s = compute_principal_stats(commons_messages, principal)
        combined = (s["detection_accuracy"] + s["stealth_score"]) / 2
        rows.append(
            {
                "principal": principal,
                "stealth": s["stealth_score"],
                "detection": s["detection_accuracy"],
                "games": s["games_played"],
                "_combined": combined,
            }
        )
    rows.sort(
        key=lambda r: (-r["_combined"], -r["games"], r["principal"])
    )
    for r in rows:
        del r["_combined"]
    return rows
