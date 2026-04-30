# Architecture

System overview, component map, and process model. Read [`PROTOCOL_MAPPING.md`](PROTOCOL_MAPPING.md) next for the verb-by-verb gameplay encoding.

## What we are building

A multi-process Python system where each game participant is a separately-enrolled spacebase1 principal. Bots make decisions via LLM calls. Humans make decisions through a local web UI. The game state lives entirely in the intent space — there is no central game server with a database.

## Core architectural principle

**The intent space is the only source of truth.** No process holds canonical game state. Every agent reconstructs game state by scanning the public game space, the relevant private subspaces it has access to, and applying deterministic interpretation rules from [`GAME_RULES.md`](GAME_RULES.md). Disagreements are visible (different cursors, different scan times) but the underlying log is consistent.

This is *strict* native-protocol style. We deliberately do not maintain a shadow database, even when it would be more convenient. The cost: some logic is repeated across agents. The benefit: the project's claims about being intent-space-native are literally true.

## Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Spacebase1 hosted intent space                   │
│  ┌───────────┐  ┌───────────────┐  ┌────────────────────────────┐  │
│  │  commons  │  │  home-space-i │  │ shared-game-space          │  │
│  │  (public  │  │  (per-player, │  │   public game thread       │  │
│  │  lobby +  │  │  for steward  │  │ ┌────────────────────────┐ │  │
│  │  signup)  │  │  invites)     │  │ │ shared-coven-space     │ │  │
│  │           │  │               │  │ │  (mafia only)          │ │  │
│  │           │  │               │  │ └────────────────────────┘ │  │
│  │           │  │               │  │ ┌────────────────────────┐ │  │
│  │           │  │               │  │ │ shared-detective-space │ │  │
│  │           │  │               │  │ │  (detective + host)    │ │  │
│  │           │  │               │  │ └────────────────────────┘ │  │
│  │           │  │               │  │ ┌────────────────────────┐ │  │
│  │           │  │               │  │ │ shared-doctor-space    │ │  │
│  │           │  │               │  │ │  (doctor + host)       │ │  │
│  │           │  │               │  │ └────────────────────────┘ │  │
│  └───────────┘  └───────────────┘  └────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
        ▲             ▲                       ▲
        │             │                       │
   sign │       bind  │    auth+post+scan     │ auth+post+scan
        │             │                       │
┌───────┴─────────────┴───────────────────────┴─────────────────────────┐
│  Local processes (one per principal, often on same dev machine)        │
│                                                                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │
│  │ host agent  │  │ bot agent 1 │  │ bot agent N │  │ human agent  │  │
│  │ - announces │  │ - villager  │  │ - mafia     │  │ - bridge     │  │
│  │   phases    │  │ - LLM loop  │  │ - LLM loop  │  │   stdin/HTTP │  │
│  │ - commit    │  │ - persona   │  │ - persona   │  │   to local UI│  │
│  │   reveal    │  │             │  │             │  │              │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘  │
│                                                          │             │
│                                                          ▼             │
│                                                   ┌──────────────┐    │
│                                                   │ Human UI     │    │
│                                                   │ (FastAPI +   │    │
│                                                   │  HTMX)       │    │
│                                                   │ localhost:N  │    │
│                                                   └──────────────┘    │
│                                                                        │
│  ┌──────────────────┐                                                  │
│  │ spectator agent  │  (optional — Town Crier challenge)               │
│  │ - scans game     │                                                  │
│  │ - posts summary  │                                                  │
│  └──────────────────┘                                                  │
└────────────────────────────────────────────────────────────────────────┘
```

### Agent inventory

| Agent | Count | Role | Distinguishing capability |
|---|---|---|---|
| Game Host | 1 | Service-intent provider; announces phase transitions; runs commit-reveal | Holds the role-substrate mapping privately during play; reveals at end |
| Villager bot | N (≥3) | Standard villager LLM agent | Lies / deduces from public log only |
| Mafia bot | N (≥2) | Mafia LLM agent | Plus access to coven shared subspace |
| Detective bot | 0 or 1 | Detective LLM agent | Plus access to detective notebook subspace |
| Doctor bot | 0 or 1 | Doctor LLM agent | Plus access to doctor notebook subspace |
| Human player | 0..7 | Human-controlled seat (any role) | Decisions via local UI; agent is just a bridge |
| Spectator | 0 or 1 | Read-only commentary agent | Scans + posts periodic INTENTs into commons |

**Total processes for canonical 7-seat demo:** 8 (host + 7 players) + 1 optional spectator + 1 human-UI process if any humans = 9-11.

## Process model

Each agent is a separate Python process, each with:

- its own `.intent-space-<role>/` workspace directory (keypair, enrollment state, transcripts)
- its own enrolled spacebase1 principal
- one open `HttpSpaceToolSession` to its bound game space (and optionally additional sessions to private subspaces it participates in)
- its own scan loop that polls the relevant spaces

**Why separate processes, not threads:**

- The SDK's `LocalState` writes keypairs and enrollment data to a workspace directory. Two threads sharing one workspace would corrupt state.
- Each process can be restarted independently. If a bot crashes, the game continues; the bot rejoins by reconnecting and scanning from its last cursor.
- Real-process isolation forces honest "no shared memory" — the only communication is through intent space, which is the protocol's whole point.

**Process orchestration for demo:**

- Use `tmux` or `Procfile`-style runner for live demos
- Or a single Python script that uses `subprocess.Popen` to launch each agent in its own subprocess with `--workspace .intent-space-<role>` flag

## Space topology

| Space | Kind | Audience | Participants | Read by |
|---|---|---|---|---|
| `commons` | hosted | `intent-space://spacebase1/space/commons` | All enrolled agents | Anyone (public read allowed) |
| `space-<player-i-home>` | home (per-player) | `intent-space://spacebase1/space/<id>` | Player-i only | Player-i only |
| `space-<game-id>` | shared | `intent-space://spacebase1/space/<game-id>` | All 7 players + host (+ spectator) | Listed participants only |
| `space-<coven-id>` | shared | `intent-space://spacebase1/space/<coven-id>` | Mafia players + host | Mafia + host only |
| `space-<detective-id>` | shared | `intent-space://spacebase1/space/<detective-id>` | Detective + host | Detective + host |
| `space-<doctor-id>` | shared | `intent-space://spacebase1/space/<doctor-id>` | Doctor + host | Doctor + host |

Audience scheme is per-space: `intent-space://spacebase1/space/<spaceId>`. Token issued at signup is bound to that audience. Cross-audience access yields 401. Source: [`big-d/spacebase1/src/index.ts`](../../big-d/spacebase1/src/index.ts) lines 152, 1240-1280.

## Data shape

### What lives in payloads

The protocol body is opaque bytes; we use JSON consistently. Standard payload fields used across this project:

```json
{
  "content": "human-readable text",
  "kind": "machine-readable subtype, e.g. 'vote' or 'accusation'",
  "data": { /* structured fields, kind-specific */ },
  "game_id": "game-...",
  "phase": "day-1-discussion",
  "timestamp_iso": "2026-04-30T18:00:00Z"
}
```

Kind is the dispatcher. Bot scan loops route messages by `payload.kind`.

### Kind taxonomy

| `kind` | Verb | Where | Schema |
|---|---|---|---|
| `lobby-open` | INTENT | commons | `{seats: 7, opens_at}` — host advertises |
| `lobby-join` | PROMISE on lobby | commons | `{principal_id, agent_label}` — player joins |
| `game-init` | INTENT | game space | `{role_commitments: [hash...], substrate_commitments: [hash...]}` |
| `phase-start` | INTENT | game space | `{phase: "day-1-discussion", deadline_ms}` |
| `phase-end` | COMPLETE on phase-start | game space | `{tally?, lynched?, killed?}` |
| `accusation` | INTENT | day-discussion | `{target: principal_id, claim: "..."}` |
| `defense` | INTENT | day-discussion | `{claim: "..."}` |
| `vote` | INTENT | day-vote | `{target: principal_id}` |
| `id-vote` | INTENT | day-vote | `{target: principal_id, claimed_substrate: "human"|"agent"}` |
| `coven-target` | INTENT | coven space | `{target: principal_id}` |
| `coven-confirm` | PROMISE on coven-target | coven space | `{}` |
| `investigate` | INTENT | detective notebook | `{target: principal_id}` |
| `investigate-result` | COMPLETE on investigate | detective notebook | `{target, role, substrate}` |
| `protect` | INTENT | doctor notebook | `{target: principal_id}` |
| `death` | COMPLETE on phase-start | game space | `{target, cause: "lynch"|"mafia"|"saved"}` |
| `game-end` | INTENT | game space | `{winner: "villagers"|"mafia"}` |
| `reveal` | COMPLETE on game-init | game space | `{role_assignments: {...}, substrate_assignments: {...}, salts: [...]}` |
| `substrate-guess` | ASSESS on player presence | game space | `{target: principal_id, guess: "human"|"agent", confidence, reasoning}` |
| `summary` | INTENT | commons | `{game_id, summary_text, winners, mvp}` (spectator) |

### What lives outside payloads

- LLM API keys → env var, never posted
- Local cursors → SDK's `.intent-space/state/cursors.json`
- Bot reasoning chain-of-thought → debug log file, not posted (only the final emitted INTENT body is public)
- Human UI session state → in-memory in the human UI process

## Phase timing model

Each phase is a single INTENT in the game space, with `deadline_ms` in payload. Phase advance trigger:

- Either `now_ms() >= deadline_ms` AND host has scanned at least once after deadline → host posts `phase-end`
- OR all relevant living players have posted their phase action (e.g. all voted) → host posts `phase-end` early

Bots compute current phase by scanning the game space and finding the latest unmatched `phase-start` (i.e. one with no corresponding `phase-end`). This is *protocol-derived state* — no bot maintains a "phase" variable independently.

| Phase | Default duration | Earlier trigger |
|---|---|---|
| Day-N Discussion | 60s | Host can shorten if quiet |
| Day-N Vote | 30s | All living players voted |
| Day-N Tally | instant | Host posts `phase-end` with tally |
| Night-N Mafia | 30s | Mafia consensus reached in coven |
| Night-N Detective | 20s | Detective acted |
| Night-N Doctor | 20s | Doctor acted |
| Night-N Wake | instant | Host posts deaths |

Adjust per-phase by `min(60s, 15s × living_humans)` when human players are seated.

## Failure modes and recovery

| Failure | Behavior | Recovery |
|---|---|---|
| Bot crashes mid-game | Game continues; bot's seat goes silent | Restart process; bot reconnects, scans cursor, rejoins |
| Network blip on commons | `signup`/`scan` fail with HTTP error | SDK doesn't auto-retry; wrapper retries with backoff |
| Steward provisioning slow | `wait_for_promise` raises TimeoutError | Increase `wait_seconds`; or fall back to manual claim flow |
| Host crashes | Phase advancement stops | Restart host; it scans the game space, finds the latest unmatched phase-start, resumes |
| Player refuses to act in time | Phase ends without their action | Treated as no-vote / no-action |
| Two players post conflicting `phase-end` | Both visible; later one is ignored by other agents | First valid `phase-end` (by sequence) wins per scan order |

The append-only log makes recovery trivial: every agent rebuilds its state from `scan_full(game_space)` on restart.

## Out-of-scope

Explicitly NOT building:

- Persistent backend database
- Web frontend for spectators (we use the agent pack's existing dashboard)
- Mobile UI
- Real authentication beyond what spacebase1 provides
- Cross-game persistent identity tied to real-world users
- Payment / monetization
- Production-scale concerns (rate limiting, abuse, etc.)
