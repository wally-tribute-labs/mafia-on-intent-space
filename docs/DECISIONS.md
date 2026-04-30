# Decisions Log

ADR-style log of design decisions. Each entry: context, decision, alternatives considered, consequences.

---

## D1 — Host as service-intent provider, not orchestrator

**Date:** 2026-04-30
**Status:** Accepted

**Context.** Mafia needs phase coordination (advancing day → night, announcing tally results, posting deaths). The intent-space ethos is "no dispatcher, no orchestrator." A host that arbitrates ties and advances phases looks like an orchestrator at first glance.

**Decision.** Treat the host as a *service-intent provider* per the spec's "observe before act" doctrine. The host:

- Announces phase boundaries by posting INTENTs ("Day 1 begins", "Day 1 vote ends")
- Computes deterministic results (vote tally, kill resolution) from the public log
- Reveals committed roles/substrates at game end

The host does NOT:

- Dispatch work to specific players
- Force any player to act
- Hold canonical game state outside the protocol

Other agents observe the host's service intents and choose to participate. This is the same posture spacebase1's commons steward takes.

**Alternatives considered.**

- **No host, full peer-coordinated.** Cleaner native-ness but adds significant complexity (commit-reveal of roles needs a different scheme; phase advancement needs quorum protocol). Cut for time.
- **Host = full orchestrator with authority over state.** Simpler to build but fights the protocol explicitly. Bad for the native-ness score.

**Consequences.**

- Host code is non-trivial but bounded (~6h per [`BUILD_PLAN.md`](BUILD_PLAN.md) Phase 2)
- Native-ness score takes a small ding (host is more central than ideal)
- Net rubric: still strong A on native-ness; the fall-back of full peer coordination is documented as future work

---

## D2 — One game = one shared space + per-faction subspaces, not one mega-space

**Date:** 2026-04-30
**Status:** Accepted

**Context.** Need to model: public game thread, mafia coven, detective notebook, doctor notebook. Plus per-game scoping (each game is its own thing).

**Decision.** Each game gets:
- 1 shared game space (all players + host)
- 1 shared coven space (mafia + host)
- 1 shared detective notebook (detective + host) — only if detective role assigned
- 1 shared doctor notebook (doctor + host) — only if doctor role assigned

All four are provisioned at game start by the host requesting them from its home space.

**Alternatives considered.**

- **One big shared space with body-encryption for private parts.** Less clean. Would need a key-distribution dance. Mixes concerns.
- **Per-faction spaces created lazily as needed.** Adds latency mid-game. Synchronous provisioning at start is simpler.
- **Reuse one game space across multiple games.** Append-only log gets noisy. Per-game space gives clean audience scope.

**Consequences.**

- Game start provisions 3-4 spaces (host hits steward 3-4 times)
- Per-game scope is clean for stats and replay
- Slightly more spaces in commons over time; not a problem at hackathon scale

---

## D3 — Each bot is a separate process

**Date:** 2026-04-30
**Status:** Accepted

**Context.** N agents need to participate in a game. Could be threads, processes, async coroutines, or even a single big loop.

**Decision.** Each agent (host, 7 bots, human bridge, spectator) is a separate Python process. Each has its own workspace directory (`.intent-space-<role>/`). Communication is exclusively via the protocol.

**Alternatives considered.**

- **Async single-process.** Cleanest code, but: SDK uses `LocalState` writing to a workspace dir, sharing one workspace across async tasks would corrupt enrollment state. Could work with one workspace per "agent context" but the SDK is sync-blocking which would force a thread-per-agent anyway.
- **Threads with a shared session pool.** SDK isn't thread-safe (subprocess calls to openssl, file I/O for cursors). Rejected.
- **Docker containers per agent.** Overkill; great for production, terrible for hackathon iteration speed.

**Consequences.**

- Need a runner script that launches all N processes (`scripts/run_bot_game.py`)
- True isolation; aligns with "the protocol is the only shared substrate" claim
- Easier to demo (one tmux pane per agent, judge can see them all)

---

## D4 — Per-game commit-reveal for roles AND substrates

**Date:** 2026-04-30
**Status:** Accepted

**Context.** Roles must be hidden during play, verifiable at end. Substrates (human/agent) likewise — to support the post-game ASSESS layer.

**Decision.** Host generates per-player `(salt_i, role_i, substrate_i)` at game start. Posts a `game-init` INTENT containing only `[sha256(role_i + salt_i)]` and `[sha256(substrate_i + salt_i)]`. Privately delivers each player's `(role_i, salt_i)` via that player's home space. Reveals all salts at game end via COMPLETE on `game-init`.

**Alternatives considered.**

- **Trust the host (no commit-reveal).** Loses the ability to prove no mid-game tampering. Hackathon might not care, but it kills the native-ness narrative.
- **Players generate randomness collectively (no host knowledge).** Pure but very complex. Each player would need a verifiable random function and a multi-party computation. Far beyond hackathon scope.
- **Single salt for all (smaller payload).** Marginal savings, harder to verify per-player.

**Consequences.**

- Host code includes `hashlib.sha256` and salt generation
- Player code includes verification on receipt of their `(role, salt)`
- End-of-game verification is a 5-line script anyone can run
- Demo gold: judge can verify cryptographically

---

## D5 — Substrate is orthogonal to role; not a third faction

**Date:** 2026-04-30
**Status:** Accepted

**Context.** Mixed human+bot games could either:
(a) treat substrate as a hidden game-affecting attribute (e.g. "humans win bonus if humans collectively survive"), or
(b) treat substrate as orthogonal flavor — same Mafia rules, plus a parallel Turing-test layer.

**Decision.** (b). Mafia rules unchanged. Substrate has zero effect on the standard win condition. Substrate guessing is a parallel meta-game with its own scoring (stealth + detection accuracy in commons).

**Alternatives considered.**

- **Substrate as third faction.** Rejected explicitly by user feedback ("not straight agents vs humans"). Also feels gimmicky.
- **Substrate as a buffing attribute.** Would distort the Mafia balance; needs new playtesting.
- **Substrate IS the game (drop Mafia roles).** Loses the social-deduction depth. Rejected.

**Consequences.**

- Game logic stays simple
- Substrate-guessing is purely additive; players can ignore it during play and still win Mafia
- The Identification Vote mechanic (see [`GAME_RULES.md`](GAME_RULES.md)) bridges the two layers without distorting the core

---

## D6 — Identification Vote with substrate-locking penalty

**Date:** 2026-04-30
**Status:** Accepted

**Context.** Substrate guessing as a purely-post-game ASSESS is too passive. Players need an in-game cost-bearing mechanic to make substrate-detection a real strategic decision.

**Decision.** Once per day, during the Vote phase, a player may post `kind=id-vote` instead of (or in addition to) `kind=vote`. Correct guess: target's substrate is publicly locked. Wrong guess: accuser's own substrate is publicly locked. Locking doesn't kill anyone — it just makes the fact public for the rest of the log.

**Alternatives considered.**

- **No in-game substrate mechanic; only post-game ASSESSes.** Too passive. Demo less interesting.
- **Substrate-vote-to-lynch.** Would let players win by "outing" without playing Mafia at all. Distorts the game.
- **Free guessing without penalty.** No cost, no signal. Spam.

**Consequences.**

- Adds a strategic dimension to votes (do I lynch, or do I substrate-vote?)
- Bots have a `id_vote_propensity` config knob
- One per day cap prevents spam

---

## D7 — Fixed 7-player game for the demo, 5/9 supported as variants

**Date:** 2026-04-30
**Status:** Accepted

**Context.** Different player counts give different game balance. Need to pick a default.

**Decision.** Demo default = 7 players (2 mafia, 1 detective, 1 doctor, 3 villagers). 5 and 9 supported via `--player-count` flag.

**Alternatives considered.**

- **5-player demo (faster).** 5 minutes vs 7 minutes; loses the "doctor" complexity which is a meaningful protocol mechanic to show.
- **9-player demo (more drama).** 10+ minutes; too long for live demo.

**Consequences.**

- Demo length ~5-7 minutes for 7-player game
- Phase durations tuned for 7

---

## D8 — Use Anthropic Claude Haiku for all bot LLM calls

**Date:** 2026-04-30
**Status:** Accepted

**Context.** Bots need an LLM. Choices include Claude (Haiku/Sonnet/Opus), OpenAI (gpt-4o-mini/gpt-4o), Google (Gemini), local (Ollama).

**Decision.** Default to Claude Haiku 3.5. Make the provider swappable via env var (so OpenAI is a 5-line fallback).

**Alternatives considered.**

- **Claude Sonnet/Opus.** 5-10x more expensive, marginally better play. Not worth it.
- **OpenAI gpt-4o-mini.** Comparable; we go with Claude because the project lives in the spacebase1 ecosystem which is Sky Valley / Differ adjacent; Anthropic API access is easy to assume.
- **Local LLM via Ollama.** Free but slower (5-30s per call), brittle setup. Demo failure risk.

**Consequences.**

- ~$0.07 per game (50 games = $3.50)
- Requires `ANTHROPIC_API_KEY` env var
- Provider abstraction in `src/bots/llm.py` so swap is trivial

---

## D9 — Commons is the canonical home for cross-game state

**Date:** 2026-04-30
**Status:** Accepted

**Context.** Where do post-game ASSESSes live? Where does the spectator post commentary? Where does the submission INTENT go?

**Decision.** Commons is the canonical place for:
- Lobby announcements (`lobby-open`, `lobby-join`)
- Post-game ASSESSes (target the lobby-join PROMISE, which lives in commons)
- Spectator summary INTENTs
- The hackathon submission INTENT
- Anything that should accumulate cross-game

Per-game shared spaces hold:
- Game-specific INTENTs (phase-start, votes, deaths, reveal, etc.)

**Alternatives considered.**

- **Per-game shared space holds everything including reputation.** Reputation across games becomes invisible. Bad.
- **Each principal's home space holds their own ASSESSes.** Hidden from others (private). Defeats the purpose of public reputation.

**Consequences.**

- Commons gets noisy over many games; that's the point
- Stats CLI scans commons to build leaderboards
- Spectator and judge both watch commons

---

## D10 — FastAPI + HTMX for human UI

**Date:** 2026-04-30
**Status:** Accepted

**Context.** Need a local UI for human players. Options: Streamlit, React+FastAPI, FastAPI+HTMX, CLI, Jupyter widgets.

**Decision.** FastAPI + HTMX. Server-side rendering, no JavaScript framework, fast iteration.

**Alternatives considered.**

- **Streamlit.** Auto-rerun fights the live-update model.
- **React + FastAPI.** Build toolchain overhead.
- **CLI.** Loses the substrate-mimicry vibe; harder for live judges to engage with.
- **Jupyter.** Rendering reliability issues under demo stress.

**Consequences.**

- Single Python process, no Node
- Trivial to demo on any laptop
- See [`HUMAN_UI.md`](HUMAN_UI.md) for full spec

---

## D11 — Don't build cross-game agent learning

**Date:** 2026-04-30
**Status:** Accepted (as scope cut)

**Context.** Could agents learn from past games (their own and others') to improve over time? Adjust their strategy based on observed BROKEN ASSESSes?

**Decision.** No. The substrate is THERE for it (commons accumulates the data), but we don't implement it. Document as future work.

**Alternatives considered.**

- **Implement basic prior-game memory in bot prompts.** Would add ~6 hours; significant payoff for the "adaptive apps" alignment with Sky Valley's product. Tempting.

**Consequences.**

- Saves time
- Alignment story with Sky Valley's commercial thesis is one step removed (substrate ready, not exercised)
- Mention in repo as "obvious next step that the substrate already supports"
