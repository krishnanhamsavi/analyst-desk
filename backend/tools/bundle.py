"""Assemble one run's evidence into a single citable bundle.

Why a bundle exists rather than agents just calling tools ad hoc:

  * **Stable citation ids.** Sources get ids (S1, S2, ...) once, up front. Agents
    cite [S3]; the Fact-Checker later resolves [S3] back to the exact numbers it
    vouches for. Without a shared id space, citations can't be verified.
  * **Fairness.** Bull and Bear must argue from the *same* evidence, or the debate
    is rigged by whoever happened to fetch more data.
  * **One fetch per run.** Six agents sharing one bundle means one round of API
    calls, not six.

Agents can still call tools individually later (the Q&A agent does), but a run
starts from a bundle.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from core.schemas import SourceRef, ToolResult
from tools.filings import get_recent_filings
from tools.market import (
    get_analyst_estimates,
    get_company_profile,
    get_fundamentals,
    get_price_history,
)
from tools.news import get_recent_news

log = logging.getLogger(__name__)

# Horizon drives which evidence matters most, and therefore how much price
# history we pull and how the prompts weight each dimension.
HORIZONS: dict[str, dict[str, Any]] = {
    "short": {
        # 2y even for a short horizon: a trailing 1-year return computed from a
        # 1-year window lands on the first row of the series, which is the least
        # reliable point in it. Charts can be sliced down; accuracy can't be
        # recovered after the fact.
        "label": "Right now / next few weeks",
        "price_period": "2y",
        "emphasis": "momentum, recent price action, live news catalysts",
    },
    "medium": {
        "label": "6-12 months",
        "price_period": "2y",
        "emphasis": "a balance of fundamentals and momentum",
    },
    "long": {
        "label": "3-5 years",
        "price_period": "5y",
        "emphasis": "fundamentals, moat, and business quality; short-term noise is ignored",
    },
}
DEFAULT_HORIZON = "medium"


@dataclass
class EvidenceBundle:
    """Everything the agents are allowed to reason from, for one run."""

    ticker: str
    company_name: str
    horizon: str
    results: dict[str, ToolResult] = field(default_factory=dict)
    sources: list[SourceRef] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------- lookups

    def source(self, ref_id: str) -> SourceRef | None:
        return next((s for s in self.sources if s.ref_id == ref_id), None)

    @property
    def valid_ref_ids(self) -> list[str]:
        return [s.ref_id for s in self.sources]

    @property
    def failures(self) -> dict[str, str]:
        return {name: r.error or "unknown" for name, r in self.results.items() if not r.ok}

    def data(self, tool: str) -> dict[str, Any]:
        result = self.results.get(tool)
        return result.data if result and result.ok else {}

    # ---------------------------------------------------------- rendering

    def source_catalogue(self) -> str:
        """The citation menu shown to agents: id, what it is, what it vouches for."""
        lines = []
        for s in self.sources:
            detail = _compact(s.detail, limit=700)
            lines.append(f"[{s.ref_id}] {s.label}\n      {detail}")
        return "\n".join(lines)

    def evidence_brief(self) -> str:
        """Human-readable dossier handed to every research agent."""
        horizon = HORIZONS.get(self.horizon, HORIZONS[DEFAULT_HORIZON])
        parts = [
            f"COMPANY: {self.company_name} ({self.ticker})",
            f"HORIZON: {horizon['label']} -- weight your analysis toward {horizon['emphasis']}.",
            f"DATA AS OF: {self.fetched_at.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
        ]

        for tool, result in self.results.items():
            if not result.ok:
                parts.append(f"## {tool}\n  NO DATA: {result.error}\n")
                continue
            refs = " ".join(f"[{s.ref_id}]" for s in result.sources)
            parts.append(f"## {tool}  {refs}\n{_compact(result.data, limit=4000)}\n")

        if self.failures:
            parts.append(
                "## GAPS IN THE EVIDENCE\n"
                + "\n".join(f"  - {k}: {v}" for k, v in self.failures.items())
                + "\nSay so explicitly if a gap weakens your argument.\n"
            )

        parts.append("## CITABLE SOURCES\n" + self.source_catalogue())
        return "\n".join(parts)


def _compact(value: Any, limit: int, indent: int = 2) -> str:
    """Render nested data as terse indented lines. Cheaper in tokens than JSON."""
    lines: list[str] = []

    def walk(node: Any, depth: int) -> None:
        pad = " " * (indent * (depth + 1))
        if isinstance(node, dict):
            for k, v in node.items():
                if v is None or v == {} or v == []:
                    continue
                if isinstance(v, (dict, list)):
                    lines.append(f"{pad}{k}:")
                    walk(v, depth + 1)
                else:
                    lines.append(f"{pad}{k}: {v}")
        elif isinstance(node, list):
            for item in node[:12]:
                if isinstance(item, (dict, list)):
                    walk(item, depth + 1)
                    lines.append("")
                else:
                    lines.append(f"{pad}- {item}")
        else:
            lines.append(f"{pad}{node}")

    walk(value, 0)
    text = "\n".join(l for l in lines if l.strip() or l == "")
    return text[:limit] + ("\n  ...(truncated)" if len(text) > limit else "")


def build_evidence_bundle(
    ticker: str,
    company_name: str | None = None,
    horizon: str = DEFAULT_HORIZON,
    on_tool_event: Callable[[str, ToolResult], None] | None = None,
) -> EvidenceBundle:
    """Fetch every data source for a ticker, in parallel, and assign citation ids.

    `on_tool_event` is called as each tool lands so the orchestrator can stream
    "tool_called" events to the UI while fetching is still in flight.
    """
    ticker = ticker.strip().upper()
    horizon = horizon if horizon in HORIZONS else DEFAULT_HORIZON
    period = HORIZONS[horizon]["price_period"]

    jobs: dict[str, Callable[[], ToolResult]] = {
        "get_company_profile": lambda: get_company_profile(ticker),
        "get_price_history": lambda: get_price_history(ticker, period),
        "get_fundamentals": lambda: get_fundamentals(ticker),
        "get_analyst_estimates": lambda: get_analyst_estimates(ticker),
        "get_recent_news": lambda: get_recent_news(ticker),
        "get_recent_filings": lambda: get_recent_filings(ticker),
    }

    results: dict[str, ToolResult] = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(fn) for name, fn in jobs.items()}
        for name, future in futures.items():
            try:
                result = future.result(timeout=120)
            except Exception as exc:  # a hung source must not sink the run
                result = ToolResult.failure(name, f"Tool raised: {exc}", ticker)
            results[name] = result
            if on_tool_event:
                on_tool_event(name, result)

    # Assign citation ids in a stable order so reruns produce comparable memos.
    sources: list[SourceRef] = []
    for name in jobs:
        for src in results[name].sources:
            src.ref_id = f"S{len(sources) + 1}"
            sources.append(src)

    if not company_name:
        company_name = results["get_company_profile"].data.get("name") or ticker

    bundle = EvidenceBundle(
        ticker=ticker,
        company_name=company_name,
        horizon=horizon,
        results=results,
        sources=sources,
    )
    log.info(
        "Evidence bundle for %s: %d sources, %d tool failures",
        ticker,
        len(sources),
        len(bundle.failures),
    )
    return bundle
