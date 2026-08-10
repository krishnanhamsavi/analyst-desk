"""Phase 2 acceptance CLI: watch a single agent research a company.

    python -m agents.demo "Apple"
    python -m agents.demo NVDA --horizon long
    python -m agents.demo TSLA --view "I think their robotaxi plans will flop"

Prints the live event stream as the agent works, then its structured, cited
output. Every claim shown carries the source it came from.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid

from agents.bull import run_bull
from core.events import Event, EventBus
from tools.bundle import DEFAULT_HORIZON, HORIZONS, build_evidence_bundle
from tools.claude_tools import SourceRegistry
from tools.resolver import resolve_ticker

_RULE = "=" * 78


def _header(text: str) -> None:
    print(f"\n{_RULE}\n{text}\n{_RULE}")


def _wrap(text: str, indent: str = "     ", width: int = 74) -> str:
    import textwrap

    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.demo",
        description="Run the Bull agent against a company and show its cited output.",
    )
    parser.add_argument("query", help='Company name or ticker, e.g. "Apple" or AAPL')
    parser.add_argument("--horizon", choices=sorted(HORIZONS), default=DEFAULT_HORIZON)
    parser.add_argument("--view", default=None, help="Your own gut-feeling take, for the agent to address")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # --- resolve -----------------------------------------------------------
    resolution = resolve_ticker(args.query)
    print(f"\n{resolution.message}")
    if not resolution.resolved or not resolution.ticker:
        return 1

    # --- fetch evidence ----------------------------------------------------
    _header(f"FETCHING EVIDENCE: {resolution.ticker} | horizon={args.horizon}")
    started = time.perf_counter()
    bundle = build_evidence_bundle(resolution.ticker, resolution.name, horizon=args.horizon)
    print(f"  {len(bundle.sources)} sources in {time.perf_counter() - started:.1f}s")
    for tool, err in bundle.failures.items():
        print(f"  [no data] {tool}: {err}")

    # --- run the agent, streaming its work ---------------------------------
    _header(f"BULL AGENT AT WORK — {bundle.company_name}")
    bus = EventBus(run_id=uuid.uuid4().hex[:8], sinks=[lambda e: print("  " + e.line())])
    registry = SourceRegistry()

    try:
        thesis = run_bull(bundle, registry, bus, user_view=args.view)
    except Exception as exc:
        print(f"\nRun failed: {type(exc).__name__}: {exc}")
        return 1

    # --- report ------------------------------------------------------------
    _header(f"BULL CASE — {bundle.company_name} ({bundle.ticker})")
    print("\nTHESIS")
    print(_wrap(thesis.thesis))

    print(f"\nCONFIDENCE: {thesis.confidence.upper()}")
    print(_wrap(thesis.confidence_reasoning))

    print("\nSUPPORTING POINTS")
    for i, point in enumerate(thesis.supporting_points, 1):
        source = registry.get(point.evidence_ref)
        label = source.label if source else "UNKNOWN SOURCE"
        flag = "" if source else "   <-- UNVERIFIABLE CITATION"
        print(f"\n  {i}. [{point.dimension}] {point.claim}")
        print(_wrap(point.reasoning, indent="       "))
        print(f"       source [{point.evidence_ref}] {label}{flag}")

    print("\nKEY ASSUMPTION")
    print(_wrap(thesis.key_assumption))

    print("\nBIGGEST RISK TO THIS THESIS")
    print(_wrap(thesis.biggest_risk_to_thesis))

    print("\nWHAT WOULD CHANGE MY MIND")
    print(_wrap(thesis.what_would_change_my_mind))

    if thesis.evidence_gaps:
        print("\nEVIDENCE GAPS")
        for gap in thesis.evidence_gaps:
            print(_wrap(f"- {gap}"))

    # --- citation audit ----------------------------------------------------
    refs = [p.evidence_ref for p in thesis.supporting_points]
    unknown = [r for r in refs if not registry.get(r)]
    _header("CITATION AUDIT")
    print(f"  claims: {len(refs)} | distinct sources cited: {len(set(refs))}")
    print(f"  unverifiable citations: {len(unknown)}" + (f" {unknown}" if unknown else " (all check out)"))
    print("\nResearch, not advice. This is analysis to think with, not a recommendation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
