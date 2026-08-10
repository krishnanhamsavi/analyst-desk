"""The debate: one rebuttal round between Bull and Bear.

This is where weak arguments get exposed. Up to this point each analyst has been
arguing into a vacuum, and an unchallenged argument always sounds better than it
is. Here the orchestrator hands each one the other's full case and asks a
narrower question: which of these claims does not hold up, and why?

Both rebuttals are generated from the *same* pre-debate positions. Running them
sequentially would let the second analyst rebut a rebuttal, which is a different
(and unfair) exercise -- the Bear would always get the last word.
"""

from __future__ import annotations

from agents.base import Agent
from agents.schemas import DirectionalThesis, RebuttalSet
from core.events import EventBus
from tools.claude_tools import SourceRegistry


def _format_opponent_case(side: str, thesis: DirectionalThesis) -> str:
    lines = [
        f"# The {side}'s case",
        "",
        f"**Their thesis:** {thesis.thesis}",
        "",
        f"**Their stated confidence:** {thesis.confidence} — {thesis.confidence_reasoning}",
        "",
        "**Their supporting claims:**",
    ]
    for i, point in enumerate(thesis.supporting_points, 1):
        lines += [
            f"{i}. [{point.dimension}] {point.claim}",
            f"   Their reasoning: {point.reasoning}",
            f"   They cite: [{point.evidence_ref}]",
        ]
    lines += [
        "",
        f"**The assumption their case depends on:** {thesis.key_assumption}",
        "",
        f"**What they say would change their mind:** {thesis.what_would_change_my_mind}",
        "",
        f"**The risk they themselves acknowledge:** {thesis.biggest_risk_to_thesis}",
    ]
    if thesis.evidence_gaps:
        lines += ["", "**Gaps they admitted:**"] + [f"- {g}" for g in thesis.evidence_gaps]
    return "\n".join(lines)


def _rebuttal_task(
    own_side: str,
    own_thesis: DirectionalThesis,
    opponent_side: str,
    opponent_thesis: DirectionalThesis,
    registry: SourceRegistry,
) -> str:
    return "\n".join(
        [
            f"You argued the {own_side} case. Your own thesis was:",
            f'"{own_thesis.thesis}"',
            "",
            f"You are now reading the {opponent_side}'s argument for the first time.",
            "",
            _format_opponent_case(opponent_side, opponent_thesis),
            "",
            "## The sources both of you were working from",
            registry.catalogue(limit_detail=400),
            "",
            "## Your task",
            f"Rebut the {opponent_side}'s strongest claims. Check their citations "
            "against what those sources actually say. Concede anything they got "
            "right. Name their single best point honestly.",
        ]
    )


def run_debate(
    bull: DirectionalThesis,
    bear: DirectionalThesis,
    registry: SourceRegistry,
    bus: EventBus,
    ticker: str,
) -> tuple[RebuttalSet, RebuttalSet]:
    """Both sides rebut simultaneously. Returns (bull_rebuttal, bear_rebuttal)."""
    bus.emit("debate_round", round=1, note="Bull and Bear exchange rebuttals")

    # No tools here: this round is about reasoning over evidence already on the
    # table, not about fetching more. Letting them re-research would turn a
    # rebuttal into a second opening statement.
    bull_rebutter = Agent("Bull", "rebuttal", RebuttalSet, max_tool_calls=0, phase="rebuttal")
    bear_rebutter = Agent("Bear", "rebuttal", RebuttalSet, max_tool_calls=0, phase="rebuttal")

    bull_rebuttal = bull_rebutter.run(
        task=_rebuttal_task("Bull", bull, "Bear", bear, registry),
        registry=registry,
        ticker=ticker,
        bus=bus,
    )
    bear_rebuttal = bear_rebutter.run(
        task=_rebuttal_task("Bear", bear, "Bull", bull, registry),
        registry=registry,
        ticker=ticker,
        bus=bus,
    )

    concessions = sum(
        1 for r in (*bull_rebuttal.rebuttals, *bear_rebuttal.rebuttals) if r.concession
    )
    bus.emit(
        "debate_round",
        round=1,
        note="rebuttals complete",
        bull_critiques=len(bull_rebuttal.rebuttals),
        bear_critiques=len(bear_rebuttal.rebuttals),
        concessions=concessions,
    )
    return bull_rebuttal, bear_rebuttal  # type: ignore[return-value]
