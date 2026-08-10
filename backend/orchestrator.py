"""The orchestrator: an explicit state machine over the whole desk.

    RESOLVE → GATHER → RESEARCH → DEBATE → SYNTHESIZE → VERIFY → DONE

Written by hand, on purpose. A framework could hide this control flow behind an
"autonomous swarm", but then nobody -- including the person who built it -- could
say exactly what happened in what order, or why. Every transition here is one
readable function call that emits an event and persists its result.

Two properties this buys:

  * **Fairness is structural.** Bull and Bear run from the same evidence bundle,
    in isolation, and rebut the same pre-debate positions. Nothing about the
    ordering advantages one side.
  * **A failure degrades the run instead of ending it.** If the Bear falls over,
    the memo is written from what survived and says so. Only losing the evidence
    layer itself is fatal.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agents.debate import run_debate
from agents.factchecker import apply_verification, run_factchecker
from agents.moderator import run_moderator
from agents.research import run_bear, run_bull, run_risk
from agents.schemas import (
    DirectionalThesis,
    RebuttalSet,
    ResearchMemo,
    RiskAssessment,
    VerificationReport,
)
from core.events import Event, EventBus, EventSink
from tools.bundle import DEFAULT_HORIZON, EvidenceBundle, build_evidence_bundle
from tools.claude_tools import SourceRegistry
from tools.resolver import resolve_ticker

log = logging.getLogger(__name__)


class Stage(str, Enum):
    RESOLVE = "resolve"
    GATHER = "gather"
    RESEARCH = "research"
    DEBATE = "debate"
    SYNTHESIZE = "synthesize"
    VERIFY = "verify"
    DONE = "done"
    FAILED = "failed"


@dataclass
class RunResult:
    """Everything one analysis produced, including what went wrong."""

    run_id: str
    query: str
    ticker: str | None = None
    company_name: str | None = None
    horizon: str = DEFAULT_HORIZON
    user_view: str | None = None
    stage: Stage = Stage.RESOLVE

    bundle: EvidenceBundle | None = None
    registry: SourceRegistry = field(default_factory=SourceRegistry)

    bull: DirectionalThesis | None = None
    bear: DirectionalThesis | None = None
    risk: RiskAssessment | None = None
    bull_rebuttal: RebuttalSet | None = None
    bear_rebuttal: RebuttalSet | None = None
    memo: ResearchMemo | None = None
    verification: VerificationReport | None = None

    events: list[Event] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.stage == Stage.DONE and self.memo is not None

    def summary_line(self) -> str:
        if not self.ok:
            return f"run {self.run_id}: {self.stage.value} — {self.error or 'incomplete'}"
        flagged = (
            sum(1 for f in self.verification.findings if f.verdict != "supported")
            if self.verification
            else 0
        )
        return (
            f"run {self.run_id}: {self.ticker} memo ready in {self.elapsed_s:.0f}s "
            f"(confidence={self.memo.confidence}, {flagged} claim(s) flagged)"
        )


class Orchestrator:
    """Runs one analysis from a plain-English query to a verified memo."""

    def __init__(self, sinks: list[EventSink] | None = None) -> None:
        self._sinks = list(sinks or [])

    def run(
        self,
        query: str,
        horizon: str = DEFAULT_HORIZON,
        user_view: str | None = None,
        ticker_override: str | None = None,
    ) -> RunResult:
        run_id = uuid.uuid4().hex[:8]
        bus = EventBus(run_id, sinks=self._sinks)
        result = RunResult(run_id=run_id, query=query, horizon=horizon, user_view=user_view)
        started = time.perf_counter()

        bus.emit("run_started", query=query, horizon=horizon, has_user_view=bool(user_view))

        try:
            self._resolve(result, bus, ticker_override)
            self._gather(result, bus)
            self._research(result, bus)
            self._debate(result, bus)
            self._synthesize(result, bus)
            self._verify(result, bus)
            result.stage = Stage.DONE
        except _RunFailed as exc:
            result.stage = Stage.FAILED
            result.error = str(exc)
            bus.emit("error", message=str(exc))
        except Exception as exc:  # unexpected: log it, don't leak a traceback to the UI
            log.exception("Run %s crashed", run_id)
            result.stage = Stage.FAILED
            result.error = f"{type(exc).__name__}: {exc}"
            bus.emit("error", message=result.error)

        result.elapsed_s = time.perf_counter() - started
        result.events = bus.replay()
        bus.emit(
            "run_finished",
            stage=result.stage.value,
            elapsed_s=round(result.elapsed_s, 1),
            degraded=result.degraded,
        )
        result.events = bus.replay()
        return result

    # ------------------------------------------------------------ stages

    def _stage(self, result: RunResult, bus: EventBus, stage: Stage) -> None:
        result.stage = stage
        bus.emit("run_stage", stage=stage.value)

    def _resolve(self, result: RunResult, bus: EventBus, override: str | None) -> None:
        self._stage(result, bus, Stage.RESOLVE)

        if override:
            result.ticker = override.strip().upper()
            result.company_name = override.strip().upper()
            bus.emit("run_stage", stage="resolve", resolved=result.ticker, confirmed=True)
            return

        resolution = resolve_ticker(result.query)
        if not resolution.resolved or not resolution.ticker:
            raise _RunFailed(resolution.message)

        result.ticker = resolution.ticker
        result.company_name = resolution.name or resolution.ticker
        bus.emit(
            "run_stage",
            stage="resolve",
            resolved=result.ticker,
            name=result.company_name,
            needs_confirmation=resolution.needs_confirmation,
            candidates=[c.model_dump() for c in resolution.candidates[:5]],
        )

    def _gather(self, result: RunResult, bus: EventBus) -> None:
        self._stage(result, bus, Stage.GATHER)
        assert result.ticker

        bundle = build_evidence_bundle(
            result.ticker,
            result.company_name,
            horizon=result.horizon,
            on_tool_event=lambda name, res: bus.emit(
                "tool_result",
                agent="desk",
                tool=name,
                ok=res.ok,
                error=res.error,
                refs=[s.ref_id for s in res.sources],
            ),
        )

        if not bundle.sources:
            raise _RunFailed(
                f"No data could be retrieved for {result.ticker}. Every source failed, "
                "so there is nothing to analyse. Check the ticker and try again."
            )

        result.bundle = bundle
        if bundle.failures:
            result.degraded += [f"no data from {t}" for t in bundle.failures]
        bus.emit("run_stage", stage="gather", sources=len(bundle.sources), failures=list(bundle.failures))

    def _research(self, result: RunResult, bus: EventBus) -> None:
        """Bull, Bear and Risk, each in isolation.

        Run sequentially rather than in threads: they share one SourceRegistry,
        and interleaved id assignment would make citations depend on thread
        timing -- the same run could produce different ids twice. Determinism is
        worth more here than the wall-clock saving.
        """
        self._stage(result, bus, Stage.RESEARCH)
        assert result.bundle

        result.bull = self._attempt(
            "Bull", lambda: run_bull(result.bundle, result.registry, bus, result.user_view), result, bus
        )
        result.bear = self._attempt(
            "Bear", lambda: run_bear(result.bundle, result.registry, bus, result.user_view), result, bus
        )
        result.risk = self._attempt(
            "Risk", lambda: run_risk(result.bundle, result.registry, bus), result, bus
        )

        if result.bull is None and result.bear is None:
            raise _RunFailed("Both research agents failed; there is no analysis to moderate.")

    def _debate(self, result: RunResult, bus: EventBus) -> None:
        self._stage(result, bus, Stage.DEBATE)

        if not (result.bull and result.bear):
            result.degraded.append("debate skipped — only one side produced a case")
            bus.emit("debate_round", round=1, note="skipped: only one side available")
            return

        pair = self._attempt(
            "Debate",
            lambda: run_debate(result.bull, result.bear, result.registry, bus, result.ticker or ""),
            result,
            bus,
        )
        if pair:
            result.bull_rebuttal, result.bear_rebuttal = pair

    def _synthesize(self, result: RunResult, bus: EventBus) -> None:
        self._stage(result, bus, Stage.SYNTHESIZE)
        assert result.bundle

        empty_rebuttal = RebuttalSet(
            rebuttals=[],
            strongest_opposing_point="(no rebuttal round took place)",
            position_after_debate="(unchanged — no debate)",
        )
        empty_thesis = DirectionalThesis(
            thesis="(this side produced no case — treat its absence as missing analysis, "
            "not as agreement with the other side)",
            supporting_points=[],
            key_assumption="(none)",
            biggest_risk_to_thesis="(none)",
            what_would_change_my_mind="(none)",
            confidence="low",
            confidence_reasoning="This side of the argument was not produced.",
        )
        empty_risk = RiskAssessment(
            risks=[],
            overall_risk_rating="medium",
            rating_reasoning="The risk assessment did not complete for this run.",
            volatility_note="(risk assessment unavailable)",
        )

        memo = run_moderator(
            bundle=result.bundle,
            bull=result.bull or empty_thesis,
            bear=result.bear or empty_thesis,
            bull_rebuttal=result.bull_rebuttal or empty_rebuttal,
            bear_rebuttal=result.bear_rebuttal or empty_rebuttal,
            risk=result.risk or empty_risk,
            registry=result.registry,
            bus=bus,
            user_view=result.user_view,
        )
        result.memo = memo

    def _verify(self, result: RunResult, bus: EventBus) -> None:
        self._stage(result, bus, Stage.VERIFY)
        assert result.memo

        report = self._attempt(
            "FactChecker",
            lambda: run_factchecker(result.memo, result.registry, bus, result.ticker or ""),
            result,
            bus,
        )
        if report is None:
            # Never silently ship an unverified memo as if it had been checked.
            result.degraded.append(
                "VERIFICATION DID NOT RUN — claims in this memo are unchecked"
            )
            return

        result.verification = report
        result.memo = apply_verification(result.memo, report)

    # ------------------------------------------------------------ helpers

    def _attempt(self, label: str, fn, result: RunResult, bus: EventBus) -> Any:
        """Run one step; on failure record it and continue with what we have."""
        try:
            return fn()
        except Exception as exc:
            message = f"{label} failed: {type(exc).__name__}: {exc}"
            log.warning(message)
            result.degraded.append(message)
            bus.emit("error", agent=label, message=message)
            return None


class _RunFailed(Exception):
    """A failure the user should see as a message, not a stack trace."""


def run_analysis(
    query: str,
    horizon: str = DEFAULT_HORIZON,
    user_view: str | None = None,
    sinks: list[EventSink] | None = None,
) -> RunResult:
    """Convenience entry point: one call from query to verified memo."""
    return Orchestrator(sinks=sinks).run(query, horizon=horizon, user_view=user_view)
