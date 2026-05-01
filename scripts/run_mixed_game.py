#!/usr/bin/env python3
"""Run a mixed human+bot game end-to-end.

Like run_bot_game.py but reserves seats for human players.  Each human
seat is served by a bridge process (human_bridge_launcher.py) that
handles lobby-join, invitation acceptance, and the web UI.

Usage:
    python scripts/run_mixed_game.py --human-count 1 --player-count 7 --phase-multiplier 1.0

Each human gets a web UI at localhost:8000, 8001, etc.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

# Import shared infrastructure from the bot-only orchestrator.
# (The import also triggers _load_dotenv() which reads .env into os.environ.)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_bot_game import (  # noqa: E402
    HOST_WORKSPACE,
    LOGS_DIR,
    PROJECT_ROOT,
    SPECTATOR_WORKSPACE,
    Child,
    _ensure_spectator_enrolled,
    _grep_log_for,
    _open_log,
    _print_spaces_banner,
    _read_observatory_url,
    _spawn,
    _terminate_all,
    _wait_for_bots,
    _wait_for_host,
    _wait_for_host_spaces,
)


HUMAN_BASE_PORT = 8000


# ---------------------------------------------------------------------------
# Human workspace helpers
# ---------------------------------------------------------------------------


def _human_workspace(i: int, total: int) -> Path:
    """Return the workspace path for human seat *i* (1-based)."""
    if total == 1:
        return PROJECT_ROOT / ".intent-space-mafia-human"
    return PROJECT_ROOT / f".intent-space-mafia-human-{i}"


def _human_agent_name(i: int, total: int) -> str:
    if total == 1:
        return "mafia-human"
    return f"mafia-human-{i}"


def _ensure_human_enrolled(
    i: int, total: int, log: logging.Logger,
) -> bool:
    """Enroll a human workspace if its enrollment-summary.json is missing."""
    ws = _human_workspace(i, total)
    if (ws / "enrollment-summary.json").exists():
        return True

    name = _human_agent_name(i, total)
    log.info("human workspace %s missing -- enrolling as %s ...", ws, name)
    enroll_script = PROJECT_ROOT / "scripts" / "enroll_agent.py"
    if not enroll_script.exists():
        log.error("enrollment script not found: %s", enroll_script)
        return False

    enroll_log = LOGS_DIR / f"enroll-{name}.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with enroll_log.open("w", encoding="utf-8") as fh:
        rc = subprocess.run(
            [
                sys.executable,
                str(enroll_script),
                "--agent-name", name,
                "--workspace", str(ws),
            ],
            cwd=str(PROJECT_ROOT),
            stdout=fh,
            stderr=subprocess.STDOUT,
            text=True,
        ).returncode

    if rc != 0:
        log.error("human enrollment failed (rc=%d) -- see %s", rc, enroll_log)
        return False
    log.info("human enrolled: %s (log: %s)", name, enroll_log)
    return True


def _verify_mixed_workspaces(
    player_count: int, human_count: int, log: logging.Logger,
) -> bool:
    """Verify host + bot + human workspaces are all enrolled."""
    host_ws = HOST_WORKSPACE
    if not (host_ws / "enrollment-summary.json").exists():
        log.error("missing host workspace %s -- run `make enroll-host`",
                  host_ws)
        return False

    bot_count = player_count - human_count
    for i in range(1, bot_count + 1):
        ws = PROJECT_ROOT / f".intent-space-mafia-player-{i}"
        if not (ws / "enrollment-summary.json").exists():
            log.error("missing bot workspace %s -- run `make enroll-players`",
                      ws)
            return False

    for i in range(1, human_count + 1):
        if not _ensure_human_enrolled(i, human_count, log):
            return False

    return True


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------


def _print_human_banner(human_count: int, player_count: int) -> None:
    """Print the BIG OBVIOUS banner with human UI URLs."""
    bot_count = player_count - human_count
    print()
    print("=" * 64)
    print()
    print("  MAFIA ON INTENT SPACE -- MIXED GAME")
    print(f"  {player_count} players: {human_count} human"
          f" + {bot_count} bot{'s' if bot_count != 1 else ''}")
    print()
    print("  HUMAN PLAYER URLs (open in browser):")
    print()
    for i in range(1, human_count + 1):
        port = HUMAN_BASE_PORT + i - 1
        label = f"Human {i}" if human_count > 1 else "Human"
        print(f"  >>> {label}: http://localhost:{port} <<<")
    print()
    print("=" * 64)
    print(flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch host + human bridges + bot subprocesses "
                    "for a mixed game.",
    )
    parser.add_argument("--human-count", type=int, default=1)
    parser.add_argument("--player-count", type=int, default=7)
    parser.add_argument("--phase-multiplier", type=float, default=1.0)
    parser.add_argument("--lobby-deadline-s", type=float, default=90.0)
    parser.add_argument("--max-days", type=int, default=7)
    parser.add_argument("--timeout-s", type=float, default=1200.0,
                        help="Hard timeout for host process.")
    parser.add_argument("--bot-finish-grace-s", type=float, default=45.0,
                        help="Grace period for bots after host exits.")
    parser.add_argument("--spectator-finish-grace-s", type=float, default=60.0)
    parser.add_argument("--persona", default="random",
                        help="Force bot persona or 'random'.")
    parser.add_argument("--llm-model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--rng-seed", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--spectator", choices=["on", "off"], default="on")
    parser.add_argument("--spaces-wait-s", type=float, default=180.0,
                        help="How long to wait for host-spaces.json.")
    args = parser.parse_args(argv)

    # ---- Validation -----------------------------------------------------
    if args.human_count < 1:
        print("error: --human-count must be >= 1", file=sys.stderr)
        return 1
    if args.human_count >= args.player_count:
        print("error: --human-count must be < --player-count",
              file=sys.stderr)
        return 1

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("orchestrator")

    bot_count = args.player_count - args.human_count

    if bot_count > 0 and "ANTHROPIC_API_KEY" not in os.environ:
        log.error("ANTHROPIC_API_KEY not set; bots will fail to call the LLM")
        return 2

    if not _verify_mixed_workspaces(
        args.player_count, args.human_count, log,
    ):
        return 3

    if args.spectator == "on":
        if not _ensure_spectator_enrolled(log):
            log.error("could not enroll spectator -- aborting")
            return 3

    # ---- Housekeeping ---------------------------------------------------
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    for stale_ndjson in LOGS_DIR.glob("*.ndjson"):
        stale_ndjson.unlink(missing_ok=True)

    stale = HOST_WORKSPACE / "host-spaces.json"
    if stale.exists():
        try:
            stale.unlink()
        except OSError:
            pass

    rng = random.Random(args.rng_seed)
    base_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    children: list[Child] = []
    host_child: Child | None = None
    run_id = uuid.uuid4().hex[:12]
    log.info("run_id=%s (%d human + %d bots)", run_id, args.human_count, bot_count)

    def _shutdown(_signum=None, _frame=None) -> None:
        log.info("shutdown requested")
        _terminate_all(children, log)
        sys.exit(130)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # --- 1. Launch host --------------------------------------------------
    host_argv = [
        sys.executable, "-m", "src.host.main",
        "--player-count", str(args.player_count),
        "--phase-multiplier", str(args.phase_multiplier),
        "--lobby-deadline-s", str(args.lobby_deadline_s),
        "--max-days", str(args.max_days),
        "--workspace", str(HOST_WORKSPACE),
        "--log-level", args.log_level,
        "--game-id", run_id,
    ]
    log.info("spawning host")
    host_child = _spawn("host", host_argv, base_env)
    children.append(host_child)

    time.sleep(2.0)

    # --- 2. Launch human bridge launchers (before bots to claim seats) ---
    human_children: list[Child] = []
    for i in range(1, args.human_count + 1):
        ws = _human_workspace(i, args.human_count)
        port = HUMAN_BASE_PORT + i - 1
        human_argv = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "human_bridge_launcher.py"),
            "--workspace", str(ws),
            "--port", str(port),
            "--game-id", run_id,
            "--lobby-wait-s",
            str(max(args.lobby_deadline_s + 30.0, 120.0)),
            "--substrate", "human",
            "--log-level", args.log_level,
        ]
        name = f"human-{i}" if args.human_count > 1 else "human"
        log.info("spawning %s bridge (port %d)", name, port)
        child = _spawn(name, human_argv, base_env)
        children.append(child)
        human_children.append(child)

    # --- 3. Launch bots --------------------------------------------------
    bot_children: list[Child] = []
    for i in range(1, bot_count + 1):
        ws = PROJECT_ROOT / f".intent-space-mafia-player-{i}"
        bot_argv = [
            sys.executable, "-m", "src.bots.run",
            "--workspace", str(ws),
            "--persona", args.persona,
            "--llm-model", args.llm_model,
            "--substrate", "agent",
            "--lobby-wait-s",
            str(max(args.lobby_deadline_s + 30.0, 120.0)),
            "--log-level", args.log_level,
            "--game-id", run_id,
        ]
        if args.rng_seed is not None:
            bot_argv += ["--rng-seed", str(rng.randint(0, 1_000_000))]
        log.info("spawning bot mafia-player-%d", i)
        bot = _spawn(f"mafia-player-{i}", bot_argv, base_env)
        children.append(bot)
        bot_children.append(bot)

    # --- 3b. Spectator (optional, on by default) -------------------------
    spectator_child: Child | None = None
    if args.spectator == "on":
        spec_argv = [
            sys.executable, "-m", "src.spectator.main",
            "--workspace", str(SPECTATOR_WORKSPACE),
            "--agent-name", "mafia-spectator",
            "--poll-interval-s", "5.0",
            "--log-level", args.log_level,
        ]
        log.info("spawning spectator")
        spectator_child = _spawn("spectator", spec_argv, base_env)
        children.append(spectator_child)

    # --- 4. Print banners ------------------------------------------------
    _print_human_banner(args.human_count, args.player_count)

    spaces_data = _wait_for_host_spaces(args.spaces_wait_s, log)
    if spaces_data is not None:
        _print_spaces_banner(spaces_data)
        # Reprint human URLs after the (possibly long) spaces output
        _print_human_banner(args.human_count, args.player_count)

    # --- 5. Wait for host to finish --------------------------------------
    log.info("waiting for host to exit (timeout=%.0fs) ...", args.timeout_s)
    all_player_children = human_children + bot_children
    finished, elapsed = _wait_for_host(
        host_child, all_player_children,
        timeout_s=args.timeout_s, log=log,
    )
    host_rc = host_child.proc.returncode
    log.info("host exited rc=%s after %.1fs", host_rc, elapsed)

    # --- 6. Grace period + teardown --------------------------------------
    _wait_for_bots(bot_children, grace_s=args.bot_finish_grace_s, log=log)
    if spectator_child is not None and spectator_child.proc.poll() is None:
        log.info("waiting up to %.0fs for spectator ...",
                 args.spectator_finish_grace_s)
        _wait_for_bots(
            [spectator_child], grace_s=args.spectator_finish_grace_s, log=log,
        )
    _terminate_all(children, log)

    # --- 7. Summary ------------------------------------------------------
    host_log = LOGS_DIR / "host.log"
    saw_game_end = (
        _grep_log_for(host_log, "host.game-end")
        or _grep_log_for(host_log, '"winner":')
    )
    obs_url = _read_observatory_url(HOST_WORKSPACE)

    print()
    print("=" * 64)
    print("  orchestrator summary (mixed game)")
    print("=" * 64)
    print(f"  host exit code:         {host_rc}")
    print(f"  host duration:          {elapsed:.1f}s")
    print(f"  host completed cleanly: {finished}")
    print(f"  saw game-end signal:    {saw_game_end}")
    print(f"  host log:               {host_log}")
    if obs_url:
        print(f"  host observatory URL:   {obs_url}")
    print("  human exits:")
    for c in human_children:
        print(f"    {c.name}: rc={c.proc.returncode}")
    print("  bot exits:")
    for c in bot_children:
        print(f"    {c.name}: rc={c.proc.returncode}")
    if spectator_child is not None:
        print(f"  spectator exit code:    {spectator_child.proc.returncode}")
    print("=" * 64)

    _print_human_banner(args.human_count, args.player_count)

    final_spaces = None
    spaces_path = HOST_WORKSPACE / "host-spaces.json"
    if spaces_path.exists():
        try:
            final_spaces = json.loads(spaces_path.read_text())
        except Exception:
            final_spaces = None
    if final_spaces is not None:
        _print_spaces_banner(final_spaces)

    if not finished:
        return 4
    if host_rc != 0:
        return host_rc or 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
