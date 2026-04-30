"""FastAPI + HTMX bridge for a human Mafia player.

One process per human (no auth). The bridge:
  - holds an already-enrolled HttpSpaceToolSession
  - polls the public game space (and an optional role-private subspace)
    on a background asyncio task
  - serves a single page at / driven by HTMX polling (every 2s on the feeds)
  - takes form posts at /post and emits INTENT/PROMISE messages via the SDK
  - exposes /rewrite_robot for the Robot Mode UX

The bridge does NOT call an LLM for game decisions (that is the human's
job). LLM is only used for the optional Robot Mode rewrite in llm.py.

See docs/HUMAN_UI.md for the layout, docs/ARCHITECTURE.md §Process model
for why this is a separate process, and docs/AGENT_DESIGN.md §Robot Mode
for the rewrite prompt.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


# --- Phase determination -----------------------------------------------------
# src/game/phase.current_phase consumes nested SDK-shaped messages directly
# and returns the full nested phase-start dict (or None). Phase fields like
# `phase`, `deadline_ms`, `living` therefore live under m["payload"].
from src.game.phase import current_phase as _current_phase  # noqa: E402


# --- Module paths ------------------------------------------------------------
HERE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))


# --- Phase → action template -------------------------------------------------

def _action_template_for(phase_name: Optional[str], role: Optional[str], game_ended: bool) -> str:
    if game_ended:
        return "action_endgame.html"
    if not phase_name:
        return "action_discussion.html"
    p = phase_name
    if p.endswith("-discussion"):
        return "action_discussion.html"
    if p.endswith("-vote"):
        return "action_vote.html"
    if p.startswith("night-"):
        if role == "mafia" and "-mafia" in p:
            return "action_mafia.html"
        if role == "detective" and "-detective" in p:
            return "action_detective.html"
        if role == "doctor" and "-doctor" in p:
            return "action_doctor.html"
        return "action_villager_night.html"
    return "action_discussion.html"


# --- Phase deadline helper ---------------------------------------------------

def _deadline_ms(phase: Optional[dict]) -> Optional[int]:
    """Extract deadline from a nested SDK-shaped phase-start dict."""
    if not phase:
        return None
    payload = phase.get("payload") or {}
    dl = payload.get("deadline_ms")
    return dl if isinstance(dl, int) else None


def _phase_name(phase: Optional[dict]) -> Optional[str]:
    if not phase:
        return None
    payload = phase.get("payload") or {}
    name = payload.get("phase")
    return name if isinstance(name, str) else None


# --- Bridge state container --------------------------------------------------

class BridgeState:
    """In-memory cache of the most recent scan results.

    The scan loop writes here; HTTP handlers read. Single-process, single
    asyncio loop — a plain dict is fine, no locking needed.
    """

    def __init__(
        self,
        session: Any,
        game_space_id: str,
        private_space_id: Optional[str] = None,
        role: Optional[str] = None,
        scan_interval_s: float = 1.5,
        feed_limit: int = 50,
    ) -> None:
        self.session = session
        self.game_space_id = game_space_id
        self.private_space_id = private_space_id
        self.role = role
        self.scan_interval_s = scan_interval_s
        self.feed_limit = feed_limit
        # Both lists hold SDK-shaped messages (envelope + payload).
        # src/game/phase.current_phase consumes this nested shape directly.
        self.public_messages: list[dict] = []
        self.private_messages: list[dict] = []
        self._last_error: Optional[str] = None

    def my_principal_id(self) -> Optional[str]:
        try:
            ident = self.session.identity()
        except Exception:
            return None
        if isinstance(ident, dict):
            return ident.get("principalId") or ident.get("agentId")
        return None

    def scan_once(self) -> None:
        try:
            pub = self.session.scan_full(self.game_space_id)
            self.public_messages = list(pub.get("messages") or [])
        except Exception as exc:  # surface but don't crash the loop
            self._last_error = f"public scan: {exc!r}"
        if self.private_space_id:
            try:
                priv = self.session.scan_full(self.private_space_id)
                self.private_messages = list(priv.get("messages") or [])
            except Exception as exc:
                self._last_error = f"private scan: {exc!r}"

    def current_phase(self) -> Optional[dict]:
        return _current_phase(self.public_messages)

    def game_ended(self) -> bool:
        for m in self.public_messages:
            payload = m.get("payload") or {}
            if payload.get("kind") == "game-end":
                return True
        return False

    def living_players(self) -> list[str]:
        """Living-player principals derived from the latest phase-start.

        Falls back to the union of all senders we've ever seen if the phase
        payload doesn't carry a `living` field.
        """
        phase = self.current_phase()
        if phase:
            payload = phase.get("payload") or {}
            living = payload.get("living")
            if isinstance(living, list) and living:
                return [str(x) for x in living]
        seen: list[str] = []
        for m in self.public_messages:
            sid = m.get("senderId")
            if isinstance(sid, str) and sid not in seen:
                seen.append(sid)
        return seen


# --- Action emission ---------------------------------------------------------

def _build_payload(action_kind: str, form: dict[str, str], state: BridgeState, phase: Optional[dict]) -> dict:
    """Translate a form submission into a payload dict.

    Schema fields here mirror src/game/schemas.py. We don't import & validate
    against pydantic so this module stays usable when src/game is being
    built in parallel — the host & bots are the strict validators on the
    receiving end.
    """
    phase_name = _phase_name(phase)
    base: dict[str, Any] = {"kind": action_kind, "phase": phase_name}
    text = (form.get("text") or "").strip()
    if text:
        base["content"] = text

    if action_kind in {"accusation", "defense", "observation"}:
        target = (form.get("target") or "").strip()
        if action_kind == "accusation":
            base["target"] = target
            base["claim"] = text or "(no comment)"
        elif action_kind == "defense":
            base["claim"] = text or "(no comment)"
        return base

    if action_kind == "vote":
        base["target"] = (form.get("target") or "").strip()
        return base

    if action_kind == "id-vote":
        base["target"] = (form.get("target") or "").strip()
        claimed = (form.get("claimed_substrate") or "").strip()
        if claimed not in {"human", "agent"}:
            raise HTTPException(status_code=400, detail="claimed_substrate must be human|agent")
        base["claimed_substrate"] = claimed
        return base

    if action_kind == "coven-target":
        base["target"] = (form.get("target") or "").strip()
        if text:
            base["reasoning"] = text
        return base

    if action_kind == "coven-confirm":
        return base

    if action_kind == "investigate":
        base["target"] = (form.get("target") or "").strip()
        return base

    if action_kind == "protect":
        base["target"] = (form.get("target") or "").strip()
        if (form.get("self_protect") or "").lower() in {"1", "true", "on", "yes"}:
            base["self_protect"] = True
        return base

    if action_kind == "substrate-guess":
        base["target"] = (form.get("target") or "").strip()
        guess = (form.get("guess") or "").strip()
        if guess not in {"human", "agent"}:
            raise HTTPException(status_code=400, detail="guess must be human|agent")
        base["guess"] = guess
        try:
            base["confidence"] = float(form.get("confidence") or "0.5")
        except ValueError:
            base["confidence"] = 0.5
        if text:
            base["reasoning"] = text
        return base

    # "pass" / "sleep" — informational no-ops, posted as observation.
    if action_kind in {"pass", "sleep"}:
        return {"kind": "observation", "content": f"({action_kind})", "phase": phase_name}

    raise HTTPException(status_code=400, detail=f"unknown action_kind: {action_kind!r}")


def _post_message(state: BridgeState, payload: dict) -> dict:
    """Emit the configured payload as an INTENT into the game space.

    We choose the parent space by `kind`: mafia/detective/doctor private
    actions go into the relevant private subspace if one is bound; otherwise
    everything goes into the public game space. This keeps the bridge usable
    even before private subspaces have been provisioned.
    """
    private_kinds = {"coven-target", "coven-confirm", "investigate", "protect"}
    parent_id = state.private_space_id if (payload.get("kind") in private_kinds and state.private_space_id) else state.game_space_id
    content = payload.get("content") or payload.get("kind") or ""
    msg = state.session.intent(content, parent_id=parent_id, payload=payload)
    return state.session.post(msg, step="human-bridge.post")


# --- App factory -------------------------------------------------------------

def create_app(state: BridgeState, *, run_scan_loop: bool = True) -> FastAPI:
    """Build the FastAPI app bound to a (possibly mock) BridgeState.

    Tests pass `run_scan_loop=False` to keep the lifespan inert.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task: Optional[asyncio.Task] = None
        if run_scan_loop:
            async def _loop() -> None:
                while True:
                    await asyncio.to_thread(state.scan_once)
                    await asyncio.sleep(state.scan_interval_s)
            task = asyncio.create_task(_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    app = FastAPI(lifespan=lifespan)
    app.state.bridge = state
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def _render_page(request: Request) -> HTMLResponse:
        phase = state.current_phase()
        phase_name = _phase_name(phase)
        action_template = _action_template_for(phase_name, state.role, state.game_ended())
        ctx = {
            "phase_name": phase_name or "(waiting for game-start)",
            "deadline_ms": _deadline_ms(phase),
            "role": state.role or "unknown",
            "principal_id": state.my_principal_id() or "",
            "messages": state.public_messages[-state.feed_limit:],
            "private_messages": state.private_messages[-state.feed_limit:],
            "has_private": bool(state.private_space_id),
            "living_players": state.living_players(),
            "action_template": action_template,
            "game_ended": state.game_ended(),
        }
        return TEMPLATES.TemplateResponse(request, "page.html", ctx)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return _render_page(request)

    @app.get("/feed/public", response_class=HTMLResponse)
    def feed_public(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            "feed.html",
            {
                "messages": state.public_messages[-state.feed_limit:],
                "feed_id": "public",
                "label": "PUBLIC",
            },
        )

    @app.get("/feed/private")
    def feed_private(request: Request):
        if not state.private_space_id:
            return Response(status_code=204)
        return TEMPLATES.TemplateResponse(
            request,
            "feed.html",
            {
                "messages": state.private_messages[-state.feed_limit:],
                "feed_id": "private",
                "label": "PRIVATE",
            },
        )

    @app.post("/post", response_class=HTMLResponse)
    async def post(request: Request) -> HTMLResponse:
        form = dict(await request.form())
        action_kind = (form.get("action_kind") or "").strip()
        if not action_kind:
            raise HTTPException(status_code=400, detail="action_kind is required")
        phase = state.current_phase()
        payload = _build_payload(action_kind, form, state, phase)
        try:
            posted = _post_message(state, payload)
        except Exception as exc:
            return HTMLResponse(
                f'<div class="status status-error">post failed: {exc!s}</div>',
                status_code=500,
            )
        msg_id = posted.get("intentId") if isinstance(posted, dict) else None
        return HTMLResponse(
            f'<div class="status status-ok">posted {action_kind}'
            f'{f" (id {msg_id})" if msg_id else ""}</div>'
        )

    @app.post("/rewrite_robot", response_class=HTMLResponse)
    async def rewrite_robot(text: str = Form(...)) -> HTMLResponse:
        from . import llm as _llm  # local import keeps import-time cheap
        try:
            rewritten = _llm.robot_mode_rewrite(text)
        except Exception as exc:
            return HTMLResponse(
                f'<div class="status status-error">rewrite failed: {exc!s}</div>',
                status_code=500,
            )
        return HTMLResponse(
            f'<div class="robot-preview">'
            f'<div class="robot-label">Posting as:</div>'
            f'<textarea name="text" form="action-form" rows="3">{rewritten}</textarea>'
            f'</div>'
        )

    return app


# --- CLI ---------------------------------------------------------------------

def _build_session_from_workspace(workspace: str, agent_name: str, endpoint: str) -> Any:
    sdk_path = os.environ.get("INTENT_SPACE_SDK_PATH")
    if sdk_path and sdk_path not in sys.path:
        sys.path.insert(0, sdk_path)
    try:
        from http_space_tools import HttpSpaceToolSession  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Could not import HttpSpaceToolSession. Set INTENT_SPACE_SDK_PATH "
            "to ccmarket/plugins/intent-space-agent-pack/sdk or add it to "
            f"PYTHONPATH. Original error: {exc!s}"
        )
    session = HttpSpaceToolSession(
        endpoint=endpoint,
        workspace=Path(workspace),
        agent_name=agent_name,
    )
    session.connect()
    return session


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.human.bridge")
    parser.add_argument("--workspace", required=True, help="Path to the human's enrolled workspace dir")
    parser.add_argument("--game-space-id", required=True, help="Shared game space id to scan and post into")
    parser.add_argument("--private-space-id", default=None, help="Optional role-private subspace id")
    parser.add_argument("--role", default=None, choices=["villager", "mafia", "detective", "doctor"])
    parser.add_argument("--agent-name", default="human-player", help="Agent name in the SDK workspace")
    parser.add_argument("--endpoint", default="https://spacebase1.differ.ac/commons")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--scan-interval", type=float, default=1.5)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = _parse_args(argv)
    session = _build_session_from_workspace(args.workspace, args.agent_name, args.endpoint)
    state = BridgeState(
        session=session,
        game_space_id=args.game_space_id,
        private_space_id=args.private_space_id,
        role=args.role,
        scan_interval_s=args.scan_interval,
    )
    app = create_app(state, run_scan_loop=True)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
