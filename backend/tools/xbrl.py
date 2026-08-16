"""Financial statement data straight from SEC XBRL.

Why this exists: the fundamentals tool reads Yahoo, which is scraped and
occasionally wrong in ways nobody notices. We already caught it reporting a
0.45% dividend yield as 45%. One field being that wrong means others are wrong
too, just less visibly.

XBRL is the structured data companies file with the regulator themselves. Every
figure here traces to a specific form, filed on a specific date, covering a
specific period, and a company that files a wrong number has a legal problem
rather than a scraping bug. When the two sources disagree, this one wins.

Yahoo is still the right source for prices, and for ratios that need a live
share price. It is the wrong source for what a company earned.
"""

from __future__ import annotations

import logging
from typing import Any

from core import cache
from core.config import settings
from core.schemas import SourceRef, ToolResult

log = logging.getLogger(__name__)

_TOOL = "get_sec_financials"
_COMPANY_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# The line items that carry an argument. Several tags exist for the same concept
# because filers choose different ones, so each entry lists them in preference
# order and we take the first that returns data.
_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps_diluted": ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "shareholders_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
}


def _company_facts(cik: str) -> dict[str, Any]:
    """Every XBRL fact this company has filed, trimmed to the tags we use.

    One request instead of fifteen. The per-concept endpoint also returns an
    empty body for tags a filer reports under a different name, which is how
    Coca-Cola came back with no financials at all despite filing them, so
    reading the full set and picking what is there is both faster and more
    reliable.

    The raw document runs to megabytes, so only the tags we care about are
    kept before caching.
    """

    def fetch() -> dict[str, Any]:
        import httpx

        from tools.filings import _sec_throttle

        wanted = {tag for tags in _CONCEPTS.values() for tag in tags}

        _sec_throttle()
        resp = httpx.get(
            _COMPANY_FACTS.format(cik=cik),
            headers={"User-Agent": settings.sec_user_agent},
            timeout=90,
            follow_redirects=True,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()

        gaap = (resp.json().get("facts") or {}).get("us-gaap") or {}
        return {tag: payload for tag, payload in gaap.items() if tag in wanted}

    try:
        payload, _ = cache.cached("xbrl_facts", cik, fetch, ttl_hours=24 * 7)
        return payload or {}
    except Exception as exc:
        log.warning("XBRL company facts failed for CIK %s: %s", cik, exc)
        return {}


def _annual_by_period_end(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Annual figures keyed by the period end date they cover.

    Keyed by end date rather than by fiscal year on purpose. The `fy` field is
    the year of the *filing* that reported a fact, not the year the fact
    describes, so a 10-K restating three prior years tags all of them with its
    own `fy`. Grouping on that silently mixes years, which is how a gross margin
    of 405% appears: last year's profit over a three-year-old revenue.

    Duration facts (revenue, profit) are filtered to roughly a full year, so
    quarterly rows cannot be mistaken for annual ones. Instant facts (cash,
    assets) have no start date and are taken as-is.
    """
    units = payload.get("units") or {}
    facts = units.get("USD") or units.get("USD/shares") or next(iter(units.values()), [])

    from datetime import date

    by_end: dict[str, dict[str, Any]] = {}
    for fact in facts:
        form = str(fact.get("form", ""))
        end = fact.get("end")
        if not end or not form.startswith("10-K"):
            continue

        start = fact.get("start")
        if start:
            try:
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            except ValueError:
                continue
            if not 340 <= days <= 400:
                continue  # quarterly or cumulative, not a fiscal year

        existing = by_end.get(end)
        # A later filing restates earlier periods; prefer the most recent view.
        if existing is None or str(fact.get("filed", "")) > str(existing.get("filed", "")):
            by_end[end] = {
                "period_end": end,
                "fiscal_year": fact.get("fy"),
                "value": fact.get("val"),
                "form": form,
                "filed": fact.get("filed"),
                "accession": fact.get("accn"),
            }
    return by_end


def _growth_pct(series: list[dict[str, Any]]) -> float | None:
    if len(series) < 2:
        return None
    latest, prior = series[0].get("value"), series[1].get("value")
    if not isinstance(latest, (int, float)) or not isinstance(prior, (int, float)) or not prior:
        return None
    return round((latest / prior - 1) * 100, 2)


def _margin_pct(numerator: Any, denominator: Any) -> float | None:
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
        return None
    if not denominator:
        return None
    return round(numerator / denominator * 100, 2)


def get_sec_financials(ticker: str) -> ToolResult:
    """Audited financials from the company's own SEC filings."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return ToolResult.failure(_TOOL, "No ticker supplied.")

    from tools.filings import _cik_for_ticker

    cik = _cik_for_ticker(ticker)
    if not cik:
        return ToolResult.failure(
            _TOOL,
            f"'{ticker}' is not in the SEC registry, so audited figures are not "
            "available. Non-US listings file elsewhere.",
            ticker,
        )

    # concept -> {period_end: fact}
    #
    # Every tag for a concept is merged rather than stopping at the first that
    # returns data. Filers switch tags between years: Nvidia moved to
    # RevenueFromContractWithCustomer partway through, so taking only the first
    # match pinned the whole report to 2022 while newer years sat under the
    # other tag. Later filings win on ties.
    facts = _company_facts(cik)
    series: dict[str, dict[str, dict[str, Any]]] = {}
    for concept, tags in _CONCEPTS.items():
        merged: dict[str, dict[str, Any]] = {}
        for tag in tags:
            payload = facts.get(tag)
            if not payload:
                continue
            for end, fact in _annual_by_period_end(payload).items():
                existing = merged.get(end)
                if existing is None or str(fact.get("filed", "")) > str(
                    existing.get("filed", "")
                ):
                    merged[end] = fact
        if merged:
            series[concept] = merged

    anchor = series.get("revenue") or series.get("net_income")
    if not anchor:
        return ToolResult.failure(
            _TOOL, f"No XBRL financial data returned for '{ticker}'.", ticker
        )

    # Every figure must describe the same period. Mixing them is what produced a
    # 405% gross margin: this year's profit divided by a three-year-old revenue.
    period_ends = sorted(anchor, reverse=True)
    target_end = period_ends[0]
    prior_end = period_ends[1] if len(period_ends) > 1 else None

    def value_at(name: str, end: str | None) -> Any:
        if end is None:
            return None
        fact = (series.get(name) or {}).get(end)
        return fact.get("value") if fact else None

    def latest(name: str) -> Any:
        return value_at(name, target_end)

    def growth(name: str) -> float | None:
        current, previous = value_at(name, target_end), value_at(name, prior_end)
        if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
            return None
        if not previous:
            return None
        return round((current / previous - 1) * 100, 2)

    revenue = latest("revenue")
    latest_year = anchor[target_end]

    data: dict[str, Any] = {
        "source": "SEC XBRL, as filed by the company",
        "fiscal_year": latest_year.get("fiscal_year"),
        "period_end": latest_year.get("period_end"),
        "filed": latest_year.get("filed"),
        "form": latest_year.get("form"),
        "latest_annual": {
            name: latest(name) for name in series if latest(name) is not None
        },
        "growth_pct": {
            "revenue": growth("revenue"),
            "net_income": growth("net_income"),
            "eps_diluted": growth("eps_diluted"),
        },
        "margins_pct": {
            "gross": _margin_pct(latest("gross_profit"), revenue),
            "operating": _margin_pct(latest("operating_income"), revenue),
            "net": _margin_pct(latest("net_income"), revenue),
        },
        "free_cash_flow": (
            latest("operating_cash_flow") - latest("capex")
            if isinstance(latest("operating_cash_flow"), (int, float))
            and isinstance(latest("capex"), (int, float))
            else None
        ),
        "history": {
            name: [
                {"period_end": end, "value": rows[end]["value"]}
                for end in sorted(rows, reverse=True)[:4]
            ]
            for name, rows in series.items()
            if name in ("revenue", "net_income", "eps_diluted", "operating_income")
        },
        "note": (
            "These figures come from the company's own filings rather than a data "
            "vendor. Where they disagree with market-data fundamentals, these are "
            "authoritative for what the company earned."
        ),
    }

    source = SourceRef(
        ref_id="",
        kind="filing",
        label=(
            f"SEC XBRL: {ticker} audited financials, FY{data['fiscal_year']} "
            f"({data['form']} filed {data['filed']})"
        ),
        url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K",
        detail={
            "fiscal_year": data["fiscal_year"],
            "period_end": data["period_end"],
            "latest_annual": data["latest_annual"],
            "growth_pct": data["growth_pct"],
            "margins_pct": data["margins_pct"],
            "free_cash_flow": data["free_cash_flow"],
        },
    )
    return ToolResult(tool=_TOOL, ticker=ticker, data=data, sources=[source])
