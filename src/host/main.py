"""Host CLI entry point.

Usage:
    python -m src.host.main \\
        --player-count 7 \\
        --workspace .intent-space-mafia-game-host \\
        --phase-multiplier 1.0

Smoke mode:
    python -m src.host.main --player-count 0
        Opens a lobby with 0 expected players, lets it time out, then
        runs the game loop with an empty player set so it short-circuits
        to "draw" + a summary in commons.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from urllib.parse import quote


SPACEBASE1_ORIGIN = "https://spacebase1.differ.ac"


def _build_observatory_url(space_id: str, station_token: str) -> str:
    return (
        f"{SPACEBASE1_ORIGIN}/observatory#"
        f"origin={quote(SPACEBASE1_ORIGIN, safe='')}"
        f"&space={quote(space_id, safe='')}"
        f"&token={quote(station_token, safe='')}"
    )


def _label_space(space_id: str, state) -> str:
    """Human-readable label for an observatory entry."""
    if space_id == "commons":
        return "commons"
    if space_id == state.host_home_space_id:
        return "host-home"
    if space_id == state.game_space_id:
        return "game-space (public day discussion + voting)"
    if state.coven_id and space_id == state.coven_id:
        return "coven (mafia private)"
    if state.detective_id and space_id == state.detective_id:
        return "detective-notebook"
    if state.doctor_id and space_id == state.doctor_id:
        return "doctor-notebook"
    for card in state.player_cards:
        if card.private_space_id == space_id:
            return f"role-card to {card.role} ({card.principal_id[-8:]})"
    return f"other ({space_id[-8:]})"


def _dump_spaces_summary(workspace: Path, state, log: logging.Logger) -> Path:
    """Persist every space the host can observe, with an observatory URL."""
    spaces: list[dict] = []
    for space_id, access in state.subspace_access.items():
        if not isinstance(access, dict):
            continue
        token = access.get("station_token")
        if not isinstance(token, str):
            continue
        spaces.append({
            "space_id": space_id,
            "label": _label_space(space_id, state),
            "audience": access.get("audience"),
            "observatory_url": _build_observatory_url(space_id, token),
        })
    spaces.sort(key=lambda s: (
        0 if s["label"] == "commons" else
        1 if s["label"] == "host-home" else
        2 if s["label"].startswith("game-space") else
        3 if s["label"].startswith("coven") else
        4 if s["label"].startswith("detective") else
        5 if s["label"].startswith("doctor") else
        6
    ))
    out = {
        "host_principal_id": state.host_principal_id,
        "host_home_space_id": state.host_home_space_id,
        "game_space_id": state.game_space_id,
        "coven_id": state.coven_id,
        "detective_id": state.detective_id,
        "doctor_id": state.doctor_id,
        "players": list(state.players),
        "role_truth": dict(state.role_truth),
        "lobby_intent_id": state.lobby_intent_id,
        "spaces": spaces,
    }
    path = workspace / "host-spaces.json"
    try:
        path.write_text(json.dumps(out, indent=2))
        log.info("wrote host-spaces.json with %d space(s) -> %s", len(spaces), path)
    except OSError as exc:
        log.warning("could not write host-spaces.json: %s", exc)
    return path


def _resolve_sdk_path(start: Path) -> Path:
    """Mirror scripts/enroll_agent.py's SDK locator."""
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


def _load_enrollment(workspace: Path) -> dict:
    summary = workspace / "enrollment-summary.json"
    if not summary.exists():
        raise FileNotFoundError(
            f"{summary} missing — run `make enroll-host` first"
        )
    return json.loads(summary.read_text())


def _build_session(workspace: Path):
    project_root = Path(__file__).resolve().parents[2]
    sdk_path = _resolve_sdk_path(project_root / "scripts")
    sys.path.insert(0, str(sdk_path))
    from http_space_tools import HttpSpaceToolSession  # noqa: WPS433

    session = HttpSpaceToolSession(
        endpoint=f"{SPACEBASE1_ORIGIN}/commons",
        workspace=workspace,
        agent_name="mafia-game-host",
    )
    session.connect()
    session.confirm_current_space()
    return session


def _lookup_station(session, *, space_id: str | None = None,
                    audience_suffix: str | None = None) -> dict | None:
    """Find a (endpoint, station_token, audience, principal_id) entry in the
    session's persisted known_stations. Returns None if not present.
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
        if not (isinstance(token, str) and isinstance(endpoint, str) and isinstance(audience, str)):
            continue
        return {
            "endpoint": endpoint,
            "station_token": token,
            "audience": audience,
            "principal_id": entry.get("principalId"),
        }
    return None


def _switch_to(session, target: dict, *, log: logging.Logger, label: str) -> None:
    """connect_to(...) the session to the target station.

    `target` keys: endpoint, station_token, audience, principal_id (optional).
    Raises if connect_to is unavailable or fails.
    """
    connect_to = getattr(session, "connect_to", None)
    if not callable(connect_to):
        raise RuntimeError("session lacks connect_to method")
    connect_to(
        endpoint=target["endpoint"],
        station_token=target["station_token"],
        audience=target["audience"],
        sender_id=target.get("principal_id"),
    )
    log.info("switched session -> %s (audience=%s)", label, target["audience"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Mafia game host.")
    parser.add_argument("--player-count", type=int, default=7)
    parser.add_argument(
        "--workspace",
        default=".intent-space-mafia-game-host",
        help="Host enrollment workspace directory.",
    )
    parser.add_argument("--phase-multiplier", type=float, default=1.0)
    parser.add_argument("--lobby-deadline-s", type=float, default=60.0)
    parser.add_argument("--max-days", type=int, default=7)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--game-id", default=None,
                        help="Optional per-run tag echoed in the lobby-open "
                             "payload so bots can filter out stale lobbies.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("host.main")

    workspace = Path(args.workspace).resolve()
    enrollment = _load_enrollment(workspace)
    log.info("loaded enrollment: %s", enrollment.get("principal_id"))

    try:
        session = _build_session(workspace)
    except Exception as exc:
        log.error("failed to build session: %s", exc)
        return 2

    # Capture commons + home access so we can flip between them. The
    # lobby phase needs commons (post lobby-open + scan for lobby-join).
    # start_game needs home (steward shared-space provisioning runs from
    # the host's home space). phase_loop's _connect_to_space already
    # handles further switching across game/coven/etc using state.subspace_access.
    home_space_id = enrollment.get("home_space_id")
    if not isinstance(home_space_id, str):
        log.error("enrollment summary missing home_space_id")
        return 2
    commons_target = _lookup_station(session, audience_suffix="/space/commons")
    home_target = _lookup_station(session, space_id=home_space_id)
    if commons_target is None or home_target is None:
        log.error("could not locate commons + home stations in known-stations.json "
                  "(commons=%s, home=%s)", commons_target is not None, home_target is not None)
        return 2

    from .lobby import run_lobby
    from .phase_loop import run_game_loop
    from .start import start_game

    try:
        _switch_to(session, commons_target, log=log, label="commons")
    except Exception as exc:
        log.error("switch to commons failed: %s", exc)
        return 3

    try:
        lobby = run_lobby(
            session,
            player_count=args.player_count,
            deadline_s=args.lobby_deadline_s,
            game_id=args.game_id,
        )
    except Exception as exc:
        log.error("lobby failed: %s", exc)
        return 3

    try:
        _switch_to(session, home_target, log=log, label="home")
    except Exception as exc:
        log.error("switch back to home failed: %s", exc)
        return 3

    log.info(
        "lobby returned: %d players, deadline_reached=%s",
        len(lobby.players),
        lobby.deadline_reached,
    )

    # Substrate truth comes from the lobby joiners. Here we default every
    # joined principal to "agent" since track A doesn't decide which
    # bots/humans signed up. The runner that orchestrates real games can
    # set substrates externally.
    player_substrates = {p: "agent" for p in lobby.players}

    try:
        state = start_game(
            session,
            players=lobby.players,
            player_substrates=player_substrates,
            multiplier=args.phase_multiplier,
        )
        state.lobby_intent_id = lobby.lobby_intent_id
    except Exception as exc:
        log.error("start_game failed: %s", exc)
        return 4

    log.info(
        "game space provisioned: %s (coven=%s, detective=%s, doctor=%s)",
        state.game_space_id,
        state.coven_id,
        state.detective_id,
        state.doctor_id,
    )

    _dump_spaces_summary(workspace, state, log)

    try:
        outcome = run_game_loop(session, state, max_days=args.max_days)
    except Exception as exc:
        log.error("phase loop failed: %s", exc)
        return 5

    log.info("game outcome: %s", outcome)
    print(json.dumps(outcome, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
