"""Phase 1 tests: the grounded tool layer.

Split into two groups:

  * **Offline tests** exercise the parsing, scoring and failure logic against
    fixtures. They are fast, deterministic, and run in CI with no network.
  * **Live tests** (marked `network`) hit the real APIs. They catch upstream
    breakage -- yfinance changing a field name, EDGAR changing a URL shape --
    which is the most likely way this project breaks in practice.

Run everything:      pytest
Skip the live ones:  pytest -m "not network"
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Tests run from the repo root or from backend/; make imports work either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.schemas import SourceRef, ToolResult  # noqa: E402
from tools.filings import (  # noqa: E402
    _densest_risk_passage,
    _extract_risk_factors,
    _looks_like_table_of_contents,
    _risk_language_density,
    _strip_html,
)
from tools.market import _num, _pct  # noqa: E402
from tools.news import _normalise_item, _to_iso  # noqa: E402
from tools.resolver import _normalise, _score_name, resolve_ticker  # noqa: E402

# --------------------------------------------------------------------- offline


class TestToolResult:
    def test_failure_is_structured_not_an_exception(self):
        result = ToolResult.failure("get_fundamentals", "no data", "ZZZZ")
        assert result.ok is False
        assert result.error == "no data"
        assert result.sources == []

    def test_source_ref_cites_by_id(self):
        ref = SourceRef(ref_id="S3", kind="fundamentals", label="yfinance: AAPL")
        assert ref.cite() == "[S3]"


class TestNumericHelpers:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (None, None),
            ("not a number", None),
            (float("nan"), None),
            (float("inf"), None),
            (35.97359, 35.97),      # ratios lose spurious precision
            (3_500_000_000.4, 3_500_000_000),  # big values stay whole
        ],
    )
    def test_num_coerces_and_rounds(self, raw, expected):
        assert _num(raw) == expected

    def test_pct_handles_zero_and_none_denominators(self):
        assert _pct(110, 100) == 10.0
        assert _pct(110, 0) is None
        assert _pct(None, 100) is None


class TestResolver:
    def test_normalise_strips_filler_words(self):
        assert _normalise("Apple stock") == "apple"
        assert _normalise("Tesla, Inc.") == "tesla"

    def test_exact_name_outranks_partial(self):
        assert _score_name("apple", "Apple Inc.") > _score_name("apple", "Apple Hospitality REIT")

    def test_empty_query_is_rejected_without_network(self):
        result = resolve_ticker("")
        assert result.resolved is False
        assert "company name or ticker" in result.message


class TestHtmlAndFilingParsing:
    def test_strip_html_decodes_numeric_entities(self):
        """The bug that hid Apple's Risk Factors: &#160; padding inside headings."""
        html = "<p>Item 1A.&#160;&#160;&#160;Risk&nbsp;Factors</p>"
        assert _strip_html(html) == "Item 1A. Risk Factors"

    def test_table_of_contents_is_detected(self):
        toc = (
            "Item 1. Business 1 Item 1A. Risk Factors 5 Item 1B. Unresolved Staff "
            "Comments 17 Item 2. Properties 17 Item 3. Legal Proceedings 18"
        )
        assert _looks_like_table_of_contents(toc) is True

    def test_risk_prose_is_not_mistaken_for_contents(self):
        prose = (
            "The following summarizes factors that could have a material adverse "
            "effect on the Company's business. Adverse macroeconomic conditions may "
            "materially harm demand, and competition could reduce margins."
        )
        assert _looks_like_table_of_contents(prose) is False
        assert _risk_language_density(prose) > 4

    def test_extracts_section_and_ignores_contents_and_cross_references(self):
        contents = "Item 1. Business 7 Item 1A: Risk Factors 13 Item 1B: Unresolved 42 "
        body = (
            "Item 1A: Risk Factors "
            + (
                "Our business could be materially harmed if demand declines. "
                "Competition may adversely affect margins and litigation could "
                "disrupt operations, which may materially harm results. "
            )
            * 30
        )
        cross_ref = " See Item 1A. Risk Factors of this Annual Report for details. "
        text = contents + body + "Item 1B. Unresolved Staff Comments" + cross_ref

        section = _extract_risk_factors(text)
        assert section is not None
        assert section.lstrip().startswith("Our business could be materially harmed")
        assert "Unresolved Staff Comments" not in section

    def test_returns_none_when_no_risk_section_exists(self):
        assert _extract_risk_factors("Just a quarterly earnings press release. " * 50) is None

    def test_fallback_finds_section_without_any_heading(self):
        """Microsoft's 10-K never writes the heading; density alone must find it."""
        filler = "The company sells software to enterprise customers worldwide. " * 200
        risky = (
            "Our results could be adversely affected by competition. Litigation may "
            "materially harm us, and a failure to innovate could disrupt revenue. "
            "Uncertain macroeconomic conditions may adversely affect demand. "
        ) * 60
        section = _densest_risk_passage(filler + risky + filler)
        assert section is not None
        assert "adversely" in section


class TestNewsNormalisation:
    def test_handles_legacy_flat_payload(self):
        item = {
            "title": "Apple beats estimates",
            "publisher": "Reuters",
            "link": "https://example.com/a",
            "providerPublishTime": 1_700_000_000,
        }
        record = _normalise_item(item)
        assert record["title"] == "Apple beats estimates"
        assert record["publisher"] == "Reuters"
        assert record["published_at"].startswith("2023-11-14")

    def test_handles_nested_payload(self):
        item = {
            "content": {
                "title": "Nvidia announces chip",
                "provider": {"displayName": "Bloomberg"},
                "canonicalUrl": {"url": "https://example.com/b"},
                "pubDate": "2026-01-05T10:00:00Z",
                "summary": "A summary.",
            }
        }
        record = _normalise_item(item)
        assert record["publisher"] == "Bloomberg"
        assert record["url"] == "https://example.com/b"

    def test_item_without_title_is_dropped(self):
        assert _normalise_item({"publisher": "Reuters"}) is None

    def test_to_iso_passes_through_unparseable_values(self):
        assert _to_iso(None) is None
        assert _to_iso("not a date") == "not a date"


class TestGracefulFailure:
    """The rule: a bad ticker degrades the run, it never crashes it."""

    @pytest.mark.parametrize("bad", ["", "   ", "!!!"])
    def test_blank_and_junk_tickers_fail_without_network(self, bad):
        from tools.market import get_fundamentals, get_price_history

        for tool in (get_price_history, get_fundamentals):
            result = tool(bad)
            assert result.ok is False
            assert result.error


# ------------------------------------------------------------------ live/network


@pytest.mark.network
class TestLiveData:
    def test_resolves_plain_company_name(self):
        result = resolve_ticker("Apple")
        assert result.resolved and result.ticker == "AAPL"
        assert result.needs_confirmation is True  # a name, not a symbol -> confirm

    def test_exact_ticker_needs_no_confirmation(self):
        result = resolve_ticker("NVDA")
        assert result.resolved and result.ticker == "NVDA"
        assert result.needs_confirmation is False

    def test_unknown_company_resolves_to_nothing(self):
        assert resolve_ticker("zzzznotarealcompany").resolved is False

    def test_price_history_returns_derived_metrics(self):
        from tools.market import get_price_history

        result = get_price_history("AAPL", "2y")
        assert result.ok
        assert result.data["latest_close"] > 0
        assert result.data["returns_pct"]["1_year"] is not None
        assert result.data["annualised_volatility_pct"] > 0
        assert len(result.data["chart_series"]) > 50
        assert result.sources[0].detail["latest_close"] == result.data["latest_close"]

    def test_fundamentals_have_valuation_and_margins(self):
        from tools.market import get_fundamentals

        result = get_fundamentals("MSFT")
        assert result.ok
        assert result.data["valuation"]["trailing_pe"] > 0
        assert result.data["profitability_pct"]["gross_margin"] > 0

    def test_filings_include_links_and_risk_factors(self):
        from tools.filings import get_recent_filings

        result = get_recent_filings("AAPL")
        assert result.ok
        assert any(f["form"] == "10-Q" for f in result.data["filings"])
        assert all(f["url"].startswith("https://www.sec.gov/") for f in result.data["filings"])
        assert result.data["risk_factors"]

    def test_foreign_issuer_filings_are_supported(self):
        """Toyota files 20-F, not 10-K. Without support this returns nothing."""
        from tools.filings import get_recent_filings

        result = get_recent_filings("TM")
        assert result.ok
        assert result.data["filings"]

    def test_bad_ticker_degrades_the_bundle_but_keeps_the_run(self):
        from tools.bundle import build_evidence_bundle

        bundle = build_evidence_bundle("ZZZZFAKE")
        assert bundle.sources == []
        # Every source must fail cleanly, whatever the current tool count is.
        from tools.bundle import build_evidence_bundle as _b

        assert len(bundle.failures) == len(bundle.results)
        assert len(bundle.results) >= 6
        assert bundle.evidence_brief()  # still renders, so agents can say "no data"

    def test_bundle_assigns_unique_sequential_citation_ids(self):
        from tools.bundle import build_evidence_bundle

        bundle = build_evidence_bundle("AAPL")
        ids = bundle.valid_ref_ids
        assert ids == [f"S{i + 1}" for i in range(len(ids))]
        assert len(set(ids)) == len(ids)
        assert bundle.source("S1") is not None
