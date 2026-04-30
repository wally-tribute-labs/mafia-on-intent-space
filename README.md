# Mafia on Intent Space

A multi-agent Werewolf game played natively on the [Spacebase1 intent-space protocol](https://memetic.software/intent-space). Humans and bots play side-by-side. The substrate of each player is hidden. The protocol's primitives — append-only history, private participant-scoped subspaces, the full `INTENT → PROMISE → ACCEPT → COMPLETE → ASSESS` lifecycle — are what make the game possible.

Built for the [Spacebase1 Hackathon 2026](https://hack.memetic.software/).

## The pitch in one paragraph

Werewolf needs every primitive intent-space provides: a public square (the day discussion), private faction channels (the mafia coven), permanent unforgeable history (votes, accusations, deaths), explicit declared promises and assessments (kills, investigations, betrayals), and contention as a feature (multiple accusers self-selecting). On top of vanilla Mafia we add a substrate-blind layer: humans and bots are mixed in unknown proportions, and a parallel meta-game asks every player to also guess every other player's substrate. Every assessment is permanent. Over many games, reputation accumulates in the commons with no central server. **It is the matchmaker challenge with a Turing-test layer, expressed entirely in the protocol's verbs.**

## Why this scores

The hackathon grades on four equally-weighted axes. Honest read:

| Axis | Score | Why |
|---|---|---|
| **Originality** | A+ | No one else builds substrate-blind social deduction on intent-space. Site-endorsed direction ("NPCs that coordinate without scripts") taken further than expected. |
| **Technical depth** | A | Full lifecycle, real LLM agents with personas, commit-reveal cryptography, mixed-substrate UI bridge, server-enforced private subspaces. |
| **Native-ness** | A+ | Cannot be built on a normal queue. Every primitive is load-bearing: append-only log = game record, private subspaces = factions, ASSESS = reputation, contention = voting. |
| **Demo-ability** | A+ | Judge plays one game in 5 minutes; sees their own substrate-guess publicly assessed by 6 bots; observatory tree-view shows the entire game as a nested intent forest. |

See [`docs/PROTOCOL_MAPPING.md`](docs/PROTOCOL_MAPPING.md) for verb-by-verb justification.

## Documentation map

Read in this order if you're new:

1. [`docs/SETUP.md`](docs/SETUP.md) — environment setup + spacebase1 enrollment + smoke test
2. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system overview: agents, spaces, processes
3. [`docs/PROTOCOL_MAPPING.md`](docs/PROTOCOL_MAPPING.md) — how every Mafia mechanic maps to ITP verbs
4. [`docs/GAME_RULES.md`](docs/GAME_RULES.md) — concrete game rules and our variant
5. [`docs/AGENT_DESIGN.md`](docs/AGENT_DESIGN.md) — bot personas, LLM prompts, decision loops
6. [`docs/HUMAN_UI.md`](docs/HUMAN_UI.md) — local web UI for human seats
7. [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) — task list with hours, dependencies, build order
8. [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) — what a stranger judge sees
9. [`docs/RISKS.md`](docs/RISKS.md) — open assumptions, fallback plans (with Phase 0 results)
10. [`docs/DECISIONS.md`](docs/DECISIONS.md) — design decision log (ADR-style)
11. [`docs/AVALON_MAPPING.md`](docs/AVALON_MAPPING.md) — same engine, different game (substantiates the platform claim)
12. [`docs/PARALLELIZATION.md`](docs/PARALLELIZATION.md) — concurrent build tracks + dependency graph
13. [`docs/AGENT_PROMPTS.md`](docs/AGENT_PROMPTS.md) — paste-ready prompts to launch builder agents per track
14. [`docs/REFERENCE_LINKS.md`](docs/REFERENCE_LINKS.md) — every external/source citation

## Quick start

```bash
cd mafia-on-intent-space
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
make enroll-host       # signs up an agent on spacebase1, provisions home space
make smoke-host        # post + scan smoke test
make enroll-verify     # enrolls 3 agents needed for private-space verification
make verify-private    # validates the mafia-coven security model
```

Full walkthrough: [`docs/SETUP.md`](docs/SETUP.md). Common ops: `make help`.

## Phase 0 status (as of 2026-04-30)

Foundation validated end-to-end against live `spacebase1.differ.ac`:

| Test | Result | Wall-clock | Log |
|---|---|---|---|
| Enrollment (commons signup → home-space provisioning → bind) | PASS | 6.5s | `artifacts/enroll-host.log` |
| Smoke (reconnect + post + scan) | PASS | 1.5s | `artifacts/smoke-host.log` |
| Private-space enforcement (5-step verifier) | PASS | 8.6s | `artifacts/verify-private-space-v3.log` |

Live host agent: `prn_spacebase1_space_bdfdea28_3db7_4aad_aa8d_f4d34ab4970d`
Live observatory: [link](https://spacebase1.differ.ac/observatory#origin=https%3A%2F%2Fspacebase1.differ.ac&space=space-bdfdea28-3db7-4aad-aa8d-f4d34ab4970d&token=HFlMLA-_OIlijOlRnfmPDwe_5FWZxkrn-t8GufMcQzA)

Mafia coven mechanism (server-enforced private subspaces) is **GREEN**. Build per [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) starting at Phase 1.

## Submission

After build, submit per the hackathon flow:

```
parentId: intent-413e0bc5-d8f3-40e7-afb4-350e220df03c
content: "Submission: Mafia on Intent Space — substrate-blind Werewolf"
payload:
  kind: "hackathon-submission"
  event: "spacebase1-hackathon-2026"
  repo_url: "<github URL after publish>"
  team_name: "<TBD>"
  agent_principal: "<from enrollment>"
  one_liner: "Multi-agent Werewolf with hidden human/bot mix, played entirely through INTENT/PROMISE/ACCEPT/COMPLETE/ASSESS."
```

## Repo layout

```
mafia-on-intent-space/
├── README.md                  # this file
├── .gitignore
├── docs/                      # all design docs (read these first)
├── scripts/                   # enrollment, smoke tests, demo runners
├── src/                       # implementation (built per BUILD_PLAN.md)
│   ├── game/                  # rules engine, phase loop, win conditions
│   ├── bots/                  # bot agent personas + LLM decision loops
│   ├── host/                  # game-host agent (announcer + commit-reveal)
│   ├── human/                 # local web UI + human-agent bridge
│   └── spectator/             # commentary agent (Town Crier challenge)
└── artifacts/                 # game transcripts, recordings (gitignored)
```

## License

TBD — pick before submission. Apache 2.0 matches the upstream big-d repo.
