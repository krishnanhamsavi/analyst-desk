"""The three research agents: Bull, Bear, and Risk Manager.

They run in parallel and in **context isolation** -- separate message histories,
separate tool budgets, no visibility of each other. The Bull cannot soften its
case because it saw the Bear's, and vice versa. They meet only at the debate
stage, when the orchestrator deliberately shows each one the other's output.

That isolation is what makes the disagreement real rather than one model
negotiating with itself.
"""

from __future__ import annotations

from agents.base import Agent
from agents.briefing import build_briefing, horizon_period
from agents.schemas import DirectionalThesis, RiskAssessment
from core.config import settings
from core.events import EventBus
from tools.bundle import EvidenceBundle
from tools.claude_tools import SourceRegistry

BULL_TASK = (
    "Build the strongest honest case that this stock OUTPERFORMS over the stated "
    "horizon. Ground every claim in the evidence, and be candid about where your "
    "case is weakest."
)

BEAR_TASK = (
    "Build the strongest honest case that this stock UNDERPERFORMS, or is priced "
    "for an outcome the evidence does not support, over the stated horizon. Ground "
    "every claim in the evidence, and be candid about where your case is weakest."
)

RISK_TASK = (
    "Map what could go wrong for someone holding this stock, regardless of which "
    "direction it moves. You are not arguing a side. Ground every risk in the "
    "evidence, and do not pad the list with generic risks."
)


def _run(
    agent: Agent,
    task: str,
    bundle: EvidenceBundle,
    registry: SourceRegistry,
    bus: EventBus,
    user_view: str | None,
):
    shared, assignment = build_briefing(bundle, registry, task, user_view)
    return agent.run(
        task=assignment,
        registry=registry,
        ticker=bundle.ticker,
        bus=bus,
        default_period=horizon_period(bundle),
        shared_context=shared,
    )


def run_bull(
    bundle: EvidenceBundle,
    registry: SourceRegistry,
    bus: EventBus,
    user_view: str | None = None,
) -> DirectionalThesis:
    agent = Agent("Bull", "bull", DirectionalThesis, max_tool_calls=6, effort=settings.research_effort)
    return _run(agent, BULL_TASK, bundle, registry, bus, user_view)  # type: ignore[return-value]


def run_bear(
    bundle: EvidenceBundle,
    registry: SourceRegistry,
    bus: EventBus,
    user_view: str | None = None,
) -> DirectionalThesis:
    agent = Agent("Bear", "bear", DirectionalThesis, max_tool_calls=6, effort=settings.research_effort)
    return _run(agent, BEAR_TASK, bundle, registry, bus, user_view)  # type: ignore[return-value]


def run_risk(
    bundle: EvidenceBundle,
    registry: SourceRegistry,
    bus: EventBus,
) -> RiskAssessment:
    agent = Agent("Risk", "risk", RiskAssessment, max_tool_calls=5, effort=settings.research_effort)
    # The Risk Manager is deliberately not shown the user's view: an opinion about
    # direction is exactly the thing a risk assessment must not be anchored to.
    return _run(agent, RISK_TASK, bundle, registry, bus, None)  # type: ignore[return-value]
