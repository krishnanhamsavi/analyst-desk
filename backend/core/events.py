"""Typed events emitted as a run progresses.

Every meaningful thing an agent does becomes an Event. The CLI prints them, the
WebSocket layer (Phase 4) forwards them to the browser, and the database stores
them as an audit log. One event stream serves all three, so the UI can never
show something the audit log doesn't have.

This is the "observable" principle from the spec made concrete: an agent that
works invisibly is indistinguishable from one that made everything up.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "run_started",
    "run_stage",          # orchestrator moved to a new state machine stage
    "agent_started",
    "agent_thinking",     # streamed reasoning text
    "tool_called",
    "tool_result",
    "agent_finished",
    "debate_round",
    "memo_ready",
    "verification_result",
    "run_finished",
    "error",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Event(BaseModel):
    """One observable step in a run."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    run_id: str
    type: EventType
    agent: str | None = None
    ts: datetime = Field(default_factory=_utcnow)
    data: dict[str, Any] = Field(default_factory=dict)

    def line(self) -> str:
        """One-line rendering for the CLI."""
        who = f"[{self.agent}] " if self.agent else ""
        stamp = self.ts.strftime("%H:%M:%S")

        if self.type == "tool_called":
            args = self.data.get("args") or {}
            extra = " ".join(f"{k}={v}" for k, v in args.items())
            return f"{stamp} {who}calls {self.data.get('tool')}({extra})"
        if self.type == "tool_result":
            status = "ok" if self.data.get("ok") else f"NO DATA: {self.data.get('error')}"
            refs = self.data.get("refs") or []
            return f"{stamp} {who}  -> {status}" + (f"  cites {' '.join(refs)}" if refs else "")
        if self.type == "agent_thinking":
            return f"{stamp} {who}{(self.data.get('text') or '').strip()[:160]}"
        if self.type == "agent_finished":
            return f"{stamp} {who}done ({self.data.get('tool_calls', 0)} tool calls, {self.data.get('elapsed_s', 0)}s)"
        if self.type == "error":
            return f"{stamp} {who}ERROR: {self.data.get('message')}"
        return f"{stamp} {who}{self.type}: {self.data or ''}"


# An event sink. The CLI prints; the API broadcasts; tests collect.
EventSink = Callable[[Event], None]


class EventBus:
    """Collects events and fans them out to any number of sinks."""

    def __init__(self, run_id: str, sinks: list[EventSink] | None = None) -> None:
        self.run_id = run_id
        self.events: list[Event] = []
        self._sinks: list[EventSink] = list(sinks or [])

    def subscribe(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    def emit(
        self,
        type: EventType,
        agent: str | None = None,
        **data: Any,
    ) -> Event:
        event = Event(run_id=self.run_id, type=type, agent=agent, data=data)
        self.events.append(event)
        for sink in self._sinks:
            try:
                sink(event)
            except Exception:  # a broken UI must never break the analysis
                pass
        return event

    def replay(self) -> list[Event]:
        return list(self.events)
