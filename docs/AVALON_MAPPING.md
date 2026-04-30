# Avalon on the Same Substrate

A short doc demonstrating that the Mafia engine generalizes to other social-deduction games using the same intent-space primitives. Avalon (and Resistance, and Secret Hitler) all map to the same kind taxonomy with minor additions.

If we publish this alongside the Mafia code, we provide a credible "platform, not just a game" claim without writing a second game from scratch.

## What's different about Avalon vs Mafia

- No nightly mafia kills (no permanent eliminations during the round)
- Players go on quests; quests succeed or fail
- Quests are voted on by a team selected each round
- Special characters (Merlin, Percival, Morgana, Mordred, Oberon, Assassin) have asymmetric information

## Phase loop (Avalon variant)

```
Mission N Setup        →  team-leader proposes a team
Mission N Approval     →  all players vote yay/nay on the team composition
Mission N Quest        →  if approved, the team votes pass/fail (privately)
Mission N Reveal       →  result announced
... repeat until 3 quests succeed (good wins) or 3 quests fail (evil wins)
Final Assassination    →  if good won, evil's Assassin guesses Merlin's identity
```

5 missions max; first to 3 wins.

## Kind taxonomy (additions only)

The Mafia kind taxonomy from [`PROTOCOL_MAPPING.md`](PROTOCOL_MAPPING.md) carries over. Avalon adds:

| `kind` | Verb | Where | Schema |
|---|---|---|---|
| `quest-leader-rotation` | INTENT | game space | `{leader: principal_id, mission_index}` |
| `team-proposal` | INTENT | game space | `{team: [principal_ids], mission_index}` |
| `team-approval` | INTENT | game space | `{vote: "yay"|"nay"}` (one per player per round) |
| `quest-card` | INTENT | quest subspace | `{card: "pass"|"fail"}` (one per team member; only Evil may "fail") |
| `quest-result` | COMPLETE on team-proposal | game space | `{passed_count, failed_count, success: bool}` |
| `merlin-guess` | INTENT | game space | `{target: principal_id}` (Assassin only, after good wins) |

## Spaces

| Space | Purpose |
|---|---|
| `commons` | Lobby + cross-game ASSESSes (same as Mafia) |
| Per-player home space | Per-player private notebook (used for evil-knowledge delivery from host) |
| `space-<game-id>` | Public game space with all players + host |
| `space-<evil-id>` | Shared subspace: all evil characters + host (mutual recognition) |
| `space-<merlin-id>` | Shared subspace: Merlin alone + host (sees evil minus Mordred) |
| `space-<percival-id>` | Shared subspace: Percival alone + host (sees Merlin and Morgana, can't tell which is which) |
| `space-<quest-N>` | Per-quest shared subspace: just the proposed team + host (vote privately) |

## Special characters → private subspaces

Each special character with asymmetric information gets a private subspace shared with the host where their secret is delivered:

```
At game start, after game-init, host requests:
  - shared(host + all_evil)            → mutual recognition, mafia-style coven
  - shared(host + Merlin)              → carries 'evil_principals' minus Mordred
  - shared(host + Percival)            → carries 'merlin_or_morgana_principals'
```

Each character's worldview is just whatever they can scan in their private subspace. No central state.

## Quest mechanic

For each mission:

1. Host posts `team-proposal` INTENT in game space with team composition (chosen by current leader).
2. All players post `team-approval` INTENTs (their vote).
3. Host computes majority. If approved:
   - Host requests a fresh shared space with `participant_principals = team + [host]`. This is the quest subspace.
   - Each team member posts a `quest-card` INTENT in the quest subspace (privately).
   - Host counts pass/fail votes and posts `quest-result` COMPLETE in the public game space.
4. If not approved, leadership rotates. After 5 rejected proposals in a row, evil wins by default.

**Why a fresh shared space per quest:** the quest votes must be private among the team, hidden from non-team players. Same primitive as the mafia coven. Reuse, not new code.

## Final Assassination

If good has won 3 missions, evil gets one shot:

```
shared-game-space/
└── INTENT (host, kind=phase-start, phase=assassination):
      "Good has won 3 quests. Assassin: identify Merlin."
    └── INTENT (Assassin, kind=merlin-guess):
          payload: {target: principal_id}
└── COMPLETE (host) on phase-start:
      kind: "phase-end"
      payload:
        guessed_target: ...
        actual_merlin: ...
        winner: "evil" if guessed_target == actual_merlin else "good"
```

## Reuse vs new code

Implementing Avalon on top of the Mafia engine would reuse:

- Lobby formation (identical)
- Commit-reveal for character assignments (identical, just different role pool)
- Phase loop (identical structure, different phase names)
- Substrate-blind layer (identical — humans and bots can play either game)
- Post-game ASSESS layer (identical)
- Stats CLI (identical)

New code needed:

- Quest team-proposal flow (~50 lines)
- Per-quest shared-space provisioning (~30 lines, reuses Mafia coven code)
- Final Assassination phase (~30 lines)
- Avalon-specific bot prompts (~3 hours, not in MVP for Mafia hackathon)

**Estimated effort to add Avalon after Mafia ships:** ~8 hours.

## Why this matters for the hackathon

The hackathon rewards "intent-space native-ness" — the substrate of the game must be the protocol, not state held elsewhere. By demonstrating that the same engine plays both Mafia AND Avalon (with documented mapping), we substantiate the claim that this is a *general substrate for social-deduction games*, not a one-off Mafia clone.

We don't need to ship Avalon to claim this. The mapping doc is sufficient evidence.
