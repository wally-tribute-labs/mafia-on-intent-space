#!/usr/bin/env python3
"""Run a full bot-vs-bot game end-to-end.

Launches:
  - the host  (`python -m src.host.main`) as a subprocess
  - one bot   (`python -m src.bots.run`)  per player slot

All subprocesses share the project root as CWD, get their per-agent
workspace directory via `--workspace`, and have their stdout+stderr
forwarded to `logs/<agent>.log` (line-buffered).

Usage:
    python scripts/run_bot_game.py --player-count 7 --phase-multiplier 0.5

On Ctrl+C / SIGTERM the orchestrator sends SIGTERM to every child, waits
5 seconds, then SIGKILLs anything still alive. Bots that exit before the
host (e.g. crashed early) get re-flagged in the final summary.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"


def _load_dotenv() -> None:
    """Best-effort: read PROJECT_ROOT/.env into os.environ so subprocesses
    inherit ANTHROPIC_API_KEY without the user having to `source` first.

    Lines starting with `#` are skipped. Quotes are stripped. Existing
    env vars take precedence (no overwrite).
    """
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value


_load_dotenv()


@dataclass
class Child:
    name: str
    proc: subprocess.Popen
    log_path: Path
    log_handle: IO[str]
    started_at: float = field(default_factory=time.time)


def _open_log(name: str) -> tuple[Path, IO[str]]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{name}.log"
    handle = log_path.open("w", buffering=1, encoding="utf-8")
    handle.write(f"# {name} — started {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    handle.flush()
    return log_path, handle


def _spawn(name: str, argv: list[str], env: dict[str, str]) -> Child:
    log_path, log_handle = _open_log(name)
    proc = subprocess.Popen(
        argv,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return Child(name=name, proc=proc, log_path=log_path, log_handle=log_handle)


def _terminate_all(children: list[Child], log: logging.Logger, grace_s: float = 5.0) -> None:
    """Send SIGTERM to every still-running child, then SIGKILL after grace."""
    alive = [c for c in children if c.proc.poll() is None]
    if not alive:
        return
    log.info("sending SIGTERM to %d running child(ren)", len(alive))
    for c in alive:
        try:
            c.proc.terminate()
        except Exception as exc:
            log.warning("terminate(%s) failed: %s", c.name, exc)
    deadline = time.time() + grace_s
    while time.time() < deadline:
        alive = [c for c in children if c.proc.poll() is None]
        if not alive:
            break
        time.sleep(0.25)
    alive = [c for c in children if c.proc.poll() is None]
    for c in alive:
        log.warning("SIGKILL %s (didn't exit on SIGTERM)", c.name)
        try:
            c.proc.kill()
        except Exception as exc:
            log.warning("kill(%s) failed: %s", c.name, exc)
    for c in children:
        try:
            c.log_handle.close()
        except Exception:
            pass


def _verify_player_workspaces(player_count: int, log: logging.Logger) -> bool:
    missing: list[Path] = []
    for i in range(1, player_count + 1):
        ws = PROJECT_ROOT / f".intent-space-mafia-player-{i}"
        if not (ws / "enrollment-summary.json").exists():
            missing.append(ws)
    if missing:
        log.error("missing enrolled player workspace(s):")
        for ws in missing:
            log.error("  %s", ws)
        log.error("run `make enroll-players` first")
        return False
    host_ws = PROJECT_ROOT / ".intent-space-mafia-game-host"
    if not (host_ws / "enrollment-summary.json").exists():
        log.error("missing host workspace %s — run `make enroll-host`", host_ws)
        return False
    return True


def _read_observatory_url(workspace: Path) -> str | None:
    summary = workspace / "enrollment-summary.json"
    if not summary.exists():
        return None
    try:
        data = json.loads(summary.read_text())
    except Exception:
        return None
    url = data.get("observatory_url")
    return url if isinstance(url, str) else None


def _wait_for_host(host: Child, bot_children: list[Child],
                   *, timeout_s: float, log: logging.Logger) -> tuple[bool, float]:
    """Wait until the host process exits or the timeout is reached.

    Periodically reaps zombie bots (so the final summary is accurate) and
    surfaces early bot deaths in the log.
    """
    start = time.time()
    last_status_at = start
    seen_dead: set[str] = set()
    while True:
        rc = host.proc.poll()
        if rc is not None:
            return True, time.time() - start
        elapsed = time.time() - start
        if elapsed > timeout_s:
            log.warning("host still running after %.0fs — giving up wait", timeout_s)
            return False, elapsed
        # Surface early bot deaths.
        for c in bot_children:
            if c.name in seen_dead:
                continue
            brc = c.proc.poll()
            if brc is not None:
                seen_dead.add(c.name)
                log.warning("bot %s exited early with code %d (log=%s)",
                            c.name, brc, c.log_path.name)
        # Heartbeat every 30s.
        if time.time() - last_status_at >= 30.0:
            living_bots = sum(1 for c in bot_children if c.proc.poll() is None)
            log.info("[%.0fs] host running, %d/%d bots alive",
                     elapsed, living_bots, len(bot_children))
            last_status_at = time.time()
        time.sleep(1.0)


def _wait_for_bots(bot_children: list[Child], *, grace_s: float, log: logging.Logger) -> None:
    """Give bots time to scan game-end and exit cleanly before teardown."""
    deadline = time.time() + grace_s
    while time.time() < deadline:
        alive = [c for c in bot_children if c.proc.poll() is None]
        if not alive:
            return
        time.sleep(1.0)
    alive = [c.name for c in bot_children if c.proc.poll() is None]
    if alive:
        log.info("bot grace expired; still running: %s", ", ".join(alive))


def _grep_log_for(path: Path, needle: str) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return any(needle in line for line in f)
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch host + bot subprocesses for a full game.",
    )
    parser.add_argument("--player-count", type=int, default=7)
    parser.add_argument("--phase-multiplier", type=float, default=1.0)
    parser.add_argument("--lobby-deadline-s", type=float, default=90.0)
    parser.add_argument("--max-days", type=int, default=7)
    parser.add_argument("--timeout-s", type=float, default=600.0,
                        help="Hard timeout for the host process before forced teardown.")
    parser.add_argument("--bot-finish-grace-s", type=float, default=45.0,
                        help="After host exits, wait this long for bots to observe game-end.")
    parser.add_argument("--persona", default="random",
                        help="Force a specific persona for every bot, or 'random'.")
    parser.add_argument("--llm-model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--rng-seed", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("orchestrator")

    if args.player_count > 0 and "ANTHROPIC_API_KEY" not in os.environ:
        log.error("ANTHROPIC_API_KEY not set in env; bots will fail to call the LLM")
        return 2

    if not _verify_player_workspaces(args.player_count, log):
        return 3

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.rng_seed)
    base_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    children: list[Child] = []
    host_child: Child | None = None
    # Per-run identifier so bots can ignore stale lobby-open INTENTs left
    # behind by previous runs in the (append-only) commons.
    run_id = uuid.uuid4().hex[:12]
    log.info("run_id=%s", run_id)

    def _shutdown(_signum=None, _frame=None) -> None:
        log.info("shutdown requested")
        _terminate_all(children, log)
        sys.exit(130)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # --- 1. Launch the host ---
    host_argv = [
        sys.executable, "-m", "src.host.main",
        "--player-count", str(args.player_count),
        "--phase-multiplier", str(args.phase_multiplier),
        "--lobby-deadline-s", str(args.lobby_deadline_s),
        "--max-days", str(args.max_days),
        "--workspace", str(PROJECT_ROOT / ".intent-space-mafia-game-host"),
        "--log-level", args.log_level,
        "--game-id", run_id,
    ]
    log.info("spawning host: %s", " ".join(host_argv))
    host_child = _spawn("host", host_argv, base_env)
    children.append(host_child)

    # Brief pause so the host has the lobby-open INTENT in commons before
    # bots start scanning. Avoids 7 bots racing to scan-empty-commons.
    time.sleep(2.0)

    # --- 2. Launch each bot ---
    bot_children: list[Child] = []
    for i in range(1, args.player_count + 1):
        ws = PROJECT_ROOT / f".intent-space-mafia-player-{i}"
        bot_argv = [
            sys.executable, "-m", "src.bots.run",
            "--workspace", str(ws),
            "--persona", args.persona,
            "--llm-model", args.llm_model,
            "--substrate", "agent",
            "--lobby-wait-s", str(max(args.lobby_deadline_s + 30.0, 120.0)),
            "--log-level", args.log_level,
            "--game-id", run_id,
        ]
        if args.rng_seed is not None:
            bot_argv += ["--rng-seed", str(rng.randint(0, 1_000_000))]
        log.info("spawning bot mafia-player-%d", i)
        bot = _spawn(f"mafia-player-{i}", bot_argv, base_env)
        children.append(bot)
        bot_children.append(bot)

    # --- 3. Wait for the host to finish ---
    log.info("waiting for host to exit (timeout=%.0fs) ...", args.timeout_s)
    finished, elapsed = _wait_for_host(
        host_child, bot_children, timeout_s=args.timeout_s, log=log,
    )
    host_rc = host_child.proc.returncode
    log.info("host exited rc=%s after %.1fs (or timed out)", host_rc, elapsed)

    # --- 4. Give bots a chance to see game-end, then tear down leftovers ---
    _wait_for_bots(bot_children, grace_s=args.bot_finish_grace_s, log=log)
    _terminate_all(children, log)

    # --- 5. Summary ---
    host_log = LOGS_DIR / "host.log"
    saw_game_end = _grep_log_for(host_log, "host.game-end") or \
                   _grep_log_for(host_log, "\"winner\":")
    obs_url = _read_observatory_url(PROJECT_ROOT / ".intent-space-mafia-game-host")

    print()
    print("=" * 64)
    print(f"orchestrator summary")
    print("=" * 64)
    print(f"  host exit code:        {host_rc}")
    print(f"  host duration:         {elapsed:.1f}s")
    print(f"  host completed cleanly:{finished}")
    print(f"  saw game-end signal:   {saw_game_end}")
    print(f"  host log:              {host_log}")
    print(f"  bot logs:              {LOGS_DIR}/mafia-player-*.log")
    if obs_url:
        print(f"  host observatory URL:  {obs_url}")
    bot_exits = []
    for c in bot_children:
        bot_exits.append((c.name, c.proc.returncode))
    print(f"  bot exit codes:")
    for name, rc in bot_exits:
        print(f"    {name}: rc={rc}")
    print("=" * 64)

    if not finished:
        return 4
    if host_rc != 0:
        return host_rc or 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
