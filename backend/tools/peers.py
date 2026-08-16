"""Peer comparison: is this multiple actually high, or does the whole sector trade there?

The gap this closes is the one the agents kept flagging themselves. "Nvidia trades
at 17.4x forward earnings" is not a claim about anything until you know what AMD
and the rest of the sector trade at. Without peers, every valuation argument in
the memo reduces to comparing a company against its own history, which cannot
tell you whether the *sector* has re-rated.

Peers come from Yahoo's own "similar tickers" endpoint rather than a hand-built
map, so coverage does not rot as companies change. Where that fails we fall back
to a small industry map for the majors, because a wrong peer set is worse than
none and an empty one is worse than either.
"""

from __future__ import annotations

import logging
from statistics import median
from typing import Any

from core import cache
from core.schemas import SourceRef, ToolResult
from tools.market import _num, _yf_info

log = logging.getLogger(__name__)

_TOOL = "get_peer_comparison"
_SIMILAR_URL = "https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/{ticker}"

# Fallback peer sets for large caps where the API is flaky. Deliberately small:
# a curated list that is wrong is worse than no comparison at all.
_FALLBACK_PEERS: dict[str, list[str]] = {
    "AAPL": ["MSFT", "GOOGL", "DELL", "HPQ", "SONY"],
    "MSFT": ["AAPL", "GOOGL", "AMZN", "ORCL", "CRM"],
    "NVDA": ["AMD", "AVGO", "INTC", "QCOM", "TSM"],
    "GOOGL": ["MSFT", "META", "AMZN", "AAPL"],
    "TSLA": ["GM", "F", "RIVN", "LCID", "BYDDY"],
    "KO": ["PEP", "KDP", "MNST", "CCEP"],
    "PEP": ["KO", "MDLZ", "GIS", "KHC"],
    "AMZN": ["WMT", "BABA", "TGT", "COST"],
    "META": ["GOOGL", "SNAP", "PINS", "RDDT"],
    "JPM": ["BAC", "WFC", "C", "GS"],
    "BAC": ["JPM", "WFC", "C", "USB"],
}

# Comparing a bank to a chipmaker teaches nothing, so peers must share the
# company's industry unless we had to fall back to the curated list.
_MAX_PEERS = 5


def _similar_tickers(ticker: str) -> list[str]:
    """Ask Yahoo which companies it considers comparable."""

    def fetch() -> list[str]:
        from curl_cffi import requests as cr

        resp = cr.get(_SIMILAR_URL.format(ticker=ticker), impersonate="chrome", timeout=20)
        resp.raise_for_status()
        payload = resp.json() or {}
        results = (payload.get("finance") or {}).get("result") or []
        if not results:
            return []
        return [
            str(row.get("symbol")).upper()
            for row in (results[0].get("recommendedSymbols") or [])
            if row.get("symbol")
        ]

    try:
        peers, _ = cache.cached(_TOOL, f"similar|{ticker}", fetch, ttl_hours=24 * 7)
        return peers or []
    except Exception as exc:
        log.debug("Peer lookup failed for %s: %s", ticker, exc)
        return []


def _snapshot(ticker: str) -> dict[str, Any] | None:
    """The handful of comparable numbers, for one company."""
    try:
        info, _ = _yf_info(ticker)
    except Exception:
        return None
    if not info or not info.get("symbol"):
        return None

    def pct(key: str) -> float | None:
        value = _num(info.get(key))
        return round(value * 100, 2) if value is not None else None

    return {
        "ticker": ticker,
        "name": info.get("shortName") or info.get("longName") or ticker,
        "industry": info.get("industry"),
        "market_cap": _num(info.get("marketCap")),
        "trailing_pe": _num(info.get("trailingPE")),
        "forward_pe": _num(info.get("forwardPE")),
        "price_to_sales": _num(info.get("priceToSalesTrailing12Months")),
        "ev_to_ebitda": _num(info.get("enterpriseToEbitda")),
        "revenue_growth_pct": pct("revenueGrowth"),
        "gross_margin_pct": pct("grossMargins"),
        "operating_margin_pct": pct("operatingMargins"),
        "net_margin_pct": pct("profitMargins"),
    }


def _median_of(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [r[key] for r in rows if r.get(key) is not None]
    return round(median(values), 2) if values else None


def _verdict(subject: float | None, peer_median: float | None) -> str | None:
    """Plain-English placement, so the agent does not have to do the arithmetic."""
    if subject is None or peer_median in (None, 0):
        return None
    ratio = subject / peer_median
    if ratio >= 1.30:
        return f"{round((ratio - 1) * 100)}% above the peer median"
    if ratio <= 0.70:
        return f"{round((1 - ratio) * 100)}% below the peer median"
    return "roughly in line with peers"


def get_peer_comparison(ticker: str, max_peers: int = _MAX_PEERS) -> ToolResult:
    """Compare this company's valuation and margins against similar companies."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return ToolResult.failure(_TOOL, "No ticker supplied.")

    subject = _snapshot(ticker)
    if subject is None:
        return ToolResult.failure(_TOOL, f"No data available for '{ticker}'.", ticker)

    # Take Yahoo's suggestions and the curated list together. Requiring an exact
    # industry-string match found only one peer for Nvidia, because vendors label
    # "Semiconductors" and "Semiconductor Equipment" differently. A median of one
    # is not a median, so breadth matters more than a perfectly tight match.
    candidates: list[str] = []
    for source_list in (_similar_tickers(ticker), _FALLBACK_PEERS.get(ticker, [])):
        for candidate in source_list:
            if candidate != ticker and candidate not in candidates:
                candidates.append(candidate)

    same_industry: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for candidate in candidates:
        if len(same_industry) >= max_peers:
            break
        snap = _snapshot(candidate)
        if snap is None:
            continue
        if subject.get("industry") and snap.get("industry") == subject["industry"]:
            same_industry.append(snap)
        else:
            other.append(snap)

    # Prefer exact-industry peers, then widen only as far as needed for a
    # meaningful median.
    peers = same_industry[:max_peers]
    widened = False
    if len(peers) < 3:
        peers += other[: max_peers - len(peers)]
        widened = bool(other)

    if not peers:
        return ToolResult.failure(
            _TOOL,
            f"Could not identify comparable companies for '{ticker}'. Any valuation "
            "claim must therefore be framed against the company's own history, not "
            "against its sector.",
            ticker,
        )

    medians = {
        key: _median_of(peers, key)
        for key in (
            "trailing_pe",
            "forward_pe",
            "price_to_sales",
            "ev_to_ebitda",
            "revenue_growth_pct",
            "gross_margin_pct",
            "operating_margin_pct",
            "net_margin_pct",
        )
    }

    placement = {
        key: _verdict(subject.get(key), medians.get(key))
        for key in ("trailing_pe", "forward_pe", "price_to_sales", "ev_to_ebitda")
    }

    data = {
        "subject": subject,
        "peers": peers,
        "peer_median": medians,
        "where_it_sits": {k: v for k, v in placement.items() if v},
        "peer_quality": (
            "loose: too few companies share this exact industry, so the group "
            "includes adjacent businesses and the median is indicative only"
            if widened
            else f"same industry as the subject ({subject.get('industry')})"
        ),
        "caveat": (
            "Peer medians describe how the market currently prices this group. A "
            "whole sector can be expensive together, so being in line with peers is "
            "not evidence that a price is reasonable."
        ),
    }

    source = SourceRef(
        ref_id="",
        kind="fundamentals",
        label=f"Peer comparison: {ticker} vs {', '.join(p['ticker'] for p in peers)}",
        url=f"https://finance.yahoo.com/quote/{ticker}/analysis",
        detail={
            "subject": {k: subject[k] for k in ("ticker", "trailing_pe", "forward_pe",
                                                "price_to_sales", "ev_to_ebitda",
                                                "net_margin_pct", "revenue_growth_pct")},
            "peers": [
                {k: p[k] for k in ("ticker", "trailing_pe", "forward_pe",
                                   "price_to_sales", "net_margin_pct")}
                for p in peers
            ],
            "peer_median": medians,
            "where_it_sits": data["where_it_sits"],
        },
    )
    return ToolResult(tool=_TOOL, ticker=ticker, data=data, sources=[source])
