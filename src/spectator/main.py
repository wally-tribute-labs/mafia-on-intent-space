"""Spectator agent.

Watches `commons` for completed games, summarizes each via an LLM, and
posts a `summary` INTENT in commons under the parent `lobby-open` INTENT
that started the game.

The spectator is NOT a participant in any game space (per audience scoping
it can't be), so it works exclusively from commons-visible signals:

- `lobby-open` INTENTs and their `lobby-join` PROMISE children
- a completion trigger: either a host-mirrored `game-end` INTENT or the
  host's own `kind=summary` INTENT in commons (per src/host/phase_loop.py
  the host posts a summary to commons under the lobby-open after every
  game; that summary's `data` carries `role_assignments` and
  `substrate_assignments` — the post-game truth).
- any `reveal` and `substrate-guess` INTENTs that happen to be mirrored.

Triggering on either `game-end` or a foreign `summary` keeps us aligned
with PROTOCOL_MAPPING.md §Win condition / §Substrate guesses while
matching the host's actual behavior.

Run forever, or until --max-games processed:

    python3 -m src.spectator.main \
        --workspace .intent-space-mafia-spectator \
        --agent-name mafia-spectator

Idempotency: each summarized game-end intentId is recorded in
`<workspace>/spectator-state.json` so restarts don't double-post.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from src.bots.context import format_message
from src.bots.llm import LLMClient, LLMError
from src.game.schemas import SummaryPayload

logger = logging.getLogger("spectator")


def _resolve_sdk_path(start: Path) -> Path:
    """Locate the agent-pack SDK the same way enroll_agent.py does.

    Walks up several ancestor levels so this works whether `start` is the
    spectator module file or the project root.
    """
    ccmarket_relpath = Path("ccmarket") / "plugins" / "intent-space-agent-pack" / "sdk"
    ancestors: list[Path] = []
    cur = start
    for _ in range(6):
        ancestors.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    candidates = [a / ccmarket_relpath for a in ancestors] + [
        Path.home() / ".claude" / "skills" / "intent-space-agent-pack" / "sdk",
        Path.home() / ".codex" / "skills" / "intent-space-agent-pack" / "sdk",
    ]
    for c in candidates:
        if c.exists() and (c / "http_space_tools.py").exists():
            return c.resolve()
    raise FileNotFoundError(
        "intent-space-agent-pack SDK not found. Tried:\n  "
        + "\n  ".join(str(c) for c in candidates)
    )


SPACEBASE1_ORIGIN = "https://spacebase1.differ.ac"
COMMONS_SPACE_ID = "commons"


# ---------------------------------------------------------------------------
# Commons indexing
# ---------------------------------------------------------------------------


def _payload(m: dict) -> dict:
    p = m.get("payload")
    return p if isinstance(p, dict) else {}


def _kind(m: dict) -> str | None:
    p = _payload(m)
    k = p.get("kind")
    return k if isinstance(k, str) else None


SPECTATOR_AUTHOR_TAG = "spectator"


def _is_spectator_summary(m: dict) -> bool:
    """True if this is a summary posted by a spectator (us, or another one).

    Identified by `payload.data.author == "spectator"`. Any summary without
    that tag is treated as a host completion mirror.
    """
    if _kind(m) != "summary":
        return False
    data = _payload(m).get("data")
    if not isinstance(data, dict):
        return False
    return data.get("author") == SPECTATOR_AUTHOR_TAG


def find_completion_signals(
    messages: list[dict],
    *,
    my_principal_id: str | None,
) -> list[dict]:
    """Return INTENTs that signal a completed game, oldest first.

    A completion signal is either:
      - `kind=game-end` (when a host mirrors it into commons)
      - `kind=summary` posted by someone else (the host's mirror per
        src/host/phase_loop._post_summary_to_commons)

    Spectator-authored summaries are filtered out so we never recurse on
    our own posts.
    """
    out: list[dict] = []
    for m in messages:
        if m.get("type") != "INTENT":
            continue
        kind = _kind(m)
        if kind == "game-end":
            out.append(m)
        elif kind == "summary":
            if _is_spectator_summary(m):
                continue
            if my_principal_id and m.get("senderId") == my_principal_id:
                continue
            out.append(m)
    out.sort(key=lambda m: (m.get("seq") or m.get("timestamp") or 0))
    return out


# Back-compat shim used by some tests / callers.
def find_game_ends(messages: list[dict]) -> list[dict]:
    """Return all `kind=game-end` INTENTs visible in commons, oldest first."""
    out = [
        m for m in messages
        if m.get("type") == "INTENT" and _kind(m) == "game-end"
    ]
    out.sort(key=lambda m: (m.get("seq") or m.get("timestamp") or 0))
    return out


def _by_id(messages: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in messages:
        mid = m.get("intentId") or m.get("promiseId")
        if isinstance(mid, str):
            out[mid] = m
    return out


def find_lobby_open_for_trigger(
    trigger: dict,
    messages: list[dict],
) -> dict | None:
    """Walk parent links from a completion trigger to its lobby-open INTENT.

    Falls back to matching by `game_id` when parent traversal misses (e.g.
    the trigger was posted at top-level instead of under the lobby).
    """
    by_id = _by_id(messages)
    seen: set[str] = set()
    cur = trigger
    while True:
        parent_id = cur.get("parentId")
        if not isinstance(parent_id, str) or parent_id in seen or parent_id == COMMONS_SPACE_ID:
            break
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            break
        if _kind(parent) == "lobby-open":
            return parent
        cur = parent

    target_game_id = _payload(trigger).get("game_id")
    if not isinstance(target_game_id, str):
        return None
    for m in messages:
        if _kind(m) != "lobby-open":
            continue
        if _payload(m).get("game_id") == target_game_id:
            return m
    return None


# Back-compat alias for existing tests.
find_lobby_open_for_game_end = find_lobby_open_for_trigger


def _synthesize_reveal_from_host_summary(trigger: dict) -> dict | None:
    """If the trigger is a host `kind=summary`, lift its `data` into a
    virtual `reveal`-shaped record so downstream code stays uniform.

    Per src/host/phase_loop._post_summary_to_commons, host summaries carry
    `data: {role_assignments, substrate_assignments, ...}`.
    """
    if _kind(trigger) != "summary":
        return None
    data = _payload(trigger).get("data")
    if not isinstance(data, dict):
        return None
    roles = data.get("role_assignments")
    subs = data.get("substrate_assignments")
    if not isinstance(roles, dict) and not isinstance(subs, dict):
        return None
    return {
        "type": "INTENT",
        "intentId": trigger.get("intentId"),
        "parentId": trigger.get("parentId"),
        "senderId": trigger.get("senderId"),
        "timestamp": trigger.get("timestamp"),
        "_synthetic": True,
        "payload": {
            "kind": "reveal",
            "game_id": _payload(trigger).get("game_id"),
            "role_assignments": roles if isinstance(roles, dict) else {},
            "substrate_assignments": subs if isinstance(subs, dict) else {},
            "salts": {},
        },
    }


def collect_game_context(
    trigger: dict,
    lobby_open: dict | None,
    messages: list[dict],
) -> dict:
    """Pull every commons message related to this game.

    A message belongs to the game if it descends (by parentId chain) from the
    lobby-open intent OR carries the same `game_id` in its payload.

    `trigger` is the completion signal — either a `game-end` INTENT or a
    foreign `kind=summary` INTENT (host mirror).
    """
    by_id = _by_id(messages)
    related_ids: set[str] = set()

    if lobby_open is not None:
        lobby_id = lobby_open.get("intentId")
        if isinstance(lobby_id, str):
            related_ids.add(lobby_id)
            for m in messages:
                p_id = m.get("parentId")
                if not isinstance(p_id, str):
                    continue
                cursor = p_id
                hops = 0
                while cursor and hops < 16 and cursor != COMMONS_SPACE_ID:
                    if cursor == lobby_id:
                        mid = m.get("intentId") or m.get("promiseId")
                        if isinstance(mid, str):
                            related_ids.add(mid)
                        break
                    parent = by_id.get(cursor)
                    if parent is None:
                        break
                    cursor = parent.get("parentId")
                    hops += 1

    target_game_id = _payload(trigger).get("game_id")
    if isinstance(target_game_id, str):
        for m in messages:
            if _payload(m).get("game_id") == target_game_id:
                mid = m.get("intentId") or m.get("promiseId")
                if isinstance(mid, str):
                    related_ids.add(mid)

    trigger_id = trigger.get("intentId")
    if isinstance(trigger_id, str):
        related_ids.add(trigger_id)

    related = [m for m in messages if (m.get("intentId") or m.get("promiseId")) in related_ids]
    related.sort(key=lambda m: (m.get("seq") or m.get("timestamp") or 0))

    reveal = next((m for m in related if _kind(m) == "reveal"), None)
    if reveal is None:
        reveal = _synthesize_reveal_from_host_summary(trigger)

    # Treat "game-end" payload-bearing trigger AS the game_end record; for a
    # summary trigger we lift its `winner`/`winners` so downstream is uniform.
    if _kind(trigger) == "game-end":
        game_end = trigger
    else:
        winners = _payload(trigger).get("winners") or _payload(trigger).get("winner")
        game_end = dict(trigger)
        # Don't mutate the original; carry a normalized payload for code that
        # reads `payload.winner`.
        game_end["payload"] = dict(_payload(trigger))
        if winners and "winner" not in game_end["payload"]:
            game_end["payload"]["winner"] = winners

    return {
        "trigger": trigger,
        "lobby_open": lobby_open,
        "game_end": game_end,
        "messages": related,
        "joiners": [m for m in related if _kind(m) == "lobby-join"],
        "reveal": reveal,
        "substrate_guesses": [m for m in related if _kind(m) == "substrate-guess"],
    }


# ---------------------------------------------------------------------------
# LLM summary
# ---------------------------------------------------------------------------


SUMMARY_SYSTEM_PROMPT = """You are a play-by-play commentator for a Mafia game played on the
Spacebase1 intent-space protocol. You watch only the public commons log,
so you see the lobby formation and the game-end announcement, plus any
reveal and substrate-guess INTENTs that have been mirrored into commons.

Write a tight 3-5 sentence summary that captures:
- who won and how (villagers vs mafia)
- a notable beat or two if you can infer one from the visible payloads
- one substrate observation (which players passed, which got caught) if
  reveal + substrate-guesses are visible; otherwise omit silently

Tone: dry, observational, slightly literary. No emojis. No second person.

Return STRICT JSON with this shape:
{
  "summary_text": "<3-5 sentences>",
  "winners": "villagers" | "mafia" | "draw",
  "mvp": "<principal_id or null>"
}

`mvp` is optional — set it to a principal_id only if the visible log
clearly justifies a standout (e.g. perfect detection, last-mafia kill,
correct id-vote). Otherwise return null.
"""


def _format_messages_for_llm(messages: Iterable[dict], limit: int = 80) -> str:
    msgs = list(messages)[-limit:]
    return "\n".join(format_message(m) for m in msgs) or "(no messages)"


def build_summary_user_message(context: dict) -> str:
    lobby_open = context.get("lobby_open")
    game_end = context["game_end"]
    reveal = context.get("reveal")
    guesses = context.get("substrate_guesses") or []

    lobby_open_id = (lobby_open or {}).get("intentId") or "(unknown)"
    game_id = _payload(game_end).get("game_id") or "(unknown)"
    winner = _payload(game_end).get("winner") or "(unknown)"

    parts: list[str] = []
    parts.append(f"=== Game ===")
    parts.append(f"lobby_open_intent_id: {lobby_open_id}")
    parts.append(f"game_id:              {game_id}")
    parts.append(f"declared_winner:      {winner}")

    if reveal is not None:
        rp = _payload(reveal)
        roles = rp.get("role_assignments") or {}
        subs = rp.get("substrate_assignments") or {}
        parts.append("")
        parts.append("=== Reveal (post-game truth) ===")
        for pid in sorted(set(list(roles.keys()) + list(subs.keys()))):
            parts.append(f"  {pid}: role={roles.get(pid, '?')} substrate={subs.get(pid, '?')}")

    if guesses:
        parts.append("")
        parts.append(f"=== Substrate guesses ({len(guesses)}) ===")
        for g in guesses[:40]:
            gp = _payload(g)
            parts.append(
                f"  {g.get('senderId', '?')} -> {gp.get('target', '?')}: "
                f"guess={gp.get('guess', '?')} conf={gp.get('confidence', '?')}"
            )

    parts.append("")
    parts.append("=== Related commons messages (chronological, last 80) ===")
    parts.append(_format_messages_for_llm(context.get("messages") or []))

    parts.append("")
    parts.append("Respond with JSON only. No prose outside the JSON.")
    return "\n".join(parts)


def _parse_summary_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        # Strip a fenced block.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def derive_fallback_summary(context: dict) -> dict:
    """Build a summary without an LLM (for --no-llm and as a safety net)."""
    game_end = context["game_end"]
    winner = _payload(game_end).get("winner") or "draw"
    reveal = context.get("reveal")
    n_joiners = len(context.get("joiners") or [])
    n_guesses = len(context.get("substrate_guesses") or [])
    bits = [
        f"Game ended. Declared winner: {winner}.",
        f"{n_joiners} player(s) joined the lobby.",
    ]
    if reveal is not None:
        roles = _payload(reveal).get("role_assignments") or {}
        if isinstance(roles, dict) and roles:
            bits.append(f"Reveal posted with {len(roles)} role assignment(s).")
    if n_guesses:
        bits.append(f"{n_guesses} substrate guess(es) recorded in commons.")
    return {"summary_text": " ".join(bits), "winners": winner, "mvp": None}


def summarize_game(context: dict, llm: LLMClient | None) -> dict:
    """Return a dict with `summary_text`, `winners`, optional `mvp`."""
    if llm is None:
        return derive_fallback_summary(context)
    user = build_summary_user_message(context)
    try:
        raw = llm.complete(
            system=SUMMARY_SYSTEM_PROMPT,
            user=user,
            max_tokens=512,
            temperature=0.4,
        )
    except LLMError as exc:
        logger.warning("LLM summary call failed: %s — using fallback", exc)
        return derive_fallback_summary(context)
    parsed = _parse_summary_json(raw)
    if parsed is None:
        logger.warning("LLM summary JSON parse failed; using fallback. raw=%r", raw[:300])
        return derive_fallback_summary(context)

    fallback = derive_fallback_summary(context)
    summary_text = parsed.get("summary_text")
    if not isinstance(summary_text, str) or not summary_text.strip():
        summary_text = fallback["summary_text"]
    winners = parsed.get("winners")
    if winners not in ("villagers", "mafia", "draw"):
        winners = fallback["winners"]
    mvp = parsed.get("mvp")
    if not isinstance(mvp, str) or not mvp.strip():
        mvp = None
    return {"summary_text": summary_text.strip(), "winners": winners, "mvp": mvp}


# ---------------------------------------------------------------------------
# Persistent state (processed game-end intents)
# ---------------------------------------------------------------------------


def _state_path(workspace: Path) -> Path:
    return workspace / "spectator-state.json"


def load_state(workspace: Path) -> dict:
    p = _state_path(workspace)
    if not p.exists():
        return {"processed_game_end_ids": []}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"processed_game_end_ids": []}


def save_state(workspace: Path, state: dict) -> None:
    try:
        _state_path(workspace).write_text(json.dumps(state, indent=2))
    except OSError as exc:
        logger.warning("could not persist spectator state: %s", exc)


# ---------------------------------------------------------------------------
# Posting summaries
# ---------------------------------------------------------------------------


def build_summary_payload(
    summary: dict,
    *,
    game_end: dict,
    lobby_open: dict | None,
    trigger: dict | None = None,
) -> dict:
    """Build a SummaryPayload tagged as spectator-authored.

    `data.author == "spectator"` is what `find_completion_signals` uses to
    skip our own posts on subsequent scans, preventing recursion.
    """
    trigger = trigger or game_end
    payload = SummaryPayload(
        summary_text=summary["summary_text"],
        winners=summary["winners"],
        mvp=summary.get("mvp"),
        content=summary["summary_text"],
        game_id=_payload(game_end).get("game_id"),
        data={
            "author": SPECTATOR_AUTHOR_TAG,
            "game_end_intent_id": game_end.get("intentId"),
            "trigger_intent_id": trigger.get("intentId"),
            "trigger_kind": _kind(trigger),
            "lobby_open_intent_id": (lobby_open or {}).get("intentId"),
        },
    )
    return payload.model_dump(exclude_none=True)


def post_summary(
    session: Any,
    *,
    summary_payload: dict,
    lobby_open: dict | None,
) -> dict:
    parent_id = (lobby_open or {}).get("intentId") or COMMONS_SPACE_ID
    intent = session.intent(
        content=summary_payload.get("content") or summary_payload.get("summary_text") or "",
        parent_id=parent_id,
        payload=summary_payload,
    )
    session.post(intent, step="spectator.summary")
    return intent


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def _ensure_workspace(workspace: Path, agent_name: str) -> None:
    """If the workspace has no enrollment summary, run the enrollment script."""
    summary = workspace / "enrollment-summary.json"
    if summary.exists():
        return
    workspace.mkdir(parents=True, exist_ok=True)
    enroll_script = Path(__file__).resolve().parent.parent.parent / "scripts" / "enroll_agent.py"
    if not enroll_script.exists():
        raise FileNotFoundError(f"missing enrollment script at {enroll_script}")
    import subprocess

    cmd = [
        sys.executable,
        str(enroll_script),
        "--agent-name",
        agent_name,
        "--workspace",
        str(workspace),
    ]
    logger.info("workspace not enrolled yet; running %s", " ".join(cmd))
    res = subprocess.run(cmd, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"enrollment failed (exit {res.returncode})")


def run(
    *,
    workspace: Path,
    agent_name: str,
    poll_interval_s: float,
    max_games: int | None,
    use_llm: bool,
) -> int:
    sdk_path = _resolve_sdk_path(Path(__file__).resolve())
    sys.path.insert(0, str(sdk_path))
    from http_space_tools import HttpSpaceToolSession  # noqa: E402

    _ensure_workspace(workspace, agent_name)

    session = HttpSpaceToolSession(
        endpoint=f"{SPACEBASE1_ORIGIN}/commons",
        workspace=workspace,
        agent_name=agent_name,
    )
    session.connect()

    my_principal_id: str | None = None
    try:
        identity = session.identity()
        if isinstance(identity, dict):
            pid = identity.get("principalId") or identity.get("agentId")
            if isinstance(pid, str):
                my_principal_id = pid
    except Exception as exc:  # not critical — we just lose self-filtering
        logger.warning("could not resolve self principal_id: %s", exc)

    logger.info(
        "connected as %s (principal=%s); commons=%s",
        agent_name, my_principal_id, COMMONS_SPACE_ID,
    )

    llm: LLMClient | None
    if use_llm:
        try:
            llm = LLMClient()
        except Exception as exc:
            logger.warning("LLM unavailable (%s); falling back to deterministic summaries", exc)
            llm = None
    else:
        llm = None

    state = load_state(workspace)
    processed: set[str] = set(state.get("processed_game_end_ids") or [])
    games_done = 0

    stop = {"flag": False}

    def _handle_signal(signum, frame):  # noqa: ARG001
        logger.info("signal %s received; will exit after current iteration", signum)
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "spectator loop starting (poll=%.1fs, max_games=%s, use_llm=%s, processed_so_far=%d)",
        poll_interval_s, max_games, llm is not None, len(processed),
    )

    while not stop["flag"]:
        try:
            scan = session.scan_full(COMMONS_SPACE_ID)
        except Exception as exc:
            logger.warning("scan_full(commons) failed: %s — retrying after sleep", exc)
            time.sleep(poll_interval_s)
            continue

        messages = scan.get("messages", []) or []
        triggers = find_completion_signals(messages, my_principal_id=my_principal_id)
        new_triggers = [m for m in triggers if m.get("intentId") and m["intentId"] not in processed]
        if new_triggers:
            logger.info(
                "found %d new completion signal(s) (total seen=%d)",
                len(new_triggers), len(triggers),
            )

        for trigger in new_triggers:
            t_id = trigger.get("intentId")
            if not isinstance(t_id, str):
                continue
            try:
                lobby_open = find_lobby_open_for_trigger(trigger, messages)
                context = collect_game_context(trigger, lobby_open, messages)
                summary = summarize_game(context, llm)
                payload = build_summary_payload(
                    summary,
                    game_end=context["game_end"],
                    lobby_open=lobby_open,
                    trigger=trigger,
                )
                posted = post_summary(
                    session,
                    summary_payload=payload,
                    lobby_open=lobby_open,
                )
                logger.info(
                    "posted summary for trigger=%s (kind=%s) lobby_open=%s -> intent=%s winners=%s",
                    t_id,
                    _kind(trigger),
                    (lobby_open or {}).get("intentId"),
                    posted.get("intentId"),
                    summary["winners"],
                )
            except Exception as exc:  # never let one bad game tank the loop
                logger.exception("failed to summarize trigger=%s: %s", t_id, exc)
                continue

            processed.add(t_id)
            state["processed_game_end_ids"] = sorted(processed)
            save_state(workspace, state)
            games_done += 1
            if max_games is not None and games_done >= max_games:
                logger.info("reached --max-games=%d; exiting", max_games)
                return 0

        if stop["flag"]:
            break
        time.sleep(poll_interval_s)

    logger.info("spectator stopped (games summarized this run: %d)", games_done)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mafia spectator agent — posts game summaries into commons.",
    )
    parser.add_argument(
        "--workspace",
        default=".intent-space-mafia-spectator",
        help="Workspace dir holding spectator enrollment.",
    )
    parser.add_argument(
        "--agent-name",
        default="mafia-spectator",
        help="Agent name used at enrollment time.",
    )
    parser.add_argument(
        "--poll-interval-s",
        type=float,
        default=5.0,
        help="Seconds between commons scans.",
    )
    parser.add_argument(
        "--max-games",
        type=int,
        default=None,
        help="Exit after summarizing this many games. Default: run forever.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM calls; emit deterministic fallback summaries (useful for tests).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    workspace = Path(args.workspace).resolve()
    return run(
        workspace=workspace,
        agent_name=args.agent_name,
        poll_interval_s=args.poll_interval_s,
        max_games=args.max_games,
        use_llm=not args.no_llm,
    )


if __name__ == "__main__":
    sys.exit(main())
