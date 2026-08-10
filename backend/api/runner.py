"""Bridges the synchronous orchestrator to async WebSocket clients.

The orchestrator is deliberately blocking, sequential code -- that's what makes
it readable. But a run takes minutes, and the browser needs events *as they
happen*, so the two have to be joined carefully:

  * Each run executes on a worker thread, leaving the event loop free.
  * Its event sink hands events to the loop via `call_soon_threadsafe`, the only
    safe way to touch asyncio objects from another thread.
  * Every event is also buffered. A browser that connects late -- or reconnects
    after a dropped connection -- gets the full history first, then live events.
    Without that replay, a refresh mid-run would show an empty screen for
    minutes with no indication anything was happening.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core import db
from core.events import Event
from orchestrator import Orchestrator, RunResult, Stage
from tools.bundle import DEFAULT_HORIZON

log = logging.getLogger(__name__)

# One run occupies a thread for minutes; a small pool bounds concurrent spend
# as much as it bounds CPU.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="desk-run")


@dataclass
class RunSession:
    """One in-flight or finished run, plus everyone watching it."""

    run_id: str
    query: str
    horizon: str
    user_view: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    status: str = "running"  # running | done | failed
    events: list[dict[str, Any]] = field(default_factory=list)
    result: RunResult | None = None
    error: str | None = None

    subscribers: set[asyncio.Queue] = field(default_factory=set)

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed")


def _event_payload(event: Event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "type": event.type,
        "agent": event.agent,
        "ts": event.ts.isoformat(),
        "data": event.data,
    }


class RunManager:
    """Starts runs, fans their events out, and keeps them retrievable."""

    def __init__(self, max_retained: int = 50) -> None:
        self._sessions: dict[str, RunSession] = {}
        self._max_retained = max_retained

    # -------------------------------------------------------------- lookup

    def get(self, run_id: str) -> RunSession | None:
        return self._sessions.get(run_id)

    def active(self) -> list[RunSession]:
        return [s for s in self._sessions.values() if not s.finished]

    # --------------------------------------------------------------- start

    def start(
        self,
        query: str,
        horizon: str = DEFAULT_HORIZON,
        user_view: str | None = None,
    ) -> RunSession:
        """Kick off a run and return immediately with its id."""
        loop = asyncio.get_running_loop()
        run_id = uuid.uuid4().hex[:8]
        session = RunSession(run_id=run_id, query=query, horizon=horizon, user_view=user_view)
        self._sessions[run_id] = session
        self._evict_old()

        def sink(event: Event) -> None:
            # Called on the worker thread; hop back to the loop before touching
            # any asyncio object.
            loop.call_soon_threadsafe(self._publish, session, _event_payload(event))

        def work() -> RunResult:
            return Orchestrator(sinks=[sink]).run(
                query, horizon=horizon, user_view=user_view, run_id=run_id
            )

        future = _EXECUTOR.submit(work)

        def done(fut) -> None:
            try:
                result = fut.result()
                session.result = result
                session.status = "done" if result.ok else "failed"
                session.error = result.error
                db.save_run(result)
            except Exception as exc:  # a crash must still close the stream
                log.exception("Run %s crashed in the worker", run_id)
                session.status = "failed"
                session.error = f"{type(exc).__name__}: {exc}"

            loop.call_soon_threadsafe(
                self._publish,
                session,
                {
                    "type": "stream_closed",
                    "run_id": run_id,
                    "data": {"status": session.status, "error": session.error},
                    "agent": None,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )

        future.add_done_callback(done)
        return session

    # ----------------------------------------------------------- streaming

    def _publish(self, session: RunSession, payload: dict[str, Any]) -> None:
        session.events.append(payload)
        for queue in list(session.subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # A stalled browser must not block the run or other viewers.
                log.warning("Dropping event for a slow subscriber on run %s", session.run_id)

    async def subscribe(self, session: RunSession) -> asyncio.Queue:
        """Attach a listener, pre-loaded with everything it missed."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        for payload in session.events:
            queue.put_nowait(payload)
        session.subscribers.add(queue)
        return queue

    def unsubscribe(self, session: RunSession, queue: asyncio.Queue) -> None:
        session.subscribers.discard(queue)

    # ------------------------------------------------------------ retention

    def _evict_old(self) -> None:
        """Drop the oldest finished runs. Everything is in the database anyway."""
        finished = sorted(
            (s for s in self._sessions.values() if s.finished),
            key=lambda s: s.created_at,
        )
        excess = len(self._sessions) - self._max_retained
        for session in finished[: max(0, excess)]:
            self._sessions.pop(session.run_id, None)


def serialise_result(result: RunResult) -> dict[str, Any]:
    """The full run as JSON for the frontend."""
    import json

    def dump(model):
        return json.loads(model.model_dump_json()) if model is not None else None

    price = result.bundle.data("get_price_history") if result.bundle else {}
    profile = result.bundle.data("get_company_profile") if result.bundle else {}

    return {
        "run_id": result.run_id,
        "stage": result.stage.value,
        "ok": result.ok,
        "error": result.error,
        "degraded": result.degraded,
        "elapsed_s": round(result.elapsed_s, 1),
        "query": result.query,
        "ticker": result.ticker,
        "company_name": result.company_name,
        "horizon": result.horizon,
        "user_view": result.user_view,
        "profile": {
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "summary": profile.get("business_summary"),
            "website": profile.get("website"),
        },
        "chart": {
            "series": price.get("chart_series") or [],
            "latest_close": price.get("latest_close"),
            "as_of": price.get("as_of"),
            "sma_50": (price.get("moving_averages") or {}).get("sma_50"),
            "sma_200": (price.get("moving_averages") or {}).get("sma_200"),
            "range_52w": price.get("range_52w") or {},
            "returns_pct": price.get("returns_pct") or {},
            "volatility_pct": price.get("annualised_volatility_pct"),
        },
        "bull": dump(result.bull),
        "bear": dump(result.bear),
        "risk": dump(result.risk),
        "bull_rebuttal": dump(result.bull_rebuttal),
        "bear_rebuttal": dump(result.bear_rebuttal),
        "memo": dump(result.memo),
        "verification": dump(result.verification),
        "sources": [json.loads(s.model_dump_json()) for s in result.registry.all()],
    }
