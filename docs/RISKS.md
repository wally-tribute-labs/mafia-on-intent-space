# Risks and Open Assumptions

What could break and what we do if it does. Updated as we learn more.

## Validated assumptions (green)

These have been confirmed by reading the source. We can rely on them.

| # | Assumption | How verified |
|---|---|---|
| V1 | Per-space audience tokens; cross-space scan returns 401 | Read [`big-d/spacebase1/src/index.ts`](../../big-d/spacebase1/src/index.ts) lines 152, 1240-1280 |
| V2 | Public read scoped to commons only (others 403) | Source line 1582 |
| V3 | Shared spaces require `participant_principals: string[]`; partial sets refused | [`big-d/spacebase1/src/shared-spaces.ts`](../../big-d/spacebase1/src/shared-spaces.ts) lines 290-338 |
| V4 | Each named participant must have a bound home space first | Same source |
| V5 | Steward provisioning lifecycle: PROMISE → ACCEPT → COMPLETE | [agent-setup doc](https://spacebase1.differ.ac/agent-setup) + skill source |
| V6 | Wire verbs: INTENT, PROMISE, DECLINE, ACCEPT, COMPLETE, ASSESS, SCAN, SCAN_RESULT, AUTH, AUTH_RESULT, ERROR | [`itp-verb-header-body-profile.md`](../../big-d/intent-space/docs/itp-verb-header-body-profile.md) §2 |
| V7 | Bodies are opaque bytes; payloads can be any structure | Same source §1.4 |
| V8 | SDK is pure stdlib + openssl shell-out | Read [`intent_space_sdk.py`](../../ccmarket/plugins/intent-space-agent-pack/sdk/intent_space_sdk.py) imports |
| V9 | SDK exposes `intent`, `promise`, `accept`, `complete`, `assess`, `decline` builders | [`_space_tools_common.py`](../../ccmarket/plugins/intent-space-agent-pack/sdk/_space_tools_common.py) lines 547-577 |
| V10 | `wait_for_promise/complete/intent/decline` exist with `wait_seconds` parameter | Same source lines 578-588 |
| V11 | HTTP transport supports SSE via `stream_space` | Same source line 421 |
| V12 | Observatory URL auto-included in signup response | Onboarding skill source |

## Validated by live runs (green — Phase 0 results, 2026-04-30)

| # | Assumption | Result | Evidence |
|---|---|---|---|
| U1 | Spacebase1 commons is reachable | **PASS** | `artifacts/enroll-host.log` — full signup in 6.5s |
| U2 | Steward responds within 15s typical | **PASS** | All four steward COMPLETEs received in <8s end-to-end |
| U3 | Shared-space provisioning works | **PASS** (for 2-participant; 7-participant pending Phase 2.4) | `artifacts/verify-private-space-v3.log` |
| U4 | Non-participant gets 401 (or equivalent) on shared space | **PASS** | Step E of `verify_private_space.py`: outsider with own home-space token + shared-space audience → HTTP 401 |
| U5 | Multi-session per process works (`connect_to`) | **PASS** | Verifier opens 3 sessions in one process; each binds different audiences |
| U6 | Workspace isolation is real | **PASS by inspection** | Each `.intent-space-<name>/` has its own keypair, `enrollment-summary.json`, known-stations |
| U7 | `post_and_confirm` returns the persisted message | **PASS** | `artifacts/smoke-host.log` — confirmed message visible in subsequent `scan_full` |
| U8 | Shared-space invitations arrive as INTENTs in participant home space | **PASS** | Step B of verifier: `wait_for_intent(participant_home, payload_predicate=...)` matched within 30s |
| U9 | LLM rate limits OK | Pending Phase 5.2 | Anthropic Haiku 1000 RPM — well above expected ~30/min |

**Additional finding from Phase 0:**

- `commons_space_id` was returned as `None` from initial commons signup (not as a string `"commons"`). The SDK handled this gracefully via the fallback chain in `connect()`. Not a bug, just useful to know — first INTENT to commons must use `parent_id="commons"` literal, not `commons_space_id`.
- `known_stations` records use camelCase (`stationToken`, `spaceId`), not snake_case. SDK accessor methods (`identity()`, `connect_to()`) abstract this; only direct file inspection sees the underlying form.
- The SDK records `connect_to` attempts in `known_stations` even when the resulting auth fails. Filter by `source == "connect"` (not `"connect_to"`) to find tokens that actually authenticated successfully.

## Unvalidated assumptions (yellow — verify during build)

| # | Assumption | When verified |
|---|---|---|
| U10 | Shared-space provisioning with 7-8 participants works (not just 2) | Phase 2.4 first run with full lobby |
| U11 | Request 4 shared spaces in quick succession (game + coven + detective + doctor) doesn't rate-limit | Phase 2.4 |
| U12 | Multi-process subprocess.Popen lifecycle is stable across 8+ concurrent agents | Phase 5.1 |
| U13 | Stream endpoint (`stream_space`) actually delivers SSE events promptly | Optional; tested only if used (currently we poll) |

## Live risks (orange — known unknowns, plan a fallback)

| # | Risk | Probability | Impact | Fallback |
|---|---|---|---|---|
| R1 | Spacebase1 commons goes down during hackathon | Medium | High | Self-host HTTP reference station from `big-d/http-reference-station/` |
| R2 | Steward provisioning intermittent | Medium | Medium | Wrapper retries `wait_for_promise` with exponential backoff up to 60s |
| R3 | Anthropic API outage | Low | High | Switch to OpenAI gpt-4o-mini via env var; bot prompts already JSON-only |
| R4 | Shared-space invitation INTENT delivery delayed | Medium | Medium | Bots poll home space with `wait_for_intent(timeout=60s)` before joining game |
| R5 | Rate limit on commons post throughput | Unknown | Medium | Add per-process rate limiting (1 post/sec) to scan loops |
| R6 | Token expiry during long game | Low | Medium | Use the `continue` endpoint to re-issue; SDK supports this |
| R7 | Observatory URL doesn't render correctly | Low | Demo-only | Use the local `dashboard/launch.py` as backup |
| R8 | Pre-game lobby sits empty (no bot processes started) | High if misconfigured | Demo-failing | Pre-flight check in `run_bot_game.py` confirming all 7 bots are alive before posting lobby-open |

## Hard fallbacks (red — what to do if a critical assumption breaks)

### Private subspace fallback (if V1-V4 don't work as expected)

If the smoke test (Phase 0.5) shows that:

- Non-participants CAN read a shared space, OR
- Shared spaces don't exist as expected, OR
- Server doesn't actually enforce participant lists

Then we pivot to **encrypted-body coven**:

- All players are participants of the same single game space
- Mafia-only messages are posted with payload bodies encrypted via shared symmetric key (AES-GCM)
- Symmetric key is generated by host at game start, distributed to mafia via per-player private invitations (using each mafia's home space — which IS server-enforced as private)
- Public log shows opaque ciphertext for coven posts; only mafia decrypt
- Same effect, more crypto work, slightly less native (uses opaque-body doctrine creatively)

**Time cost of pivot:** +6 hours (key gen, distribution, encrypt/decrypt wrappers).

### Steward unreliable fallback

If steward provisioning fails > 50% of the time:

- Use `tcp-reference-station` locally instead of spacebase1
- Lose hosted commons but gain reliability
- Submit with note: "Run locally; commons hosted version available as a recording"

### LLM unavailable fallback

If both Anthropic and OpenAI are down:

- Pre-record a full bot-only game with cached LLM responses
- Demo from recording
- Submit with note about offline mode
- Honest in submission: this is a fallback, not the design

## Issues we've already decided to accept

These are real risks we've deliberately not mitigated:

| # | Accepted issue | Why we're OK with it |
|---|---|---|
| A1 | Host is partially orchestrator-shaped (announces phases) | Framed as service-intent provider; observe-before-act doctrine. Not pure but pragmatic. Documented in [`DECISIONS.md`](DECISIONS.md) §D1 |
| A2 | Bot LLM prompts will produce some flat play | Iteration in Phase 5.3; not perfectible in time |
| A3 | Human UI has no auth (single-tenant per workspace) | Demo only; not a product |
| A4 | Cross-game reputation is observable but not used by bots during play | Stretch in Phase 8.3 if time permits |
| A5 | Identification Vote could feel gimmicky if used poorly | Rules clear about cost (wrong guess locks accuser); bots have low default propensity |

## Open design questions to resolve before Phase 5

| # | Question | Decision needed by | Default if unresolved |
|---|---|---|---|
| Q1 | Should host be a player or strict-non-player? | Phase 2.1 | Strict non-player (cleaner, mostly what we describe) |
| Q2 | Phase length scaling formula | Phase 5.3 | `min(60s, 15s × living_humans)` |
| Q3 | Number of personas per game (random subset of pool) | Phase 4.4 | All bots get random distinct persona; reuse if pool < N |
| Q4 | Robot Mode default | Phase 6.4 | Off, per-message toggle |
| Q5 | Game-end ASSESS: required or optional from each player | Phase 7.4 | Required (block exit until all posted, with timeout fallback) |

## How to triage a new risk during build

1. Is it blocking my current task? → fix or defer the task
2. Does it threaten the gate of the current phase? → escalate; consider re-scoping
3. Does it threaten a downstream phase? → add it here under live risks; continue current task
4. Is it operational only (won't affect submission quality)? → note here under "accepted"

## Update log

- 2026-04-30 (initial): RISKS doc created. V1-V12 validated by reading sources. U1-U9 pending Phase 0 runs.
- 2026-04-30 (after Phase 0): U1-U9 validated by live runs. Mafia coven mechanism GREEN. New unknowns U10-U13 identified. See `artifacts/enroll-host.log`, `artifacts/smoke-host.log`, `artifacts/verify-private-space-v3.log`.
- 2026-04-30 (Track A3 — host modules): SDK quirks discovered while wiring `src/host/{lobby,start,phase_loop,main}.py`:
  - Schema constraint conflict with role-card delivery: `schemas.py` is locked for A3, but the host needs to deliver `(role, salt, substrate, substrate_salt)` to each player privately. No existing kind has those fields. Workaround: posted as `kind="shared-space-invitation"` with the role-card tucked under `BasePayload.data`. If `schemas.py` ever gets a dedicated `role-card` kind, swap to it.
  - Phase-end COMPLETE pattern: protocol's COMPLETE expects a `promise_id`. We don't issue an explicit "phase" PROMISE — the phase-start INTENT itself is the anchor. Workaround in `_post_phase_end`: pass the phase-start `intentId` as `promise_id` since the SDK doesn't validate that linkage server-side; the semantically-meaningful linkage is `payload.ends_phase_intent_id` per the A1 reconciliation. If a future SDK rev rejects this, switch to issuing a self-PROMISE on each phase-start.
  - Per-player private notebook = N+1 steward calls (game space + 1 per player + 1 per faction). For 7 seats this is 11 provisioning round-trips. Latency-sensitive; relevant to U11. Falls back to skipping role-card delivery via `skip_role_card_delivery=True` in `start_game` for tests / smoke runs.
  - `connect_to` switches `current_space_id`; the host has to reconnect every time it crosses spaces (game ↔ coven ↔ detective ↔ doctor ↔ commons). `phase_loop._connect_to_space` currently uses a test-only `set_current_space` shim — real Cloud-Run wiring needs the access materials cached at provision time and a real `connect_to(...)` call. Tracking as a TODO before live integration runs.
- 2026-04-30 (post-A3 integration patch): closed three of the four A3-flagged gaps so I1 can run live.
  - Added `kind=role-card` + `RoleCardPayload` to `src/game/schemas.py`. `start.py` now uses the dedicated kind instead of the `shared-space-invitation` + `data` workaround. Resolves the first A3 quirk above.
  - Added `kind=coven-membership` + `CovenMembershipPayload`. `start.py` posts one in the coven space immediately after provisioning, so MafiaBots get a canonical teammate list (and a known-host PID to exclude from sender-inference) without inference. Documented in `docs/PROTOCOL_MAPPING.md` §Private factions.
  - `GameState.subspace_access: dict[str, dict[str, str]]` now caches `(endpoint, station_token, audience)` per provisioned space (game + coven + detective + doctor + per-player notebooks). The host's home + commons access materials are looked up from `session.known_stations()` at start. `phase_loop._connect_to_space(session, space_id, state)` calls real `session.connect_to(...)` when materials are present, falling back to the `set_current_space` test shim only when they aren't. Resolves the fourth A3 quirk.
  - Phase-end COMPLETE quirk (#2 in A3 notes) is unchanged — still using phase-start intentId as promise_id. Live-only revisit.
  - Outstanding live concern: commons reconnection. We capture commons access via `endpoint_suffix="/commons"` lookup at start. If the SDK persists the commons station with a different identifier across signup/connect cycles, `subspace_access["commons"]` will be missing and `_connect_to_space` will fall back to the no-op path. The summary post may then fail (audience mismatch on the active station). Will surface during I1 live run; mitigation is to additionally try `connect_to` to `host_home_space_id` (the lobby phase already proves commons is reachable from there).
