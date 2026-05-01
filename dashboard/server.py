"""Mafia on Intent-Space -- Live Dashboard Backend.

FastAPI server that:
  1. Reads host-spaces.json to discover game topology and role assignments.
  2. Reads host-events.ndjson for game structure (phases, deaths, outcomes).
  3. Reads bot *.ndjson logs for all player actions (discussions, votes,
     night actions with reasoning).
  4. Serves a static React dashboard at /.
  5. Exposes /api/game for the frontend.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOST_WORKSPACE = PROJECT_ROOT / ".intent-space-mafia-game-host"
HOST_SPACES_PATH = HOST_WORKSPACE / "host-spaces.json"
LOGS_DIR = PROJECT_ROOT / "logs"

app = FastAPI(title="Mafia Dashboard")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _short(pid: str) -> str:
    return pid[-8:] if len(pid) > 8 else pid


def _load_host_spaces() -> dict[str, Any]:
    if not HOST_SPACES_PATH.exists():
        return {}
    return json.loads(HOST_SPACES_PATH.read_text())


def _resolve_principal_map() -> dict[str, str]:
    """Map bot workspace slot name -> principal_id from enrollment files."""
    mapping: dict[str, str] = {}
    for ws_dir in PROJECT_ROOT.iterdir():
        if not ws_dir.name.startswith(".intent-space-mafia-player-"):
            continue
        enrollment = ws_dir / ".intent-space" / "state" / "enrollment.json"
        if enrollment.exists():
            try:
                data = json.loads(enrollment.read_text())
                pid = data.get("principal_id") or data.get("principalId", "")
                slot = ws_dir.name.replace(".intent-space-", "")
                mapping[slot] = pid
            except Exception:
                pass
    return mapping


def _load_host_events() -> list[dict]:
    """Read host-events.ndjson for game structure events."""
    path = LOGS_DIR / "host-events.ndjson"
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        for line in path.open():
            line = line.strip()
            if line:
                events.append(json.loads(line))
    except Exception as exc:
        log.warning("error reading host-events.ndjson: %s", exc)
    return events


def _load_bot_actions(principal_map: dict[str, str]) -> list[dict]:
    """Read all bot ndjson logs for player actions."""
    actions: list[dict] = []
    if not LOGS_DIR.exists():
        return actions

    for ndjson in sorted(LOGS_DIR.glob("mafia-player-*.ndjson")):
        slot = ndjson.stem
        pid = principal_map.get(slot, "")
        try:
            for line in ndjson.open():
                entry = json.loads(line.strip())
                action = entry.get("action")
                if not action or action == "game-end":
                    continue
                actions.append({
                    "ts": entry.get("ts", 0),
                    "action": action,
                    "phase": entry.get("phase", ""),
                    "sender": pid,
                    "sender_short": _short(pid),
                    "target": entry.get("target", ""),
                    "target_short": _short(entry["target"]) if isinstance(entry.get("target"), str) and entry.get("target") else "",
                    "content": entry.get("content", ""),
                    "reasoning": entry.get("reasoning", ""),
                    "post_id": entry.get("post_id", ""),
                    "bot_slot": slot,
                })
        except Exception as exc:
            log.warning("error reading %s: %s", ndjson, exc)

    actions.sort(key=lambda a: a.get("ts", 0))
    return actions


def _build_phases(host_events: list[dict]) -> list[dict]:
    """Extract phase timeline from host events."""
    phases: list[dict] = []
    for ev in host_events:
        if ev["kind"] == "phase-start":
            phase_name = ev.get("phase", "")
            space_id = ev.get("space_id", "")
            if phase_name.endswith("-mafia"):
                space_label = "coven"
            elif phase_name.endswith("-detective"):
                space_label = "detective-notebook"
            elif phase_name.endswith("-doctor"):
                space_label = "doctor-notebook"
            elif "discussion" in phase_name or "vote" in phase_name:
                space_label = "game-space"
            else:
                space_label = space_id
            phases.append({
                "phase": phase_name,
                "ts": ev.get("ts", 0),
                "living": ev.get("living", []),
                "space_label": space_label,
            })
        elif ev["kind"] == "phase-end":
            tally = ev.get("tally")
            lynched = ev.get("lynched")
            if tally or lynched:
                phases.append({
                    "phase": "vote-result",
                    "ts": ev.get("ts", 0),
                    "tally": tally,
                    "lynched": lynched,
                })
    return phases


def _build_deaths(host_events: list[dict]) -> list[dict]:
    """Extract death events from host events."""
    deaths: list[dict] = []
    for ev in host_events:
        if ev["kind"] == "death":
            deaths.append({
                "target": ev.get("target", ""),
                "target_short": _short(ev.get("target", "")),
                "cause": ev.get("cause", ""),
                "ts": ev.get("ts", 0),
            })
    return deaths


def _build_game_outcome(host_events: list[dict]) -> dict | None:
    """Extract game-end from host events."""
    for ev in reversed(host_events):
        if ev["kind"] == "game-end":
            return {"winner": ev.get("winner", ""), "ts": ev.get("ts", 0)}
    return None


def _group_messages_by_phase(
    bot_actions: list[dict],
    phases: list[dict],
    deaths: list[dict],
) -> list[dict]:
    """Build a chronological message feed grouped by phase.

    Each phase dict gets a 'messages' list containing the bot actions
    that occurred during that phase, plus any deaths.
    """
    timeline: list[dict] = []

    phase_starts = [p for p in phases if p.get("phase") and p["phase"] != "vote-result"]
    vote_results = {p.get("ts"): p for p in phases if p["phase"] == "vote-result"}

    for i, phase in enumerate(phase_starts):
        phase_name = phase["phase"]
        phase_ts = phase.get("ts", 0)
        next_ts = phase_starts[i + 1]["ts"] if i + 1 < len(phase_starts) else float("inf")

        phase_msgs = [
            a for a in bot_actions
            if a.get("phase") == phase_name
            or (phase_ts <= a.get("ts", 0) < next_ts and not a.get("phase"))
        ]

        phase_deaths = [d for d in deaths if phase_ts <= d.get("ts", 0) < next_ts]

        vr = None
        for ts, result in vote_results.items():
            if phase_ts <= ts < next_ts:
                vr = result
                break

        timeline.append({
            "phase": phase_name,
            "ts": phase_ts,
            "living": phase.get("living", []),
            "messages": phase_msgs,
            "deaths": phase_deaths,
            "vote_result": vr,
        })

    return timeline


@app.get("/api/game")
def get_game():
    host_data = _load_host_spaces()
    if not host_data:
        return {
            "status": "waiting",
            "message": "Waiting for game to start...",
            "players": {},
            "timeline": [],
            "deaths": [],
            "outcome": None,
            "spaces_meta": [],
        }

    role_truth = host_data.get("role_truth", {})
    players: dict[str, dict] = {}
    for pid, role in role_truth.items():
        players[pid] = {
            "principal_id": pid,
            "short": _short(pid),
            "role": role,
        }

    principal_map = _resolve_principal_map()
    for slot, pid in principal_map.items():
        if pid in players:
            players[pid]["bot_slot"] = slot

    host_events = _load_host_events()
    bot_actions = _load_bot_actions(principal_map)

    phases = _build_phases(host_events)
    deaths = _build_deaths(host_events)
    outcome = _build_game_outcome(host_events)

    timeline = _group_messages_by_phase(bot_actions, phases, deaths)

    spaces_meta = []
    for space_info in host_data.get("spaces", []):
        spaces_meta.append({
            "space_id": space_info.get("space_id"),
            "label": space_info.get("label"),
            "observatory_url": space_info.get("observatory_url"),
        })

    return {
        "status": "ok",
        "fetched_at": time.time(),
        "game_space_id": host_data.get("game_space_id"),
        "coven_id": host_data.get("coven_id"),
        "detective_id": host_data.get("detective_id"),
        "doctor_id": host_data.get("doctor_id"),
        "players": players,
        "timeline": timeline,
        "deaths": deaths,
        "outcome": outcome,
        "spaces_meta": spaces_meta,
    }


FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"
LEGACY_STATIC = Path(__file__).parent / "static"


@app.get("/")
def serve_index():
    for d in (FRONTEND_DIST, LEGACY_STATIC):
        idx = d / "index.html"
        if idx.exists():
            return FileResponse(idx)
    return JSONResponse(
        {"error": "no frontend build found -- run `npm run build` in dashboard/frontend"},
        status_code=404,
    )


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")
elif LEGACY_STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(LEGACY_STATIC)), name="static")
