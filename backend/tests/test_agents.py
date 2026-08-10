"""Phase 2 tests: the agent runtime and its plumbing.

These run offline. The agent's *judgement* can't be unit-tested — but everything
that makes its output trustworthy can be: citation ids must be stable and
resolvable, tool failures must degrade rather than crash, and the cache must
never remember a failure as if it were data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.base import PROMPTS_DIR, load_prompt  # noqa: E402
from agents.schemas import Claim, DirectionalThesis  # noqa: E402
from core import cache  # noqa: E402
from core.events import EventBus  # noqa: E402
from core.schemas import SourceRef  # noqa: E402
from tools.claude_tools import TOOL_SCHEMAS, SourceRegistry, ToolDispatcher  # noqa: E402
from tools.market import _dividend_yield_pct  # noqa: E402


def _ref(label: str, kind: str = "fundamentals", url: str | None = None) -> SourceRef:
    return SourceRef(ref_id="", kind=kind, label=label, url=url)


class TestSourceRegistry:
    """Citations are only checkable if ids are stable and shared."""

    def test_assigns_sequential_ids(self):
        reg = SourceRegistry()
        out = reg.register([_ref("a"), _ref("b"), _ref("c")])
        assert [s.ref_id for s in out] == ["S1", "S2", "S3"]

    def test_same_source_keeps_one_id_across_agents(self):
        """Bull and Bear citing the same data must cite the same id."""
        reg = SourceRegistry()
        first = reg.register([_ref("yfinance: AAPL fundamentals")])
        second = reg.register([_ref("yfinance: AAPL fundamentals")])
        assert first[0].ref_id == second[0].ref_id == "S1"
        assert len(reg.all()) == 1

    def test_distinct_sources_get_distinct_ids(self):
        reg = SourceRegistry()
        reg.register([_ref("AAPL fundamentals"), _ref("AAPL price", kind="price_history")])
        assert len(reg.all()) == 2

    def test_lookup_is_case_insensitive_and_handles_unknown(self):
        reg = SourceRegistry()
        reg.register([_ref("a")])
        assert reg.get("s1") is not None
        assert reg.get("S99") is None

    def test_catalogue_renders_without_sources(self):
        assert "no sources" in SourceRegistry().catalogue()


class TestToolDispatcher:
    def test_unknown_tool_returns_text_not_exception(self):
        """A hallucinated tool name must not end the run."""
        dispatcher = ToolDispatcher("AAPL", SourceRegistry())
        text, result = dispatcher.run("get_insider_trades", {})
        assert result.ok is False
        assert "No such tool" in text

    def test_bad_ticker_degrades_with_a_do_not_guess_instruction(self):
        dispatcher = ToolDispatcher("ZZZZFAKE", SourceRegistry())
        text, result = dispatcher.run("get_fundamentals", {})
        assert result.ok is False
        assert "Do not guess" in text

    def test_every_schema_has_a_handler_and_vice_versa(self):
        """A tool Claude can see but we can't run is a guaranteed failed call."""
        dispatcher = ToolDispatcher("AAPL", SourceRegistry())
        assert {t["name"] for t in TOOL_SCHEMAS} == set(dispatcher._handlers)

    def test_schemas_are_well_formed(self):
        for schema in TOOL_SCHEMAS:
            assert schema["name"] and len(schema["description"]) > 80
            assert schema["input_schema"]["type"] == "object"


class TestCachePoisoning:
    """The bug that made every 2-year price fetch fail for hours."""

    def test_empty_payload_is_not_cached(self):
        cache.clear("test_poison")
        calls = {"n": 0}

        def failing_fetch():
            calls["n"] += 1
            return {}  # what a rate-limited upstream returns

        for _ in range(3):
            cache.cached("test_poison", "k", failing_fetch)

        assert calls["n"] == 3, "a failed fetch was cached and never retried"

    def test_real_payload_is_cached(self):
        cache.clear("test_poison_ok")
        calls = {"n": 0}

        def good_fetch():
            calls["n"] += 1
            return {"value": 1}

        for _ in range(3):
            payload, _ = cache.cached("test_poison_ok", "k", good_fetch)
            assert payload == {"value": 1}

        assert calls["n"] == 1
        cache.clear("test_poison_ok")


class TestDividendYield:
    """The bug the Bull agent caught: a 0.45% yield reported as 45%."""

    def test_derives_from_rate_and_price(self):
        assert _dividend_yield_pct({"dividendRate": 1.0, "currentPrice": 223.96}) == 0.45
        assert _dividend_yield_pct({"dividendRate": 2.12, "currentPrice": 87.05}) == 2.44

    def test_falls_back_to_reported_percent_unscaled(self):
        """yfinance reports 0.45 meaning 0.45% -- multiplying by 100 was the bug."""
        assert _dividend_yield_pct({"dividendYield": 0.45}) == 0.45

    def test_rejects_impossible_yields(self):
        assert _dividend_yield_pct({"dividendYield": 4500}) is None

    def test_no_dividend_data_is_none(self):
        assert _dividend_yield_pct({}) is None


class TestPrompts:
    def test_bull_prompt_loads_and_carries_its_core_rules(self):
        prompt = load_prompt("bull")
        lowered = prompt.lower()
        assert "evidence" in lowered
        assert "never tell anyone to buy, sell, or hold" in lowered
        assert "biggest_risk_to_thesis" in prompt

    def test_missing_prompt_fails_loudly(self):
        with pytest.raises(FileNotFoundError):
            load_prompt("no_such_agent")

    def test_every_prompt_file_forbids_advice(self):
        """The research-not-advice rule is a product guarantee, not a suggestion."""
        for path in PROMPTS_DIR.glob("*.md"):
            text = path.read_text(encoding="utf-8").lower()
            assert "buy, sell, or hold" in text, f"{path.name} is missing the advice boundary"


class TestOutputSchemas:
    def test_claim_requires_an_evidence_ref(self):
        """Citation is a schema constraint, not a request."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Claim(claim="Margins are strong", reasoning="because", dimension="profitability")

    def test_thesis_round_trips(self):
        thesis = DirectionalThesis(
            thesis="t",
            supporting_points=[
                Claim(claim="c", evidence_ref="S1", reasoning="r", dimension="growth")
            ],
            key_assumption="k",
            biggest_risk_to_thesis="b",
            what_would_change_my_mind="w",
            confidence="moderate",
            confidence_reasoning="cr",
        )
        assert thesis.supporting_points[0].evidence_ref == "S1"
        assert thesis.evidence_gaps == []


class TestEventBus:
    def test_events_reach_sinks_and_are_replayable(self):
        seen = []
        bus = EventBus("run1", sinks=[seen.append])
        bus.emit("agent_started", agent="Bull")
        bus.emit("tool_called", agent="Bull", tool="get_fundamentals", args={})
        assert len(seen) == 2
        assert [e.type for e in bus.replay()] == ["agent_started", "tool_called"]

    def test_a_broken_sink_cannot_break_a_run(self):
        def explodes(_event):
            raise RuntimeError("UI died")

        bus = EventBus("run2", sinks=[explodes])
        bus.emit("agent_started", agent="Bull")  # must not raise
        assert len(bus.replay()) == 1

    def test_lines_render_for_every_event_type(self):
        bus = EventBus("run3")
        bus.emit("tool_called", agent="Bull", tool="get_fundamentals", args={"period": "1y"})
        bus.emit("tool_result", agent="Bull", ok=True, refs=["S1"])
        bus.emit("error", agent="Bull", message="boom")
        for event in bus.replay():
            assert event.line()
