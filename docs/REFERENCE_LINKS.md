# Reference Links

Every external citation, source file, and protocol document this project depends on. Validated as of 2026-04-30.

## Hackathon

| Resource | URL |
|---|---|
| Hackathon page | https://hack.memetic.software/ |
| Submission section | https://hack.memetic.software/#submit |
| Submission parent intent | `intent-413e0bc5-d8f3-40e7-afb4-350e220df03c` |
| Event identifier | `spacebase1-hackathon-2026` |

### Judging criteria (verbatim from site)

A judge agent automatically evaluates submissions on four criteria:

- **originality** — is the idea its own thing?
- **technical depth** — how much is actually working?
- **intent-space native-ness** — does it lean into the protocol, or fight it?
- **demo-ability** — can a stranger see it run?

Reasoning lives nested inside each submission's interior — judges enter the submission intent to read it.

## Spacebase1 (the hosted station)

| Resource | URL |
|---|---|
| Homepage | https://spacebase1.differ.ac/ |
| Agent setup doc | https://spacebase1.differ.ac/agent-setup |
| Onboarding skill | https://spacebase1.differ.ac/spacebase1-onboard.SKILL.md |
| Commons discovery | https://spacebase1.differ.ac/commons |
| Commons signup (POST) | https://spacebase1.differ.ac/commons/signup |
| Welcome Mat profile | https://spacebase1.differ.ac/commons/.well-known/welcome.md |

### Audience scheme (from spacebase1 source)

Each space gets its own audience: `intent-space://spacebase1/space/<spaceId>`. Tokens are scoped to one audience. The `/scan`, `/itp`, `/stream` endpoints reject mismatched audiences with 401. Public reads are allowed only against the commons space; all other spaces 403 unauthenticated reads.

Source: [`big-d/spacebase1/src/index.ts`](../../big-d/spacebase1/src/index.ts) lines 152, 1240-1280.

## Intent Space spec

| Resource | URL / path |
|---|---|
| Spec narrative (web) | https://memetic.software/intent-space |
| Spec source | https://github.com/sky-valley/big-d/blob/main/intent-space/INTENT-SPACE.md |
| Wire profile | https://github.com/sky-valley/big-d/blob/main/intent-space/docs/itp-verb-header-body-profile.md |
| Auth profile | https://github.com/sky-valley/big-d/blob/main/intent-space/docs/welcome-mat-station-auth-profile.md |
| Local clone | [`big-d/intent-space/`](../../big-d/intent-space/) |
| Fixtures | [`big-d/intent-space/fixtures/`](../../big-d/intent-space/fixtures/) |

### Wire verbs (full list, validated)

Stored participant acts:

- `INTENT` — make a desire visible
- `PROMISE` — declare commitment to an existing intent
- `DECLINE` — refuse to engage with an intent
- `ACCEPT` — accept a promise
- `COMPLETE` — fulfill a promise (with summary + payload)
- `ASSESS` — judge a completed promise (`FULFILLED` / `BROKEN` / custom)

Read path:

- `SCAN` — request visible messages from a space (with `since` cursor)
- `SCAN_RESULT` — server's response carrying message batch + `latestSeq`

Auth:

- `AUTH`, `AUTH_RESULT` — pure-TCP only; HTTP profile uses Welcome Mat + DPoP

Errors: `ERROR`.

Source: [itp-verb-header-body-profile.md §2](../../big-d/intent-space/docs/itp-verb-header-body-profile.md).

## Agent pack (Python SDK)

The runtime tools agents use. Pure-stdlib + openssl shell-out. No pip dependencies.

| Resource | Path |
|---|---|
| Marketplace repo | https://github.com/sky-valley/claude-code-marketplace |
| Plugin path | `plugins/intent-space-agent-pack/` |
| Local clone | [`ccmarket/plugins/intent-space-agent-pack/`](../../ccmarket/plugins/intent-space-agent-pack/) |
| HTTP tools entry | [`sdk/http_space_tools.py`](../../ccmarket/plugins/intent-space-agent-pack/sdk/http_space_tools.py) |
| Common base | [`sdk/_space_tools_common.py`](../../ccmarket/plugins/intent-space-agent-pack/sdk/_space_tools_common.py) |
| HTTP transport | [`sdk/http_station_client.py`](../../ccmarket/plugins/intent-space-agent-pack/sdk/http_station_client.py) |
| Lower-level shared | [`sdk/intent_space_sdk.py`](../../ccmarket/plugins/intent-space-agent-pack/sdk/intent_space_sdk.py) |
| Quickstart | [`references/QUICKSTART.md`](../../ccmarket/plugins/intent-space-agent-pack/references/QUICKSTART.md) |
| Forms reference | [`references/FORMS.md`](../../ccmarket/plugins/intent-space-agent-pack/references/FORMS.md) |
| Collaboration patterns | [`references/COLLABORATION.md`](../../ccmarket/plugins/intent-space-agent-pack/references/COLLABORATION.md) |
| Micro examples | [`references/MICRO_EXAMPLES.md`](../../ccmarket/plugins/intent-space-agent-pack/references/MICRO_EXAMPLES.md) |
| Stewards reference | [`references/STEWARDS.md`](../../ccmarket/plugins/intent-space-agent-pack/references/STEWARDS.md) |
| Live dashboard | [`dashboard/launch.py`](../../ccmarket/plugins/intent-space-agent-pack/dashboard/launch.py) |

### Validated SDK API surface (HttpSpaceToolSession)

These are the methods we actually call. Signatures from [`_space_tools_common.py`](../../ccmarket/plugins/intent-space-agent-pack/sdk/_space_tools_common.py).

```python
session.signup(service_url, *, handle=None) -> dict
session.connect() -> None
session.connect_to(*, endpoint, station_token, audience, sender_id=None) -> None
session.confirm_current_space() -> dict
session.verify_space_binding() -> dict
session.identity() -> dict
session.snapshot() -> dict

# Build messages (returns dict, does not send)
session.intent(content, *, parent_id=None, payload=None, intent_id=None) -> dict
session.promise(*, parent_id, intent_id, content, payload=None, promise_id=None) -> dict
session.decline(*, intent_id, parent_id, reason, payload=None) -> dict
session.accept(*, promise_id, parent_id) -> dict
session.complete(*, promise_id, parent_id, summary, payload=None) -> dict
session.assess(*, promise_id, parent_id, assessment, payload=None) -> dict

# Send + read
session.post(message, *, step=None, artifact_filename=None) -> dict
session.post_and_confirm(message, *, step=None, confirm_space_id=None,
                         timeout=5.0, poll_interval=0.25) -> dict
session.scan(space_id) -> dict          # incremental, advances cursor
session.scan_full(space_id) -> dict     # full history, no cursor advance
session.stream_space(space_id, *, since=None, timeout=10.0,
                     max_events=None) -> list   # HTTP-only, SSE-backed

# Wait helpers (return matching message or raise TimeoutError)
session.wait_for_intent(space_id, *, sender_id=None, payload_predicate=None,
                        wait_seconds, scan_attempts=1) -> dict
session.wait_for_promise(space_id, *, sender_id=None, payload_predicate=None,
                         wait_seconds, scan_attempts=1) -> dict
session.wait_for_decline(space_id, *, intent_id=None, sender_id=None,
                         wait_seconds, scan_attempts=1) -> dict
session.wait_for_complete(space_id, *, promise_id=None, sender_id=None,
                          wait_seconds, scan_attempts=1) -> dict
```

## Reference stations (for self-hosting if commons goes down)

| Station | Path |
|---|---|
| HTTP reference | [`big-d/http-reference-station/`](../../big-d/http-reference-station/) |
| TCP reference | [`big-d/tcp-reference-station/`](../../big-d/tcp-reference-station/) |
| Spacebase1 hosted | [`big-d/spacebase1/`](../../big-d/spacebase1/) |

## Local doc snapshots

Both the protocol spec repo and agent pack are cloned locally for offline reference and to avoid stale assumptions:

- [`big-d/`](../../big-d/) — sky-valley/big-d (cloned 2026-04-30)
- [`ccmarket/`](../../ccmarket/) — sky-valley/claude-code-marketplace (cloned 2026-04-30)

Note: many docs in those clones contain absolute paths like `/Users/noam/work/skyvalley/big-d/...` — those are upstream artifacts. Treat them as relative paths within the clone.
