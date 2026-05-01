#!/usr/bin/env python3
"""Post the hackathon-submission INTENT in commons.

Per https://hack.memetic.software/#submit, submission lives in commons as
a single INTENT parented to the well-known submission anchor:

    intent-413e0bc5-d8f3-40e7-afb4-350e220df03c

The payload carries:
    kind:            "hackathon-submission"
    event:           "spacebase1-hackathon-2026"
    repo_url:        <github URL>
    team_name:       <team>
    agent_principal: <our host's home principal_id>
    one_liner:       <pitch>

Idempotency: scans the anchor's nested space for a previous submission by
this agent_principal first; if found, prints the existing intentId
without posting. Use --force to override.

Usage:
    python scripts/submit_hackathon.py \\
        --repo-url https://github.com/<user>/<repo> \\
        --team-name "Mafia Coven" \\
        --one-liner "Mixed-substrate Mafia on intent-space."
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


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


_SDK_PATH = _resolve_sdk_path(SCRIPT_DIR)
sys.path.insert(0, str(_SDK_PATH))

from http_space_tools import HttpSpaceToolSession  # noqa: E402


SPACEBASE1_ORIGIN = "https://spacebase1.differ.ac"
COMMONS_SPACE_ID = "commons"
SUBMISSION_PARENT_ID = "intent-413e0bc5-d8f3-40e7-afb4-350e220df03c"
EVENT = "spacebase1-hackathon-2026"


def _lookup_station(session, *, audience_suffix: str) -> dict | None:
    for entry in session.known_stations() or []:
        if not isinstance(entry, dict):
            continue
        aud = entry.get("audience")
        if not isinstance(aud, str) or not aud.endswith(audience_suffix):
            continue
        token = entry.get("stationToken")
        endpoint = entry.get("endpoint")
        if not (isinstance(token, str) and isinstance(endpoint, str)):
            continue
        return {
            "endpoint": endpoint,
            "station_token": token,
            "audience": aud,
            "principal_id": entry.get("principalId"),
        }
    return None


def _switch_to_commons(session) -> None:
    target = _lookup_station(session, audience_suffix="/space/commons")
    if target is None:
        raise RuntimeError("commons station not found in known-stations.json")
    session.connect_to(
        endpoint=target["endpoint"],
        station_token=target["station_token"],
        audience=target["audience"],
        sender_id=target.get("principal_id"),
    )


def _find_existing_submission(session, agent_principal: str) -> str | None:
    """Look for a prior hackathon-submission from this agent in the anchor's
    nested space. Returns the intentId or None.
    """
    try:
        scan = session.scan_full(SUBMISSION_PARENT_ID)
    except Exception:
        return None
    for m in scan.get("messages", []):
        if m.get("type") != "INTENT":
            continue
        payload = m.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") != "hackathon-submission":
            continue
        if payload.get("event") != EVENT:
            continue
        if payload.get("agent_principal") != agent_principal:
            continue
        iid = m.get("intentId")
        if isinstance(iid, str):
            return iid
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Submit the hackathon project INTENT.")
    p.add_argument("--workspace",
                   default=str(PROJECT_ROOT / ".intent-space-mafia-game-host"))
    p.add_argument("--repo-url", required=True)
    p.add_argument("--team-name", required=True)
    p.add_argument("--one-liner", required=True)
    p.add_argument("--force", action="store_true",
                   help="Submit even if a prior submission from this agent exists.")
    args = p.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    summary_path = workspace / "enrollment-summary.json"
    if not summary_path.exists():
        print(f"ERROR: missing {summary_path}", file=sys.stderr)
        return 2
    summary = json.loads(summary_path.read_text())
    home_principal = summary.get("principal_id")
    if not isinstance(home_principal, str):
        print(f"ERROR: malformed enrollment summary (missing principal_id)", file=sys.stderr)
        return 2

    session = HttpSpaceToolSession(
        endpoint=f"{SPACEBASE1_ORIGIN}/commons",
        workspace=workspace,
        agent_name="mafia-game-host",
    )
    session.connect()
    _switch_to_commons(session)

    # The session is now authed to commons; its agent_id is the
    # commons-scoped principal. We carry the HOME principal in the
    # payload so judges can correlate it to our shared-space activity.
    existing = _find_existing_submission(session, home_principal)
    if existing and not args.force:
        print(f"Already submitted: {existing}")
        print(f"  (use --force to override)")
        return 0

    payload = {
        "kind": "hackathon-submission",
        "event": EVENT,
        "repo_url": args.repo_url,
        "team_name": args.team_name,
        "agent_principal": home_principal,
        "one_liner": args.one_liner,
    }
    content = f"Submission: {args.team_name} — {args.one_liner}"
    intent = session.intent(
        content=content,
        parent_id=SUBMISSION_PARENT_ID,
        payload=payload,
    )
    confirmed = session.post_and_confirm(
        intent,
        step="hackathon.submit",
        confirm_space_id=SUBMISSION_PARENT_ID,
    )
    intent_id = confirmed.get("intentId") or intent.get("intentId")
    print(f"Submission posted.")
    print(f"  intentId:        {intent_id}")
    print(f"  parent:          {SUBMISSION_PARENT_ID}")
    print(f"  agent_principal: {home_principal}")
    print(f"  repo_url:        {args.repo_url}")
    print(f"  team_name:       {args.team_name}")
    print(f"  one_liner:       {args.one_liner}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
