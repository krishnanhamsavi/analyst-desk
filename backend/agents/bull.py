"""The Bull agent, and the briefing pack every research agent starts from.

The pack matters as much as the prompt. Handing each agent the same pre-fetched
evidence does two things: it makes the debate fair (neither side can win by
having fetched more data), and it means one round of API calls serves the whole
desk instead of six. Agents still call tools -- the pack is a starting point, not
a cage -- and the cache makes those calls near-instant when they overlap.
"""

from __future__ import annotations

from core.events import EventBus
from agents.base import Agent
from agents.schemas import DirectionalThesis
from tools.bundle import DEFAULT_HORIZON, HORIZONS, EvidenceBundle
from tools.claude_tools import SourceRegistry


def build_briefing(
    bundle: EvidenceBundle,
    registry: SourceRegistry,
    role_task: str,
    user_view: str | None = None,
) -> str:
    """Assemble the opening user message for a research agent."""
    horizon = HORIZONS.get(bundle.horizon, HORIZONS[DEFAULT_HORIZON])

    # Register the pre-fetched sources so the pack's citation ids and any the
    # agent later fetches itself share one numbering.
    for result in bundle.results.values():
        if result.ok:
            registry.register(result.sources)

    parts = [
        f"# Assignment\n{role_task}",
        "",
        f"**Company:** {bundle.company_name} ({bundle.ticker})",
        f"**Horizon:** {horizon['label']} — weight your analysis toward {horizon['emphasis']}.",
        f"**Data as of:** {bundle.fetched_at.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    if user_view:
        parts += [
            "## The user's own view",
            f'The person requesting this analysis said: "{user_view}"',
            "",
            "Address this directly. If the evidence supports them, say so and show "
            "which evidence. If it does not, say that plainly and show why. Do not "
            "flatter the view, and do not dismiss it without evidence.",
            "",
        ]

    parts += [
        "## Your briefing pack",
        "This is what the desk has already pulled. Call tools if your argument "
        "needs something that isn't here.",
        "",
        bundle.evidence_brief(),
        "",
        "## Citable sources",
        registry.catalogue(),
        "",
        "Work through the evidence, pull anything else you need, then produce your "
        "structured analysis.",
    ]
    return "\n".join(parts)


def bull_agent(max_tool_calls: int = 6) -> Agent:
    return Agent(
        name="Bull",
        prompt_name="bull",
        output_model=DirectionalThesis,
        max_tool_calls=max_tool_calls,
    )


def run_bull(
    bundle: EvidenceBundle,
    registry: SourceRegistry,
    bus: EventBus,
    user_view: str | None = None,
) -> DirectionalThesis:
    """Research and argue the case that this stock outperforms."""
    task = (
        "Build the strongest honest case that this stock OUTPERFORMS over the "
        "stated horizon. Ground every claim in the evidence, and be candid about "
        "where your case is weakest."
    )
    briefing = build_briefing(bundle, registry, task, user_view)
    result = bull_agent().run(
        task=briefing,
        registry=registry,
        ticker=bundle.ticker,
        bus=bus,
        default_period=HORIZONS.get(bundle.horizon, HORIZONS[DEFAULT_HORIZON])["price_period"],
    )
    return result  # type: ignore[return-value]
