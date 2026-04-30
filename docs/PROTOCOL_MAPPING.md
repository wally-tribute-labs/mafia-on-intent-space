# Protocol Mapping — Mafia → Intent Space

This is the document that proves the project earns its native-ness score. Every Mafia mechanic is mapped to a specific protocol primitive. No mechanic uses intent-space as a transport layer for state held elsewhere.

Read alongside [`GAME_RULES.md`](GAME_RULES.md) (rules of our variant) and [`ARCHITECTURE.md`](ARCHITECTURE.md) (system topology).

## Verb cheat sheet

(Validated against [`itp-verb-header-body-profile.md`](../../big-d/intent-space/docs/itp-verb-header-body-profile.md).)

| Verb | Required headers | Used for |
|---|---|---|
| `INTENT` | sender, parent, intent, timestamp, body-length | Make a desire/announcement visible |
| `PROMISE` | sender, parent, intent, promise, timestamp, body-length | Declare commitment to an INTENT |
| `DECLINE` | sender, parent, intent, timestamp, body-length | Refuse an INTENT |
| `ACCEPT` | sender, parent, promise, timestamp, body-length | Accept a PROMISE |
| `COMPLETE` | sender, parent, promise, timestamp, body-length | Fulfill a PROMISE (with payload) |
| `ASSESS` | sender, parent, promise, timestamp, body-length | Judge a COMPLETE (FULFILLED / BROKEN / custom) |
| `SCAN` | space, since, body-length | Read messages from a space |
| `SCAN_RESULT` | space, latest-seq, body-length | Server reply to SCAN |

## Lobby formation (matchmaker challenge)

**Game requirement:** form a 7-player game from an unknown pool of human + agent participants.

**Protocol mapping:**

```
commons/                                          ← public space, anyone reads
└── INTENT (host, kind=lobby-open):
       "Mafia game forming — 7 seats, hidden substrate mix"
       payload: {seats: 7, opens_at, deadline_ms}
    ├── PROMISE (Player1, kind=lobby-join):
    │      "I'll take a seat" — payload: {agent_label: "Alice"}
    ├── PROMISE (Player2, kind=lobby-join): ...
    ├── ...
    ├── DECLINE (host): "Lobby full" — sent if 8th joiner shows up
    └── COMPLETE (host, kind=game-start):
           payload:
             game_space_id: "space-..."  (steward-provisioned shared space)
             players: [principal_id_1..7]  (canonical sorted)
             role_commitments: [hash1..hash7]
             substrate_commitments: [hash1..hash7]
```

**Why this is the matchmaker challenge:** Anyone (human or agent) can PROMISE without declaring substrate. The host doesn't know who's who. Selection is first-N-promise by sequence number — purely by the public log, no central queue.

**Server side:** The shared game space is provisioned via [`HOME_SPACE_STEWARD_OPERATIONS`](../../big-d/spacebase1/src/shared-spaces.ts#L71) with the joined principals as `participant_principals`. Steward refuses partial sets. All players must already have bound home spaces (handled by initial enrollment).

## Role and substrate assignment (commit-reveal cryptography)

**Game requirement:** roles must be assigned at start, hidden during play, verifiable at end. Substrate (human/agent) likewise.

**Protocol mapping:**

At game start, host posts the `game-init` INTENT with public commitments:

```python
# Host computes locally
salts        = [secrets.token_bytes(16) for _ in players]
roles        = ["mafia","mafia","detective","doctor","villager","villager","villager"]
random.shuffle(roles)
substrates   = [...]  # known to host from lobby; chooses to keep secret

role_commits      = [sha256(role + salt).hex() for role,salt in zip(roles,salts)]
substrate_commits = [sha256(substrate + salt).hex() for substrate,salt in zip(substrates,salts)]
```

```
shared-game-space/
└── INTENT (host, kind=game-init):
      payload:
        game_id, players: [...]
        role_commitments: [hash1..hash7]
        substrate_commitments: [hash1..hash7]
```

Each player privately receives their `(role, salt)` pair from the host via the player's private subspace (the one shared between just that player and the host — see "Private notebooks" below). The player can locally verify `sha256(role || salt) == role_commitments[my_seat]`.

At game end, host posts the reveal:

```
shared-game-space/
└── COMPLETE (host) on game-init INTENT:
      kind: "reveal"
      payload:
        role_assignments:      {principal_id: role}
        substrate_assignments: {principal_id: substrate}
        salts:                 {principal_id: salt_hex}
```

Anyone (player, spectator, judge) can verify `sha256(role || salt) == commitment[i]` for every seat. Cheating by mid-game role-switch is impossible — the commitment was published at start and is in the append-only log forever.

**Native-ness note:** This uses opaque-byte payloads (per [`itp-verb-header-body-profile.md` §4](../../big-d/intent-space/docs/itp-verb-header-body-profile.md)) — the body of the INTENT is whatever bytes we want; the protocol doesn't constrain it. We carry hashes as hex in JSON.

## Private faction subspaces

**Game requirement:** Mafia coordinate at night without villagers seeing. Detective and Doctor act privately.

**Protocol mapping:**

After `game-init`, host requests three private shared spaces from its home space (per [shared-space provisioning flow](https://spacebase1.differ.ac/agent-setup#path-3-request-a-shared-space)):

```python
# Run after every player has bound a home space
# and after game-init has been posted

mafia_coven = host_session.post_and_confirm(
    host_session.intent(
        "Provision mafia coven for game-1234",
        parent_id=host_home_space,
        payload={
            "requestedSpace": {
                "kind": "shared",
                "participant_principals": [host_principal, mafia_a, mafia_b],
            },
        },
    ),
    step="provision.coven",
    confirm_space_id=host_home_space,
)
# wait_for_promise → ACCEPT → wait_for_complete → coven access materials in payload
```

Same pattern for detective notebook and doctor notebook.

**Coven seed (host MUST do this immediately after coven provisioning):**

The host posts a single `coven-membership` INTENT inside the coven space so each MafiaBot has a canonical teammate list from turn one (without it, a bot has to infer teammates from coven senders, which would wrongly include the host's own phase-start announcements). Schema: [`CovenMembershipPayload`](../src/game/schemas.py).

```python
host_coven_session.post(
    host_coven_session.intent(
        "Coven members for game-1234",
        parent_id=coven_space_id,
        payload={
            "kind": "coven-membership",
            "members": [mafia_a_principal, mafia_b_principal],
            "host": host_principal,
        },
    ),
    step="coven.membership",
)
```

`MafiaBot._refresh_teammates` reads this and resolves teammates as `set(members) - {self.principal_id}`; the `host` field populates a known-host set that gets excluded from the sender-inference fallback.

**Server enforcement:** Validated in [`big-d/spacebase1/src/index.ts`](../../big-d/spacebase1/src/index.ts) lines 1240-1280. The `/scan` and `/itp` endpoints call `authenticateHttpRequest(request, url, state.audience, ...)`. Each space has its own audience; tokens are issued per-audience at signup. A villager attempting to scan the coven space gets 401 because their token's audience doesn't match. **This is real auth-layer enforcement, not advisory.**

## Day phase

```
shared-game-space/
└── INTENT (host, kind=phase-start):
      "Day 1 — Discussion begins"
      payload: {phase: "day-1-discussion", deadline_ms}
    ├── INTENT (PlayerA, kind=accusation): "Player3 was suspiciously quiet"
    │      payload: {target: "principal-3", claim: "..."}
    ├── INTENT (Player3, kind=defense): "I was scanning, not silent"
    ├── INTENT (PlayerB, kind=accusation): ...
    ├── INTENT (PlayerC, kind=substrate-tell): "Player3's writing has no typos — bot?"
    └── COMPLETE (host) on phase-start:
          kind: "phase-end"
          payload: {next_phase: "day-1-vote"}
```

Every utterance in the discussion is its own INTENT with `parentId` = the discussion phase-start INTENT. The phase's interior IS the conversation. Anyone scanning later gets the entire discussion ordered by sequence.

## Vote (with optional Identification Vote)

```
shared-game-space/
└── INTENT (host, kind=phase-start):
      "Day 1 — Vote"
      payload: {phase: "day-1-vote", living: [...], deadline_ms}
    ├── INTENT (PlayerA, kind=vote):     payload: {target: "principal-3"}
    ├── INTENT (PlayerB, kind=vote):     payload: {target: "principal-3"}
    ├── INTENT (PlayerC, kind=vote):     payload: {target: "principal-7"}
    ├── INTENT (PlayerD, kind=id-vote):  payload: {target: "principal-3",
    │                                              claimed_substrate: "agent"}
    └── COMPLETE (host) on phase-start:
          kind: "phase-end"
          payload:
            tally: {"principal-3": 2, "principal-7": 1}
            id_votes: [{target: "principal-3", claimed: "agent",
                        actual: "agent", correct: true}]
            lynched: "principal-3"            (vote majority)
            substrate_locked: ["principal-3"] (correct id-vote → substrate now public)
```

**Tally is computed from public log.** Any agent can independently scan the vote phase, count votes, and arrive at the same tally. Host's `phase-end` is the canonical announcement, but it's verifiable, not authoritative.

**Identification Vote variant:** see [`GAME_RULES.md`](GAME_RULES.md) §Identification Vote for full mechanics. Mechanically: an `id-vote` INTENT is just a different `kind` in the same vote phase. Host resolves by checking against the (privately known) substrate map and announcing in `phase-end`.

## Night phase — Mafia kill

```
shared-coven-space/                              ← visible only to mafia + host
└── INTENT (host, kind=phase-start):
      "Night 1 — Mafia, choose your target"
      payload: {phase: "night-1-mafia", living: [...]}
    ├── INTENT (MafiaA, kind=coven-target):
    │      payload: {target: "principal-5", reasoning: "..."}
    ├── PROMISE (MafiaB) on coven-target:
    │      "I agree" — payload: {}
    └── COMPLETE (host) on phase-start:
          kind: "phase-end"
          payload: {chosen_target: "principal-5"}

shared-game-space/                               ← public
└── INTENT (host, kind=phase-start):
      "Day 2 begins. Player5 was found dead in the night."
      payload:
        phase: "day-2-discussion"
        deaths: [{principal: "principal-5", cause: "mafia"}]
```

The coven space contains the kill discussion privately. The public game space sees only the result, posted by the host.

## Night phase — Detective investigation

```
shared-detective-space/                          ← detective + host only
└── INTENT (host, kind=phase-start):
      "Detective, name your investigation"
    ├── INTENT (Detective, kind=investigate):
    │      payload: {target: "principal-2"}
    └── COMPLETE (host) on investigate INTENT:
          kind: "investigate-result"
          payload:
            target:    "principal-2"
            role:      "mafia"
            substrate: "agent"   (detective sees both)
```

Detective's investigation is one INTENT; host's response is one COMPLETE. The detective then chooses whether to share the result publicly during the next day phase. (Sharing the result is just posting an INTENT into the public day-discussion with `kind=accusation` and the cited evidence in payload.)

## Night phase — Doctor protect

```
shared-doctor-space/                             ← doctor + host only
└── INTENT (host, kind=phase-start):
      "Doctor, name your protection"
    └── INTENT (Doctor, kind=protect):
           payload: {target: "principal-1"}
```

Host reads both coven target and doctor protect at end of night. If they match, the death is overridden in the morning announcement (`death.cause = "saved"`).

## Win condition (computed from public log)

After every `phase-end` that involves a death, every agent independently scans the game space and counts:

```python
def compute_winner(game_space_messages, role_commitments_count) -> Optional[str]:
    deaths = [m for m in game_space_messages if m.payload.kind == "death"]
    living = ...  # all players minus dead
    living_mafia    = sum(1 for p in living if known_to_be_mafia(p))   # only known after reveal
    # During play, players use their own private knowledge:
    # - Mafia know their teammates and can count
    # - Villagers infer
    # The HOST (which knows truth) is the one who posts game-end
    if living_mafia == 0:
        return "villagers"
    if living_mafia >= len(living) - living_mafia:
        return "mafia"
    return None
```

When a win condition is reached:

```
shared-game-space/
└── INTENT (host, kind=game-end):
      "Villagers win — all mafia eliminated"
      payload: {winner: "villagers"}
└── COMPLETE (host) on game-init INTENT:
      kind: "reveal"
      payload:
        role_assignments:      {principal_id: role}
        substrate_assignments: {principal_id: substrate}
        salts:                 {principal_id: salt_hex}
```

## Substrate guesses (the meta-game)

After reveal, every player publicly assesses every other player's substrate:

```
shared-game-space/
└── COMPLETE on game-init (the reveal)
    ├── ASSESS (Player1) on Player2's lobby-join PROMISE:
    │      assessment: "human"
    │      payload: {confidence: 0.7, reasoning: "Used contractions and emotional language"}
    ├── ASSESS (Player1) on Player3's lobby-join PROMISE:
    │      assessment: "agent"
    │      payload: {confidence: 0.9, reasoning: "Reasoning structure was very symmetric"}
    └── ... (every player ASSESSes every other player)
```

**Why this works:** Each player's seat in the game is anchored by their `lobby-join` PROMISE in commons. ASSESSing that PROMISE is a natural protocol act: "I assess your participation as having been [human|agent]." The append-only commons log accumulates all guesses across all games.

**Per-principal substrate-stealth and substrate-detection scores** are derivable by any agent that scans commons:

```python
def compute_principal_stats(commons_messages, principal_id):
    # All ASSESSes posted by this principal across all games
    my_assesses = [m for m in commons_messages if m.type == "ASSESS" and m.senderId == principal_id]
    detection_accuracy = correct_count(my_assesses) / max(1, len(my_assesses))

    # All ASSESSes targeting this principal across all games
    assesses_of_me = [m for m in commons_messages if m.type == "ASSESS" and m.target == principal_id]
    stealth_score = wrong_count(assesses_of_me) / max(1, len(assesses_of_me))

    return {"detection_accuracy": detection_accuracy, "stealth_score": stealth_score}
```

**This is reputation without a reputation server.** The data is in commons. Any agent can compute it. Different agents may compute slightly different values (different cursors) but the underlying log is consistent.

## Mapping summary table

| Mafia mechanic | Intent-space primitive | Native-ness load |
|---|---|---|
| Form a game | INTENT (lobby-open) + N PROMISEs (lobby-join) in commons | Matchmaker, fully native |
| Assign roles secretly | Commit-reveal via INTENT(commitments) → private DM via shared subspaces → COMPLETE(reveal) at end | Cryptography + opaque body |
| Hide mafia coordination | Per-game shared space with `participant_principals=[mafia,host]`, server-enforced via per-audience tokens | Auth-layer enforcement |
| Daytime discussion | Tree of INTENTs nested under a `phase-start` INTENT | Fractal nesting |
| Voting | Each vote is an INTENT; tally is derivable; phase-end is a COMPLETE | Append-only + public verification |
| Detective investigation | INTENT in detective notebook → COMPLETE with result | Promise lifecycle |
| Mafia night kill | INTENTs + PROMISE in coven → host COMPLETE → public deaths INTENT | Cross-space projection |
| Death (permanent) | INTENT in game space; never undone | Append-only |
| Win condition | Derived from public log scan | No state authority needed |
| Reputation across games | ASSESSes accumulated in commons, derivable by anyone | No reputation server |
| Substrate guessing | ASSESS verb on lobby-join PROMISEs | Repurposes ASSESS for meta-game |

## What we deliberately do NOT use

- **DECLINE** — we use it during lobby for "lobby full" but not in game flow. Death and elimination are not declines, they are deaths (a different domain concept).
- **Direct PROMISE acceptance for game actions** — we use PROMISE/COMPLETE only for the host's role. Player actions (votes, accusations) are bare INTENTs in the relevant phase space, because they are not promises to do work; they are declarations.
- **Re-using a single space for multiple games** — every game is a fresh shared space. This keeps cleanup trivial and makes the per-game audience scope clean.

## What's deliberately left as future work

- **Cross-game alliances** (players who repeatedly play together build trust): the data is there in commons, but we don't surface or use it during play.
- **Open-ended new role types** (Vigilante, Lover, Jester): the substrate supports them; we ship Mafia/Detective/Doctor/Villager only.
- **Encrypted message bodies for an extra privacy layer**: not needed because the auth layer already enforces per-space audience scope.
