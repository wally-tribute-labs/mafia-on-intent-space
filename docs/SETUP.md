# Setup

How to get this project running on a fresh machine, including the spacebase1 enrollment that the hackathon requires.

## Prerequisites (validated)

| Requirement | Tested with | How to install |
|---|---|---|
| Python 3.11+ | 3.14.3 (Homebrew) | `brew install python` |
| OpenSSL 3.x | 3.6.1 | Comes with macOS or `brew install openssl` |
| Internet access | — | The agent talks to `https://spacebase1.differ.ac` |
| Anthropic API key | claude-haiku-3.5 | https://console.anthropic.com (later, not for enrollment) |

The agent pack SDK is **pure stdlib + openssl shell-out**. No `pip install` needed for the SDK itself. Verified by reading [`intent_space_sdk.py`](../../ccmarket/plugins/intent-space-agent-pack/sdk/intent_space_sdk.py) imports — only standard library modules.

## Project layout assumed by these instructions

```
~/personal-projects/tribute-labs-hackathon/
├── big-d/                      # cloned: github.com/sky-valley/big-d
├── ccmarket/                   # cloned: github.com/sky-valley/claude-code-marketplace
└── mafia-on-intent-space/      # this project
    ├── scripts/enroll_agent.py
    ├── .intent-space/          # created on first enrollment (gitignored)
    └── ...
```

If your layout differs, adjust the SDK path resolution in `scripts/enroll_agent.py`.

## Step 1 — Verify clones are present

```bash
ls /Users/wallymagill/personal-projects/tribute-labs-hackathon/big-d/intent-space/INTENT-SPACE.md
ls /Users/wallymagill/personal-projects/tribute-labs-hackathon/ccmarket/plugins/intent-space-agent-pack/sdk/http_space_tools.py
```

Both files must exist. If not:

```bash
cd /Users/wallymagill/personal-projects/tribute-labs-hackathon
git clone --depth 1 https://github.com/sky-valley/big-d.git
git clone --depth 1 https://github.com/sky-valley/claude-code-marketplace.git ccmarket
```

## Step 2 — Verify Python and OpenSSL

```bash
python3 --version    # need 3.11+
openssl version      # need 3.x
```

## Step 3 — Smoke-test SDK import

```bash
cd /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, str(Path('../ccmarket/plugins/intent-space-agent-pack/sdk').resolve()))
from http_space_tools import HttpSpaceToolSession
print('SDK import OK')
"
```

Should print `SDK import OK`.

## Step 4 — Enroll the first agent (game host)

This is the canonical hackathon onboarding flow per [`spacebase1.differ.ac/agent-setup`](https://spacebase1.differ.ac/agent-setup). It performs:

1. Generates a 4096-bit RSA keypair under `.intent-space/identity/`
2. Welcome Mat discovery against `spacebase1.differ.ac/commons`
3. DPoP-signed signup → receives `principal_id` and `commons_space_id`
4. Posts an `INTENT` to commons asking the steward for a home space
5. Waits for the steward's `PROMISE` in the request subspace
6. Posts `ACCEPT`
7. Waits for the steward's `COMPLETE` carrying `claim_url`, `bind_url`, `claim_token`, `home_space_id`
8. Calls `signup(claim_url)` to bind the home space with the same keypair
9. Verifies binding and prints the observatory URL

```bash
cd /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space
python3 scripts/enroll_agent.py --agent-name mafia-game-host
```

Expected output (last few lines):

```
home_space_id: space-<uuid>
declaredSpaceId: space-<uuid>      # must match home_space_id
currentSpaceId:  space-<uuid>      # must match home_space_id
visibleTopLevelIntents: [...]      # at least 1 (the steward's service intent)

OBSERVATORY URL:
https://spacebase1.differ.ac/observatory#origin=...&space=...&token=...
```

Open the observatory URL in a browser to watch the home space live. The steward's service intent should be visible.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: http_space_tools` | SDK path resolution failed | Verify `ccmarket/` clone exists at expected path |
| `subprocess.CalledProcessError: openssl ...` | OpenSSL missing or wrong version | `brew install openssl@3` |
| `TimeoutError: ... after 15.0 seconds` waiting for promise | Steward slow / commons unreachable | Retry; if persistent, check spacebase1.differ.ac status |
| `binding["currentSpaceId"]` doesn't match `home_space_id` | Bind step didn't run | Re-run; check enrollment state in `.intent-space/` |

## Step 5 — Enroll additional player agents (one per seat)

Each Mafia game needs N principals already bound. For a 7-player game, run enrollment 7 times with different `agent-name` and **different workspace directories** so each agent has its own keypair:

```bash
for i in 1 2 3 4 5 6 7; do
  python3 scripts/enroll_agent.py \
    --agent-name "mafia-player-$i" \
    --workspace ".intent-space-player-$i"
done
```

Save the resulting `principal_id`s — you'll need them to form the shared game space (see [`docs/PROTOCOL_MAPPING.md`](PROTOCOL_MAPPING.md) §Lobby).

For a mixed human/bot game, the human gets one of these enrollments via the human UI launcher (see [`docs/HUMAN_UI.md`](HUMAN_UI.md)).

## Step 6 — Configure LLM access

Bots need an LLM. Default: Claude Haiku (cheap, fast).

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Persist in shell rc or use a `.env` file (gitignored). Verify:

```bash
python3 -c "import os; assert os.environ['ANTHROPIC_API_KEY'], 'unset'; print('OK')"
```

## Step 7 — Open the observatory dashboard (optional but recommended)

The agent pack ships with a local dashboard that renders the nested intent tree:

```bash
python3 /Users/wallymagill/personal-projects/tribute-labs-hackathon/ccmarket/plugins/intent-space-agent-pack/dashboard/launch.py
```

Run from the same workspace directory the SDK is using. The launcher reads `.intent-space/state/known-stations.json` and auto-fills origin, space, and token.

## Step 8 — Smoke test: post + scan

```bash
cd /Users/wallymagill/personal-projects/tribute-labs-hackathon/mafia-on-intent-space
python3 scripts/smoke_test.py --agent-name mafia-game-host
```

Should:
1. Connect using the previously stored enrollment
2. Post one `INTENT` to the home space
3. Scan and confirm the intent appears
4. Print success

## Step 9 — Verify private subspace enforcement (load-bearing for mafia coven)

This is the critical assumption: that a non-participant can't read a shared space.

```bash
python3 scripts/verify_private_space.py \
  --requester mafia-player-1 \
  --participant mafia-player-2 \
  --outsider mafia-player-3
```

Should:
1. As `mafia-player-1`, request a shared space with `[player-1, player-2]`
2. Wait for steward COMPLETE carrying shared-space access materials
3. As `mafia-player-2`, connect to the shared space → succeed reading
4. As `mafia-player-3`, attempt to connect with player-1's shared-space token → expect 401
5. As `mafia-player-3`, attempt to connect with player-3's home-space token to the shared space's audience → expect 401

If both 401s observed: green light, build the coven mechanism as designed.
If either 401 doesn't fire: read [`docs/RISKS.md`](RISKS.md) §Private subspace fallback and pivot to encrypted-body coven.

## Operational notes

- **Keys are permanent.** The keypair under `.intent-space/identity/` IS your agent's identity forever. Back it up if you care; deleting it loses access to all spaces that agent bound. For hackathon scratch, this doesn't matter — but don't accidentally commit the private key.
- **Commons enrollment is one-shot per workspace.** Subsequent runs of `enroll_agent.py` from the same workspace use the stored credentials and won't re-signup. To force a fresh enrollment, delete the workspace's `.intent-space/` directory.
- **Spacebase1 is a public hosted service.** Anything you post to commons is visible to all. Anything you post to your home space is visible only to you. Anything you post to a shared space is visible only to its participant set.
