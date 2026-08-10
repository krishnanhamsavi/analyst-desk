"""The Moderator: reads everything, referees, writes the memo."""

from __future__ import annotations

from agents.base import Agent
from agents.schemas import (
    DirectionalThesis,
    RebuttalSet,
    ResearchMemo,
    RiskAssessment,
)
from core.config import settings
from core.events import EventBus
from tools.bundle import DEFAULT_HORIZON, HORIZONS, EvidenceBundle
from tools.claude_tools import SourceRegistry


def _thesis_block(side: str, thesis: DirectionalThesis, rebuttal: RebuttalSet) -> str:
    lines = [
        f"## The {side}'s case",
        f"**Thesis:** {thesis.thesis}",
        f"**Self-assessed confidence:** {thesis.confidence} — {thesis.confidence_reasoning}",
        "",
        "**Claims:**",
    ]
    for i, point in enumerate(thesis.supporting_points, 1):
        lines += [
            f"{i}. [{point.dimension}] {point.claim}  (cites [{point.evidence_ref}])",
            f"   Reasoning: {point.reasoning}",
        ]
    lines += [
        "",
        f"**Key assumption:** {thesis.key_assumption}",
        f"**Would change their mind:** {thesis.what_would_change_my_mind}",
        f"**Risk they acknowledge:** {thesis.biggest_risk_to_thesis}",
    ]
    if thesis.evidence_gaps:
        lines += ["**Gaps they admitted:**"] + [f"- {g}" for g in thesis.evidence_gaps]

    lines += ["", f"### What the {side} said in rebuttal of the other side"]
    for reb in rebuttal.rebuttals:
        tag = "CONCEDED" if reb.concession else reb.critique_type.upper()
        cite = f" [{reb.evidence_ref}]" if reb.evidence_ref else ""
        lines += [
            f"- ({tag}) targeting: {reb.targets_claim}",
            f"  {reb.critique}{cite}",
        ]
    lines += [
        f"**Best point they conceded the opponent made:** {rebuttal.strongest_opposing_point}",
        f"**Their position after the debate:** {rebuttal.position_after_debate}",
    ]
    return "\n".join(lines)


def _risk_block(risk: RiskAssessment) -> str:
    lines = [
        "## The Risk Manager's assessment (direction-agnostic)",
        f"**Overall risk rating:** {risk.overall_risk_rating} — {risk.rating_reasoning}",
        f"**What volatility means here:** {risk.volatility_note}",
        "",
        "**Risks identified:**",
    ]
    for item in risk.risks:
        lines.append(
            f"- [{item.severity}] {item.risk}  (cites [{item.evidence_ref}])\n"
            f"  Why it matters: {item.why_it_matters}"
        )
    return "\n".join(lines)


def run_moderator(
    bundle: EvidenceBundle,
    bull: DirectionalThesis,
    bear: DirectionalThesis,
    bull_rebuttal: RebuttalSet,
    bear_rebuttal: RebuttalSet,
    risk: RiskAssessment,
    registry: SourceRegistry,
    bus: EventBus,
    user_view: str | None = None,
) -> ResearchMemo:
    """Synthesise the whole debate into one balanced, cited memo."""
    horizon = HORIZONS.get(bundle.horizon, HORIZONS[DEFAULT_HORIZON])

    task_parts = [
        f"# Write the research memo for {bundle.company_name} ({bundle.ticker})",
        "",
        f"**Horizon:** {horizon['label']}",
        f"**Data as of:** {bundle.fetched_at.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    if user_view:
        task_parts += [
            "## The user's own view",
            f'They said: "{user_view}"',
            "Assess in `user_view_assessment` how it held up against the evidence.",
            "",
        ]

    task_parts += [
        _thesis_block("Bull", bull, bull_rebuttal),
        "",
        _thesis_block("Bear", bear, bear_rebuttal),
        "",
        _risk_block(risk),
        "",
        "## The sources everyone worked from",
        registry.catalogue(limit_detail=500),
        "",
        "## Your task",
        "Weigh all of it and write the memo. Every point you carry into the memo "
        "must cite a source id from the list above. Decide which arguments "
        "survived the debate and mark them honestly. Write it so someone who "
        "knows nothing about stocks finishes the summary genuinely informed.",
    ]

    agent = Agent(
        name="Moderator",
        prompt_name="moderator",
        output_model=ResearchMemo,
        model=settings.moderator_model,
        effort=settings.judgment_effort,
        max_tool_calls=0,  # the moderator judges evidence; it does not gather more
        phase="synthesis",
    )
    memo = agent.run(
        task="\n".join(task_parts),
        registry=registry,
        ticker=bundle.ticker,
        bus=bus,
    )
    bus.emit("memo_ready", agent="Moderator", confidence=memo.confidence)
    return memo  # type: ignore[return-value]
