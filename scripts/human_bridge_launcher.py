#!/usr/bin/env python3
"""Launch a human bridge with automatic lobby-join and invitation handling.

Combines the bot SDK's lobby/invitation flow (from src.bots.run) with the
human bridge's FastAPI web UI (from src.human.bridge).  Designed to be
spawned as a subprocess by run_mixed_game.py.

Flow:
  1. Connect via enrollment, join lobby, wait for invitations
  2. Build per-space sessions, identify role + game space
  3. Start bridge web UI on the configured port

Usage (called by run_mixed_game.py):
    python scripts/human_bridge_launcher.py \\
        --workspace .intent-space-mafia-human \\
        --port 8000 \\
        --game-id abc123
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Importing src.bots.run triggers SDK sys.path bootstrap as a side effect.
from src.bots.run import (  # noqa: E402
    SPACEBASE1_ORIGIN,
    _build_subspace_session,
    _identify,
    _join_lobby,
    _lookup_station,
    _switch_to,
    _wait_for_invitations,
)

from http_space_tools import HttpSpaceToolSession  # noqa: E402
from src.human.bridge import BridgeState, create_app  # noqa: E402


# ---------------------------------------------------------------------------
# DualSessionBridgeState — game-space + private-space on separate stations
# ---------------------------------------------------------------------------


class DualSessionBridgeState(BridgeState):
    """BridgeState that uses separate SDK sessions for game and private spaces.

    The primary session (self.session) is connected to the game-space station
    and handles public feed scanning + public message posting.  The private
    session (self._private_session) is connected to the role-subspace station
    and handles coven/detective/doctor feed scanning.
    """

    def __init__(
        self,
        *,
        private_session: Optional[HttpSpaceToolSession] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._private_session = private_session

    def scan_once(self) -> None:
        try:
            pub = self.session.scan_full(self.game_space_id)
            self.public_messages = list(pub.get("messages") or [])
        except Exception as exc:
            self._last_error = f"public scan: {exc!r}"
        if self.private_space_id and self._private_session:
            try:
                priv = self._private_session.scan_full(self.private_space_id)
                self.private_messages = list(priv.get("messages") or [])
            except Exception as exc:
                self._last_error = f"private scan: {exc!r}"


# ---------------------------------------------------------------------------
# CLI + main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="human_bridge_launcher",
        description="Launch human bridge with auto lobby-join.",
    )
    parser.add_argument("--workspace", required=True,
                        help="Path to the human's enrolled workspace dir")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--game-id", default=None,
                        help="Per-run game ID from orchestrator")
    parser.add_argument("--lobby-wait-s", type=float, default=180.0)
    parser.add_argument("--invitation-wait-s", type=float, default=240.0)
    parser.add_argument("--invitation-settle-s", type=float, default=45.0)
    parser.add_argument("--min-invitations", type=int, default=2)
    parser.add_argument("--substrate", default="human",
                        choices=["human", "agent"])
    parser.add_argument("--scan-interval", type=float, default=1.5)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    workspace = Path(args.workspace).resolve()
    workspace_name = workspace.name
    agent_name = workspace_name.replace(".intent-space-", "") or workspace_name
    log = logging.getLogger(f"human-bridge.{agent_name}")

    # ---- Read enrollment ------------------------------------------------
    summary_path = workspace / "enrollment-summary.json"
    if not summary_path.exists():
        log.error("missing %s -- run scripts/enroll_agent.py first",
                  summary_path)
        return 2

    summary = json.loads(summary_path.read_text())
    principal_id = summary.get("principal_id")
    home_space_id = summary.get("home_space_id")
    if not isinstance(principal_id, str) or not isinstance(home_space_id, str):
        log.error("malformed enrollment summary: %r", summary)
        return 2
    log.info("principal=%s home=%s agent=%s",
             principal_id, home_space_id, agent_name)

    # ---- Build home session ---------------------------------------------
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

    commons_target = _lookup_station(
        home_session, audience_suffix="/space/commons",
    )
    home_target = _lookup_station(home_session, space_id=home_space_id)
    if commons_target is None or home_target is None:
        log.error("could not locate commons + home stations "
                  "(commons=%s, home=%s)",
                  commons_target is not None, home_target is not None)
        return 3

    home_principal_id = home_target.get("principal_id") or principal_id

    # ---- Join lobby (in commons) ----------------------------------------
    try:
        _switch_to(home_session, commons_target, log=log, label="commons")
    except Exception as exc:
        log.error("switch to commons failed: %s", exc)
        return 4
    try:
        _join_lobby(
            home_session, home_principal_id, args.substrate,
            args.lobby_wait_s, log,
            game_id=args.game_id,
            agent_label=agent_name,
        )
    except Exception as exc:
        log.error("lobby join failed: %s", exc)
        return 4

    invitation_not_before_ms = int(time.time() * 1000) - 1000

    # ---- Switch back to home for invitations ----------------------------
    try:
        _switch_to(home_session, home_target, log=log, label="home")
    except Exception as exc:
        log.error("switch back to home failed: %s", exc)
        return 5

    # ---- Wait for invitations -------------------------------------------
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

    # ---- Build per-space sessions ---------------------------------------
    space_sessions: dict[str, HttpSpaceToolSession] = {}
    for inv in invitations:
        payload = inv.get("payload") or {}
        access = payload.get("access")
        space_id = payload.get("shared_space_id")
        if not (isinstance(access, dict) and isinstance(space_id, str)):
            continue
        try:
            sess = _build_subspace_session(
                workspace, agent_name, access, principal_id,
            )
        except Exception as exc:
            log.error("connect_to %s failed: %s", space_id, exc)
            return 6
        space_sessions[space_id] = sess
        log.info("connected to invited space %s", space_id)

    # ---- Identify role + game space -------------------------------------
    try:
        role, game_session, role_subspace_session = _identify(
            space_sessions, principal_id, log,
        )
    except Exception as exc:
        log.error("space identification failed: %s", exc)
        return 7

    game_space_id: str | None = None
    private_space_id: str | None = None
    for sid, sess in space_sessions.items():
        if sess is game_session:
            game_space_id = sid
        if role_subspace_session is not None and sess is role_subspace_session:
            private_space_id = sid

    if game_space_id is None:
        log.error("could not determine game_space_id from identified sessions")
        return 7

    log.info("READY: role=%s game_space=%s private_space=%s port=%d",
             role, game_space_id, private_space_id, args.port)

    # ---- Build bridge state + app ---------------------------------------
    state = DualSessionBridgeState(
        session=game_session,
        game_space_id=game_space_id,
        private_space_id=private_space_id,
        role=role,
        scan_interval_s=args.scan_interval,
        private_session=role_subspace_session,
    )

    app = create_app(state, run_scan_loop=True)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
