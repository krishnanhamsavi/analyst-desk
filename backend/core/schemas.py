"""Shared data contracts for the tool layer.

Two ideas carry the whole grounding story:

  SourceRef  -- a citable pointer to where a number came from. Every tool
                attaches one or more. Agents cite these by `ref_id`, and the
                Fact-Checker later resolves `ref_id` back to the raw payload.

  ToolResult -- the uniform envelope every tool returns. Tools never raise and
                never return bare dicts; a failure is a ToolResult with ok=False
                so a single bad data source can never crash an analysis run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceKind = Literal[
    "price_history",
    "fundamentals",
    "profile",
    "filing",
    "news",
    "estimates",
    "resolver",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceRef(BaseModel):
    """A citable pointer to a piece of retrieved evidence."""

    ref_id: str = Field(description="Short id agents cite inline, e.g. 'S1'.")
    kind: SourceKind
    label: str = Field(description="Human-readable, e.g. 'yfinance: AAPL fundamentals'.")
    url: str | None = None
    fetched_at: datetime = Field(default_factory=_utcnow)
    from_cache: bool = False
    detail: dict[str, Any] = Field(
        default_factory=dict,
        description="The specific values this ref vouches for, so the Fact-Checker "
        "can verify a claim without re-fetching.",
    )

    def cite(self) -> str:
        return f"[{self.ref_id}]"


class ToolResult(BaseModel):
    """Uniform envelope for every tool call."""

    tool: str
    ok: bool = True
    ticker: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    sources: list[SourceRef] = Field(default_factory=list)
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def failure(cls, tool: str, error: str, ticker: str | None = None) -> "ToolResult":
        """A structured 'no data' result. Tools return this instead of raising."""
        return cls(tool=tool, ok=False, ticker=ticker, error=error)

    def summary_line(self) -> str:
        if not self.ok:
            return f"{self.tool}: NO DATA ({self.error})"
        return f"{self.tool}: ok, {len(self.sources)} source(s)"


class TickerCandidate(BaseModel):
    """One possible match from the name -> ticker resolver."""

    ticker: str
    name: str
    exchange: str | None = None
    kind: str | None = Field(default=None, description="EQUITY, ETF, INDEX, ...")
    score: float = Field(default=0.0, description="Higher = better match.")
    reason: str = ""


class ResolvedTicker(BaseModel):
    """Result of resolving free text like 'apple stock' to a symbol."""

    query: str
    resolved: bool
    ticker: str | None = None
    name: str | None = None
    needs_confirmation: bool = False
    candidates: list[TickerCandidate] = Field(default_factory=list)
    message: str = ""
