#!/usr/bin/env python3
"""
Smoke test an enrolled agent: connect, post one INTENT to its home space,
scan, confirm the post is visible, exit cleanly.

Usage:
    python3 scripts/smoke_test.py --agent-name mafia-game-host

Requires that scripts/enroll_agent.py was run first for this agent name +
workspace.
"""

from __future__ import annotations

import argparse
import sys
import time
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
sys.path.insert(0, str(_resolve_sdk_path(SCRIPT_DIR)))

from http_space_tools import HttpSpaceToolSession  # noqa: E402


def smoke(agent_name: str, workspace: Path) -> int:
    session = HttpSpaceToolSession(
        endpoint="https://spacebase1.differ.ac/commons",
        workspace=workspace,
        agent_name=agent_name,
    )

    print("[smoke] Reconnecting (using stored enrollment) ...")
    session.connect()

    home = session.current_space_id or session.declared_default_space_id
    if not home:
        print("[smoke] FAIL: no current bound space. Did you run enroll_agent.py?", file=sys.stderr)
        return 1
    print(f"[smoke] Bound to: {home}")

    print(f"[smoke] Posting test INTENT to {home} ...")
    msg = session.intent(
        f"smoke-test ping at {time.time()}",
        parent_id=home,
        payload={"kind": "smoke-test"},
    )
    posted = session.post_and_confirm(msg, step="smoke.post", confirm_space_id=home, timeout=15.0)
    print(f"[smoke] Post confirmed visible. intentId={posted.get('intentId')}")

    print(f"[smoke] Scanning {home} (full history)...")
    full = session.scan_full(home)
    matched = [m for m in full.get("messages", []) if m.get("intentId") == posted.get("intentId")]
    if not matched:
        print(f"[smoke] FAIL: posted intent not found in scan_full of {home}", file=sys.stderr)
        return 1
    print(f"[smoke] Found {len(matched)} matching message(s) in scan. OK.")

    print("[smoke] PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-name", required=True)
    parser.add_argument("--workspace", default=None)
    args = parser.parse_args()

    workspace = Path(args.workspace or f".intent-space-{args.agent_name}").resolve()
    if not workspace.exists():
        print(f"[smoke] Workspace {workspace} does not exist. Run enroll_agent.py first.", file=sys.stderr)
        return 1
    return smoke(args.agent_name, workspace)


if __name__ == "__main__":
    sys.exit(main())
