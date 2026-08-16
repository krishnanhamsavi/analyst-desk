"""Export finished runs as static JSON the frontend can replay without a backend.

The point: a public demo of this product is a liability if it is live. Runs take
five minutes and cost real money, so an open URL is an invitation for a stranger
to spend your API budget in a loop.

But every run is already fully recorded, the memo, the sources, the verification
and the complete event stream, so a finished run can be replayed exactly as it
happened. The visitor sees the analysts working, the debate, the memo and the
fact-checker's findings, and it costs nothing and cannot be abused.

    python export_demo.py                  # export the best recent runs
    python export_demo.py --runs a1b2 c3d4 # pick specific ones

Writes to frontend/public/demo/, which Vite serves as static files.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from core import db
from core.config import REPO_ROOT

OUTPUT_DIR = REPO_ROOT / "frontend" / "public" / "demo"

# Replay pacing. Real runs take minutes; nobody watches a demo for five of them,
# so events are re-timed on the client. This records the original gaps so the
# replay keeps the *shape* of the run (long thinking pauses, quick tool calls)
# without the wall-clock cost.
MAX_REPLAY_SECONDS = 75


def _pick_best(limit: int) -> list[str]:
    """Choose runs that show the product at its most convincing.

    A run where the fact-checker found nothing proves less than one where it
    caught something, so flagged claims are a feature here, not a defect.
    """
    candidates = [r for r in db.list_runs(50) if r["stage"] == "done"]

    seen_tickers: set[str] = set()
    chosen: list[str] = []
    # Prefer runs that flagged claims, then spread across different companies so
    # the demo does not show the same one three times.
    for row in sorted(candidates, key=lambda r: (-(r["claims_flagged"] or 0))):
        if row["ticker"] in seen_tickers:
            continue
        seen_tickers.add(row["ticker"])
        chosen.append(row["run_id"])
        if len(chosen) >= limit:
            break
    return chosen


def _relative_timings(events: list[dict]) -> list[dict]:
    """Attach a replay offset in seconds to each event, compressed to fit."""
    from datetime import datetime

    stamps: list[float] = []
    for event in events:
        try:
            stamps.append(datetime.fromisoformat(event["ts"]).timestamp())
        except (KeyError, TypeError, ValueError):
            stamps.append(stamps[-1] if stamps else 0.0)

    if not stamps:
        return events

    start, end = stamps[0], stamps[-1]
    span = max(end - start, 1.0)
    scale = min(1.0, MAX_REPLAY_SECONDS / span)

    for event, stamp in zip(events, stamps):
        event["replay_offset_s"] = round((stamp - start) * scale, 2)
    return events


def export_run(run_id: str) -> dict | None:
    stored = db.load_run(run_id)
    if stored is None:
        print(f"  ! {run_id}: not found")
        return None
    if stored.get("stage") != "done":
        print(f"  ! {run_id}: incomplete ({stored.get('stage')}), skipping")
        return None

    artifacts = stored.pop("artifacts", {})
    memo = artifacts.get("memo")
    if not memo:
        print(f"  ! {run_id}: no memo, skipping")
        return None

    sources = artifacts.get("sources") or []
    verification = artifacts.get("verification")

    payload = {
        "run_id": stored["run_id"],
        "status": "done",
        "stage": "done",
        "ok": True,
        "demo": True,
        "created_at": stored.get("created_at"),
        "elapsed_s": stored.get("elapsed_s"),
        "query": stored.get("query"),
        "ticker": stored.get("ticker"),
        "company_name": stored.get("company_name"),
        "horizon": stored.get("horizon"),
        "user_view": stored.get("user_view"),
        "degraded": stored.get("degraded") or [],
        "memo": memo,
        "verification": verification,
        "bull": artifacts.get("bull"),
        "bear": artifacts.get("bear"),
        "risk": (artifacts.get("bear") or {}).get("risk_assessment"),
        "bull_rebuttal": artifacts.get("bull_rebuttal"),
        "bear_rebuttal": artifacts.get("bear_rebuttal"),
        "sources": sources,
        "events": _relative_timings(stored.get("events") or []),
        "suggested_questions": [
            "Explain this simply",
            "Why is this risky?",
            "What could go wrong?",
            "What would change the conclusion?",
        ],
        # The chart lives inside the price source rather than being stored
        # separately, so it is reconstructed here.
        "chart": _chart_from_sources(sources),
        "profile": _profile_from_sources(sources),
    }

    flagged = (
        sum(1 for f in (verification or {}).get("findings", []) if f["verdict"] != "supported")
        if verification
        else 0
    )
    print(
        f"  + {run_id}  {payload['ticker']:6} {len(sources):3} sources, "
        f"{len(payload['events']):3} events, {flagged} flagged"
    )
    return payload


def _chart_from_sources(sources: list[dict]) -> dict:
    for src in sources:
        if src.get("kind") == "price_history":
            detail = src.get("detail") or {}
            return {
                "series": detail.get("chart_series") or [],
                "latest_close": detail.get("latest_close"),
                "as_of": detail.get("as_of"),
                "sma_50": (detail.get("moving_averages") or {}).get("sma_50"),
                "sma_200": (detail.get("moving_averages") or {}).get("sma_200"),
                "range_52w": detail.get("range_52w") or {},
                "returns_pct": detail.get("returns_pct") or {},
                "volatility_pct": detail.get("annualised_volatility_pct"),
            }
    return {"series": []}


def _profile_from_sources(sources: list[dict]) -> dict:
    for src in sources:
        if src.get("kind") == "profile":
            detail = src.get("detail") or {}
            return {
                "sector": detail.get("sector"),
                "industry": detail.get("industry"),
                "website": detail.get("website"),
            }
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export runs for the static demo.")
    parser.add_argument("--runs", nargs="*", help="Specific run ids. Omit to auto-pick.")
    parser.add_argument("--limit", type=int, default=5, help="How many to auto-pick.")
    parser.add_argument("--clean", action="store_true", help="Wipe the demo folder first.")
    args = parser.parse_args(argv)

    if args.clean and OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    run_ids = args.runs or _pick_best(args.limit)
    if not run_ids:
        print("No completed runs to export. Run an analysis first.")
        return 1

    print(f"Exporting {len(run_ids)} run(s) to {OUTPUT_DIR}")
    index = []
    for run_id in run_ids:
        payload = export_run(run_id)
        if payload is None:
            continue
        (OUTPUT_DIR / f"{payload['run_id']}.json").write_text(
            json.dumps(payload, default=str), encoding="utf-8"
        )
        verification = payload.get("verification") or {}
        index.append(
            {
                "run_id": payload["run_id"],
                "ticker": payload["ticker"],
                "company_name": payload["company_name"],
                "horizon": payload["horizon"],
                "confidence": (payload.get("memo") or {}).get("confidence"),
                "verification_verdict": verification.get("overall_verdict"),
                "claims_flagged": sum(
                    1 for f in verification.get("findings", []) if f["verdict"] != "supported"
                ),
                "created_at": payload.get("created_at"),
                "elapsed_s": payload.get("elapsed_s"),
                "user_view": payload.get("user_view"),
            }
        )

    if not index:
        print("Nothing exported.")
        return 1

    (OUTPUT_DIR / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    total_kb = sum(f.stat().st_size for f in OUTPUT_DIR.glob("*.json")) / 1024
    print(f"\nWrote {len(index)} run(s), {total_kb:.0f} KB total.")
    print("Build the frontend with VITE_DEMO_MODE=true to serve them without a backend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
