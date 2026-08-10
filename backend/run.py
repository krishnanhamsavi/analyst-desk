"""Phase 3 acceptance CLI: a full desk run, from a name to a verified memo.

    python run.py "Apple"
    python run.py NVDA --horizon short
    python run.py Tesla --view "I think their robotaxi plans will flop"
    python run.py --history

Streams the whole debate live, prints the memo, and shows the Fact-Checker's
audit. Everything is persisted, so any run can be reopened later.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import textwrap

from core import db
from core.events import Event
from orchestrator import Orchestrator
from tools.bundle import DEFAULT_HORIZON, HORIZONS

_RULE = "=" * 78
_THIN = "-" * 78

_VERDICT_MARK = {"supported": "ok  ", "unsupported": "FAIL", "misrepresented": "WARN"}


def _header(text: str) -> None:
    print(f"\n{_RULE}\n{text}\n{_RULE}")


def _wrap(text: str, indent: str = "   ", width: int = 76) -> str:
    return textwrap.fill(str(text), width=width, initial_indent=indent, subsequent_indent=indent)


def _print_event(event: Event) -> None:
    # Keep the live feed readable: show what agents *do*, not every word they think.
    if event.type == "agent_thinking":
        return
    print("  " + event.line())


def _print_memo(result) -> None:
    memo = result.memo
    _header(f"RESEARCH MEMO — {result.company_name} ({result.ticker})")
    print(f"  Horizon: {HORIZONS[result.horizon]['label']}   |   Run: {result.run_id}")
    print("  Research to help you think — not advice telling you what to do.")

    print(f"\n{_THIN}\nIN PLAIN ENGLISH\n{_THIN}")
    print(_wrap(memo.plain_summary))

    gauge = {"high": "|####      | Fairly confident",
             "moderate": "|##        | Mixed",
             "low": "|#         | Uncertain"}[memo.confidence]
    print(f"\nCONFIDENCE  {gauge}")
    print(_wrap(memo.confidence_reasoning))

    print(f"\n{_THIN}\nKEY RISKS\n{_THIN}")
    for risk in memo.key_risks:
        print(_wrap(f"!  {risk}"))

    for title, points, mark in (
        ("THE CASE FOR", memo.bull_case, "^"),
        ("THE CASE AGAINST", memo.bear_case, "v"),
    ):
        print(f"\n{_THIN}\n{title}\n{_THIN}")
        for point in points:
            contested = "" if point.survived_debate else "   (contested in debate)"
            print(_wrap(f"{mark}  {point.point}  [{point.evidence_ref}]{contested}"))

    print(f"\n{_THIN}\nWHAT WOULD HAVE TO BE TRUE\n{_THIN}")
    print("   For the case FOR to win:")
    for cond in memo.bull_needs_to_be_true:
        print(_wrap(f"- {cond}", indent="      "))
    print("\n   For the case AGAINST to win:")
    for cond in memo.bear_needs_to_be_true:
        print(_wrap(f"- {cond}", indent="      "))

    print(f"\n{_THIN}\nHOW THE DEBATE WENT\n{_THIN}")
    print(_wrap(memo.how_the_debate_went))

    if memo.user_view_assessment:
        print(f"\n{_THIN}\nYOUR VIEW, TESTED\n{_THIN}")
        print(_wrap(memo.user_view_assessment))

    print(f"\n{_THIN}\nWHAT THIS MEANS FOR YOU\n{_THIN}")
    print(_wrap(memo.what_this_means_for_you))


def _print_verification(result) -> None:
    print(f"\n{_THIN}\nVERIFICATION — independent check of every claim\n{_THIN}")
    report = result.verification
    if report is None:
        print(_wrap("!! Verification did not run. Treat the claims above as UNCHECKED."))
        return

    flagged = [f for f in report.findings if f.verdict != "supported"]
    print(f"   Verdict: {report.overall_verdict.replace('_', ' ')}   "
          f"({len(report.findings)} claims checked, {len(flagged)} flagged)")
    print(_wrap(report.summary))

    if flagged:
        print("\n   Flagged claims:")
        for finding in flagged:
            print(_wrap(f"[{_VERDICT_MARK[finding.verdict]}] {finding.claim}", indent="      "))
            print(_wrap(finding.explanation, indent="          "))
    else:
        print(_wrap("Every checkable claim matched its cited source.", indent="   "))


def _print_sources(result) -> None:
    print(f"\n{_THIN}\nSOURCES\n{_THIN}")
    for src in result.registry.all():
        url = f"  {src.url}" if src.url else ""
        print(f"   [{src.ref_id}] {src.label}{url}")


def _show_history(limit: int) -> int:
    runs = db.list_runs(limit)
    _header("RUN HISTORY")
    if not runs:
        print("  No runs recorded yet.")
        return 0
    print(f"  {'run':<10}{'ticker':<8}{'horizon':<9}{'confidence':<12}{'verified':<20}when")
    for r in runs:
        verdict = (r["verification_verdict"] or "not run").replace("_", " ")
        print(
            f"  {r['run_id']:<10}{(r['ticker'] or '-'):<8}{(r['horizon'] or '-'):<9}"
            f"{(r['confidence'] or '-'):<12}{verdict:<20}"
            f"{(r['created_at'] or '').replace('T', ' ')[:16]}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python run.py",
        description="Run the full analyst desk on a company and print a verified memo.",
    )
    parser.add_argument("query", nargs="?", help='Company name or ticker, e.g. "Apple"')
    parser.add_argument("--horizon", choices=sorted(HORIZONS), default=DEFAULT_HORIZON)
    parser.add_argument("--view", default=None, help="Your own take, for the agents to test")
    parser.add_argument("--history", action="store_true", help="List past runs and exit")
    parser.add_argument("--json", default=None, help="Also write the full result to this path")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.history:
        return _show_history(20)
    if not args.query:
        parser.error("a company name or ticker is required (or use --history)")

    _header(f'ANALYST DESK — "{args.query}" | horizon={args.horizon}')
    print("  Bull, Bear and Risk research independently, then debate. A moderator")
    print("  writes the memo and an independent checker verifies every claim.\n")

    result = Orchestrator(sinks=[_print_event]).run(
        args.query, horizon=args.horizon, user_view=args.view
    )

    if not result.ok:
        print(f"\nRun did not complete: {result.error}")
        db.save_run(result)
        return 1

    _print_memo(result)
    _print_verification(result)
    _print_sources(result)

    if result.degraded:
        print(f"\n{_THIN}\nDEGRADED STEPS (the run continued without these)\n{_THIN}")
        for note in result.degraded:
            print(_wrap(f"- {note}"))

    db.save_run(result)

    print(f"\n{_THIN}")
    print(f"  {result.summary_line()}")
    print(f"  Saved. Reopen with:  python run.py --history")

    if args.json:
        payload = {
            "run_id": result.run_id,
            "ticker": result.ticker,
            "memo": json.loads(result.memo.model_dump_json()),
            "verification": json.loads(result.verification.model_dump_json())
            if result.verification
            else None,
            "sources": [json.loads(s.model_dump_json()) for s in result.registry.all()],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"  Wrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
