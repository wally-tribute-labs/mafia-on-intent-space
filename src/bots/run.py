"""Single bot entry point.

Usage:
    python -m src.bots.run \\
        --workspace .intent-space-mafia-player-1 \\
        --persona random \\
        --llm-model claude-haiku-4-5-20251001

Flow (matches docs/PROTOCOL_MAPPING.md §Lobby formation, §Game start):

1. Load enrollment for the workspace; build an `HttpSpaceToolSession`,
   reconnect to the home space.
2. Wait for the host's `lobby-open` INTENT in commons; post a `lobby-join`
   PROMISE underneath it.
3. Wait for invitation INTENTs from spacebase1 in our home space. Each
   invitation carries a `payload.access` bag with `{itp_endpoint,
   station_token, audience}` for a shared space (game / role-subspace /
   personal notebook).
4. For every invited shared space, build an additional
   `HttpSpaceToolSession` (sharing the workspace's identity + known-stations)
   and `connect_to(...)` it. Then scan each space to identify by content:
       - `kind=game-init`      -> game space
       - `kind=role-card`      -> personal notebook (also reveals our role)
       - `kind=coven-membership` -> coven (mafia)
       - `kind=phase-start` with `-detective`/`-doctor` -> role subspace
5. Build the BotAgent's `sessions` dict: always `{"game": game_session}`
   plus the role subspace session keyed by role for mafia/detective/doctor.
6. Instantiate the appropriate BotAgent subclass and run the loop until
   it sees `kind=game-end` and returns.

The launcher does NOT modify any A1/A2/A3/B1/B2 code. It only assembles
the session dict that those modules already expect.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# SDK + project sys.path bootstrap (mirrors scripts/enroll_agent.py)
# ---------------------------------------------------------------------------


def _resolve_sdk_path(start: Path) -> Path:
    candidates = [
        start.parent.parent / "ccmarket" / "plugins" / "intent-space-agent-pack" / "sdk",
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


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SDK_PATH = _resolve_sdk_path(_PROJECT_ROOT / "scripts")
sys.path.insert(0, str(_SDK_PATH))
sys.path.insert(0, str(_PROJECT_ROOT))

from http_space_tools import HttpSpaceToolSession  # noqa: E402

from src.bots.detective import DetectiveBot  # noqa: E402
from src.bots.doctor import DoctorBot  # noqa: E402
from src.bots.llm import LLMClient  # noqa: E402
from src.bots.mafia import MafiaBot  # noqa: E402
from src.bots.personas import load_personas  # noqa: E402
from src.bots.villager import VillagerBot  # noqa: E402


SPACEBASE1_ORIGIN = "https://spacebase1.differ.ac"
COMMONS_SPACE_ID = "commons"


# Map shorthand model aliases the orchestrator might pass to canonical
# Anthropic identifiers. Unknown aliases pass through unchanged.
_MODEL_ALIASES: dict[str, str] = {
    "claude-haiku": "claude-haiku-4-5-20251001",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-haiku-3-5": "claude-haiku-4-5-20251001",
    "claude-haiku-3.5": "claude-haiku-4-5-20251001",
    "claude-3-5-haiku": "claude-haiku-4-5-20251001",
    "claude-3-5-haiku-latest": "claude-haiku-4-5-20251001",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-sonnet-3-5": "claude-sonnet-4-6",
}


# ---------------------------------------------------------------------------
# Lobby + invitation discovery
# ---------------------------------------------------------------------------


def _lookup_station(session, *, space_id: str | None = None,
                    audience_suffix: str | None = None) -> dict | None:
    """Find a (endpoint, station_token, audience, principal_id) entry in the
    session's persisted known_stations registry.
    """
    fetcher = getattr(session, "known_stations", None)
    if not callable(fetcher):
        return None
    for entry in fetcher() or []:
        if not isinstance(entry, dict):
            continue
        if space_id is not None and entry.get("spaceId") != space_id:
            continue
        if audience_suffix is not None:
            aud = entry.get("audience")
            if not isinstance(aud, str) or not aud.endswith(audience_suffix):
                continue
        token = entry.get("stationToken")
        endpoint = entry.get("endpoint")
        audience = entry.get("audience")
        if not (isinstance(token, str) and isinstance(endpoint, str)
                and isinstance(audience, str)):
            continue
        return {
            "endpoint": endpoint,
            "station_token": token,
            "audience": audience,
            "principal_id": entry.get("principalId"),
        }
    return None


def _switch_to(session, target: dict, *, log: logging.Logger, label: str) -> None:
    session.connect_to(
        endpoint=target["endpoint"],
        station_token=target["station_token"],
        audience=target["audience"],
        sender_id=target.get("principal_id"),
    )
    log.info("switched session -> %s (audience=%s)", label, target["audience"])


def _select_target_lobby(messages: list[dict], game_id: str | None) -> dict | None:
    """Pick the relevant lobby-open INTENT from a commons scan.

    If `game_id` is provided, return the (latest) lobby-open whose payload
    carries a matching `game_id` — bulletproof against stale lobbies.

    Otherwise (direct invocation, no orchestrator tag): return the highest-
    seq lobby-open with `seats > 0`. Smoke-test 0-seat lobbies are skipped
    so they don't trap a real bot.
    """
    candidates: list[tuple[int, dict]] = []
    for m in messages:
        if m.get("type") != "INTENT":
            continue
        payload = m.get("payload")
        if not isinstance(payload, dict) or payload.get("kind") != "lobby-open":
            continue
        seq = m.get("seq")
        if not isinstance(seq, int):
            continue
        if game_id is not None:
            if payload.get("game_id") == game_id:
                candidates.append((seq, m))
            continue
        seats = payload.get("seats")
        if isinstance(seats, int) and seats > 0:
            candidates.append((seq, m))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


def _join_lobby(home_session, principal_id: str, substrate: str,
                wait_s: float, log: logging.Logger,
                game_id: str | None = None,
                agent_label: str = "bot") -> str:
    """Wait for the host's lobby-open INTENT in commons and post lobby-join.

    Returns the lobby-open intent id. Filters out stale `lobby-open`
    intents from previous runs (commons is append-only and accumulates
    them across the lifetime of the space).
    """
    if game_id:
        log.info("waiting for lobby-open in commons (game_id=%s, timeout=%.0fs)",
                 game_id, wait_s)
    else:
        log.info("waiting for lobby-open in commons (latest non-zero-seat, "
                 "timeout=%.0fs)", wait_s)
    deadline = time.time() + wait_s
    lobby: dict | None = None
    while time.time() < deadline:
        scan = home_session.scan_full(COMMONS_SPACE_ID)
        lobby = _select_target_lobby(scan.get("messages", []), game_id)
        if lobby is not None:
            break
        time.sleep(1.0)
    if lobby is None:
        raise TimeoutError(
            f"no matching lobby-open in commons after {wait_s:.0f}s "
            f"(game_id={game_id!r})"
        )
    lobby_intent_id = lobby.get("intentId")
    if not isinstance(lobby_intent_id, str):
        raise RuntimeError(f"lobby-open intent missing intentId: {lobby!r}")
    log.info("found lobby-open: %s (seq=%s)",
             lobby_intent_id, lobby.get("seq"))

    promise = home_session.promise(
        parent_id=lobby_intent_id,
        intent_id=lobby_intent_id,
        content=f"{principal_id} joining",
        payload={
            "kind": "lobby-join",
            "principal_id": principal_id,
            "agent_label": agent_label,
            "agent_substrate": substrate,
        },
    )
    home_session.post_and_confirm(
        promise,
        step="bot.lobby-join",
        confirm_space_id=lobby_intent_id,
    )
    log.info("posted lobby-join under %s", lobby_intent_id)
    return lobby_intent_id


def _find_invitations(messages: list[dict], *, not_before_ms: int | None = None) -> dict[str, dict]:
    """Pick out shared-space invitation INTENTs from a home-space scan.

    Keyed by `shared_space_id` so duplicates are dropped. Spacebase1
    invitations carry `payload.access` plus `payload.shared_space_id` but
    no explicit `kind` field — we identify them by structure.
    """
    out: dict[str, dict] = {}
    for m in messages:
        if m.get("type") != "INTENT":
            continue
        if not_before_ms is not None:
            ts = m.get("timestamp")
            if isinstance(ts, int) and ts < not_before_ms:
                continue
        payload = m.get("payload")
        if not isinstance(payload, dict):
            continue
        access = payload.get("access")
        space_id = payload.get("shared_space_id")
        if not isinstance(access, dict) or not isinstance(space_id, str):
            continue
        # Last writer wins; invitations for the same space shouldn't repeat.
        out[space_id] = m
    return out


def _wait_for_invitations(home_session, home_space_id: str, *,
                          min_count: int, deadline_s: float,
                          settle_s: float,
                          not_before_ms: int | None,
                          log: logging.Logger) -> list[dict]:
    """Poll our home space until at least `min_count` invitations arrive,
    then wait `settle_s` more so any trailing invitation is captured.

    Returns the deduped list of invitation INTENTs. Raises TimeoutError if
    `min_count` isn't reached within `deadline_s`.
    """
    log.info(
        "waiting for >=%d invitation INTENT(s) in home (timeout=%.0fs, settle=%.0fs)",
        min_count, deadline_s, settle_s,
    )
    deadline = time.time() + deadline_s
    seen: dict[str, dict] = {}
    while time.time() < deadline:
        messages = home_session.scan_full(home_space_id).get("messages", [])
        seen = _find_invitations(messages, not_before_ms=not_before_ms)
        if len(seen) >= min_count:
            break
        time.sleep(2.0)
    if len(seen) < min_count:
        raise TimeoutError(
            f"only got {len(seen)} invitation(s) after {deadline_s:.0f}s "
            f"(expected >= {min_count})"
        )
    # Settle: catch any late-arriving invitation (e.g. role subspace lands
    # a couple of seconds after the game space).
    end_settle = time.time() + settle_s
    while time.time() < end_settle:
        time.sleep(1.0)
        messages = home_session.scan_full(home_space_id).get("messages", [])
        seen = _find_invitations(messages, not_before_ms=not_before_ms)
    log.info("collected %d invitation(s): %s", len(seen), sorted(seen.keys()))
    return list(seen.values())


# ---------------------------------------------------------------------------
# Per-space session construction + role identification
# ---------------------------------------------------------------------------


def _build_subspace_session(workspace: Path, agent_name: str,
                            access: dict, principal_id: str) -> HttpSpaceToolSession:
    """Construct a fresh session and connect_to the invited shared space."""
    s = HttpSpaceToolSession(
        endpoint=access["itp_endpoint"],
        workspace=workspace,
        agent_name=agent_name,
    )
    s.connect_to(
        endpoint=access["itp_endpoint"],
        station_token=access["station_token"],
        audience=access["audience"],
        sender_id=principal_id,
    )
    return s


def _classify_space(messages: list[dict], principal_id: str) -> dict[str, Any]:
    """Tag a space by what kinds of payloads we see in it.

    Returns a flat dict of booleans + any role discovered from a role-card
    addressed to this principal.
    """
    info: dict[str, Any] = {
        "is_game": False,
        "is_notebook": False,
        "is_coven": False,
        "role_subspace_for": None,
        "discovered_role": None,
    }
    for m in messages:
        payload = m.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("kind")
        if kind == "game-init":
            info["is_game"] = True
        elif kind == "role-card" and payload.get("principal_id") == principal_id:
            info["is_notebook"] = True
            role = payload.get("role")
            if isinstance(role, str):
                info["discovered_role"] = role
        elif kind == "coven-membership":
            info["is_coven"] = True
        elif kind == "phase-start":
            phase = payload.get("phase") or ""
            if isinstance(phase, str):
                if "-detective" in phase:
                    info["role_subspace_for"] = "detective"
                elif "-doctor" in phase:
                    info["role_subspace_for"] = "doctor"
    return info


def _identify(space_sessions: dict[str, HttpSpaceToolSession],
              principal_id: str,
              log: logging.Logger) -> tuple[str, HttpSpaceToolSession,
                                            HttpSpaceToolSession | None]:
    """Identify the game space, the role, and the role-subspace session.

    Returns (role, game_session, role_subspace_session_or_None).

    Detective and doctor subspaces are normally identified by a `phase-start`
    INTENT carrying `-detective` / `-doctor` — but those don't get posted
    until night-1. To let the bot start playing day-1 anyway we fall back
    to "the leftover invited space that isn't the game and isn't this
    bot's role-card notebook" when the bot's role is detective or doctor.
    """
    game_session: HttpSpaceToolSession | None = None
    notebook_space_id: str | None = None
    role_subspace_session: HttpSpaceToolSession | None = None
    discovered_role: str = "villager"
    classified: dict[str, dict[str, Any]] = {}

    for space_id, sess in space_sessions.items():
        try:
            messages = sess.scan_full(space_id).get("messages", [])
        except Exception as exc:
            log.warning("scan failed for invited space %s: %s", space_id, exc)
            continue
        info = _classify_space(messages, principal_id)
        classified[space_id] = info
        log.info(
            "  space=%s game=%s notebook=%s coven=%s role_sub_for=%s discovered_role=%s",
            space_id, info["is_game"], info["is_notebook"], info["is_coven"],
            info["role_subspace_for"], info["discovered_role"],
        )
        if info["is_game"]:
            game_session = sess
        if info["is_notebook"] and isinstance(info["discovered_role"], str):
            discovered_role = info["discovered_role"]
            notebook_space_id = space_id
        if info["is_coven"] or info["role_subspace_for"] in {"detective", "doctor"}:
            role_subspace_session = sess

    if game_session is None:
        raise RuntimeError(
            "could not identify game space among invited spaces "
            "(no game-init INTENT seen)"
        )

    # Late-binding fallback for detective / doctor: their role subspace
    # has no identifying INTENT until night-1, but it's reliably the only
    # invited space that's neither the game nor this bot's notebook.
    if discovered_role in {"detective", "doctor"} and role_subspace_session is None:
        leftovers = [
            (sid, sess) for sid, sess in space_sessions.items()
            if not classified.get(sid, {}).get("is_game")
            and not classified.get(sid, {}).get("is_coven")
            and sid != notebook_space_id
        ]
        if len(leftovers) == 1:
            sid, sess = leftovers[0]
            role_subspace_session = sess
            log.info(
                "  fallback: assigning leftover space %s as %s subspace",
                sid, discovered_role,
            )
        elif len(leftovers) > 1:
            # Multiple candidates — stale invitations from prior games.
            # Pick the one with fewest messages (freshly provisioned).
            best_sid, best_sess, best_count = None, None, float("inf")
            for sid, sess in leftovers:
                try:
                    msgs = sess.scan_full(sid).get("messages", [])
                    count = len(msgs)
                except Exception:
                    count = 999
                log.info("  fallback candidate %s has %d messages", sid, count)
                if count < best_count:
                    best_sid, best_sess, best_count = sid, sess, count
            if best_sess is not None:
                role_subspace_session = best_sess
                log.info(
                    "  fallback: picked %s (%d msgs) as %s subspace from %d candidates",
                    best_sid, best_count, discovered_role, len(leftovers),
                )
            else:
                log.warning(
                    "  fallback for %s subspace failed: %d candidate(s) (%s)",
                    discovered_role, len(leftovers), [sid for sid, _ in leftovers],
                )

    return discovered_role, game_session, role_subspace_session


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _select_persona(name: str | None, rng: random.Random) -> dict[str, Any]:
    personas = load_personas()
    if name and name != "random":
        if name in personas:
            return personas[name]
    return rng.choice(list(personas.values()))


def _build_bot(role: str, persona: dict[str, Any], principal_id: str,
               sessions: dict[str, HttpSpaceToolSession], llm: LLMClient,
               substrate: str, agent_name: str, log_dir: Path):
    bot_cls = {
        "villager": VillagerBot,
        "mafia": MafiaBot,
        "detective": DetectiveBot,
        "doctor": DoctorBot,
    }.get(role)
    if bot_cls is None:
        raise ValueError(f"unknown role from role-card: {role!r}")
    return bot_cls(
        role=role,
        persona=persona,
        principal_id=principal_id,
        sessions=sessions,
        llm=llm,
        substrate=substrate,
        log_dir=log_dir,
        agent_name=agent_name,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a single Mafia bot.")
    parser.add_argument("--workspace", required=True,
                        help="Per-bot workspace directory (must already be enrolled).")
    parser.add_argument("--persona", default="random",
                        help="Persona name from personas.json, or 'random'.")
    parser.add_argument("--llm-model", default="claude-haiku-4-5-20251001",
                        help="Model identifier passed to the LLM provider.")
    parser.add_argument("--substrate", default="agent",
                        choices=["agent", "human"])
    parser.add_argument("--lobby-wait-s", type=float, default=180.0)
    parser.add_argument("--invitation-wait-s", type=float, default=240.0)
    parser.add_argument("--invitation-settle-s", type=float, default=45.0,
                        help="After min invitations arrive, wait this long for late ones.")
    parser.add_argument("--min-invitations", type=int, default=2,
                        help="Minimum invitations to wait for (game + notebook).")
    parser.add_argument("--rng-seed", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--game-id", default=None,
                        help="Per-run tag echoed by the host's lobby-open. "
                             "When set, the bot ignores any lobby-open "
                             "without a matching game_id.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    workspace = Path(args.workspace).resolve()
    workspace_name = workspace.name
    agent_name = workspace_name.replace(".intent-space-", "") or workspace_name
    log = logging.getLogger(f"bot.{agent_name}")

    summary_path = workspace / "enrollment-summary.json"
    if not summary_path.exists():
        log.error("missing %s — run scripts/enroll_agent.py for this workspace first",
                  summary_path)
        return 2

    summary = json.loads(summary_path.read_text())
    principal_id = summary.get("principal_id")
    home_space_id = summary.get("home_space_id")
    if not isinstance(principal_id, str) or not isinstance(home_space_id, str):
        log.error("malformed enrollment summary: %r", summary)
        return 2

    rng = random.Random(args.rng_seed)
    persona = _select_persona(args.persona, rng)
    log.info("persona=%s principal=%s home=%s", persona["name"], principal_id, home_space_id)

    # Plumb the model into LLMClient via env (the provider reads it lazily).
    canonical_model = _MODEL_ALIASES.get(args.llm_model, args.llm_model)
    os.environ.setdefault("ANTHROPIC_MODEL", canonical_model)

    # ---- Build home session ----
    home_session = HttpSpaceToolSession(
        endpoint=f"{SPACEBASE1_ORIGIN}/commons",
        workspace=workspace,
        agent_name=agent_name,
    )
    try:
        home_session.connect()
    except Exception as exc:
        log.error("home session connect failed: %s", exc)
        return 3

    # Capture commons + home access for the lobby ↔ invitation switch.
    commons_target = _lookup_station(home_session, audience_suffix="/space/commons")
    home_target = _lookup_station(home_session, space_id=home_space_id)
    if commons_target is None or home_target is None:
        log.error("could not locate commons + home stations in known-stations.json "
                  "(commons=%s, home=%s)",
                  commons_target is not None, home_target is not None)
        return 3

    # The bot's commons-scoped principal differs from its home-scoped
    # principal. The lobby-join PROMISE must carry the HOME principal so
    # the host can use it as a participant in the shared-space request.
    home_principal_id = home_target.get("principal_id") or principal_id

    # ---- Lobby (in commons) ----
    try:
        _switch_to(home_session, commons_target, log=log, label="commons")
    except Exception as exc:
        log.error("switch to commons failed: %s", exc)
        return 4
    try:
        _join_lobby(home_session, home_principal_id, args.substrate,
                    args.lobby_wait_s, log, game_id=args.game_id,
                    agent_label=agent_name)
    except Exception as exc:
        log.error("lobby join failed: %s", exc)
        return 4
    # Ignore stale invitations already present in this home space from
    # previous E2E runs. The steward timestamps current-run invitations
    # after this point.
    invitation_not_before_ms = int(time.time() * 1000) - 1000

    # ---- Switch back to home for invitations ----
    try:
        _switch_to(home_session, home_target, log=log, label="home")
    except Exception as exc:
        log.error("switch back to home failed: %s", exc)
        return 5

    # ---- Invitations ----
    try:
        invitations = _wait_for_invitations(
            home_session, home_space_id,
            min_count=args.min_invitations,
            deadline_s=args.invitation_wait_s,
            settle_s=args.invitation_settle_s,
            not_before_ms=invitation_not_before_ms,
            log=log,
        )
    except Exception as exc:
        log.error("invitation discovery failed: %s", exc)
        return 5

    # ---- Per-space sessions ----
    space_sessions: dict[str, HttpSpaceToolSession] = {}
    for inv in invitations:
        payload = inv.get("payload") or {}
        access = payload.get("access")
        space_id = payload.get("shared_space_id")
        if not (isinstance(access, dict) and isinstance(space_id, str)):
            continue
        try:
            sess = _build_subspace_session(workspace, agent_name, access, principal_id)
        except Exception as exc:
            log.error("connect_to %s failed: %s", space_id, exc)
            return 6
        space_sessions[space_id] = sess
        log.info("connected to invited space %s (audience=%s)", space_id,
                 access.get("audience"))

    try:
        role, game_session, role_subspace_session = _identify(
            space_sessions, principal_id, log
        )
    except Exception as exc:
        log.error("space identification failed: %s", exc)
        return 7

    sessions: dict[str, HttpSpaceToolSession] = {"game": game_session}
    if role in {"mafia", "detective", "doctor"} and role_subspace_session is not None:
        sessions[role] = role_subspace_session
    elif role in {"mafia", "detective", "doctor"} and role_subspace_session is None:
        log.error("role=%s requires a role subspace but none was identified", role)
        return 8

    log.info("ready: role=%s sessions=%s", role, sorted(sessions.keys()))

    # ---- LLM + bot ----
    try:
        llm = LLMClient()
    except Exception as exc:
        log.error("LLM init failed: %s", exc)
        return 9

    log_dir = _PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    bot = _build_bot(
        role=role,
        persona=persona,
        principal_id=principal_id,
        sessions=sessions,
        llm=llm,
        substrate=args.substrate,
        agent_name=agent_name,
        log_dir=log_dir,
    )

    log.info("entering bot.run_loop")
    try:
        bot.run_loop()
    except KeyboardInterrupt:
        log.info("interrupted")
        return 130
    except Exception as exc:
        log.error("bot loop crashed: %s", exc, exc_info=True)
        return 10
    log.info("bot loop finished cleanly (saw game-end)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
