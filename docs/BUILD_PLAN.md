# Build Plan

Concrete task list with hours, dependencies, and order. Tracks against [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`PROTOCOL_MAPPING.md`](PROTOCOL_MAPPING.md).

## Total budget

~48 hours for full scope (vanilla Mafia + substrate layer + human UI + spectator + Avalon doc).

Cut order if time pressure: spectator → Avalon doc → reduce bot personas to 2.

Realistic minimum (MVP+killer only, no spectator, no Avalon doc): ~38 hours.

## Build phases

### Phase 0 — Foundation (3-4 hours)

Sets up the repo, validates assumptions, gets one agent talking to spacebase1.

| # | Task | Estimate | Depends on | Output |
|---|---|---|---|---|
| 0.1 | Clone big-d and ccmarket; confirm SDK imports | 0.25h | — | sdk_smoke.py |
| 0.2 | Write enrollment script (`scripts/enroll_agent.py`) per [`SETUP.md`](SETUP.md) | 1.0h | 0.1 | enroll_agent.py |
| 0.3 | Run enrollment for one agent (host); verify observatory URL | 0.5h | 0.2 | `.intent-space-host/` populated |
| 0.4 | Write smoke test (`scripts/smoke_test.py`) — post + scan in home space | 0.75h | 0.3 | smoke_test.py passes |
| 0.5 | Write private-space verifier (`scripts/verify_private_space.py`) — 3 agents, expect 401s | 1.5h | 0.4 | green-light or pivot decision |

**Gate:** must be green by hour 4 or trigger fallback in [`RISKS.md`](RISKS.md) §Private subspace fallback.

### Phase 1 — Game engine core (6 hours)

| # | Task | Estimate | Depends on | Output |
|---|---|---|---|---|
| 1.1 | Define payload schemas + `kind` taxonomy (Pydantic models) | 0.75h | — | `src/game/schemas.py` |
| 1.2 | Phase determination from log scan (`current_phase`) | 0.5h | 1.1 | `src/game/phase.py` |
| 1.3 | Vote tally function (deterministic, derivable from log) | 0.5h | 1.1 | `src/game/tally.py` |
| 1.4 | Win condition check (mafia all dead OR mafia >= non-mafia) | 0.25h | 1.1 | `src/game/winner.py` |
| 1.5 | Commit-reveal helpers (sha256 of role+salt + verification) | 0.5h | — | `src/game/commit_reveal.py` |
| 1.6 | Death resolution (mafia kill + doctor protect + override logic) | 0.75h | 1.1 | `src/game/night_resolve.py` |
| 1.7 | Identification Vote resolution | 0.5h | 1.3 | extension to tally.py |
| 1.8 | Substrate-stat computation from commons scan | 0.75h | 1.1 | `src/game/stats.py` |
| 1.9 | Unit tests for all of the above (pytest) | 1.5h | 1.1-1.8 | `tests/test_game.py` |

**Gate:** unit tests pass; given a synthetic message log, all derivations match expected.

### Phase 2 — Game host agent (6 hours)

| # | Task | Estimate | Depends on | Output |
|---|---|---|---|---|
| 2.1 | Host main loop scaffold | 0.5h | Phase 0 + 1 | `src/host/main.py` |
| 2.2 | Lobby formation: post lobby-open INTENT in commons; collect lobby-join PROMISEs; close on N or deadline | 1.5h | 2.1 | `src/host/lobby.py` |
| 2.3 | Game start: shuffle roles, generate salts, request shared game space, post game-init INTENT with commitments | 1.0h | 2.2, 1.5 | `src/host/start.py` |
| 2.4 | Per-role private subspace provisioning (coven, detective, doctor) | 0.75h | 2.3 | extension to start.py |
| 2.5 | Phase loop: announce phase-start; wait for actions or deadline; post phase-end with derived results | 1.5h | 2.4 | `src/host/phase_loop.py` |
| 2.6 | Game end: detect win condition, post game-end INTENT, post reveal COMPLETE | 0.5h | 2.5 | extension to phase_loop.py |
| 2.7 | Host integration test: run host alone with 0 players, verify it correctly does nothing | 0.25h | 2.6 | works |

**Gate:** host process can scaffold a game (lobby, start, run phase loop with no players, post game-end after timeout).

### Phase 3 — Bot agent base (4 hours)

| # | Task | Estimate | Depends on | Output |
|---|---|---|---|---|
| 3.1 | Bot main loop (scan-decide-post) | 1.0h | Phase 0 + 1 | `src/bots/base.py` |
| 3.2 | LLM client wrapper (Anthropic Claude Haiku) | 0.5h | — | `src/bots/llm.py` |
| 3.3 | System-prompt builder (base + persona overlay + action-specific) | 1.0h | 3.2 | `src/bots/prompts.py` |
| 3.4 | Context-message construction (recent N messages + private context) | 0.75h | 1.1 | `src/bots/context.py` |
| 3.5 | Action emission (parse LLM JSON output → INTENT/PROMISE) | 0.5h | 3.1 | extension to base.py |
| 3.6 | One villager bot end-to-end test: enroll, join lobby, run for 1 game, exit cleanly | 0.25h | 3.1-3.5 | works |

**Gate:** one villager bot can complete a full game without crashing (even if its decisions are bad).

### Phase 4 — Specialized bots (3 hours)

| # | Task | Estimate | Depends on | Output |
|---|---|---|---|---|
| 4.1 | Mafia bot: extends base with coven session + coven-target action | 1.0h | 3.6, 0.5 | `src/bots/mafia.py` |
| 4.2 | Detective bot: extends base with notebook session + investigate action | 0.75h | 3.6, 0.5 | `src/bots/detective.py` |
| 4.3 | Doctor bot: extends base with notebook session + protect action | 0.75h | 3.6, 0.5 | `src/bots/doctor.py` |
| 4.4 | Persona configs (6+ personas in `bots/personas.json`) | 0.5h | — | personas.json |

**Gate:** all 4 role types can play a full game.

### Phase 5 — End-to-end bot game (3 hours)

| # | Task | Estimate | Depends on | Output |
|---|---|---|---|---|
| 5.1 | Game runner script: launches host + 7 bots in subprocesses; waits for game-end | 1.0h | Phase 4 | `scripts/run_bot_game.py` |
| 5.2 | First full bot-only game; debug | 1.5h | 5.1 | works |
| 5.3 | Polish: improve persona prompts based on observed behavior | 0.5h | 5.2 | better personas.json |

**Gate:** a full 7-bot game completes start-to-finish, with sensible-looking play.

### Phase 6 — Human UI (5 hours)

| # | Task | Estimate | Depends on | Output |
|---|---|---|---|---|
| 6.1 | Bridge process scaffold: FastAPI app + scan loop | 1.0h | Phase 0 | `src/human/bridge.py` |
| 6.2 | Page template + feed partials (HTMX) | 1.0h | 6.1 | `src/human/templates/` |
| 6.3 | Action panels: discussion, vote, mafia, detective, doctor, end-game | 1.5h | 6.2 | extensions |
| 6.4 | Robot Mode rewrite endpoint + confirm UX | 0.75h | 6.1 | `src/human/llm.py` |
| 6.5 | End-to-end: launch human bridge alongside 6 bots, play a game | 0.75h | 5.2, 6.4 | works |

**Gate:** a human can play a full game alongside 6 bots, including discussion, voting, role actions, and end-game ASSESSes.

### Phase 7 — Substrate-blind mechanics (3 hours)

These are partially built into earlier phases (lobby is substrate-agnostic from the start). This phase is the explicit polish.

| # | Task | Estimate | Depends on | Output |
|---|---|---|---|---|
| 7.1 | Verify lobby genuinely doesn't surface substrate (host shouldn't know who's bot vs human at form time) | 0.25h | 5.2 | confirmed |
| 7.2 | Substrate commit-reveal: include substrate hashes in game-init alongside roles | 0.5h | 2.3 | extension |
| 7.3 | Identification Vote in human UI + bot prompts | 1.0h | 6.3, 4.x | works |
| 7.4 | Post-game ASSESS forms in human UI; bot post-game routine | 0.75h | 6.5 | works |
| 7.5 | Substrate-locked propagation: phase-end carries `substrate_locked`, bots and UI use it | 0.5h | 7.3 | works |

**Gate:** mixed-substrate game runs cleanly, ASSESSes accumulate in commons.

### Phase 8 — Spectator and substrate (3 hours, optional)

| # | Task | Estimate | Depends on | Output |
|---|---|---|---|---|
| 8.1 | Spectator agent: scans game space, posts periodic summary INTENTs into commons | 1.0h | 5.2 | `src/spectator/main.py` |
| 8.2 | Avalon-mapping doc in repo (`docs/AVALON_MAPPING.md`) | 1.0h | — | doc |
| 8.3 | Cross-game stats CLI: pulls commons, computes per-principal stealth+detection scores, prints leaderboard | 1.0h | 1.8 | `scripts/stats.py` |

**Gate:** spectator visibly contributes commentary; stats CLI shows real numbers after a few games.

### Phase 9 — Demo polish + submission (3 hours)

| # | Task | Estimate | Depends on | Output |
|---|---|---|---|---|
| 9.1 | Run 5-10 games, refine bot prompts, tune phase durations | 1.0h | Phase 5+6 | better play |
| 9.2 | Record a demo run (screen recording of observatory + human UI in parallel) | 0.5h | 9.1 | recordings/demo.mp4 |
| 9.3 | Polish README, fill in placeholders | 0.5h | — | done |
| 9.4 | Submit per hackathon flow (post submission INTENT in commons) | 0.25h | 9.3 | submission INTENT id |
| 9.5 | Tag repo, push to public GitHub | 0.25h | 9.4 | public repo |
| 9.6 | Verify submission appears in commons; verify judge agent picks it up | 0.5h | 9.4 | confirmed |

**Gate:** submission visible in commons; demo recording exists; repo is public.

### Slack budget (4 hours)

Things will go wrong. Budget held in reserve for:

- Spacebase1 commons unreachable (fallback: self-host http-reference-station)
- Steward provisioning intermittent (retry with backoff)
- LLM API issues (fallback to gpt-4o-mini, or cached responses for demo)
- Subprocess management bugs (use `multiprocessing.Manager` if `Popen` flakes)
- Demo machine networking quirks

## Totals by phase

| Phase | Hours | Cumulative |
|---|---|---|
| 0 — Foundation | 4 | 4 |
| 1 — Game engine | 6 | 10 |
| 2 — Host | 6 | 16 |
| 3 — Bot base | 4 | 20 |
| 4 — Specialized bots | 3 | 23 |
| 5 — End-to-end bots | 3 | 26 |
| 6 — Human UI | 5 | 31 |
| 7 — Substrate mechanics | 3 | 34 |
| 8 — Spectator + extras | 3 | 37 |
| 9 — Demo + submission | 3 | 40 |
| Slack | 4 | 44 |

**Total: ~44 hours.** Tight for solo, comfortable for two.

## Cut order

If you're behind by Phase 5:

1. **Cut spectator** (saves 1h)
2. **Cut Avalon doc** (saves 1h)
3. **Cut cross-game stats CLI** (saves 1h)
4. **Reduce personas to 2** instead of 6 (saves 0.25h, costs demo charm)
5. **Drop multi-human ability** (already mostly free; just don't test multi-bridge — saves 0.5h)
6. **Drop Identification Vote** (saves 1.5h, costs originality lift)
7. **Drop human UI entirely → all-bot demo** (saves 5h, severely hurts demo-ability — last resort)

## Build order quick-ref

```
Phase 0 → 1 → 2 → 3 → 4 → 5
                            ↓ (must work end-to-end before adding human)
                            6
                            ↓
                            7
                            ↓ (optional)
                            8
                            ↓
                            9
```

## Concurrent build paths if two builders

- Builder A: Phases 0, 1, 2 (host + engine)
- Builder B: Phases 3, 4 (bots) — depends on Phase 1 schemas

After Phase 5:

- Builder A: Phase 6 (human UI)
- Builder B: Phase 7 (substrate mechanics) + Phase 8 (spectator)

Convergence: Phase 9 together.

## Deliverables checklist

- [ ] Public GitHub repo
- [ ] README accurate
- [ ] All 11 docs in `docs/`
- [ ] `scripts/enroll_agent.py` — runs on a fresh machine
- [ ] `scripts/run_bot_game.py` — runs a full bot game start to finish
- [ ] `scripts/run_mixed_game.py` — runs a mixed human+bot game
- [ ] `src/human/bridge.py` — human UI works
- [ ] Demo recording in `recordings/`
- [ ] Substrate stats output for ≥5 games
- [ ] Submission INTENT posted in commons; intentId saved in repo
