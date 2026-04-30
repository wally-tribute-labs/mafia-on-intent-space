# Agent Prompts

Paste-ready prompts to launch builder agents (Cursor subagents, Claude Code, or any LLM with filesystem access to the project).

Each prompt is **self-contained** — give it to a fresh agent with no prior context, point it at the project root, and it knows what to do, what to deliver, and how to validate.

## How to use

1. Pick a prompt from the list below matching your current build phase.
2. Open a new agent session (Cursor subagent / Claude Code window / etc.).
3. Paste the prompt verbatim.
4. Wait for the agent to deliver. Each prompt has explicit acceptance criteria — verify those before merging.
5. After several prompts complete, run the convergence prompt at the bottom to integrate.

## Conventions

- **Project root** = `/Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/`
- All paths in prompts are relative to project root unless otherwise noted.
- Each prompt assumes the agent has filesystem access to project root, and read access to `../big-d/` and `../ccmarket/`.
- Each prompt includes the relevant doc to read first. **Always make the agent read those docs**; the docs are the single source of truth.

---

## P0 — Schema-first (BLOCKING; run this first)

```
You are working in the project at /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

READ FIRST (in this order):
1. docs/PROTOCOL_MAPPING.md  (especially the kind taxonomy section)
2. docs/GAME_RULES.md         (for kind-specific schemas)
3. docs/ARCHITECTURE.md       (§Data shape)

DELIVERABLE
Write src/game/schemas.py — one Pydantic v2 module that defines:

1. A Literal type Kind covering every kind in the taxonomy:
   lobby-open, lobby-join, game-init, phase-start, phase-end,
   accusation, defense, observation, vote, id-vote, coven-target,
   coven-confirm, investigate, investigate-result, protect, death,
   game-end, reveal, substrate-guess, summary, smoke-test,
   shared-space-invitation

2. A BasePayload(BaseModel) with shared fields:
   content: str | None = None
   kind: Kind
   game_id: str | None = None
   phase: str | None = None
   timestamp_iso: str | None = None
   data: dict[str, Any] | None = None

3. One concrete subclass per kind (PhaseStartPayload, VotePayload,
   IdVotePayload, CovenTargetPayload, etc.) with the exact fields
   listed in PROTOCOL_MAPPING.md and GAME_RULES.md.
   Use Pydantic Field(...) for required fields.

4. A discriminator function `parse_payload(raw: dict) -> BasePayload`
   that picks the right subclass based on raw["kind"].

5. A pytest file tests/test_schemas.py with one round-trip test per
   subclass: build → model_dump() → parse_payload() → equality.

CONSTRAINTS
- Pure schema; NO protocol I/O, no SDK imports.
- Use Pydantic v2 (BaseModel.model_dump, model_validate, etc.). Add it
  to requirements.txt if not present (`pydantic>=2.0`).
- Validate with: `python -m pytest tests/test_schemas.py -v`
- All tests MUST pass before this prompt is considered done.

DO NOT
- Touch any file outside src/game/schemas.py, tests/test_schemas.py,
  requirements.txt.
- Write protocol-layer code (host, bots, UI).
- Add fields not specified in the docs.

OUTPUT
- Files written
- pytest output (last 20 lines)
- Any kind from PROTOCOL_MAPPING.md you couldn't model and why
```

**Estimated time:** 45 min for an LLM agent. **Sequential blocker** — every other track depends on this.

---

## TRACK A prompts

### A1 — Phase + tally + win condition

```
You are working in /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

PRECONDITION
- src/game/schemas.py must exist (P0 done)
- tests/test_schemas.py must pass

READ FIRST
1. docs/ARCHITECTURE.md  (§Phase timing model, §Data shape)
2. docs/PROTOCOL_MAPPING.md  (§Day phase, §Vote, §Win condition)
3. docs/GAME_RULES.md  (§Game-end conditions, §Day Vote rules,
                        §Identification Vote)
4. src/game/schemas.py  (your input data shape)

DELIVERABLES
1. src/game/phase.py
   - current_phase(messages: list[dict]) -> Optional[dict]
     Returns the latest phase-start INTENT with no matching phase-end
     COMPLETE. Logic per ARCHITECTURE.md §Phase timing model.

2. src/game/tally.py
   - compute_tally(phase_start_intent: dict, vote_messages: list[dict])
     -> {target_principal: count}
     Counts only the FIRST vote per sender per phase (later duplicates
     ignored).
   - resolve_lynch(tally: dict) -> Optional[str]
     Returns the lynch target (argmax). Returns None on tie.
   - resolve_id_votes(id_vote_messages: list[dict],
                      truth: dict[str, str])
     -> list[{voter, target, claimed, actual, correct, locked}]
     Where `truth` maps principal_id -> "human"|"agent".
     `locked` = target if correct else voter.

3. src/game/winner.py
   - compute_winner(living_principals: set[str],
                    role_truth: dict[str, str]) -> Optional[str]
     Returns "villagers", "mafia", "draw", or None (game ongoing).

4. tests/test_phase.py, tests/test_tally.py, tests/test_winner.py
   - At least 5 tests per module
   - Cover: happy path, tie, no votes, all-mafia-dead, mafia-majority,
     id-vote correct + incorrect

CONSTRAINTS
- Pure derivation from message lists. NO SDK calls, NO network.
- Functions are pure; no class state.
- Validate: `python -m pytest tests/test_phase.py tests/test_tally.py tests/test_winner.py -v`

DO NOT
- Touch src/game/schemas.py
- Write host or bot code
- Touch tests for other modules

OUTPUT
- Files written
- pytest output
```

**Estimated time:** 1.5h.

### A2 — Commit-reveal + night resolution + stats

```
You are working in /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

PRECONDITION
- A1 done; phase/tally/winner modules exist and tested

READ FIRST
1. docs/PROTOCOL_MAPPING.md  (§Role and substrate assignment,
                              §Night phase, §Substrate guesses)
2. docs/GAME_RULES.md  (§Night rules, §Cheating prevention)
3. src/game/schemas.py

DELIVERABLES
1. src/game/commit_reveal.py
   - generate_salts(n: int) -> list[bytes]            (16 bytes each)
   - commit(value: str, salt: bytes) -> str           (sha256 hex)
   - verify(value: str, salt_hex: str, commitment: str) -> bool

2. src/game/night_resolve.py
   - resolve_night(coven_targets: list[dict],
                   doctor_protect: dict | None)
     -> {killed: principal_id | None, cause: "mafia"|"saved"|"none"}

3. src/game/stats.py
   - compute_principal_stats(commons_messages: list[dict],
                             principal_id: str)
     -> {detection_accuracy: float, stealth_score: float,
         games_played: int, ...}
   - leaderboard(commons_messages: list[dict])
     -> list[{principal, stealth, detection, games}]
       sorted by combined score

4. tests/test_commit_reveal.py, tests/test_night.py, tests/test_stats.py
   - commit_reveal: round trip + tampering detection
   - night: doctor saves, doctor mismatched, no doctor, all roles
   - stats: empty input, single game, multi-game with conflicting assesses

VALIDATE
- `python -m pytest tests/test_commit_reveal.py tests/test_night.py tests/test_stats.py -v`

DO NOT
- Touch src/game/schemas.py or anything from A1
- Write host or bot code
```

**Estimated time:** 2h.

### A3 — Host agent

```
You are working in /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

PRECONDITION
- A1 + A2 done; all src/game/*.py modules exist
- Host agent is enrolled (workspace .intent-space-mafia-game-host
  exists; verify with `cat .intent-space-mafia-game-host/enrollment-summary.json`)
  If not, run `make enroll-host` first.

READ FIRST
1. docs/ARCHITECTURE.md  (§Components, §Process model, §Phase timing)
2. docs/PROTOCOL_MAPPING.md  (entire doc)
3. docs/DECISIONS.md  (D1, D2, D4)
4. scripts/enroll_agent.py  (for SDK usage patterns)
5. ../ccmarket/plugins/intent-space-agent-pack/sdk/_space_tools_common.py
   (for the actual SDK API surface)

DELIVERABLES
1. src/host/main.py — CLI entry point
   - argparse: --player-count (default 7), --workspace
     (default .intent-space-mafia-game-host),
     --phase-multiplier (default 1.0)
   - Loads enrollment, connects, dispatches to lobby → game loop

2. src/host/lobby.py
   - run_lobby(session, player_count, deadline_s) -> list[principal_id]
   - Posts INTENT(kind=lobby-open) in commons
   - Collects PROMISE(kind=lobby-join) until count met or deadline
   - Returns sorted list of joined principals

3. src/host/start.py
   - start_game(session, players, player_substrates, multiplier) ->
     {game_space_id, coven_id, detective_id, doctor_id, role_truth,
      substrate_truth, salts}
   - Generates roles + salts; substrate_truth comes from caller
   - Requests game shared space via host home space (per
     PROTOCOL_MAPPING.md §Lobby)
   - Posts game-init INTENT with commitments
   - Provisions coven/detective/doctor private subspaces (only those
     for assigned roles)
   - Sends each player their (role, salt, substrate, salt) tuple via
     a private invitation INTENT in the player's home space (uses
     the shared-space invitation pattern)

4. src/host/phase_loop.py
   - run_game_loop(session, game_state, multiplier)
   - Posts each phase-start INTENT, waits, posts phase-end COMPLETE
   - Uses src/game/phase.py + tally.py + winner.py + night_resolve.py
   - When winner != None, posts game-end INTENT + reveal COMPLETE
   - Returns game outcome dict

5. tests/test_host_lobby.py, tests/test_host_start.py
   - Mock the SDK session (don't hit network in tests)
   - Verify the expected sequence of post() calls

CONSTRAINTS
- Use ONLY the SDK methods listed in docs/REFERENCE_LINKS.md
  §Validated SDK API surface
- All payloads validated against src/game/schemas.py before posting
- Phase durations from docs/GAME_RULES.md, scaled by multiplier
- Catch + re-raise with context any SDK exceptions

VALIDATE
- pytest tests/test_host_*.py
- Optional manual: `python -m src.host.main --player-count 0`
  (should run lobby with 0 expected players, post game-end immediately
   after timeout)

DO NOT
- Touch src/bots/* or src/human/*
- Modify schemas.py or game modules

OUTPUT
- Files written
- Test output
- Any SDK quirks discovered (add to docs/RISKS.md §Update log)
```

**Estimated time:** 6h.

---

## TRACK B prompts

### B1 — Bot base loop + LLM client

```
You are working in /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

PRECONDITION
- P0 done (schemas.py exists)
- A1 done (phase.py exists for current_phase)

READ FIRST
1. docs/AGENT_DESIGN.md  (entire doc, including system prompts and JSON shapes)
2. docs/ARCHITECTURE.md  (§Process model)
3. ../ccmarket/plugins/intent-space-agent-pack/sdk/_space_tools_common.py

DELIVERABLES
1. src/bots/llm.py
   - class LLMClient with .complete(system, user, max_tokens, temperature) -> str
   - Default impl uses Anthropic Claude Haiku 3.5 via httpx (NOT the SDK
     anthropic-py client — keep deps minimal). Read ANTHROPIC_API_KEY env var.
   - Provider abstraction so OpenAI swap is trivial (env: LLM_PROVIDER).

2. src/bots/prompts.py
   - build_system_prompt(role, substrate, action_kind, persona) -> str
   - build_user_message(phase, public_messages, private_messages,
                        action_kind) -> str
   - All prompts as docstrings/constants per AGENT_DESIGN.md
   - Truncates public_messages to most recent 30

3. src/bots/context.py
   - format_message(msg: dict) -> str  (one line per msg, log-style)
   - extract_living_players(game_messages, all_principals) -> set[str]
   - already_acted_this_phase(messages, phase, principal, action_kind) -> bool

4. src/bots/base.py
   - class BotAgent(role, persona, principal_id, sessions: dict[str, Session])
       sessions["game"] = main game space session
       sessions[role_subspace] = optional (for mafia/det/doc)
   - .scan_all() -> dict[space_name, list[messages]]
   - .should_act(phase) -> Optional[action_kind]    (per AGENT_DESIGN.md)
   - .decide(action_kind, scans) -> dict   (LLM call + JSON parse)
   - .emit(decision) -> dict   (build INTENT/PROMISE, post via session)
   - .run_loop()  (main loop; exits on game-end)
   - .post_substrate_guesses(reveal) — posts ASSESSes after reveal

5. tests/test_bots_base.py
   - Mock LLMClient, mock Session
   - Verify scan-decide-emit sequence with synthetic phase messages
   - Verify gating (don't speak if already spoke; don't vote twice; etc.)

CONSTRAINTS
- All payloads validated via src/game/schemas.py
- LLM errors get retried once with exponential backoff, then logged + skipped
- Bot writes per-iteration log to logs/<agent_name>.ndjson per AGENT_DESIGN.md

VALIDATE
- pytest tests/test_bots_base.py
- Optional: write a tiny demo `scripts/demo_bot_villager.py` that
  enrolls a fresh agent, joins the host's lobby, plays one stub phase.
  Don't ship in production.

DO NOT
- Touch host code or human UI
- Hardcode persona content; load from src/bots/personas.json
- Use the anthropic Python SDK; use raw httpx to keep deps minimal
```

**Estimated time:** 4h.

### B2 — Specialized bots + personas

```
You are working in /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

PRECONDITION
- B1 done; src/bots/base.py exists with BotAgent

READ FIRST
1. docs/AGENT_DESIGN.md
2. docs/PROTOCOL_MAPPING.md (§Night phase sections)
3. docs/GAME_RULES.md (§Roles)
4. src/bots/base.py

DELIVERABLES
1. src/bots/villager.py — class VillagerBot(BotAgent): pass-through
   (default behavior)

2. src/bots/mafia.py — class MafiaBot(BotAgent)
   - Override should_act to also check coven session
   - Override decide for coven-target / coven-confirm actions
   - Knows teammates from initial reveal payload it received

3. src/bots/detective.py — class DetectiveBot(BotAgent)
   - Adds detective notebook session handling
   - investigate action; uses past investigate-result history in prompt

4. src/bots/doctor.py — class DoctorBot(BotAgent)
   - Adds doctor notebook session handling
   - protect action; tracks self-protection one-shot via local state

5. src/bots/personas.json
   - At least 6 personas per docs/AGENT_DESIGN.md §Per-persona overlays
   - Each has: name, system_overlay, speaking_frequency,
     vote_threshold, id_vote_propensity, temperature, scan_interval_s

6. tests/test_bots_specialized.py
   - One test per bot type with mocked LLMClient + Session
   - Verify each role's specific actions are emitted in the right phase

VALIDATE
- pytest tests/test_bots_*.py

DO NOT
- Modify src/bots/base.py (add to it; don't restructure)
- Touch host or UI code
```

**Estimated time:** 3h.

---

## TRACK C prompts

### C1 — Human UI bridge + scaffold

```
You are working in /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

PRECONDITION
- P0 done (schemas exist)
- A1 done (phase determination exists; the UI uses it to know what to render)

READ FIRST
1. docs/HUMAN_UI.md  (entire doc)
2. docs/ARCHITECTURE.md  (§Process model)
3. docs/AGENT_DESIGN.md  (§Robot Mode)
4. ../ccmarket/plugins/intent-space-agent-pack/sdk/_space_tools_common.py

DELIVERABLES
1. src/human/bridge.py
   - FastAPI app with routes:
     GET  /             -> render templates/page.html
     GET  /feed/public  -> render templates/feed.html with recent msgs
     GET  /feed/private -> private feed (or 204 if no role-private space)
     POST /post         -> take action from form, post via SDK session
     POST /rewrite_robot -> Robot Mode rewrite via LLM
   - CLI: --workspace, --game-space-id, --port (default 8000)
   - Background scan loop in asyncio task; stores latest msgs in memory

2. src/human/templates/page.html
   - HTMX-driven (script src "https://unpkg.com/htmx.org@1.9.10")
   - Header with phase + countdown
   - Public feed (hx-trigger every 2s)
   - Private feed (conditional)
   - Action panel (different content per phase, included as partial)

3. src/human/templates/feed.html — partial for the feeds

4. src/human/templates/action_*.html — one per phase:
   action_discussion.html, action_vote.html, action_mafia.html,
   action_detective.html, action_doctor.html, action_villager_night.html,
   action_endgame.html

5. src/human/llm.py
   - robot_mode_rewrite(text: str) -> str   (per AGENT_DESIGN.md)
   - Same LLM provider abstraction as src/bots/llm.py — share if possible

6. src/human/static/style.css — minimal dark-mode styling

7. tests/test_human_bridge.py — using FastAPI TestClient, verify:
   - GET / renders without crash
   - POST /post calls session.post with correctly formed message
   - Rewrite endpoint returns rewritten text from mocked LLM

CONSTRAINTS
- One process per human (no auth)
- Auto-scrolling on the feed (CSS only — no JS framework)
- Form is submitted via HTMX (hx-post) so the page doesn't full-reload
- Add fastapi, jinja2, httpx to requirements.txt

VALIDATE
- pytest tests/test_human_bridge.py
- Manual: `python -m src.human.bridge --workspace .intent-space-mafia-player-1
  --game-space-id <some-id>` and visit localhost:8000

DO NOT
- Touch src/host/*, src/bots/*, src/game/*
- Use a JS framework; HTMX + CSS only
- Hardcode colors/layout; use CSS variables for theming
```

**Estimated time:** 4.5h.

---

## INTEGRATION prompts (single owner each)

### I1 — Bot game runner

```
You are working in /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

PRECONDITION
- A1, A2, A3, B1, B2 all done
- Host agent enrolled (.intent-space-mafia-game-host exists)
- 7 player agents enrolled (.intent-space-mafia-player-{1..7} exist).
  If not, run `make enroll-players` first.
- ANTHROPIC_API_KEY env var is set

READ FIRST
1. docs/ARCHITECTURE.md  (§Process model, §Process orchestration)
2. docs/BUILD_PLAN.md  (§Phase 5)

DELIVERABLE
scripts/run_bot_game.py
- argparse: --player-count (default 7), --phase-multiplier (default 1.0)
- Launches:
  1. src/host/main.py as a subprocess, captures its stdout to logs/host.log
  2. For each player slot, picks a role (per host's expected mix)
     and launches an appropriate bot subprocess:
       python -m src.bots.villager --workspace .intent-space-mafia-player-i
       python -m src.bots.mafia ... etc
- Each subprocess gets:
     --workspace .intent-space-mafia-player-i
     --persona <random from personas.json>
     --llm-model claude-haiku-3-5
- Wait for host process to exit (game over) or 600s timeout
- Tear down all subprocesses cleanly on exit / KeyboardInterrupt
- Print summary at end: outcome, duration, log paths

CONSTRAINTS
- Use stdlib subprocess.Popen; no async fanciness
- One subprocess per agent — true process isolation
- Forward subprocess stdout/stderr to logs/<agent>.log
- On Ctrl+C, send SIGTERM then SIGKILL after 5s grace

VALIDATE
- Run end-to-end: `python scripts/run_bot_game.py --phase-multiplier 0.5`
- Should complete in ~3-4 min
- Check logs/host.log for game-end INTENT
- Check observatory URL for the game space (printed at end)

DO NOT
- Modify host or bot code unless you find a bug — log it and let
  the relevant track owner fix it
```

**Estimated time:** 2h (mostly debugging the integration).

### I2 — Mixed human + bot game runner

```
You are working in /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

PRECONDITION
- I1 (run_bot_game.py) works end-to-end
- C1 done (human bridge exists)
- A human agent enrolled with workspace .intent-space-mafia-human

READ FIRST
1. scripts/run_bot_game.py (yours to extend)
2. docs/HUMAN_UI.md (§Bridge process architecture)

DELIVERABLE
scripts/run_mixed_game.py
- argparse: --human-count (default 1, must be < player-count),
  --player-count (default 7), --phase-multiplier
- Same as run_bot_game.py BUT:
  - Reserves human-count seats for human players
  - Launches src/human/bridge.py for each human seat (different ports:
    8000, 8001, ...)
  - Prints the human UI URLs at startup
  - Bots fill the remaining seats
- Wait for game-end OR Ctrl+C

CONSTRAINTS
- Same subprocess discipline as I1
- Print BIG OBVIOUS startup banner with human UI URLs

VALIDATE
- Run with `--human-count 1 --player-count 7 --phase-multiplier 1.0`
- Open localhost:8000 in browser
- Play through one game

DO NOT
- Modify Track A/B/C code; this is pure orchestration
```

**Estimated time:** 1.5h.

---

## EXTRAS / OPTIONAL prompts

### E1 — Spectator agent

```
You are working in /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

PRECONDITION
- A1 done; I1 (bot game runner) works

READ FIRST
1. docs/PROTOCOL_MAPPING.md (§Substrate guesses, last sections)
2. docs/BUILD_PLAN.md (§Phase 8.1)
3. docs/AGENT_DESIGN.md (LLM patterns)

DELIVERABLE
src/spectator/main.py — agent that:
- Enrolls (or uses existing .intent-space-mafia-spectator workspace)
- Scans commons for new game-end INTENTs
- For each completed game, scans the game space, summarizes via LLM
- Posts a summary INTENT in commons (kind=summary) under the parent
  intent of the lobby that started the game

ALSO
scripts/stats.py — pulls commons, computes leaderboard via
src/game/stats.py, prints to stdout

CONSTRAINTS
- Spectator runs forever (or until --max-games N)
- Doesn't interfere with running games (read-only on game spaces it's
  not a participant of — and per audience scoping it CAN'T anyway)

VALIDATE
- Run after several games complete; verify summary INTENTs in commons
```

**Estimated time:** 2h.

---

## CONVERGENCE prompt — when 2+ tracks complete

```
You are working in /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space/.

CONTEXT
The following tracks have completed:
- [list completed tracks here]

GOAL
Verify integration health. Run the test suite, run a smoke game, fix
any breakages caused by interface drift between tracks.

STEPS
1. `python -m pytest tests/ -v`  — all tests pass?
2. If A+B done: `python scripts/run_bot_game.py --phase-multiplier 0.3`
   — does a 7-bot game complete?
3. If C done: `python -m src.human.bridge --help` — does it parse?
4. Check src/game/schemas.py — has anyone added a new kind without
   updating the discriminator?
5. Check imports across src/ — any circular or missing?
6. Update docs/RISKS.md §Update log with any newly-discovered issues

OUTPUT
- Test results
- Smoke game results (if applicable)
- List of issues found
- Recommendation: ready for next phase, or block + fix
```

---

## Tips for running these in parallel

- **Open one Cursor window per track.** Each window's agent session is independent. The agent reads the prompt, the relevant docs, and acts.
- **Don't share workspaces between agents.** If two agents try to edit `src/bots/base.py` at the same time, you'll have merge pain. Stick to the module ownership table in [`PARALLELIZATION.md`](PARALLELIZATION.md).
- **Run tests after every prompt completes.** `make` targets exist for the smoke tests. Add `make test` once tests exist.
- **Daily sync (15 min):** what merged, what's blocked, what's next. If solo, sync with yourself.
- **Each agent's first move should be `cat docs/<relevant doc>`.** If they don't read the doc, the result is unreliable. Reject and re-prompt.

## Common pitfalls

- An agent invents a new SDK method that doesn't exist. **Mitigation:** every prompt requires reading `docs/REFERENCE_LINKS.md §Validated SDK API surface` and the SDK source itself.
- An agent edits files outside its track. **Mitigation:** explicit DO NOT lists in each prompt.
- An agent skips writing tests. **Mitigation:** every deliverable list includes a test file with a minimum case count, and the validate step runs pytest.
- An agent uses Pydantic v1 syntax (BaseModel.dict() instead of .model_dump()). **Mitigation:** prompts say "Pydantic v2" explicitly.
