"""The Fact-Checker: an independent audit of the finished memo.

Deliberately isolated. It receives exactly two things -- the memo, and the raw
source records -- and never sees the debate, the analysts' reasoning, or how any
conclusion was reached. A verifier that reads the argument is susceptible to it;
one that only sees claims and data can do arithmetic.

Its findings are shown to the user rather than quietly applied. Publishing the
audit is what makes the memo trustworthy: a system that hides its corrections is
asking to be taken on faith, which is the thing this product exists to avoid.
"""

from __future__ import annotations

import json

from agents.base import Agent
from agents.schemas import ResearchMemo, VerificationReport
from core.config import settings
from core.events import EventBus
from tools.claude_tools import SourceRegistry


def _claims_for_checking(memo: ResearchMemo) -> str:
    """Flatten the memo into the checkable claims, stripped of argument."""
    lines: list[str] = []
    for side, points in (("BULL", memo.bull_case), ("BEAR", memo.bear_case)):
        for point in points:
            lines.append(f"- ({side}) \"{point.point}\"  → cites [{point.evidence_ref}]")

    lines.append(f'- (SUMMARY) "{memo.plain_summary}"  → check any figures it states')
    for risk in memo.key_risks:
        lines.append(f'- (RISK) "{risk}"')
    return "\n".join(lines)


def _source_records(registry: SourceRegistry) -> str:
    """The raw data, verbatim. No interpretation, no narrative."""
    blocks = []
    for src in registry.all():
        detail = json.dumps(src.detail, default=str, indent=1)
        blocks.append(
            f"### [{src.ref_id}] {src.label}\n"
            f"Retrieved: {src.fetched_at.strftime('%Y-%m-%d %H:%M UTC')}"
            + (f"\nURL: {src.url}" if src.url else "")
            + f"\n```json\n{detail}\n```"
        )
    return "\n\n".join(blocks) or "(no sources were retrieved)"


def run_factchecker(
    memo: ResearchMemo,
    registry: SourceRegistry,
    bus: EventBus,
    ticker: str,
) -> VerificationReport:
    """Check every factual claim in the memo against the retrieved data."""
    task = "\n".join(
        [
            f"# Verify this research memo about {ticker}",
            "",
            "You have not seen the analysis that produced these claims, and you do "
            "not need it. Check each claim against the source records below.",
            "",
            "## Claims to verify",
            _claims_for_checking(memo),
            "",
            "## The source records — the only ground truth",
            _source_records(registry),
            "",
            "## Your task",
            "Issue one finding per checkable claim. Quote the source figure in each "
            "explanation so a reader can verify your verification. If a citation id "
            "does not appear in the records above, that claim is unsupported.",
        ]
    )

    agent = Agent(
        name="FactChecker",
        prompt_name="factchecker",
        output_model=VerificationReport,
        model=settings.moderator_model,
        max_tool_calls=0,  # verification against the record, not new research
    )
    report = agent.run(task=task, registry=registry, ticker=ticker, bus=bus)

    unsupported = sum(1 for f in report.findings if f.verdict != "supported")
    bus.emit(
        "verification_result",
        agent="FactChecker",
        verdict=report.overall_verdict,
        checked=len(report.findings),
        flagged=unsupported,
    )
    return report  # type: ignore[return-value]


def apply_verification(memo: ResearchMemo, report: VerificationReport) -> ResearchMemo:
    """Mark memo points the verifier could not support.

    We annotate rather than delete. A reader who sees "[unverified]" next to a
    claim learns something about the system's honesty; a claim that silently
    vanishes teaches them nothing and hides the failure.
    """
    flagged = {
        (f.claim or "").strip().lower()
        for f in report.findings
        if f.verdict != "supported"
    }
    if not flagged:
        return memo

    def annotate(points):
        for point in points:
            text = point.point.strip().lower()
            if any(text.startswith(f[:60]) or f.startswith(text[:60]) for f in flagged):
                if "[unverified]" not in point.point:
                    point.point = f"{point.point} [unverified]"
                point.survived_debate = False
        return points

    memo.bull_case = annotate(memo.bull_case)
    memo.bear_case = annotate(memo.bear_case)
    return memo
