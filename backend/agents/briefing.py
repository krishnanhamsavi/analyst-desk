"""Builds the opening message each research agent receives.

The briefing pack matters as much as the prompt. Handing every agent the same
pre-fetched evidence does two things: it makes the debate fair (neither side can
win by having fetched more data), and it means one round of API calls serves the
whole desk instead of six. Agents still call tools -- the pack is a starting
point, not a cage -- and the cache makes overlapping calls near-instant.
"""

from __future__ import annotations

from tools.bundle import DEFAULT_HORIZON, HORIZONS, EvidenceBundle
from tools.claude_tools import SourceRegistry


def register_bundle(bundle: EvidenceBundle, registry: SourceRegistry) -> None:
    """Give the pre-fetched sources citation ids in the run's shared namespace."""
    for result in bundle.results.values():
        if result.ok:
            registry.register(result.sources)


def build_briefing(
    bundle: EvidenceBundle,
    registry: SourceRegistry,
    role_task: str,
    user_view: str | None = None,
) -> tuple[str, str]:
    """Assemble the opening user message for a research agent.

    Returns (shared_block, assignment). The shared block is *identical* for
    every agent in the run, so the caller puts it at the front of the system
    prompt where it can be cached: the Bull pays to process the evidence once
    and everyone after it reads back at roughly a tenth of the price.
    """
    horizon = HORIZONS.get(bundle.horizon, HORIZONS[DEFAULT_HORIZON])
    register_bundle(bundle, registry)

    # Built once per run and reused verbatim. The source catalogue *grows* as
    # agents fetch extra data, so rebuilding this per agent would change the
    # bytes and silently defeat the cache -- prefix caching is an exact match.
    # Agents learn the ids of anything they fetch themselves from the tool
    # result, so freezing this snapshot costs them nothing.
    cached_block = getattr(bundle, "_shared_briefing", None)
    if cached_block is None:
        cached_block = "\n".join(
            [
                f"**Company:** {bundle.company_name} ({bundle.ticker})",
                f"**Horizon:** {horizon['label']} - weight your analysis toward "
                f"{horizon['emphasis']}.",
                f"**Data as of:** {bundle.fetched_at.strftime('%Y-%m-%d %H:%M UTC')}",
                "",
                "## The briefing pack",
                "What the desk has already pulled. Call tools if your argument needs "
                "something that isn't here.",
                "",
                bundle.evidence_brief(),
                "",
                "## Citable sources",
                registry.catalogue(),
            ]
        )
        setattr(bundle, "_shared_briefing", cached_block)
    shared = cached_block

    assignment = [f"# Your assignment\n{role_task}"]
    if user_view:
        assignment += [
            "",
            "## The user's own view",
            f'The person requesting this analysis said: "{user_view}"',
            "",
            "Address this directly. If the evidence supports them, say so and show "
            "which evidence. If it does not, say that plainly and show why. Do not "
            "flatter the view, and do not dismiss it without evidence.",
        ]
    assignment += [
        "",
        "Work through the evidence, pull anything else you need, then produce your "
        "structured analysis.",
    ]

    return shared, "\n".join(assignment)


def horizon_period(bundle: EvidenceBundle) -> str:
    return HORIZONS.get(bundle.horizon, HORIZONS[DEFAULT_HORIZON])["price_period"]
