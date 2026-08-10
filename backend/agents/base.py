"""The agent runtime: a hand-written tool-calling loop.

Why a manual loop rather than the SDK's tool runner
---------------------------------------------------
The runner would work, but this project's whole pitch is that the orchestration
is legible. Every turn here is a place we emit a typed event -- which tool was
called, with what arguments, what came back, which citation ids it produced --
and those events are simultaneously the live UI feed and the audit log. Owning
the loop makes that wiring explicit rather than hidden behind a callback.

Each agent runs in **context isolation**: its own message history, its own tool
budget. The Bull never sees the Bear's reasoning during research. They meet only
at the debate stage, when the orchestrator deliberately shows each one the
other's output. That isolation is what makes the disagreement real rather than
one model talking to itself.

Two phases per agent:

  1. **Research** -- the model reads its briefing pack and calls tools to dig
     deeper, until it stops asking for data or hits its budget.
  2. **Report** -- one final call with structured output, so the result is a
     validated object rather than prose we have to parse.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from core.config import settings
from core.events import EventBus
from tools.claude_tools import TOOL_SCHEMAS, SourceRegistry, ToolDispatcher

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

T = TypeVar("T", bound=BaseModel)


def load_prompt(name: str) -> str:
    """Read a prompt from prompts/<name>.md.

    Prompts live in files, not string literals, so they can be iterated on
    without touching code -- and so a diff of a prompt change is readable.
    """
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def _client():
    """Build the Anthropic client, applying the TLS fix first.

    An empty ANTHROPIC_API_KEY in .env must not shadow a real one in the
    environment, so we only pass the key when we actually have one.
    """
    from core import netsetup

    netsetup.apply()

    import anthropic

    key = (settings.anthropic_api_key or "").strip()
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


class Agent:
    """One research agent: a role, a prompt, a tool budget, an output schema."""

    def __init__(
        self,
        name: str,
        prompt_name: str,
        output_model: type[BaseModel],
        model: str | None = None,
        max_tool_calls: int = 8,
        effort: str | None = None,
    ) -> None:
        self.name = name
        self.system_prompt = load_prompt(prompt_name)
        self.output_model = output_model
        self.model = model or settings.analyst_model
        self.max_tool_calls = max_tool_calls
        self.effort = effort or settings.effort

    # ------------------------------------------------------------------ run

    def run(
        self,
        task: str,
        registry: SourceRegistry,
        ticker: str,
        bus: EventBus,
        default_period: str = "1y",
    ) -> BaseModel:
        """Research, then report. Returns a validated instance of output_model."""
        started = time.perf_counter()
        bus.emit("agent_started", agent=self.name, model=self.model, effort=self.effort)

        client = _client()
        dispatcher = ToolDispatcher(ticker, registry, default_period)
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]

        try:
            self._research(client, messages, dispatcher, bus)
            result = self._report(client, messages, registry, bus)
        except Exception as exc:
            bus.emit("error", agent=self.name, message=f"{type(exc).__name__}: {exc}")
            raise

        bus.emit(
            "agent_finished",
            agent=self.name,
            tool_calls=dispatcher.call_count,
            elapsed_s=round(time.perf_counter() - started, 1),
        )
        return result

    # ------------------------------------------------------------- phase 1

    def _research(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        dispatcher: ToolDispatcher,
        bus: EventBus,
    ) -> None:
        """Let the model pull whatever extra data its argument needs."""
        while dispatcher.call_count < self.max_tool_calls:
            response = client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=self.system_prompt,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                tools=TOOL_SCHEMAS,
                messages=messages,
            )

            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                raise RuntimeError(
                    f"{self.name} was declined by safety classifiers "
                    f"({getattr(detail, 'category', 'unknown')})"
                )

            # Surface the model's visible reasoning as it works.
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    bus.emit("agent_thinking", agent=self.name, text=block.text)

            if response.stop_reason != "tool_use":
                # The model is done gathering; keep its turn for context.
                messages.append({"role": "assistant", "content": response.content})
                return

            messages.append({"role": "assistant", "content": response.content})

            # Execute every requested tool, returning all results in one user turn.
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                bus.emit(
                    "tool_called", agent=self.name, tool=block.name, args=dict(block.input or {})
                )
                text, result = dispatcher.run(block.name, dict(block.input or {}))
                bus.emit(
                    "tool_result",
                    agent=self.name,
                    tool=block.name,
                    ok=result.ok,
                    error=result.error,
                    refs=[s.ref_id for s in result.sources],
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                        "is_error": not result.ok,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

        # Budget exhausted -- tell the model plainly rather than cutting it off.
        messages.append(
            {
                "role": "user",
                "content": (
                    f"You have used your full research budget of {self.max_tool_calls} "
                    "tool calls. Work with the evidence you have; note anything still "
                    "missing in evidence_gaps rather than guessing at it."
                ),
            }
        )

    # ------------------------------------------------------------- phase 2

    def _report(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        registry: SourceRegistry,
        bus: EventBus,
    ) -> BaseModel:
        """One structured-output call. The schema does the format enforcement."""
        valid = sorted(registry.valid_ids, key=lambda s: int(s[1:]))
        messages.append(
            {
                "role": "user",
                "content": (
                    "Now write your final analysis in the required structure.\n\n"
                    f"Every evidence_ref MUST be one of these ids you were actually "
                    f"shown: {', '.join(valid) or '(none)'}\n"
                    "A claim you cannot tie to one of those ids does not belong in "
                    "your output. Drop it."
                ),
            }
        )

        response = client.messages.parse(
            model=self.model,
            max_tokens=8000,
            system=self.system_prompt,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            messages=messages,
            output_format=self.output_model,
        )

        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(f"{self.name} returned no parseable structured output")

        self._warn_on_bad_citations(parsed, registry, bus)
        return parsed

    def _warn_on_bad_citations(
        self, parsed: BaseModel, registry: SourceRegistry, bus: EventBus
    ) -> None:
        """Flag citations pointing at ids that don't exist.

        The Fact-Checker is the real defence (Phase 3); this is an early warning
        so prompt regressions show up during development rather than in a memo.
        """
        refs: list[str] = []
        for field in ("supporting_points", "risks"):
            for item in getattr(parsed, field, None) or []:
                ref = getattr(item, "evidence_ref", None)
                if ref:
                    refs.append(ref.strip().upper())

        unknown = [r for r in refs if r not in registry.valid_ids]
        if unknown:
            bus.emit(
                "error",
                agent=self.name,
                message=f"Citations reference unknown sources: {', '.join(sorted(set(unknown)))}",
            )
