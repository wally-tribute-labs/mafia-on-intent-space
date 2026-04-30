# Human UI

The local web interface a human player uses to take their seat in a game. Built with FastAPI + HTMX (no React, no build step, fast to develop, easy to demo).

Companion to [`AGENT_DESIGN.md`](AGENT_DESIGN.md). The human's agent is *just a bridge* — it scans the protocol like any other bot, but instead of calling an LLM to decide, it asks the human via the UI.

## Why FastAPI + HTMX

- Single Python process; no Node toolchain to maintain
- HTMX gives reactive UI without JavaScript framework complexity
- Server-side rendering keeps state simple — the bridge process holds it
- Trivial demo path: `python -m mafia_human_ui` → `localhost:8000` → done

Rejected alternatives:

- **Streamlit:** auto-reruns are a fight for live-updating game state
- **React + FastAPI:** overkill, slower to iterate, more demo failure modes
- **CLI:** loses substrate-mimicry vibe, harder for a judge to engage with during demo
- **Jupyter widgets:** would work but rendering bugs in panel display under stress

## Page layout

A single page at `localhost:<port>` with three regions:

```
╔═══════════════════════════════════════════════════════════╗
║  Mafia Game #abc123   ·   Day 2 — Discussion   ·   0:42  ║
║  You are: Player 5 (role: VILLAGER)                       ║
╠═══════════════════════════════════════════════════════════╣
║                                                           ║
║  ┌── PUBLIC FEED ─────────────────┐ ┌── PRIVATE ───────┐  ║
║  │  [Player3]: I trust Player5    │ │ (no private feed │  ║
║  │  [Player7]: doubt that         │ │  for villagers)  │  ║
║  │  [Player2]: focus on Player4   │ │                  │  ║
║  │  [Player5 (you)]: I was scan.. │ │                  │  ║
║  │  [Player6]: anyone else find...│ │                  │  ║
║  │  ...                           │ │                  │  ║
║  └────────────────────────────────┘ └──────────────────┘  ║
║                                                           ║
╠═══════════════════════════════════════════════════════════╣
║  ACTION                                                   ║
║  Phase: Day Discussion                                    ║
║                                                           ║
║  Speak (free text):                                       ║
║  ┌─────────────────────────────────────────────────────┐  ║
║  │ I think Player2 keeps deflecting...                 │  ║
║  └─────────────────────────────────────────────────────┘  ║
║  ☐ Robot Mode (rewrite as bot)         [Send]            ║
║                                                           ║
║  Quick actions:                                           ║
║  [Accuse Player3] [Accuse Player7] [Defend self]         ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
```

### Header

- Game ID (linkable to observatory)
- Current phase + countdown timer
- Your seat number and role (private)
- Status indicators: connected/disconnected, scanning, last-post-time

### Public feed

- Auto-scrolling list of all public game-space messages
- Color-coded by message kind: discussion (gray), accusation (orange), vote (red), death (black), phase-end (blue)
- Each row shows: sender (anonymized seat number, e.g. "Player3"), kind, content
- Updates via HTMX polling (`hx-trigger="every 2s"`) or Server-Sent Events from the bridge
- Last 50 messages shown; full history accessible via "show all" link

### Private feed (role-conditional)

- Hidden for villagers
- Shows mafia coven / detective notebook / doctor notebook for those roles
- Same rendering as public feed, just a different source space
- Indicator: "PRIVATE — visible only to you and your role-mates"

### Action panel (phase-conditional)

The action panel changes based on the current phase and your role. The bridge process determines what's possible and renders the right form.

**Day Discussion (any role):**

- Free-text textarea + Send button
- Robot Mode checkbox (default: off)
- Quick-action buttons: "Accuse <PlayerN>", "Defend self" — pre-fill the textarea
- "Pass" button — does nothing, just acknowledges you don't want to speak

**Day Vote (any living player):**

- "Vote to lynch" radio buttons (one per living player)
- "Identification Vote" form (collapsible):
  - Target dropdown
  - Substrate radio: human / agent
  - Submit (with warning about wrong-guess penalty)
- "Abstain" button

**Night Mafia (mafia only):**

- "Choose target" radio buttons (one per living non-mafia player)
- "Confirm teammate's choice" — appears if a coven-target was already proposed; one click to PROMISE
- Discussion textarea for in-coven chat (private)

**Night Detective (detective only):**

- "Investigate" target dropdown
- Submit
- Past investigation results list (from prior nights)

**Night Doctor (doctor only):**

- "Protect" target dropdown
- Self-protect toggle (one-time use indicator)
- Submit
- Past protection log

**Night Villager:**

- "Sleep" button (does nothing, just acknowledges)
- Spectate-only public feed continues to scroll

**Game End (everyone):**

- Reveal banner: roles + substrates of all players
- Substrate-Guess form: one row per other player, dropdown {human, agent}, confidence slider, reasoning textarea
- Submit all (posts ASSESSes in batch)

## Robot Mode UX

When the human checks the "Robot Mode" checkbox before clicking Send:

1. Their text is sent to a local endpoint `/rewrite_robot` on the bridge process
2. Bridge calls Claude Haiku with the rewrite prompt (see [`AGENT_DESIGN.md`](AGENT_DESIGN.md) §Robot Mode)
3. The rewritten text is shown to the user with: "Posting as: <rewritten>" and a Confirm/Cancel
4. On Confirm, the rewritten text is posted as the human's INTENT
5. The original text is logged locally to `logs/human-<seat>.ndjson` for the user's records (not posted)

**Why a confirm step:** the human should see what's about to be posted publicly, not be surprised by the rewrite.

**Default off:** the toggle resets to off after each post. Robot Mode is per-message, not per-game. This makes it a deliberate strategic choice each time, not a passive style.

## Bridge process architecture

```
┌─────────────────────────────────────────────────────────────┐
│  python -m mafia_human_ui --workspace .intent-space-human   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Bridge process                                     │    │
│  │  - HttpSpaceToolSession (already-enrolled)          │    │
│  │  - Scan loop polling game space + private spaces    │    │
│  │  - Internal queue of pending actions                │    │
│  │  - FastAPI HTTP server on localhost:8000            │    │
│  │     - GET /             → render page               │    │
│  │     - GET /feed/public  → return latest messages    │    │
│  │     - GET /feed/private → return latest messages    │    │
│  │     - POST /post        → take action from form     │    │
│  │     - POST /rewrite_robot → call LLM rewrite        │    │
│  │     - GET /events       → SSE stream (optional)     │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
       ▲                                      │
       │ user opens browser                   │
       │                                      ▼
   localhost:8000                    spacebase1.differ.ac
```

The bridge does NOT use an LLM for game decisions (that's the human's job). It only uses LLM for the optional Robot Mode rewrite.

## File structure

```
src/human/
├── bridge.py        # main entrypoint; FastAPI app + scan loop
├── templates/
│   ├── page.html    # main page (HTMX-driven)
│   ├── feed.html    # partial template for feed updates
│   └── action.html  # partial template for action panel
├── static/
│   └── style.css    # minimal styling, dark mode
└── llm.py           # Robot Mode rewrite call
```

## Run

```bash
# Prereq: human's agent is enrolled and joined the game lobby
cd mafia-on-intent-space
python -m src.human.bridge \
  --workspace .intent-space-human \
  --game-space-id space-abc123 \
  --port 8000
```

Then open `http://localhost:8000` in a browser. The page renders, scan loop starts, action panel reflects current phase.

## Multi-human play

Each human runs their own bridge in their own workspace on a different port. They can be on different machines (each enrolls separately, each gets invited to the game space when the host requests the shared space with their principal_id in the participant list).

Demo configuration: 1 human + 6 bots on one laptop, all in separate processes, all separate workspaces. Default ports:

- Bridge: 8000
- Observatory dashboard: 8080 (default for `dashboard/launch.py`)

## Demo polish

- Dark theme by default (looks better in projector lighting)
- Keystroke ergonomics: Cmd+Enter sends the current action, R toggles Robot Mode
- Deadline countdown is visually prominent (red < 10s)
- "Phase changed" toast notification when a new phase starts
- Sound effect on death announcement (gentle, optional)

## Out of scope

- User authentication (one bridge instance per human, identified by workspace)
- Mobile responsiveness (desktop demo only)
- Game replay viewer (the observatory is enough)
- Customizable themes
