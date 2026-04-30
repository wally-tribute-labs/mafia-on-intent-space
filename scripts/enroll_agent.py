#!/usr/bin/env python3
"""
Enroll an agent on Spacebase1 commons and provision its home space.

Implements Path 2 (self-service through commons) of the onboarding skill at
https://spacebase1.differ.ac/spacebase1-onboard.SKILL.md

Usage:
    python3 scripts/enroll_agent.py --agent-name mafia-game-host
    python3 scripts/enroll_agent.py --agent-name mafia-player-1 --workspace .intent-space-player-1

Output (last lines):
    home_space_id:           space-...
    declaredSpaceId:         space-...     <- must match home_space_id
    currentSpaceId:          space-...     <- must match home_space_id
    visibleTopLevelIntents:  [...]         <- at least one (steward service intent)
    OBSERVATORY URL:         https://...

After successful run, the workspace contains the agent's keypair and enrollment
state. Subsequent invocations from the same workspace use the stored credentials
(idempotent reconnect via session.connect()).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

# Resolve the agent-pack SDK regardless of how the user laid things out.
# Order: workspace-relative ../ccmarket, then home-dir Claude/Codex skill dirs.
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
        "Could not locate intent-space-agent-pack SDK. Tried:\n  "
        + "\n  ".join(str(c) for c in candidates)
        + "\n\nFix: clone https://github.com/sky-valley/claude-code-marketplace into\n"
        + f"  {candidates[0].parent.parent.parent}\n"
        + "or set PYTHONPATH manually before running this script."
    )


SCRIPT_DIR = Path(__file__).resolve().parent
SDK_PATH = _resolve_sdk_path(SCRIPT_DIR)
sys.path.insert(0, str(SDK_PATH))

from http_space_tools import HttpSpaceToolSession  # noqa: E402


SPACEBASE1_ORIGIN = "https://spacebase1.differ.ac"


def build_observatory_url(origin: str, space_id: str, station_token: str) -> str:
    """Per the onboarding skill spec, construct the observatory URL by URL-encoding
    each component of the fragment string.
    """
    return (
        f"{origin}/observatory#origin={quote(origin, safe='')}"
        f"&space={quote(space_id, safe='')}"
        f"&token={quote(station_token, safe='')}"
    )


def enroll(agent_name: str, workspace: Path, signup_wait: float, complete_wait: float) -> dict:
    workspace.mkdir(parents=True, exist_ok=True)

    session = HttpSpaceToolSession(
        endpoint=f"{SPACEBASE1_ORIGIN}/commons",
        workspace=workspace,
        agent_name=agent_name,
    )

    print(f"[enroll] Workspace: {workspace.resolve()}")
    print(f"[enroll] Agent name: {agent_name}")
    print(f"[enroll] Endpoint:   {SPACEBASE1_ORIGIN}/commons")

    # ---- Welcome Mat signup against commons ----
    print("[enroll] Step 1/8: signing up to commons (Welcome Mat + DPoP) ...")
    signup_result = session.signup(f"{SPACEBASE1_ORIGIN}/commons")
    print(f"[enroll] Signup OK. principal_id={signup_result.get('principal_id')}")
    commons_space_id = signup_result.get("commons_space_id")
    print(f"[enroll] commons_space_id={commons_space_id}")

    # ---- Connect using stored credentials ----
    print("[enroll] Step 2/8: connecting (auth) ...")
    session.connect()
    print("[enroll] Connected.")

    # ---- Confirm + scan commons ----
    print("[enroll] Step 3/8: confirming current space and scanning commons ...")
    session.confirm_current_space()
    commons_view = session.scan("commons")
    print(f"[enroll] commons messages visible: {len(commons_view.get('messages', []))}")

    # ---- Post INTENT requesting home space ----
    print("[enroll] Step 4/8: posting INTENT requesting home space provisioning ...")
    request = session.post_and_confirm(
        session.intent(
            "Please provision one home space for me.",
            parent_id="commons",
            payload={
                "requestedSpace": {"kind": "home"},
                "spacePolicy": {"visibility": "private"},
            },
        ),
        step="intent.provision-home-space",
        confirm_space_id="commons",
    )
    request_space = request["intentId"]
    print(f"[enroll] Provisioning request intent: {request_space}")

    # ---- Wait for steward PROMISE ----
    print(f"[enroll] Step 5/8: waiting for steward PROMISE (up to {signup_wait}s) ...")
    promise = session.wait_for_promise(request_space, wait_seconds=signup_wait)
    promise_id = promise["promiseId"]
    print(f"[enroll] Steward promised: {promise_id}")

    # ---- Post ACCEPT ----
    print("[enroll] Step 6/8: posting ACCEPT ...")
    session.post_and_confirm(
        session.accept(promise_id=promise_id, parent_id=request_space),
        step="accept.provision-home-space",
        confirm_space_id=request_space,
    )

    # ---- Wait for steward COMPLETE ----
    print(f"[enroll] Step 7/8: waiting for steward COMPLETE (up to {complete_wait}s) ...")
    complete = session.wait_for_complete(
        request_space,
        promise_id=promise_id,
        wait_seconds=complete_wait,
    )
    payload = complete.get("payload", {})
    claim_url = payload.get("claim_url")
    bind_url = payload.get("bind_url")
    claim_token = payload.get("claim_token")
    home_space_id = payload.get("home_space_id")
    print(f"[enroll] COMPLETE received.")
    print(f"[enroll]   claim_url:     {claim_url}")
    print(f"[enroll]   bind_url:      {bind_url}")
    print(f"[enroll]   claim_token:   {claim_token[:12]}..." if claim_token else "[enroll]   claim_token:   <missing>")
    print(f"[enroll]   home_space_id: {home_space_id}")

    if not (claim_url and home_space_id):
        raise RuntimeError(
            "Steward COMPLETE missing required fields. Got payload: "
            + json.dumps(payload, indent=2)
        )

    # ---- Bind home space ----
    print("[enroll] Step 8/8: binding home space (signup against claim service) ...")
    bind_signup_result = session.signup(claim_url)
    bind_principal = bind_signup_result.get("principal_id")
    bind_token = bind_signup_result.get("station_token")
    bind_audience = bind_signup_result.get("station_audience")
    bind_observatory = bind_signup_result.get("observatory_url")
    print(f"[enroll] Bind signup OK. principal={bind_principal}")

    # ---- Reconnect using new home-space credentials ----
    session.connect()
    binding = session.verify_space_binding()

    # ---- Build observatory URL (fall back to constructed if not in response) ----
    if not bind_observatory:
        bind_observatory = build_observatory_url(SPACEBASE1_ORIGIN, home_space_id, bind_token or "")

    print()
    print("============================================================")
    print(f"home_space_id:             {home_space_id}")
    print(f"declaredSpaceId:           {binding.get('declaredSpaceId')}")
    print(f"currentSpaceId:            {binding.get('currentSpaceId')}")
    print(f"visibleTopLevelIntents:    {len(binding.get('visibleTopLevelIntents', []))} intent(s)")
    for i, intent in enumerate(binding.get("visibleTopLevelIntents", [])[:5]):
        print(f"  [{i}] sender={intent.get('senderId')} content={intent.get('content')!r}")
    print()
    print(f"agent principal_id:        {bind_principal or session.identity().get('principalId')}")
    print()
    print(f"OBSERVATORY URL:")
    print(f"  {bind_observatory}")
    print("============================================================")

    if binding.get("declaredSpaceId") != home_space_id:
        print(f"\n[enroll] WARNING: declaredSpaceId ({binding.get('declaredSpaceId')}) does not match home_space_id ({home_space_id}).", file=sys.stderr)
    if binding.get("currentSpaceId") != home_space_id:
        print(f"\n[enroll] WARNING: currentSpaceId ({binding.get('currentSpaceId')}) does not match home_space_id ({home_space_id}).", file=sys.stderr)

    # ---- Persist a friendly summary alongside the workspace ----
    summary = {
        "agent_name": agent_name,
        "principal_id": bind_principal or session.identity().get("principalId"),
        "home_space_id": home_space_id,
        "audience": bind_audience,
        "observatory_url": bind_observatory,
        "endpoint": session.endpoint,
    }
    summary_path = workspace / "enrollment-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\n[enroll] Summary saved: {summary_path}")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Enroll an agent on Spacebase1 commons.")
    parser.add_argument("--agent-name", required=True, help="Short identifier for this agent (used in transcripts).")
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace directory for keys and enrollment state. Default: .intent-space-<agent-name>",
    )
    parser.add_argument("--signup-wait", type=float, default=20.0, help="Seconds to wait for steward PROMISE.")
    parser.add_argument("--complete-wait", type=float, default=30.0, help="Seconds to wait for steward COMPLETE.")
    args = parser.parse_args()

    workspace = Path(args.workspace or f".intent-space-{args.agent_name}")
    try:
        enroll(args.agent_name, workspace.resolve(), args.signup_wait, args.complete_wait)
    except Exception as exc:
        print(f"\n[enroll] FAILED: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
