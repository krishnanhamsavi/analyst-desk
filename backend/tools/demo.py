"""Phase 1 acceptance CLI.

    python -m tools.demo "Apple"
    python -m tools.demo NVDA --horizon long
    python -m tools.demo "Delta" --brief

Resolves a plain company name to a ticker, fetches every data source, and prints
what the agents will see. If this looks right, the grounding is right -- and
nothing downstream can be better than this.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from tools.bundle import DEFAULT_HORIZON, HORIZONS, build_evidence_bundle
from tools.resolver import resolve_ticker

# Box-drawing output. Windows terminals handle these fine on modern Python.
_RULE = "=" * 78


def _header(text: str) -> None:
    print(f"\n{_RULE}\n{text}\n{_RULE}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.demo",
        description="Resolve a company name or ticker and dump its grounded evidence.",
    )
    parser.add_argument("query", help='Company name or ticker, e.g. "Apple" or AAPL')
    parser.add_argument(
        "--horizon",
        choices=sorted(HORIZONS),
        default=DEFAULT_HORIZON,
        help="Analysis lens. Controls how much history is pulled.",
    )
    parser.add_argument(
        "--brief",
        action="store_true",
        help="Print the agent-facing evidence brief instead of a summary.",
    )
    parser.add_argument("--verbose", action="store_true", help="Show library logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # --- 1. Resolve -------------------------------------------------------
    _header(f'RESOLVING: "{args.query}"')
    resolution = resolve_ticker(args.query)
    print(resolution.message)

    if not resolution.resolved:
        return 1

    if resolution.candidates:
        print("\nCandidates considered:")
        for c in resolution.candidates:
            marker = "->" if c.ticker == resolution.ticker else "  "
            exch = f" on {c.exchange}" if c.exchange else ""
            print(f"  {marker} {c.ticker:<8} {c.name[:44]:<46}{exch} (score {c.score})")

    ticker = resolution.ticker
    assert ticker is not None

    # --- 2. Fetch ---------------------------------------------------------
    _header(f"FETCHING EVIDENCE: {ticker} | horizon={args.horizon}")
    started = time.perf_counter()

    def on_tool(name: str, result) -> None:
        status = "ok " if result.ok else "FAIL"
        note = f"{len(result.sources)} source(s)" if result.ok else result.error
        print(f"  [{status}] {name:<24} {note}")

    bundle = build_evidence_bundle(
        ticker, resolution.name, horizon=args.horizon, on_tool_event=on_tool
    )
    elapsed = time.perf_counter() - started
    print(f"\nFetched in {elapsed:.1f}s | {len(bundle.sources)} citable sources")

    if args.brief:
        _header("AGENT-FACING EVIDENCE BRIEF")
        print(bundle.evidence_brief())
        return 0

    # --- 3. Summarise -----------------------------------------------------
    price = bundle.data("get_price_history")
    fundamentals = bundle.data("get_fundamentals")
    profile = bundle.data("get_company_profile")
    news = bundle.data("get_recent_news")
    filings = bundle.data("get_recent_filings")

    _header(f"{bundle.company_name} ({ticker})")
    if profile:
        print(f"  {profile.get('sector')} / {profile.get('industry')}")
        summary = (profile.get("business_summary") or "")[:260]
        if summary:
            print(f"  {summary}...")

    if price:
        r = price["returns_pct"]
        ma = price["moving_averages"]
        rng = price["range_52w"]
        print("\nPRICE")
        print(f"  Last close        {price['latest_close']}  (as of {price['as_of']})")
        print(
            f"  Returns           1m {r['1_month']}%  3m {r['3_month']}%  "
            f"6m {r['6_month']}%  1y {r['1_year']}%"
        )
        print(f"  Volatility        {price['annualised_volatility_pct']}% annualised")
        print(f"  Trend             {ma['trend']}  (50d {ma['sma_50']}, 200d {ma['sma_200']})")
        print(
            f"  52-week range     {rng['low']} - {rng['high']} "
            f"({rng['pct_below_high']}% from high)"
        )
        print(f"  Max drawdown      {price['max_drawdown_pct']}%")

    if fundamentals:
        v, g, p, h = (
            fundamentals["valuation"],
            fundamentals["growth_pct"],
            fundamentals["profitability_pct"],
            fundamentals["financial_health"],
        )
        print("\nFUNDAMENTALS")
        print(
            f"  Valuation         P/E {v['trailing_pe']}  fwd P/E {v['forward_pe']}  "
            f"P/S {v['price_to_sales']}  EV/EBITDA {v['ev_to_ebitda']}"
        )
        print(
            f"  Growth            revenue {g['revenue_yoy']}%  earnings {g['earnings_yoy']}%"
        )
        print(
            f"  Margins           gross {p['gross_margin']}%  operating "
            f"{p['operating_margin']}%  net {p['net_margin']}%  ROE {p['return_on_equity']}%"
        )
        print(
            f"  Balance sheet     debt/equity {h['debt_to_equity']}  "
            f"current ratio {h['current_ratio']}"
        )

    if filings:
        print("\nSEC FILINGS")
        for f in filings["filings"][:4]:
            print(f"  {f['form']:<6} {f['filed_date']}  {f['url']}")
        risks = filings.get("risk_factors")
        print(
            f"  Risk Factors      {len(risks)} chars extracted"
            if risks
            else "  Risk Factors      not extracted"
        )

    if news:
        print("\nRECENT NEWS")
        for a in news["articles"][:5]:
            date = (a["published_at"] or "")[:10]
            print(f"  {date}  {(a['publisher'] or '?')[:18]:<20} {a['title'][:60]}")

    if bundle.failures:
        print("\nGAPS (handled gracefully, run continues)")
        for tool, err in bundle.failures.items():
            print(f"  {tool}: {err}")

    print(f"\nCitable source ids: {', '.join(bundle.valid_ref_ids)}")
    print("\nRerun with --brief to see exactly what the agents will read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
