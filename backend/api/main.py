"""The HTTP + WebSocket API.

Route map:

    POST   /api/resolve            "Apple" -> AAPL, with candidates to confirm
    POST   /api/runs               start an analysis, returns a run_id immediately
    GET    /api/runs               past runs (History panel)
    GET    /api/runs/{id}          the finished run: memo, verification, sources, chart
    GET    /api/runs/{id}/events   replay the audit log
    WS     /ws/runs/{id}           live event stream while the desk works
    POST   /api/runs/{id}/chat     follow-up question, streamed back as plain text
    GET    /api/health             readiness, including whether a model key is present

The run endpoint returns straight away rather than blocking for the several
minutes a run takes. The browser then opens the WebSocket and watches the desk
work -- which is the product, not a loading state.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents.qa import SUGGESTED_QUESTIONS, answer_question, build_context
from api.runner import RunManager, serialise_result
from core import db
from core.config import settings
from tools.bundle import DEFAULT_HORIZON, HORIZONS
from tools.resolver import resolve_ticker

log = logging.getLogger(__name__)

app = FastAPI(
    title="Analyst Desk",
    version="0.4.0",
    description="A multi-agent equity research desk. Research, not advice.",
)

# The frontend runs on a different port in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = RunManager()


# ------------------------------------------------------------------ models


class ResolveRequest(BaseModel):
    query: str = Field(description='Company name or ticker, e.g. "Apple" or AAPL')


class StartRunRequest(BaseModel):
    query: str
    horizon: str = DEFAULT_HORIZON
    user_view: str | None = Field(
        default=None, description="The user's own take, for the agents to test"
    )


class ChatRequest(BaseModel):
    question: str
    history: list[dict[str, str]] = Field(default_factory=list)


# ------------------------------------------------------------------ routes


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        # Whether a key is configured, never the key itself.
        "model_key_configured": bool(
            (settings.anthropic_api_key or "").strip() or _env_key_present()
        ),
        "analyst_model": settings.analyst_model,
        "moderator_model": settings.moderator_model,
        "demo_mode": settings.demo_mode,
        "active_runs": len(manager.active()),
    }


def _env_key_present() -> bool:
    import os

    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@app.get("/api/horizons")
def horizons() -> dict[str, Any]:
    return {
        "default": DEFAULT_HORIZON,
        "options": [
            {"value": key, "label": cfg["label"], "emphasis": cfg["emphasis"]}
            for key, cfg in HORIZONS.items()
        ],
    }


@app.post("/api/resolve")
def resolve(request: ResolveRequest) -> dict[str, Any]:
    """Turn a plain company name into a ticker, with candidates to confirm."""
    resolution = resolve_ticker(request.query)
    return json.loads(resolution.model_dump_json())


@app.post("/api/runs")
async def start_run(request: StartRunRequest) -> dict[str, Any]:
    if request.horizon not in HORIZONS:
        raise HTTPException(400, f"Unknown horizon. Choose one of: {', '.join(HORIZONS)}")
    if not request.query.strip():
        raise HTTPException(400, "Enter a company name or ticker.")

    session = manager.start(
        request.query.strip(),
        horizon=request.horizon,
        user_view=(request.user_view or "").strip() or None,
    )
    return {
        "run_id": session.run_id,
        "status": session.status,
        "stream_url": f"/ws/runs/{session.run_id}",
    }


@app.get("/api/runs")
def list_runs(limit: int = 20) -> dict[str, Any]:
    return {"runs": db.list_runs(min(limit, 100))}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """The full run. Serves live sessions from memory, older ones from the database."""
    session = manager.get(run_id)
    if session and session.result is not None:
        payload = serialise_result(session.result)
        payload["status"] = session.status
        payload["suggested_questions"] = SUGGESTED_QUESTIONS
        return payload

    if session and not session.finished:
        return {
            "run_id": run_id,
            "status": session.status,
            "stage": "in_progress",
            "events": session.events,
        }

    stored = db.load_run(run_id)
    if stored is None:
        raise HTTPException(404, f"No run found with id {run_id}")

    artifacts = stored.pop("artifacts", {})
    stored.update(artifacts)
    stored["status"] = "done" if stored.get("stage") == "done" else stored.get("stage")
    stored["suggested_questions"] = SUGGESTED_QUESTIONS
    return stored


@app.get("/api/runs/{run_id}/events")
def get_events(run_id: str) -> dict[str, Any]:
    """The audit log for a run."""
    session = manager.get(run_id)
    if session:
        return {"run_id": run_id, "events": session.events}

    stored = db.load_run(run_id)
    if stored is None:
        raise HTTPException(404, f"No run found with id {run_id}")
    return {"run_id": run_id, "events": stored["events"]}


@app.websocket("/ws/runs/{run_id}")
async def stream_run(websocket: WebSocket, run_id: str) -> None:
    """Live event stream. Late joiners receive everything they missed first."""
    await websocket.accept()

    session = manager.get(run_id)
    if session is None:
        stored = db.load_run(run_id)
        if stored is None:
            await websocket.send_json({"type": "error", "data": {"message": "Unknown run"}})
            await websocket.close()
            return
        # A finished run still replays: the debate is worth watching afterwards.
        for event in stored["events"]:
            await websocket.send_json(event)
        await websocket.send_json({"type": "stream_closed", "data": {"status": stored["stage"]}})
        await websocket.close()
        return

    queue = await manager.subscribe(session)
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                # Keep proxies from closing an idle connection during a long
                # model call, when minutes can pass with nothing to report.
                await websocket.send_json({"type": "heartbeat", "data": {}})
                continue

            await websocket.send_json(payload)
            if payload.get("type") == "stream_closed":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WebSocket error on run %s", run_id)
    finally:
        manager.unsubscribe(session, queue)


@app.post("/api/runs/{run_id}/chat")
async def chat(run_id: str, request: ChatRequest) -> StreamingResponse:
    """Ask the analyst a follow-up. Streams the answer as it is written."""
    session = manager.get(run_id)
    if session is None or session.result is None or session.result.memo is None:
        raise HTTPException(
            404,
            "That run isn't available for questions. Only completed runs from this "
            "server session can be discussed.",
        )

    result = session.result
    horizon_label = HORIZONS.get(result.horizon, HORIZONS[DEFAULT_HORIZON])["label"]
    context = build_context(
        company=result.company_name or result.ticker or "",
        ticker=result.ticker or "",
        horizon_label=horizon_label,
        memo=result.memo,
        verification=result.verification,
        registry=result.registry,
    )

    def generate():
        try:
            for chunk in answer_question(
                question=request.question,
                context=context,
                history=request.history,
                registry=result.registry,
                ticker=result.ticker or "",
            ):
                yield chunk
        except Exception as exc:
            log.exception("Chat failed on run %s", run_id)
            yield f"\n\n_Sorry — I couldn't finish that answer ({type(exc).__name__})._"

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")


@app.get("/api/runs/{run_id}/suggested-questions")
def suggested(run_id: str) -> dict[str, Any]:
    return {"questions": SUGGESTED_QUESTIONS}
