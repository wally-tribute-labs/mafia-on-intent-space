# Parallelization Plan

How to run multiple builders (humans, AI agents, or both) at the same time without stepping on each other.

Companion to [`BUILD_PLAN.md`](BUILD_PLAN.md). Use [`AGENT_PROMPTS.md`](AGENT_PROMPTS.md) for the actual paste-ready prompts to launch each track.

## Dependency graph

```
                 ┌─────────────────────┐
                 │  Phase 0 — DONE     │
                 │  (validation,       │
                 │   enrollment,       │
                 │   private-space)    │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ 1.1 schemas         │   ← FIRST. blocks everything below.
                 │     (Pydantic       │     ~45 min, must be done by ONE
                 │      models for     │     person to avoid merge conflicts.
                 │      all kinds)     │
                 └──────────┬──────────┘
                            │
       ┌────────────────────┼─────────────────────┐
       │                    │                     │
       ▼                    ▼                     ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│ TRACK A      │    │ TRACK B      │    │ TRACK C          │
│ Game engine  │    │ Bot loop +   │    │ Human UI         │
│ (1.2-1.9)    │    │ LLM client   │    │ scaffold         │
│              │    │ (3.1-3.5)    │    │ (6.1-6.2)        │
│ - phase      │    │ - main loop  │    │ - FastAPI app    │
│ - tally      │    │ - LLM wrap   │    │ - HTMX page      │
│ - winner     │    │ - prompts    │    │ - feed partial   │
│ - commits    │    │ - context    │    │ - action stub    │
│ - resolve    │    │ - emit       │    │                  │
│ - stats      │    │              │    │                  │
└──────┬───────┘    └──────┬───────┘    └──────┬───────────┘
       │                   │                   │
       └─────────┬─────────┘                   │
                 ▼                             │
       ┌──────────────────┐                    │
       │ TRACK A → host   │                    │
       │ (Phase 2)        │                    │
       │ - lobby          │                    │
       │ - start          │                    │
       │ - phase loop     │                    │
       │ - game-end       │                    │
       └──────┬───────────┘                    │
              │                                │
              └──────────┬─────────────────────┘
                         ▼
              ┌──────────────────────────┐
              │ Specialized bots         │
              │ (Phase 4) — needs schema │
              │ + bot base + host so we  │
              │ can run a real game      │
              └──────┬───────────────────┘
                     ▼
              ┌──────────────────────┐
              │ Phase 5 — first      │
              │ end-to-end bot game  │
              │ (host + 7 bots)      │
              │ JOINS with Track C   │
              └──────┬───────────────┘
                     ▼
              ┌──────────────────────┐
              │ Phases 7, 8, 9       │
              │ (substrate polish,   │
              │  spectator, demo)    │
              └──────────────────────┘
```

## Three concurrent tracks (max sustainable parallelism)

Tracks **A**, **B**, **C** can run in parallel once `schemas.py` exists. Each track is bounded by clear file/module ownership so concurrent edits don't conflict.

### Track A — Engine + Host

**Owner files (no one else touches):**
- `src/game/phase.py`, `src/game/tally.py`, `src/game/winner.py`
- `src/game/commit_reveal.py`, `src/game/night_resolve.py`, `src/game/stats.py`
- `src/host/main.py`, `src/host/lobby.py`, `src/host/start.py`, `src/host/phase_loop.py`
- `tests/test_game.py`, `tests/test_host.py`

**Tasks (in order):**
1. Phase determination (BUILD_PLAN 1.2)
2. Vote tally (1.3)
3. Win condition (1.4)
4. Commit-reveal helpers (1.5)
5. Night resolution (1.6)
6. Identification Vote resolution (1.7)
7. Stats (1.8)
8. Unit tests (1.9)
9. Host main loop scaffold (2.1)
10. Lobby formation (2.2)
11. Game start + private subspace provisioning (2.3-2.4)
12. Phase loop (2.5-2.6)

**Estimated:** 12 hours.

### Track B — Bot framework + Specialized bots

**Owner files:**
- `src/bots/base.py`, `src/bots/llm.py`, `src/bots/prompts.py`, `src/bots/context.py`
- `src/bots/villager.py`, `src/bots/mafia.py`, `src/bots/detective.py`, `src/bots/doctor.py`
- `src/bots/personas.json`
- `tests/test_bots.py`

**Tasks (in order):**
1. Bot main loop (BUILD_PLAN 3.1)
2. LLM client wrapper (3.2)
3. Prompt builder (3.3)
4. Context message construction (3.4)
5. Action emission (3.5)
6. End-to-end villager smoke (3.6)
7. Mafia bot (4.1) — depends on Track A having shared-space provisioning ready
8. Detective bot (4.2)
9. Doctor bot (4.3)
10. Personas (4.4)

**Estimated:** 7 hours.
**Soft dependency on Track A:** specialized bots (4.1-4.3) can be stubbed against a fake "coven space ID" until host's 2.4 is ready, then wired up.

### Track C — Human UI

**Owner files:**
- `src/human/bridge.py`, `src/human/llm.py`
- `src/human/templates/*`, `src/human/static/*`
- `tests/test_human.py`

**Tasks (in order):**
1. Bridge process scaffold + scan loop (BUILD_PLAN 6.1)
2. Page template + feed partials (6.2)
3. Action panels per phase (6.3)
4. Robot Mode rewrite endpoint (6.4)

**Estimated:** 4.25 hours.
**Soft dependency on Track A:** the page renders against ANY game space; can develop against a fake/mock space until a real game runs. Action panels reference Track A's `phase.py` to know what's allowed in each phase.

### Convergence: Phase 5 (end-to-end bot game)

After A's host + B's bots are both done, ONE person runs Phase 5.1-5.3 (game runner script + first full bot game + persona tuning). This is where the tracks meet and bugs surface.

After Phase 5, Track C joins for Phase 6.5 (human UI in real game).

## What CAN'T be parallelized

Things that require sequential single-person ownership:

- **`src/game/schemas.py`** (BUILD_PLAN 1.1) — must be written first by one person; touched by every other file. Estimated 45 min. Block on this completing before forking tracks.
- **`scripts/run_bot_game.py`** (BUILD_PLAN 5.1) — the integration script that wires host + bots together. One person needs to hold the full mental model of subprocess lifecycle.
- **Phase 9 (demo + submission)** — final polish needs single hand on the wheel.

## Module ownership table (no two builders touch same file)

| File | Owner |
|---|---|
| `src/game/schemas.py` | **Schema-writer** (Phase 1.1) — one person, writes first |
| `src/game/*.py` (everything else) | Track A |
| `src/host/*` | Track A |
| `src/bots/*` | Track B |
| `src/bots/personas.json` | Track B |
| `src/human/*` | Track C |
| `src/spectator/main.py` | Track A (after host ships) or anyone with slack |
| `scripts/enroll_agent.py` | DONE; don't touch |
| `scripts/smoke_test.py` | DONE; don't touch |
| `scripts/verify_private_space.py` | DONE; don't touch |
| `scripts/run_bot_game.py` | Phase 5 integrator (single owner) |
| `scripts/run_mixed_game.py` | Phase 6.5 integrator (single owner, after C+5 done) |
| `scripts/stats.py` | Track A (Phase 8.3) |
| `tests/test_game.py` | Track A |
| `tests/test_bots.py` | Track B |
| `tests/test_human.py` | Track C |
| `docs/*` | Anyone (mostly stable; small additions OK) |
| `Makefile` | Anyone (additive only) |
| `README.md` | Anyone (additive only) |

## Time math

**Sequential single-builder:** ~44 hours (per [`BUILD_PLAN.md`](BUILD_PLAN.md) totals).

**Two builders in parallel:**
- Builder A: Phase 0 (4h, done) + 1.1 schema (0.75h) + Track A (12h) + Phase 5 (3h) + 7 (1.5h) + 8 (3h) + 9 (3h) = ~27h
- Builder B: starts after 1.1 schema done; Track B (7h) + Track C (4h) + Phase 6.5 + 7.4 (1h) = ~12h, then helps with 5/9
- Wall clock: ~27 hours (limited by Track A)

**Three builders in parallel:**
- Builder A: as above, ~27h wall
- Builder B: Track B (7h) + extras (2-3h help on 5/7/8) = ~10h busy, idle time available
- Builder C: Track C (4h) + extras (3-4h help on 5/6.5/7) = ~8h busy, idle time available
- Wall clock: still ~27 hours, but Track A becomes the bottleneck. Speedup mostly comes from idle time being available for slack/polish.

**Three builders + AI subagents:**
- Each track can spawn subagents for individual tasks (e.g. a subagent writes `tally.py`, another writes `winner.py`, both review by builder)
- Realistic compression: ~16-20 hours wall clock with disciplined coordination

## Coordination overhead

Each pair of concurrent tracks adds a small coordination tax:

- **Daily sync (15 min):** what changed, what shipped, what's blocked
- **Schema changes are special:** if anyone needs to add a new `kind`, they post it in a `#schema-changes` chat (or the team's equivalent) BEFORE editing `schemas.py`. Schema-writer integrates.
- **No one merges to `main` without their tests passing locally** (`pytest tests/test_<owner>.py`)
- **Branching:** each track works on its own branch (`feat/track-a-engine`, `feat/track-b-bots`, `feat/track-c-ui`); merge to `main` after each phase boundary

## Branching scheme

```
main                       <- always green; integration target
├── feat/schemas           <- short-lived; merges first, unblocks tracks
├── feat/track-a-engine    <- Track A
├── feat/track-b-bots      <- Track B
└── feat/track-c-human-ui  <- Track C
```

After Phase 5 ships on main, the tracks naturally converge.

## When NOT to parallelize

- **Solo build:** sequential per BUILD_PLAN. Don't context-switch — finish a track, ship it, move on.
- **Time pressure (<24h to demo):** drop Track C (cut human UI), run all-bot demo. Saves ~5h, focuses energy.
- **One unfamiliar builder:** pair-program rather than parallel-track until the codebase is understood.

## Recommended starter configuration

Given you're in NYC at the hackathon and the demo window is short:

**If solo (you alone):** sequential per BUILD_PLAN, but use the prompts in [`AGENT_PROMPTS.md`](AGENT_PROMPTS.md) to spawn subagents for individual deliverables. The schema-first then track-A-then-track-B-then-track-C order works fine compressed.

**If 2 builders:** Builder A on Track A (engine+host), Builder B on Track B (bots) then Track C (UI). Sync at Phase 5.

**If 3 builders:** A on engine+host, B on bots, C on UI. Daily syncs. Sync at Phase 5.
