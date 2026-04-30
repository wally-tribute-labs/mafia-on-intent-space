#!/usr/bin/env python3
"""Cross-game substrate stats CLI.

Pulls the full commons log via an enrolled agent's session, runs
src/game/stats.leaderboard, and prints a human-readable table to stdout.

Usage:
    python3 scripts/stats.py
    python3 scripts/stats.py --workspace .intent-space-mafia-spectator
    python3 scripts/stats.py --json     # machine-readable
    python3 scripts/stats.py --top 20

The default workspace is `.intent-space-mafia-spectator` because the
spectator agent is the natural reader of commons; any enrolled workspace
works (it only needs read access to commons, which is public).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _resolve_sdk_path(start: Path) -> Path:
    candidates = [
        start.parent.parent / "ccmarket" / "plugins" / "intent-space-agent-pack" / "sdk",
        Path.home() / ".claude" / "skills" / "intent-space-agent-pack" / "sdk",
        Path.home() / ".codex" / "skills" / "intent-space-agent-pack" / "sdk",
    ]
    for c in candidates:
        if c.exists() and (c / "http_space_tools.py").exists():
            return c.resolve()
    raise FileNotFoundError("intent-space-agent-pack SDK not found")


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(_resolve_sdk_path(SCRIPT_DIR)))
sys.path.insert(0, str(ROOT))

from http_space_tools import HttpSpaceToolSession  # noqa: E402

from src.game.stats import leaderboard  # noqa: E402


SPACEBASE1_ORIGIN = "https://spacebase1.differ.ac"
COMMONS_SPACE_ID = "commons"


def _short(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    if width <= 3:
        return s[:width]
    return s[: width - 1] + "…"


def render_table(rows: list[dict], top: int | None) -> str:
    if not rows:
        return "(no data — no substrate-guess + reveal pairs in commons yet)"
    if top is not None:
        rows = rows[:top]
    headers = ("rank", "principal", "stealth", "detection", "games")
    width_principal = max(len(headers[1]), max(len(r["principal"]) for r in rows))
    width_principal = min(width_principal, 48)
    fmt = (
        f"{{rank:>4}}  "
        f"{{principal:<{width_principal}}}  "
        "{stealth:>8}  {detection:>9}  {games:>5}"
    )
    out = [fmt.format(rank="rank", principal="principal", stealth="stealth",
                      detection="detection", games="games")]
    out.append("-" * len(out[0]))
    for i, r in enumerate(rows, start=1):
        out.append(
            fmt.format(
                rank=i,
                principal=_short(r["principal"], width_principal),
                stealth=f"{r['stealth']:.3f}",
                detection=f"{r['detection']:.3f}",
                games=r["games"],
            )
        )
    return "\n".join(out)


def fetch_commons(workspace: Path, agent_name: str) -> list[dict]:
    session = HttpSpaceToolSession(
        endpoint=f"{SPACEBASE1_ORIGIN}/commons",
        workspace=workspace,
        agent_name=agent_name,
    )
    session.connect()
    scan = session.scan_full(COMMONS_SPACE_ID)
    return list(scan.get("messages", []) or [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute the substrate leaderboard from commons messages.",
    )
    parser.add_argument(
        "--workspace",
        default=".intent-space-mafia-spectator",
        help="Workspace of an enrolled agent (default: spectator).",
    )
    parser.add_argument(
        "--agent-name",
        default="mafia-spectator",
        help="Agent name (only relevant if the workspace lacks a stored config).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    parser.add_argument("--top", type=int, default=20, help="Show top N rows (table mode).")
    parser.add_argument(
        "--commons-file",
        default=None,
        help="Optional path to a JSON file with {\"messages\": [...]}; skips network.",
    )
    args = parser.parse_args(argv)

    if args.commons_file:
        try:
            data = json.loads(Path(args.commons_file).read_text())
            messages = data.get("messages") if isinstance(data, dict) else data
            if not isinstance(messages, list):
                raise ValueError("commons-file must contain a list (or {messages: [...]})")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"failed to load --commons-file: {exc}", file=sys.stderr)
            return 1
    else:
        workspace = Path(args.workspace).resolve()
        if not workspace.exists():
            print(
                f"workspace {workspace} does not exist. Enroll one first via "
                "scripts/enroll_agent.py or run the spectator.",
                file=sys.stderr,
            )
            return 1
        try:
            messages = fetch_commons(workspace, args.agent_name)
        except Exception as exc:
            print(f"failed to scan commons: {exc}", file=sys.stderr)
            return 1

    rows = leaderboard(messages)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(render_table(rows, top=args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
