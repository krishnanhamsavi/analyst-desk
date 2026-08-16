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
import re
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from core.config import settings
from core.events import EventBus
from core.usage import Usage, from_response
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


# Appended to every agent's prompt. Fancy punctuation is the single biggest
# source of mangled output: models escape it inconsistently inside JSON, and a
# botched escape doesn't fail loudly -- it silently eats the surrounding words.
# Asking for plain ASCII removes the whole failure class at the source.
_OUTPUT_HYGIENE = """

## Writing mechanics

**Never use dashes of any kind as punctuation.** No em dashes, no en dashes, and
no hyphen standing in for one. Where you would reach for a dash, use a comma, a
full stop, or the word "which" or "because". Two short sentences almost always
read better than one sentence held together by a dash. Hyphens inside a genuine
compound word such as "year-on-year" are fine.

Use straight quotes rather than curly ones, and write "..." rather than a single
ellipsis character.

Do not use markdown headings, bold, or bullet markers inside any field of your
structured output. Those fields are rendered as plain prose, so the syntax shows
up literally to the reader. Write ordinary sentences.
"""


# Models writing JSON sometimes escape a character twice, so an em dash arrives
# as six literal characters instead of the dash itself. Repairing that naively
# is worse than the disease: blindly decoding every \uXXXX turns  into a
# real form-feed sitting in the middle of a sentence, which is invisible in logs
# and corrupts the rendered memo. So we decode only to printable characters, and
# scrub anything else that slipped through.
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# A lone backslash before a letter is a JSON artefact, never real prose.
_STRAY_BACKSLASH = re.compile(r"\\(?=[A-Za-z])")


# Dashes used as punctuation make AI writing instantly recognisable, and they
# are the escaping hazard above. The prompt asks models not to use them; this is
# the net for when one does anyway. A hyphen inside a compound word such as
# "year-on-year" has no surrounding spaces, so it survives untouched.
_DASH_CLAUSE = re.compile(r"\s*[‒-―−]\s*|\s+-{1,2}\s+")
_LEADING_DASH = re.compile(r"^\s*[-‒-―−]\s+", re.MULTILINE)


def _strip_dashes(text: str) -> str:
    """Replace dash-as-punctuation with a comma, keeping the sentence readable."""
    text = _LEADING_DASH.sub("", text)

    def replace(match: re.Match[str]) -> str:
        before = text[: match.start()].rstrip()
        # A dash after existing punctuation just becomes a space.
        return " " if before.endswith((",", ".", ":", ";", "?", "!")) else ", "

    return _DASH_CLAUSE.sub(replace, text)


def _unescape_text(text: str) -> str:
    def decode(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char.isprintable() or char in "\n\t" else " "

    cleaned = _UNICODE_ESCAPE.sub(decode, text)
    cleaned = _CONTROL_CHARS.sub(" ", cleaned)
    cleaned = _STRAY_BACKSLASH.sub("", cleaned)
    cleaned = _strip_dashes(cleaned)
    cleaned = re.sub(r",\s*,", ",", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _unescape_model(model: BaseModel) -> None:
    """Repair over-escaped unicode anywhere in a parsed output, in place."""

    def fix(value: Any) -> Any:
        if isinstance(value, str):
            return _unescape_text(value)
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, BaseModel):
            _unescape_model(value)
        return value

    for field in type(model).model_fields:
        current = getattr(model, field, None)
        repaired = fix(current)
        if isinstance(current, (str, list)) and repaired != current:
            setattr(model, field, repaired)


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
        max_output_tokens: int = 16000,
        phase: str = "research",
    ) -> None:
        self.name = name
        # An agent appears twice in a run -- once researching, once rebutting.
        # The UI needs to tell those apart, or the debate looks like a repeat.
        self.phase = phase
        self.system_prompt = load_prompt(prompt_name) + _OUTPUT_HYGIENE
        self.output_model = output_model
        self.model = model or settings.analyst_model
        self.max_tool_calls = max_tool_calls
        self.effort = effort or settings.effort
        # max_tokens caps thinking *and* output together. At high effort the
        # reasoning can consume most of a small budget, leaving the structured
        # JSON to be cut off mid-string -- which surfaces as a parse error, not
        # as a truncation warning. 16k stays under the SDK's non-streaming
        # timeout guard while leaving ample room for a long verification report.
        self.max_output_tokens = max_output_tokens
        self.usage = Usage()
        self._system: Any = self.system_prompt

    # ------------------------------------------------------------------ run

    def run(
        self,
        task: str | list[dict[str, Any]],
        registry: SourceRegistry,
        ticker: str,
        bus: EventBus,
        default_period: str = "1y",
        shared_context: str | None = None,
    ) -> BaseModel:
        """Research, then report. Returns a validated instance of output_model."""
        started = time.perf_counter()
        bus.emit(
            "agent_started",
            agent=self.name,
            model=self.model,
            effort=self.effort,
            phase=self.phase,
        )

        client = _client()
        dispatcher = ToolDispatcher(ticker, registry, default_period)
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        self.usage = Usage()

        # Prompt caching is a *prefix* match over tools -> system -> messages, so
        # a shared block only pays off if it sits ahead of everything that
        # differs. Each agent has its own role prompt, which means the shared
        # evidence has to lead the system prompt rather than the user turn --
        # otherwise every agent writes its own cache and none of them read.
        self._system = (
            [
                {
                    "type": "text",
                    "text": shared_context,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": self.system_prompt},
            ]
            if shared_context
            else self.system_prompt
        )

        try:
            # An agent with no tool budget has nothing to research, so the
            # exploratory call would just re-read the same context and throw the
            # answer away. Skipping it removes roughly a third of the calls in a
            # run: the Moderator, the Fact-Checker and both rebuttals never call
            # tools by design.
            if self.max_tool_calls > 0:
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
            usage=self.usage.as_dict(),
        )
        return result

    def _track(self, response: Any) -> None:
        """Accumulate what this call cost, so a run can be broken down later."""
        self.usage.add(from_response(response, self.model))

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
                max_tokens=self.max_output_tokens,
                system=self._system,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
            self._track(response)

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

        try:
            response = self._parse_call(client, messages)
        except Exception as exc:
            # A truncated structured output surfaces as a JSON parse error, which
            # says nothing about the actual cause. Retry once with less thinking
            # so more of the budget goes to the output itself.
            if "json_invalid" not in str(exc) and "Invalid JSON" not in str(exc):
                raise
            bus.emit(
                "error",
                agent=self.name,
                message="Structured output was truncated; retrying with lower effort.",
            )
            response = self._parse_call(client, messages, effort="medium")

        self._track(response)

        if getattr(response, "stop_reason", None) == "max_tokens":
            raise RuntimeError(
                f"{self.name} ran out of output budget ({self.max_output_tokens} tokens) "
                "before finishing its report."
            )

        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(f"{self.name} returned no parseable structured output")

        _unescape_model(parsed)
        self._warn_on_bad_citations(parsed, registry, bus)
        return parsed

    def _parse_call(self, client: Any, messages: list[dict[str, Any]], effort: str | None = None):
        return client.messages.parse(
            model=self.model,
            max_tokens=self.max_output_tokens,
            system=self._system,
            thinking={"type": "adaptive"},
            output_config={"effort": effort or self.effort},
            messages=messages,
            output_format=self.output_model,
        )

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
