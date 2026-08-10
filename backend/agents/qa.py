"""The follow-up Q&A agent: the analyst you can keep talking to.

Unlike the research agents this one streams plain text rather than returning a
schema, because the value is conversational. It still works from the same
grounded sources, and it can call the data tools when a question needs something
the run never fetched.

Turning a one-shot report into a conversation is what makes the memo usable by a
non-expert: the memo answers the question the desk chose to ask, and this answers
the question the reader actually has.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from agents.base import _client, load_prompt
from agents.schemas import ResearchMemo, VerificationReport
from core.config import settings
from tools.claude_tools import TOOL_SCHEMAS, SourceRegistry, ToolDispatcher

SUGGESTED_QUESTIONS = [
    "Explain this simply",
    "Why is this risky?",
    "What could go wrong?",
    "Is now a good time?",
    "What would make this a bad investment?",
    "How does it make money?",
]


def build_context(
    company: str,
    ticker: str,
    horizon_label: str,
    memo: ResearchMemo,
    verification: VerificationReport | None,
    registry: SourceRegistry,
) -> str:
    """Everything the Q&A agent is allowed to answer from."""
    parts = [
        f"# The memo you wrote about {company} ({ticker})",
        f"Horizon: {horizon_label}",
        "",
        f"**Summary:** {memo.plain_summary}",
        f"**Confidence:** {memo.confidence} — {memo.confidence_reasoning}",
        "",
        "**Key risks:**",
        *[f"- {r}" for r in memo.key_risks],
        "",
        "**The case for:**",
        *[f"- {p.point} [{p.evidence_ref}]" for p in memo.bull_case],
        "",
        "**The case against:**",
        *[f"- {p.point} [{p.evidence_ref}]" for p in memo.bear_case],
        "",
        "**For the bull case to win:**",
        *[f"- {c}" for c in memo.bull_needs_to_be_true],
        "",
        "**For the bear case to win:**",
        *[f"- {c}" for c in memo.bear_needs_to_be_true],
        "",
        f"**How the debate went:** {memo.how_the_debate_went}",
        f"**Bottom line:** {memo.what_this_means_for_you}",
    ]

    if verification:
        flagged = [f for f in verification.findings if f.verdict != "supported"]
        parts += [
            "",
            f"## Your own fact-checker's audit ({verification.overall_verdict})",
            verification.summary,
        ]
        if flagged:
            parts.append("Claims it could not fully support — be upfront about these:")
            parts += [f"- ({f.verdict}) {f.claim} — {f.explanation}" for f in flagged]

    parts += [
        "",
        "## The source records behind every citation",
    ]
    for src in registry.all():
        detail = json.dumps(src.detail, default=str)[:1200]
        parts.append(f"[{src.ref_id}] {src.label}\n    {detail}")

    return "\n".join(parts)


def answer_question(
    question: str,
    context: str,
    history: list[dict[str, str]],
    registry: SourceRegistry,
    ticker: str,
) -> Iterator[str]:
    """Stream an answer token by token.

    Tools stay available: a reader can ask about something the run never
    fetched, and "I don't have that" is a worse answer than going to get it.
    """
    client = _client()
    dispatcher = ToolDispatcher(ticker, registry)
    system = load_prompt("qa") + "\n\n" + context

    messages: list[dict[str, Any]] = []
    for turn in history[-6:]:  # keep the recent thread, not the whole session
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    for _ in range(3):  # bounded tool hops, then answer with what we have
        with client.messages.stream(
            model=settings.analyst_model,
            max_tokens=4000,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            tools=TOOL_SCHEMAS,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
            response = stream.get_final_message()

        if response.stop_reason != "tool_use":
            return

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            yield f"\n\n_(checking {block.name}…)_\n\n"
            text, result = dispatcher.run(block.name, dict(block.input or {}))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": text,
                    "is_error": not result.ok,
                }
            )
        messages.append({"role": "user", "content": results})
