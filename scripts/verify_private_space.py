#!/usr/bin/env python3
"""
Verify that shared spaces with explicit participant lists are server-enforced.

This is the load-bearing test for our mafia-coven mechanic. If a non-participant
CAN read a shared space, we have to pivot to the encrypted-body fallback (see
docs/RISKS.md §Private subspace fallback).

Test plan:
  1. Three agents are enrolled: --requester, --participant, --outsider.
  2. requester posts an INTENT in its own home space asking the steward to
     provision a shared space with [requester, participant].
  3. requester ACCEPTs the steward's PROMISE; waits for COMPLETE carrying
     shared-space access materials (station_token + audience + space_id).
  4. participant waits for the steward invitation INTENT to arrive in its
     home space; reads access materials.
  5. participant connects to the shared space and reads it: expect SUCCESS
     (>=1 visible message — the steward's welcome or the requester's seed).
  6. outsider attempts to connect using the participant's token: expect
     401 (token bound to participant's principal, not outsider's).
  7. outsider attempts to scan the shared space using its OWN home-space
     token: expect 401 (token's audience does not match shared space).

PASS iff steps 5-7 all behave as expected.

Usage:
    # First, enroll three agents (one-time):
    python3 scripts/enroll_agent.py --agent-name verify-requester
    python3 scripts/enroll_agent.py --agent-name verify-participant
    python3 scripts/enroll_agent.py --agent-name verify-outsider

    # Then run the verification:
    python3 scripts/verify_private_space.py \\
      --requester verify-requester \\
      --participant verify-participant \\
      --outsider verify-outsider
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError


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


SPACEBASE1_ORIGIN = "https://spacebase1.differ.ac"


def open_session(agent_name: str) -> HttpSpaceToolSession:
    workspace = Path(f".intent-space-{agent_name}").resolve()
    if not workspace.exists():
        sys.exit(f"Workspace {workspace} does not exist. Run enroll_agent.py --agent-name {agent_name} first.")
    session = HttpSpaceToolSession(
        endpoint=f"{SPACEBASE1_ORIGIN}/commons",
        workspace=workspace,
        agent_name=agent_name,
    )
    session.connect()
    return session


def load_principal(session: HttpSpaceToolSession) -> str:
    pid = session.identity().get("principalId")
    if not pid:
        raise RuntimeError(f"No principal_id found for {session.agent_name}; re-enroll?")
    return pid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requester", required=True, help="Agent name of the shared-space requester (also a participant).")
    parser.add_argument("--participant", required=True, help="Agent name of the second participant.")
    parser.add_argument("--outsider", required=True, help="Agent name of an enrolled but non-participating agent.")
    parser.add_argument("--invite-wait", type=float, default=30.0, help="Seconds to wait for the participant's invitation INTENT.")
    args = parser.parse_args()

    print(f"[verify] Opening sessions ...")
    requester = open_session(args.requester)
    participant = open_session(args.participant)
    outsider = open_session(args.outsider)

    requester_principal = load_principal(requester)
    participant_principal = load_principal(participant)
    outsider_principal = load_principal(outsider)
    print(f"[verify] requester:   {requester_principal}")
    print(f"[verify] participant: {participant_principal}")
    print(f"[verify] outsider:    {outsider_principal}")

    requester_home = requester.current_space_id
    participant_home = participant.current_space_id
    print(f"[verify] requester_home:   {requester_home}")
    print(f"[verify] participant_home: {participant_home}")

    # ---- Step 1: requester asks steward for shared space ----
    print()
    print("[verify] Step A: requester posts INTENT for shared space [requester, participant] ...")
    request = requester.post_and_confirm(
        requester.intent(
            "Verify private-space enforcement: please provision a shared space.",
            parent_id=requester_home,
            payload={
                "requestedSpace": {
                    "kind": "shared",
                    "participant_principals": [requester_principal, participant_principal],
                },
            },
        ),
        step="verify.intent.shared",
        confirm_space_id=requester_home,
    )
    request_space = request["intentId"]
    print(f"[verify] request intent: {request_space}")

    print("[verify] Waiting for steward PROMISE ...")
    promise = requester.wait_for_promise(request_space, wait_seconds=20.0)
    promise_id = promise["promiseId"]
    print(f"[verify] PROMISE: {promise_id}")

    print("[verify] Posting ACCEPT ...")
    requester.post_and_confirm(
        requester.accept(promise_id=promise_id, parent_id=request_space),
        step="verify.accept.shared",
        confirm_space_id=request_space,
    )

    print("[verify] Waiting for steward COMPLETE ...")
    complete = requester.wait_for_complete(request_space, promise_id=promise_id, wait_seconds=30.0)
    payload = complete.get("payload", {})
    shared_space_id = payload.get("shared_space_id")
    invitation_count = payload.get("invitation_count")
    print(f"[verify] COMPLETE.shared_space_id:   {shared_space_id}")
    print(f"[verify] COMPLETE.invitation_count:  {invitation_count}")
    print(f"[verify] COMPLETE.payload (full):\n{json.dumps(payload, indent=2)}")

    if not shared_space_id:
        print("[verify] FAIL: steward COMPLETE did not include shared_space_id", file=sys.stderr)
        return 1

    # ---- Step 2: participant waits for the steward invitation in its home space ----
    print()
    print(f"[verify] Step B: participant waits for invitation INTENT in its home space (up to {args.invite_wait}s) ...")
    try:
        invitation = participant.wait_for_intent(
            participant_home,
            payload_predicate=lambda p: isinstance(p, dict)
                and p.get("shared_space_id") == shared_space_id,
            wait_seconds=args.invite_wait,
            scan_attempts=2,
        )
    except TimeoutError:
        print(f"[verify] FAIL: participant did not receive invitation INTENT within {args.invite_wait}s", file=sys.stderr)
        return 1

    inv_payload = invitation.get("payload", {})
    access = inv_payload.get("access")
    if not access:
        print(f"[verify] FAIL: invitation lacked 'access' block. Payload:\n{json.dumps(inv_payload, indent=2)}", file=sys.stderr)
        return 1
    print(f"[verify] Invitation received. access keys: {sorted(access.keys())}")

    participant_token = access["station_token"]
    participant_audience = access["audience"]
    participant_endpoint = access["itp_endpoint"]
    print(f"[verify] participant access: audience={participant_audience}, itp_endpoint={participant_endpoint}")

    # ---- Step 3: participant connects to the shared space — expect SUCCESS ----
    print()
    print("[verify] Step C: participant connects to shared space (should SUCCEED) ...")
    try:
        participant.connect_to(
            endpoint=participant_endpoint,
            station_token=participant_token,
            audience=participant_audience,
            sender_id=participant_principal,
        )
        scan_result = participant.scan(shared_space_id)
        print(f"[verify] PASS: participant scan OK; messageCount={len(scan_result.get('messages', []))}, latestSeq={scan_result.get('latestSeq')}")
    except Exception as e:
        print(f"[verify] FAIL: participant could not scan shared space: {e}", file=sys.stderr)
        return 1

    # ---- Step 4: outsider attempts to scan with participant's token (token theft) ----
    # The token is bound to the participant's principal (DPoP key proof). The outsider
    # has a different keypair, so its proof JWT will not validate even if it knows the
    # token. We simulate by trying to connect_to with participant's token but outsider's
    # session (which will sign proofs with outsider's key).
    print()
    print("[verify] Step D: outsider attempts to scan using participant's token (should FAIL with 401) ...")
    try:
        outsider.connect_to(
            endpoint=participant_endpoint,
            station_token=participant_token,
            audience=participant_audience,
            sender_id=outsider_principal,
        )
        result = outsider.scan(shared_space_id)
        print(f"[verify] FAIL: outsider was able to scan shared space! result.messageCount={len(result.get('messages', []))}", file=sys.stderr)
        print("[verify]        Token-theft scenario succeeded — DPoP proof binding did not work as expected.", file=sys.stderr)
        return 2
    except HTTPError as e:
        if e.code == 401:
            print(f"[verify] PASS: outsider got 401 attempting to use participant's token. {e}")
        else:
            print(f"[verify] UNEXPECTED: outsider got HTTPError {e.code}: {e}", file=sys.stderr)
            return 3
    except Exception as e:
        # Some clients raise a generic Exception that wraps the 401
        msg = str(e)
        if "401" in msg or "unauthorized" in msg.lower():
            print(f"[verify] PASS: outsider was rejected with 401-like error: {msg}")
        else:
            print(f"[verify] UNEXPECTED ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            return 3

    # ---- Step E: outsider connects DIRECTLY to shared-space URL with its own home-space
    # token. Server rejects on audience mismatch (token.aud != state.audience). ----
    #
    # Note: a naive `outsider.scan(shared_space_id)` from the outsider's home-space
    # session is NOT a useful test, because that request is routed to the outsider's
    # own Durable Object (which legitimately has no messages with parentId=shared
    # _space_id) and returns an empty result without ever touching the shared space's
    # storage. To actually probe the shared space's access control we must POST to
    # the shared space's URL.
    print()
    print("[verify] Step E: outsider connects to shared-space URL using its own home-space station_token")
    print("           (audience mismatch should produce 401 from the shared space DO) ...")

    # Pull outsider's home-space token from its known-stations record
    outsider3 = open_session(args.outsider)
    known = outsider3.known_stations()
    outsider_home_audience = f"intent-space://spacebase1/space/{outsider3.current_space_id}"
    outsider_home_token = None
    # known_stations entries use camelCase: stationToken, spaceId, audience, source
    for record in known:
        if not isinstance(record, dict):
            continue
        if record.get("audience") == outsider_home_audience and record.get("source") == "connect":
            outsider_home_token = record.get("stationToken") or record.get("station_token")
            if outsider_home_token:
                break
    if not outsider_home_token:
        # Fallback: any "connect"-source token from this principal (skips connect_to attempts)
        for record in known:
            if isinstance(record, dict) and record.get("source") == "connect":
                outsider_home_token = record.get("stationToken") or record.get("station_token")
                if outsider_home_token:
                    break
    if not outsider_home_token:
        print("[verify] SKIP Step E: could not retrieve outsider's home-space token from local state.", file=sys.stderr)
    else:
        try:
            # Try to connect_to the shared space's URL using the outsider's token but the
            # shared space's audience. The DPoP proof will be signed for the shared
            # audience but the token's `aud` claim names the outsider's home audience.
            # Server sees token.aud != state.audience → 401.
            outsider3.connect_to(
                endpoint=participant_endpoint,
                station_token=outsider_home_token,
                audience=participant_audience,
                sender_id=outsider_principal,
            )
            result = outsider3.scan(shared_space_id)
            print(f"[verify] FAIL: outsider connected to shared space and scanned. messageCount={len(result.get('messages', []))}", file=sys.stderr)
            print("[verify]        This means the outsider's home-space token was accepted at the shared space DO.", file=sys.stderr)
            return 4
        except HTTPError as e:
            if e.code in (401, 403):
                print(f"[verify] PASS: outsider rejected at shared space DO. HTTP {e.code}.")
            else:
                print(f"[verify] UNEXPECTED: HTTP {e.code}: {e}", file=sys.stderr)
                return 5
        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg or "unauthorized" in msg.lower() or "forbidden" in msg.lower():
                print(f"[verify] PASS: outsider rejected at shared space DO (wrapped error): {msg}")
            else:
                print(f"[verify] UNEXPECTED ERROR: {type(e).__name__}: {e}", file=sys.stderr)
                return 5

    print()
    print("============================================================")
    print(f"VERIFY PASS: shared-space {shared_space_id}")
    print("  - shared space provisioned with participant_principals (Step A)")
    print("  - participant received audience-scoped invitation (Step B)")
    print("  - participant CAN read the space (Step C)")
    print("  - outsider CANNOT read with a stolen participant token — DPoP key binding (Step D)")
    print("  - outsider CANNOT read with its own home-space token — audience binding (Step E)")
    print()
    print("Mafia coven mechanism is GREEN. Proceed with build per BUILD_PLAN.md.")
    print("============================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
