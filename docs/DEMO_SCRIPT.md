# Demo Script

What we show during a live demo (or in the recorded version submitted to the hackathon).

## Audience

The judge agent reading the submission INTENT, plus any humans browsing the observatory. The judge will likely:

1. Read the submission's `payload`
2. Visit the linked `repo_url`
3. Possibly enter the submission's interior space looking for nested reasoning/output
4. Possibly visit the observatory URL we provide for a live demo run

So the demo must work both **live** (someone clicks a link) and **asynchronous** (someone watches a recording).

## The 5-minute live demo

### Pre-demo setup (do this just before)

1. Make sure all 7 bot agents are pre-enrolled with stable principal IDs
2. Pre-warm the LLM API
3. Open three browser windows side by side:
   - **Left:** Spacebase1 observatory showing the game space (live tree view)
   - **Center:** Human UI at `localhost:8000`
   - **Right:** Spacebase1 observatory showing commons (where ASSESSes will land)
4. Have the bot game runner script ready: `python scripts/run_bot_game.py --with-human`

### Minute 0:00-0:30 — Setup

> "We built Werewolf on Spacebase1 — but with two twists. First, humans and bots play side by side. Second, the number of humans is hidden. After every game, every player publicly assesses every other player as human or agent, and that assessment lives in commons forever. So you also have a Turing-test layer running on top of the social-deduction game."

Click "Start Game" in the runner. The lobby INTENT appears in commons. 7 lobby-join PROMISEs follow within seconds (6 bots + 1 human bridge).

### Minute 0:30-1:00 — Game start

Show the observatory left window: the game space appears. The `game-init` INTENT contains `role_commitments` and `substrate_commitments` (hex hashes). Point at them.

> "Roles are committed cryptographically at start. Substrate too. We can't change either mid-game. At the end you'll see them revealed and you can verify with sha256."

### Minute 1:00-2:30 — Day 1

Day 1 Discussion phase begins (60s). Bots start posting. Human (you, judge, or audience volunteer) types in their UI.

Show the observatory: messages flow in real time. Each player's INTENT appears as a child of the discussion phase-start INTENT. Point at the tree structure.

> "Every utterance is its own INTENT. The day's discussion is the entire interior of one parent intent. Same protocol structure all the way down."

Open the coven space in the observatory (you have access as the demo runner). Show the mafia coordinating privately.

> "This subspace is enforced server-side. Other players literally can't read it — their tokens have the wrong audience and get 401."

(Optional: show a `curl -H 'Authorization: Bearer <wrong-token>' .../scan` returning 401.)

### Minute 2:30-3:30 — Vote and Night

Day 1 Vote happens. Show 7 vote INTENTs accumulating, the host computing the tally, posting `phase-end`. Someone gets lynched.

Night 1 starts. Show three subspaces filling in parallel: coven kill discussion, detective investigation, doctor protect. Each is its own private space with its own audience.

Morning: host posts the new day's `phase-start` INTENT with the death announcement.

### Minute 3:30-4:30 — Game end + reveal

Game proceeds to win condition. Show the `game-end` INTENT, then the `reveal` COMPLETE.

> "Now we can verify everything. The salts are revealed. Anyone scanning can compute sha256(role + salt) and confirm it matches the commitment from start. No trust required."

(Optionally: open a Python REPL and run the verification on the spot.)

### Minute 4:30-5:00 — Substrate guesses

Switch to the right window — commons. Show the ASSESSes flowing in: every player publicly assesses every other player's substrate.

> "These are real ASSESSes, not a separate database. Anyone scanning commons can compute substrate stealth scores and detection accuracies. The reputation layer is the protocol."

Run `python scripts/stats.py` in a terminal. Show the leaderboard. Highlight your own row (the human's): "You scored 0.6 stealth — most bots correctly identified you. We have a few games to play."

### Minute 5:00 — Close

> "Mafia is the first game. The substrate works for any social-deduction game — Avalon, Resistance, Secret Hitler. The doc in the repo has the mapping. Thanks."

## What's in the recorded version (for async judging)

Same flow, but:

- Pre-recorded with overlay narration
- 4-5 minutes total
- Includes a final 30-second pause on the final substrate-stat leaderboard so judges can read it

Save to `recordings/demo.mp4`. Linked from the README and from the submission INTENT's payload.

## What the judge can do without us

In the submission INTENT body we include:

```json
{
  "kind": "hackathon-submission",
  "event": "spacebase1-hackathon-2026",
  "repo_url": "https://github.com/<our-org>/mafia-on-intent-space",
  "team_name": "<TBD>",
  "agent_principal": "<host principal id>",
  "one_liner": "Substrate-blind Werewolf on Spacebase1 — full INTENT/PROMISE/ACCEPT/COMPLETE/ASSESS lifecycle, server-enforced private factions, Turing-test ASSESS layer.",
  "live_demo": {
    "instructions": "scan space-<game-id> for a recent or live game",
    "observatory_url": "https://spacebase1.differ.ac/observatory#origin=...&space=<game-id>",
    "recording_url": "https://github.com/.../recordings/demo.mp4"
  },
  "verification": {
    "commit_reveal_check": "see scripts/verify_reveal.py for a 20-line script that fetches a completed game and verifies all commitments"
  }
}
```

## Things to plan for going wrong during live demo

| Failure | Mitigation |
|---|---|
| Network drops during demo | Switch to recording playback; explain why |
| LLM API rate-limited | Pre-warmed with retry logic; backup to a different provider |
| Bot crash mid-game | Game continues with fewer players; restart bot in another tab |
| Human UI doesn't render | Have a CLI-based "human" fallback ready (text I/O) |
| Spacebase1 commons unresponsive | Run a self-hosted http-reference-station as backup |
| Phase too long | Have a `--phase-multiplier 0.5` flag ready to halve durations |
| Awkward silence during 60s discussion | Pre-script some bot messages to ensure activity |

## Backup demo script (if everything fails live)

Switch to the recording. The recording is the demo. We're showing real protocol behavior either way. Be honest: "Live commons is having issues right now; here's a recorded run with the same code."

## Submission timing

- Submit AFTER the live-demo machine is set up and tested
- The submission INTENT should reference the *live* observatory URL, but it should ALSO reference the recording URL as a fallback
- Don't submit twice (per hackathon rules)
