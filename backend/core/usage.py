"""Token and cost accounting.

Without this, "token usage is a lot" can only be answered by guessing. Every
model call records what it consumed and what that cost, so a run can be broken
down by agent and the expensive step is a fact rather than a hunch.

Prices are per million tokens, from the published rate card. They live here in
one place because they change, and a stale number in three files is worse than
no number at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# USD per million tokens: (input, output).
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}

# Sonnet 5 introductory pricing runs to the end of August 2026.
_SONNET_5_INTRO_UNTIL = date(2026, 8, 31)
_SONNET_5_INTRO = (2.00, 10.00)

# Cached input is billed at roughly a tenth of the normal rate; cache writes at
# ~1.25x. Both matter here because every agent re-sends the same briefing pack.
_CACHE_READ_MULTIPLIER = 0.1
_CACHE_WRITE_MULTIPLIER = 1.25


def _rates(model: str) -> tuple[float, float]:
    if model.startswith("claude-sonnet-5") and date.today() <= _SONNET_5_INTRO_UNTIL:
        return _SONNET_5_INTRO
    for name, price in _PRICES.items():
        if model.startswith(name):
            return price
    return (5.00, 25.00)  # unknown model: assume Opus-tier rather than under-report


@dataclass
class Usage:
    """Tokens consumed by one call, one agent, or a whole run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.calls += other.calls
        self.cost_usd += other.cost_usd

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def as_dict(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 4),
        }


def from_response(response, model: str) -> Usage:
    """Read the usage block off an API response and price it."""
    raw = getattr(response, "usage", None)
    if raw is None:
        return Usage()

    input_tokens = int(getattr(raw, "input_tokens", 0) or 0)
    output_tokens = int(getattr(raw, "output_tokens", 0) or 0)
    cache_read = int(getattr(raw, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(raw, "cache_creation_input_tokens", 0) or 0)

    in_rate, out_rate = _rates(model)
    cost = (
        input_tokens * in_rate
        + output_tokens * out_rate
        + cache_read * in_rate * _CACHE_READ_MULTIPLIER
        + cache_write * in_rate * _CACHE_WRITE_MULTIPLIER
    ) / 1_000_000

    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        calls=1,
        cost_usd=cost,
    )


@dataclass
class RunUsage:
    """Per-agent breakdown for one run."""

    by_agent: dict[str, Usage] = field(default_factory=dict)

    def record(self, agent: str, usage: Usage) -> None:
        self.by_agent.setdefault(agent, Usage()).add(usage)

    @property
    def total(self) -> Usage:
        combined = Usage()
        for usage in self.by_agent.values():
            combined.add(usage)
        return combined

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total.as_dict(),
            "by_agent": {name: u.as_dict() for name, u in self.by_agent.items()},
        }

    def table(self) -> str:
        """A readable breakdown, most expensive first."""
        rows = sorted(self.by_agent.items(), key=lambda kv: kv[1].cost_usd, reverse=True)
        lines = [f"  {'agent':<14}{'calls':>6}{'in':>10}{'out':>9}{'cached':>10}{'cost':>9}"]
        for name, usage in rows:
            lines.append(
                f"  {name:<14}{usage.calls:>6}{usage.input_tokens:>10,}"
                f"{usage.output_tokens:>9,}{usage.cache_read_tokens:>10,}"
                f"{'$' + format(usage.cost_usd, '.3f'):>9}"
            )
        total = self.total
        lines.append(
            f"  {'TOTAL':<14}{total.calls:>6}{total.input_tokens:>10,}"
            f"{total.output_tokens:>9,}{total.cache_read_tokens:>10,}"
            f"{'$' + format(total.cost_usd, '.3f'):>9}"
        )
        return "\n".join(lines)
