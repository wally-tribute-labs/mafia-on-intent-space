# Game Rules

The exact rules our implementation enforces. Canonical Mafia/Werewolf with two specific additions: a substrate-guessing meta-layer and an Identification Vote mechanic.

If you've never played Mafia before, [read the Wikipedia rules first](https://en.wikipedia.org/wiki/Mafia_(party_game)). This doc assumes familiarity.

## Setup

| Parameter | Value |
|---|---|
| Players | 7 (canonical demo size) |
| Roles | 2 Mafia, 1 Detective, 1 Doctor, 3 Villagers |
| Substrate mix | Unknown — anywhere from 0 to 7 humans, the rest bots |
| Phases | Day Discussion → Day Vote → Day Tally → Night → repeat |

Variations supported:

- 5-player game (1 Mafia, 1 Detective, 3 Villagers — no Doctor)
- 9-player game (3 Mafia, 1 Detective, 1 Doctor, 4 Villagers)

We ship 7 as the demo default. The host accepts a `--player-count` flag.

## Game-end conditions

- **Villagers win** when all Mafia are dead.
- **Mafia win** when `living_mafia >= living_non_mafia` (mafia can't be lynched out).

## Roles

### Villager

- No special action.
- Day: discuss, accuse, defend, vote.
- Night: sleep (but may scan and observe — the public game space shows nothing during night, so this is a no-op).

### Mafia

- Knows other Mafia (via the coven shared subspace they all participate in).
- Day: behave like a Villager (cannot reveal); may vote.
- Night: in the coven space, propose and confirm a kill target. Once Mafia consensus is reached (one INTENT, one PROMISE accepting), host posts the kill announcement.

### Detective

- Day: behave like a Villager.
- Night: in private detective notebook subspace, post `kind=investigate` INTENT naming a target. Host responds with COMPLETE giving target's role AND substrate.
- Detective may share findings publicly during the next day, or stay quiet.

### Doctor

- Day: behave like a Villager.
- Night: in private doctor notebook subspace, post `kind=protect` INTENT naming a target.
- If the doctor's target equals the Mafia's target, the kill is overridden. Morning announcement says `cause: "saved"` and no death occurs.
- Doctor may protect themselves once per game (toggle in payload).

## Phase sequence

```
Day 1 Discussion  →  Day 1 Vote  →  Day 1 Tally
        ↓ (lynch announced; if game-end, stop)
Night 1 (parallel: Mafia + Detective + Doctor act in own subspaces)
        ↓ (host resolves kill+protect; morning announces)
Day 2 Discussion  →  Day 2 Vote  →  Day 2 Tally
        ↓
... repeat until win condition
```

Day 1 has no prior night, so no morning death — the lynch is the only Day 1 elimination.

### Phase durations

Default with no humans (all-bot game):

| Phase | Duration |
|---|---|
| Day Discussion | 60s |
| Day Vote | 30s |
| Day Tally | instant |
| Night phases | 30s each (run in parallel) |
| Morning Wake | instant |

With humans: each phase scales by `min(60s, 15s × living_humans)`. So:
- 1 living human: phases capped at 60s (no scaling needed)
- 0 living humans: shrink phases to 30s (faster)
- This keeps demo length tight while giving humans time to think.

Host accepts `--phase-multiplier <float>` to scale all phases (default 1.0).

## Identification Vote (variant addition)

Once per day, during the Vote phase, any living player may post `kind=id-vote` INTENT instead of (or in addition to) a normal `kind=vote`. Payload:

```json
{
  "target": "principal-X",
  "claimed_substrate": "human" | "agent"
}
```

Resolution at Tally:

- **Correct guess:** Target's substrate is publicly locked (added to `phase-end.substrate_locked` and counted in target's stealth score forever).
- **Incorrect guess:** Accuser's own substrate is publicly locked.

Substrate locking does not kill anyone. It just makes that one fact publicly known for the rest of the game (and forever in the log). Once locked, that player's substrate is in `phase-end.substrate_locked` and other players can scan and use it strategically.

**Why this exists:** Adds strategic depth to the substrate meta-game. A player confident they've spotted a bot can spend their turn proving it (and locking the info publicly), at the cost of not casting a normal lynching vote. A wrong guess hurts them. This makes substrate-detection a real cost-bearing game decision, not just a passive post-game ASSESS.

## Day Discussion rules

- Any living player may post any number of `kind=accusation` or `kind=defense` INTENTs.
- All discussion INTENTs nest under the day's `phase-start` INTENT (its `parentId`).
- Phase ends at deadline OR if no living player posts for 15 consecutive seconds (host detects via scan).

## Day Vote rules

- Each living player posts at most one `kind=vote` AND at most one `kind=id-vote` per day.
- A second vote from the same sender in the same phase is treated as ignored (per scan order — first one wins). Host's tally only counts the first.
- Phase ends at deadline OR when all living players have voted (host detects).
- Tally is computed from public votes:
  - Lynch target = `argmax(vote_counts)`. Ties: no lynch.
  - Identification votes are resolved separately.
- Host posts COMPLETE on `phase-start` with payload `{tally, lynched, id_votes_resolved, substrate_locked}`.

## Night rules

- Three night subspaces run in parallel: coven, detective notebook, doctor notebook.
- Each subspace has its own `phase-start` INTENT posted by the host.
- Players (mafia / detective / doctor) act independently and asynchronously.
- Host waits for all night actions OR deadline, then resolves.
- **Resolution order:**
  1. Doctor's `protect` target
  2. Mafia's `coven-target` (the one with majority mafia agreement)
  3. If doctor target == mafia target, kill is overridden (`cause=saved`)
  4. If doctor target != mafia target, victim dies (`cause=mafia`)
  5. Detective's `investigate` is resolved independently with COMPLETE in detective subspace
- Morning: host posts `phase-start` INTENT for the new day with `payload.deaths` listed.

## Substrate guessing rules

- During the game: any player may post `kind=substrate-tell` INTENTs in day discussion to share their reasoning. These are not binding.
- During Day Vote: `kind=id-vote` (see above) is binding — locks substrate.
- After game-end: every living and dead player MUST post one ASSESS per other player, with `assessment: "human" | "agent"` and `payload: {confidence: 0..1, reasoning: "..."}`.
- The post-game ASSESSes are what feed long-term reputation.

## Cheating prevention

The protocol prevents most cheating by construction:

| Possible cheat | Why it can't happen |
|---|---|
| Reading the coven space without being mafia | Auth layer rejects: wrong audience token (401) |
| Switching roles mid-game | Role commitment was hashed into game-init at start; mismatch detected at reveal |
| Posting under another player's identity | Every post is signed with sender's keypair; impersonation requires private key theft |
| Erasing a vote you regret | Append-only log: posts are permanent |
| Forging a phase-end | Players verify host's tally against their own scan; mismatches are visible |

Cheats the protocol can't prevent (players must socially enforce):

- A bot operator running a bot with extra system-prompt knowledge ("you are Mafia and your teammate is Player2") — this is just choosing your tools
- A human player using out-of-band communication (Slack, voice) — orthogonal to protocol; against game etiquette but undetectable
- The host being malicious (lying about substrate map) — mitigated by commit-reveal: substrate commitment is in game-init; revealed at end; mismatches publicly visible

For hackathon scope: no out-of-band channels. We're not building a competitive Mafia league — we're proving the protocol works.

## Edge cases the implementation must handle

| Edge case | Resolution |
|---|---|
| Mafia disagree on target | Take first INTENT with PROMISE (consensus order); if none, no kill |
| Detective doesn't act | No investigation result; no public information added |
| Doctor doesn't act | No protection; mafia kill proceeds if any |
| All players die in one night | Game ends as a draw (no winner) — extremely unlikely but defined |
| Lynch creates an immediate game-end (e.g. last mafia lynched) | Game ends; no following night |
| Night kill creates an immediate game-end (e.g. mafia kill leaves them at majority) | Morning announces both the death and the game-end |
| A player disconnects mid-game | Their seat goes silent; treated as no-action; game continues |
| Tied vote | No lynch; phase-end records the tie |
| Identification Vote on a player not in the game | Discarded as malformed |

## Non-rules (clarifications)

- There is **no jail, no last-words, no day-zero**. Day 1 starts with a discussion phase immediately after game-init.
- There is **no role-swap, no shape-shift, no graveyard chat**. Dead players are out; their dead-player reasoning may be posted to commons by their agent if they wish (visible to everyone but not in the game space).
- The **host is not a player**. The host has full role+substrate visibility and arbitrates phases. The host does not vote, accuse, lynch, or kill. (Future variant: blind-host with role enforcement via consensus protocol — out of scope for hackathon.)
