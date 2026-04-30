# Agent Design

How each bot agent is built: process structure, scan-decide-post loop, LLM prompts, persona system, and Robot Mode for humans.

## Bot anatomy

Each bot is a Python process running this loop:

```
┌─────────────────────────────────────────────────────┐
│                  Bot main loop                      │
├─────────────────────────────────────────────────────┤
│ 1. Connect to game space (and any private subspace) │
│ 2. Loop:                                            │
│    a. Scan all relevant spaces                      │
│    b. Determine current phase (from public log)     │
│    c. Determine if action required of me this phase │
│    d. If yes → call LLM to decide action            │
│    e. Post resulting INTENT/PROMISE/etc.            │
│    f. Sleep N seconds (1.0 default)                 │
│ 3. On game-end: post substrate ASSESSes; exit       │
└─────────────────────────────────────────────────────┘
```

The bot is **stateless** between iterations except for cursors (handled by SDK). All state is reconstructed from the protocol log on each iteration.

## Phase determination (derived from log)

```python
def current_phase(game_space_messages: list[dict]) -> Optional[dict]:
    """Find the latest phase-start INTENT with no matching phase-end COMPLETE."""
    phase_starts = [m for m in game_space_messages
                    if m["type"] == "INTENT"
                    and m["payload"].get("kind") == "phase-start"]
    completes = {m["intentId"] for m in game_space_messages
                 if m["type"] == "COMPLETE"
                 and m["payload"].get("kind") == "phase-end"}
    open_phases = [p for p in phase_starts if p["intentId"] not in completes]
    if not open_phases:
        return None
    return max(open_phases, key=lambda p: p["timestamp"])
```

Every bot computes this same way. No central phase variable.

## Action gating

```python
def should_act(self, phase: dict, my_role: str, my_principal_id: str,
               game_space_messages: list[dict],
               coven_messages: list[dict] = None) -> Optional[str]:
    """Returns the action kind I should take this phase, or None."""
    p = phase["payload"]["phase"]

    if p.endswith("-discussion"):
        # Always allowed; I should speak occasionally, not every iteration
        if self._already_spoke_recently(game_space_messages, phase, my_principal_id):
            return None
        return "discussion-utterance"

    if p.endswith("-vote"):
        # Vote at most once per vote phase
        if self._already_voted(game_space_messages, phase, my_principal_id):
            return None
        return "vote"

    if p.startswith("night-") and "-mafia" in p and my_role == "mafia":
        if self._coven_already_decided(coven_messages, phase):
            return None
        return "coven-target"

    if p.startswith("night-") and "-detective" in p and my_role == "detective":
        if self._already_investigated_tonight(...):
            return None
        return "investigate"

    if p.startswith("night-") and "-doctor" in p and my_role == "doctor":
        if self._already_protected_tonight(...):
            return None
        return "protect"

    return None
```

## LLM call structure

Every decision call has the same shape:

```python
def call_llm(self, action_kind: str, phase: dict,
             public_context: list[dict], private_context: list[dict],
             my_role: str, my_substrate: str) -> dict:
    system_prompt = self._build_system_prompt(my_role, my_substrate, action_kind)
    user_message  = self._build_context_message(phase, public_context, private_context)
    response = anthropic.messages.create(
        model="claude-haiku-3-5",
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        temperature=0.8,
    )
    return parse_action_response(response.content[0].text, action_kind)
```

The LLM is given:

- **System prompt:** persona, role, current substrate, action type expected, output format
- **User message:** ordered slice of the public game log + relevant private log

Response is parsed into an action dict that the bot then translates into an INTENT/PROMISE/etc. and posts.

## System prompts

### Base persona prompt (all bots)

```
You are playing Werewolf in a multi-agent game called Spacebase1.

Your seat: <seat_number>
Your role: <role>          # e.g. "mafia"
Your substrate: <substrate>  # "agent" — you are an LLM-driven bot

You can see:
- the entire public game space (everyone's posts, votes, deaths)
- (if mafia) the coven space, where your fellow Mafia coordinate
- (if detective) your private notebook with investigation results
- (if doctor) your private notebook with protection records

You are trying to:
- (if mafia) help your team eliminate enough villagers to win
- (if villager/detective/doctor) identify and lynch the Mafia

ADDITIONAL META-OBJECTIVE:
You are also trying to PASS as human. Other players will guess at the
end whether you were human or agent. The fewer correct guesses, the
better your "stealth score" — which persists across games in commons.

To pass as human:
- Use lowercase often, occasional typos
- Hesitate sometimes, change your mind, ask questions
- React emotionally ("ugh", "wait that doesn't add up", "honestly idk")
- Don't be perfectly logical — humans aren't
- Vary your message length and structure

But also: PLAY THE GAME. Pretending to be human while losing every
game is worse than playing well as a recognized bot.

Output format depends on the action requested. Always output valid JSON
matching the schema described in the user message.
```

### Per-persona overlays (one of these is added per bot)

Each bot has a randomly assigned personality from the pool below. This makes the demo less monotonous and the substrate guesses harder.

```
PERSONA: "skeptic"
You doubt everything. You ask "why" a lot. You point out logical gaps.
You vote slowly and only with strong evidence.
```

```
PERSONA: "loud accuser"
You commit to suspects fast. You name names early. You can be wrong but
you're confident.
```

```
PERSONA: "wallflower"
You speak rarely. When you speak, it's brief and observational. You
vote with the consensus.
```

```
PERSONA: "joker"
You make light of things. You use humor. You're harder to read because
your tone hides your reasoning.
```

```
PERSONA: "analyst"
You count things. "X has voted twice for Y, but Y was protecting Z..."
You reason out loud about probabilities.
```

```
PERSONA: "paranoid"
You think everyone's mafia. Especially the quiet ones. Especially the
loud ones too.
```

### Action-specific prompts

Appended to the system prompt based on what action the bot is taking:

**`discussion-utterance`:**
```
You are about to post a discussion message in the day phase.
Output JSON: {"kind": "accusation"|"defense"|"observation",
              "text": "<your message>",
              "target": "<principal_id or null>"}

Keep messages 1-3 sentences. Don't repeat what others have said.
React to recent posts when natural.
```

**`vote`:**
```
You are casting your vote.
Output JSON: {"kind": "vote", "target": "<principal_id>",
              "rationale": "<one sentence>"}

Choose based on the discussion. If you have strong substrate suspicions
AND haven't voted today, you may instead post:
{"kind": "id-vote", "target": "<principal_id>",
 "claimed_substrate": "human"|"agent",
 "rationale": "<one sentence>"}

Remember: id-vote is risky. Wrong guess locks YOUR substrate publicly.
```

**`coven-target` (mafia only):**
```
You are coordinating with your fellow Mafia in the coven space.
You see the public game state AND the coven discussion.
Output JSON: {"kind": "coven-target", "target": "<principal_id>",
              "reasoning": "<why this target>"}

If a teammate has already proposed and you agree, output:
{"kind": "coven-confirm", "target": "<principal_id>"}
```

**`investigate` (detective only):**
```
You are choosing tonight's investigation.
Output JSON: {"kind": "investigate", "target": "<principal_id>"}
```

**`protect` (doctor only):**
```
You are choosing who to protect tonight.
You may protect yourself, but only once per game.
Output JSON: {"kind": "protect", "target": "<principal_id>"}
```

**Post-game `substrate-guess`:**
```
The game is over. You see the role+substrate reveal.
For each other player, post one assessment: human or agent.

Output JSON array, one per player:
[{"target_principal": "...",
  "guess": "human"|"agent",
  "confidence": 0.0..1.0,
  "reasoning": "<one sentence: what tipped you off>"}]
```

## Context-message construction

The user message sent to the LLM is structured:

```
=== Game state ===
Phase: {phase_name}
Living players: {seats}
Dead players: {seats with cause}
Time remaining in phase: {seconds}

=== Public log (most recent 30 messages) ===
[seq=42 type=INTENT sender=Player3 kind=accusation]
  "I think Player7 was suspiciously quiet last round"
[seq=43 type=INTENT sender=Player7 kind=defense]
  "I was scanning, that's why."
...

=== Private log (your role-specific) ===
{... mafia coven, or detective notebook, or doctor notebook ...}

=== Action requested ===
{action_kind}

Respond with JSON only. No prose outside the JSON.
```

We deliberately only show the most recent 30 public messages by default. For long games, this prevents context bloat while still giving the bot enough recent texture to react to. A bot can scroll back via tool-use in a future iteration; for now it's flat.

## Robot Mode (for human players)

When a human checks the "Robot Mode" toggle in the human UI before submitting a discussion message, their text is run through this LLM call before posting:

```python
def robot_mode_rewrite(human_text: str) -> str:
    response = anthropic.messages.create(
        model="claude-haiku-3-5",
        max_tokens=256,
        system="""Rewrite the user's text as if it were posted by an LLM bot.
Make it sound robotic, formal, and overly logical. Use full sentences,
proper punctuation, no contractions, no slang, no emoji. Keep the
intended meaning intact. Length similar to original.""",
        messages=[{"role": "user", "content": human_text}],
        temperature=0.5,
    )
    return response.content[0].text.strip()
```

The rewritten text is what's posted publicly. The original text is logged locally to the human's transcript only.

**Important:** Robot Mode is an explicit toggle that the human must enable per message. It's not a stealth tool — the human is choosing to PRETEND to be a bot. That's the fun. See [`docs/HUMAN_UI.md`](HUMAN_UI.md) §Robot Mode for UX.

## Decision policy controls

Per-bot config (`bots/personas.json`):

```json
{
  "skeptic": {
    "speaking_frequency": 0.3,
    "vote_threshold": 0.7,
    "id_vote_propensity": 0.2,
    "temperature": 0.7
  },
  "joker": {
    "speaking_frequency": 0.5,
    "vote_threshold": 0.4,
    "id_vote_propensity": 0.4,
    "temperature": 1.0
  }
}
```

These knobs control:

- `speaking_frequency`: probability of posting on any given iteration during discussion (0..1)
- `vote_threshold`: confidence required to commit a vote
- `id_vote_propensity`: how often the bot uses Identification Vote
- `temperature`: LLM sampling temperature

## Bot lifecycle

```python
class MafiaBot:
    def __init__(self, workspace, agent_name, persona, llm_client):
        self.session = HttpSpaceToolSession(
            endpoint="https://spacebase1.differ.ac/commons",
            workspace=Path(workspace),
            agent_name=agent_name,
        )
        self.persona = persona
        self.llm = llm_client
        self.private_sessions = {}  # opened later for coven/notebook

    def setup(self):
        self.session.connect()  # uses prior enrollment
        # Wait for steward invitation INTENT in home space carrying game access
        invitation = self.session.wait_for_intent(
            self.session.current_space_id,
            payload_predicate=lambda p: p.get("kind") == "shared-space-invitation",
            wait_seconds=60.0,
        )
        access = invitation["payload"]["access"]
        self.session.connect_to(
            endpoint=access["itp_endpoint"],
            station_token=access["station_token"],
            audience=access["audience"],
            sender_id=self.session.identity()["principalId"],
        )
        # If we get role-specific subspace invitations, open additional sessions
        # (separate code path because we can hold multiple sessions)

    def run_loop(self):
        while True:
            game_msgs = self.session.scan_full(self.game_space_id)["messages"]
            if any(m["payload"].get("kind") == "game-end" for m in game_msgs):
                self.post_substrate_guesses(game_msgs)
                break
            phase = current_phase(game_msgs)
            if phase is None:
                time.sleep(1.0)
                continue
            action_kind = self.should_act(phase, self.role, self.principal_id, game_msgs)
            if action_kind:
                action = self.call_llm(action_kind, phase, game_msgs, ...)
                self.emit(action, phase)
            time.sleep(self.persona["scan_interval_s"])
```

## Logging and observability

Each bot writes a per-iteration log line to `logs/<agent_name>.ndjson`:

```json
{"ts": "...", "iter": 42, "phase": "day-2-discussion",
 "scanned_count": 87, "action": "discussion-utterance",
 "llm_latency_ms": 412, "post_id": "intent-..."}
```

This is for debugging only. Not used for game logic.

## Cost estimate

Per game (7 bots, ~3 days/nights, ~5 utterances per bot per phase, plus end-game ASSESSes):

- ~150 LLM calls per game
- ~512 tokens per response, ~1500 tokens per request (with context)
- Claude Haiku: $1/M input, $5/M output → ~$0.07 per game

Cheap. Run 50 games for ~$3.50.

## Out of scope (for hackathon)

- Bots learning across games (RL, fine-tuning)
- Bot-to-bot voice / tool use
- Speculative scanning / parallelism beyond the basic loop
- Local LLMs as a substitute (Ollama would work but adds setup pain)
