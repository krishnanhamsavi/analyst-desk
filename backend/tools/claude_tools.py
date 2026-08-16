"""Expose the grounded tool layer to Claude, with shared citation ids.

Two jobs:

  1. **Schemas.** Describe each data tool in the JSON-Schema shape Claude's
     tool-calling API expects. Descriptions matter more than they look -- they
     are how the model decides which tool answers the question in front of it.

  2. **The SourceRegistry.** Whichever agent calls a tool, the evidence it gets
     back is registered once and handed a stable id (S1, S2, ...). Two agents
     calling the same tool cite the *same* id, and the Fact-Checker resolves
     that id back to the exact numbers to verify a claim. Without this, every
     agent would invent its own numbering and citations couldn't be checked.

The ticker is bound by the orchestrator rather than passed by the model. An
agent researching NVDA has no legitimate reason to fetch data for another
company, and binding it removes a whole class of hallucinated-argument failures.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from core.schemas import SourceRef, ToolResult
from tools.filings import get_recent_filings
from tools.market import (
    VALID_PERIODS,
    get_analyst_estimates,
    get_company_profile,
    get_fundamentals,
    get_price_history,
)
from tools.news import get_recent_news
from tools.peers import get_peer_comparison
from tools.xbrl import get_sec_financials

log = logging.getLogger(__name__)


# --------------------------------------------------------------- registry


class SourceRegistry:
    """Assigns and resolves citation ids for one run."""

    def __init__(self) -> None:
        self._by_id: dict[str, SourceRef] = {}
        self._seen: dict[tuple[str, str, str], str] = {}

    @staticmethod
    def _key(src: SourceRef) -> tuple[str, str, str]:
        return (src.kind, src.label, src.url or "")

    def register(self, sources: list[SourceRef]) -> list[SourceRef]:
        """Give each source an id, reusing the id if we've seen it before."""
        out: list[SourceRef] = []
        for src in sources:
            key = self._key(src)
            existing_id = self._seen.get(key)
            if existing_id:
                out.append(self._by_id[existing_id])
                continue
            src.ref_id = f"S{len(self._by_id) + 1}"
            self._seen[key] = src.ref_id
            self._by_id[src.ref_id] = src
            out.append(src)
        return out

    def get(self, ref_id: str) -> SourceRef | None:
        return self._by_id.get((ref_id or "").strip().upper())

    def all(self) -> list[SourceRef]:
        return list(self._by_id.values())

    @property
    def valid_ids(self) -> set[str]:
        return set(self._by_id)

    def catalogue(self, limit_detail: int = 600) -> str:
        """The citation menu agents are shown: id, what it is, what it vouches for."""
        lines = []
        for src in self.all():
            detail = json.dumps(src.detail, default=str)[:limit_detail]
            lines.append(f"[{src.ref_id}] {src.label}\n      {detail}")
        return "\n".join(lines) or "(no sources retrieved yet)"


# ----------------------------------------------------------- tool schemas

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_company_profile",
        "description": (
            "What the business actually does: sector, industry, country, employee "
            "count, and a business summary. Call this first when you need to ground "
            "an argument in the company's actual operations rather than its numbers."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_price_history",
        "description": (
            "Price action and the derived statistics an analyst quotes: returns over "
            "1/3/6/12 months, annualised volatility, 50- and 200-day moving averages, "
            "52-week range, and maximum drawdown. Call this with a different `period` "
            "when your argument depends on a longer or shorter window than the "
            "briefing pack covers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": sorted(VALID_PERIODS),
                    "description": "How far back to look. Defaults to the run's horizon.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_fundamentals",
        "description": (
            "Valuation multiples (P/E, forward P/E, P/S, P/B, EV/EBITDA), growth "
            "rates, margins, returns on capital, cash flow, and balance-sheet health. "
            "The evidence base for any claim about whether the stock is cheap or "
            "expensive, or whether the business is improving."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_analyst_estimates",
        "description": (
            "Wall Street consensus: recommendation, analyst count, and price targets "
            "with implied upside. Context about what the market already expects -- "
            "never a substitute for your own reasoning, and never quote it as proof."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recent_news",
        "description": (
            "Recent headlines with publisher, date, snippet and link. Evidence of "
            "what is being *said* about the company, not of what is true. Most useful "
            "for identifying live catalysts on a short horizon."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many headlines to return (default 10, max 20).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_sec_financials",
        "description": (
            "Audited financial statements straight from the company's SEC filings "
            "(XBRL): revenue, gross and operating profit, net income, EPS, assets, "
            "debt, cash and cash flow, with four years of history and the exact form "
            "and filing date each figure came from. Prefer these figures over "
            "market-data fundamentals when the two disagree, because these are what "
            "the company legally reported."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_peer_comparison",
        "description": (
            "How this company's valuation multiples and margins compare with similar "
            "companies, including the peer median and whether the subject sits above "
            "or below it. Call this before making ANY claim that a stock is cheap or "
            "expensive: a multiple means nothing without knowing where comparable "
            "businesses trade."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recent_filings",
        "description": (
            "SEC EDGAR filings (10-K, 10-Q, 8-K, and 20-F for foreign issuers) with "
            "links, plus the extracted Risk Factors section from the latest annual "
            "report. The highest-quality evidence available: the company itself is "
            "legally obliged to disclose what could go wrong."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


# ------------------------------------------------------------- dispatcher


class ToolDispatcher:
    """Executes a tool call from Claude against the real data layer."""

    def __init__(self, ticker: str, registry: SourceRegistry, default_period: str = "1y") -> None:
        self.ticker = ticker
        self.registry = registry
        self.default_period = default_period
        self.call_count = 0

        self._handlers: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
            "get_company_profile": lambda a: get_company_profile(self.ticker),
            "get_price_history": lambda a: get_price_history(
                self.ticker, a.get("period") or self.default_period
            ),
            "get_fundamentals": lambda a: get_fundamentals(self.ticker),
            "get_analyst_estimates": lambda a: get_analyst_estimates(self.ticker),
            "get_recent_news": lambda a: get_recent_news(
                self.ticker, min(int(a.get("limit") or 10), 20)
            ),
            "get_recent_filings": lambda a: get_recent_filings(self.ticker),
            "get_sec_financials": lambda a: get_sec_financials(self.ticker),
            "get_peer_comparison": lambda a: get_peer_comparison(self.ticker),
        }

    def run(self, name: str, args: dict[str, Any]) -> tuple[str, ToolResult]:
        """Execute a tool and return (text for the model, raw result).

        Never raises: an unknown tool or a failing data source comes back as
        text the model can reason about, so one bad call can't end a run.
        """
        self.call_count += 1
        handler = self._handlers.get(name)
        if handler is None:
            return (
                f"No such tool: {name}. Available: {', '.join(self._handlers)}",
                ToolResult.failure(name, "unknown tool", self.ticker),
            )

        try:
            result = handler(args or {})
        except Exception as exc:  # defensive: tools already swallow their own errors
            log.exception("Tool %s raised", name)
            result = ToolResult.failure(name, f"Tool raised: {exc}", self.ticker)

        if not result.ok:
            return (
                f"NO DATA from {name}: {result.error}\n"
                "Do not guess a value to fill this gap. Say the data is unavailable "
                "and reason about what that means for your argument.",
                result,
            )

        refs = self.registry.register(result.sources)
        ref_line = " ".join(f"[{r.ref_id}]" for r in refs)
        payload = json.dumps(result.data, default=str, indent=1)[:12_000]

        text = (
            f"{name} returned (cite these numbers as {ref_line}):\n{payload}"
            + (
                f"\n\nWarnings: {'; '.join(result.warnings)}"
                if result.warnings
                else ""
            )
        )
        return text, result
