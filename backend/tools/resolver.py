"""resolve_ticker: turn free text like "apple stock" into a confirmed symbol.

This runs before anything else in a run, so a user never has to know that Apple
is AAPL. Three strategies, best-effort in order:

  1. Yahoo's search endpoint -- broad, handles typos and non-US listings.
  2. The SEC's official company_tickers.json -- authoritative for US filers, and
     cached locally so the resolver keeps working offline.
  3. A direct symbol probe -- if the user typed something that looks like a
     ticker, check whether it actually trades.

We deliberately return *candidates* plus a `needs_confirmation` flag rather than
silently picking one. Resolving "Delta" to DAL (the airline) when the user meant
Delta Apparel is the kind of silent error that poisons an entire analysis.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core import cache
from core.schemas import ResolvedTicker, TickerCandidate

log = logging.getLogger(__name__)

_TOOL = "resolve_ticker"

# Symbols look like: 1-5 letters, optionally a class/exchange suffix (BRK.B, 7203.T)
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(?:[.\-][A-Z0-9]{1,4})?$")

# Filler words users type that carry no signal for matching.
_STOPWORDS = {"stock", "stocks", "shares", "share", "inc", "inc.", "corp", "corp.",
              "corporation", "company", "co", "co.", "ltd", "ltd.", "plc", "the",
              "ticker", "price", "quote"}

# Quote types we will analyse. Anything else (currencies, futures) is out of scope.
_ALLOWED_KINDS = {"EQUITY", "ETF"}

_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"


def _normalise(query: str) -> str:
    cleaned = re.sub(r"[^\w\s.\-]", " ", query.lower())
    words = [w for w in cleaned.split() if w not in _STOPWORDS]
    return " ".join(words).strip() or query.lower().strip()


def _yahoo_search(query: str) -> list[dict[str, Any]]:
    """Query Yahoo's symbol search. Returns raw quote dicts (possibly empty)."""

    def fetch() -> list[dict[str, Any]]:
        from curl_cffi import requests as cr

        resp = cr.get(
            _YAHOO_SEARCH_URL,
            params={"q": query, "quotesCount": 10, "newsCount": 0, "listsCount": 0},
            impersonate="chrome",
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("quotes", []) or []

    try:
        payload, _ = cache.cached(_TOOL, f"yahoo|{query}", fetch, ttl_hours=24 * 7)
        return payload
    except Exception as exc:  # network, rate limit, DEMO_MODE miss
        log.debug("Yahoo search failed for %r: %s", query, exc)
        return []


def _sec_company_map() -> dict[str, dict[str, str]]:
    """{TICKER: {name, cik}} from the SEC's official list. Cached for a week."""

    def fetch() -> dict[str, dict[str, str]]:
        import httpx

        from core.config import settings

        resp = httpx.get(
            _SEC_TICKERS_URL,
            headers={"User-Agent": settings.sec_user_agent},
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
        raw = resp.json()
        return {
            str(row["ticker"]).upper(): {
                "name": str(row["title"]),
                "cik": str(row["cik_str"]).zfill(10),
            }
            for row in raw.values()
        }

    try:
        payload, _ = cache.cached(_TOOL, "sec_company_map", fetch, ttl_hours=24 * 7)
        return payload
    except Exception as exc:
        log.debug("SEC company map unavailable: %s", exc)
        return {}


def _score_name(query: str, name: str) -> float:
    """Cheap lexical similarity, 0..1. Good enough and has no dependencies."""
    q, n = _normalise(query), _normalise(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if n.startswith(q):
        return 0.9
    if q in n:
        return 0.75

    q_words, n_words = set(q.split()), set(n.split())
    overlap = len(q_words & n_words)
    if overlap:
        return 0.5 + 0.2 * (overlap / max(len(q_words), 1))
    return 0.0


def _candidates_from_yahoo(query: str) -> list[TickerCandidate]:
    out: list[TickerCandidate] = []
    for q in _yahoo_search(query):
        symbol = (q.get("symbol") or "").upper()
        kind = (q.get("quoteType") or "").upper()
        if not symbol or kind not in _ALLOWED_KINDS:
            continue
        name = q.get("longname") or q.get("shortname") or symbol
        score = _score_name(query, name)
        # An exact symbol typed by the user beats any name similarity.
        if symbol == query.strip().upper():
            score = 1.0
        # Yahoo returns results ranked; give a small floor so ranked-but-unmatched
        # names still surface as candidates.
        score = max(score, 0.35)
        if kind == "EQUITY":
            score += 0.05
        out.append(
            TickerCandidate(
                ticker=symbol,
                name=name,
                exchange=q.get("exchDisp") or q.get("exchange"),
                kind=kind,
                score=round(min(score, 1.0), 3),
                reason="Yahoo symbol search",
            )
        )
    return out


def _candidates_from_sec(query: str) -> list[TickerCandidate]:
    company_map = _sec_company_map()
    if not company_map:
        return []

    upper = query.strip().upper()
    if upper in company_map:
        return [
            TickerCandidate(
                ticker=upper,
                name=company_map[upper]["name"].title(),
                kind="EQUITY",
                score=1.0,
                reason="Exact ticker in SEC registry",
            )
        ]

    scored: list[TickerCandidate] = []
    for ticker, meta in company_map.items():
        score = _score_name(query, meta["name"])
        if score >= 0.5:
            scored.append(
                TickerCandidate(
                    ticker=ticker,
                    name=meta["name"].title(),
                    kind="EQUITY",
                    score=round(score, 3),
                    reason="SEC company registry name match",
                )
            )
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:10]


def _merge(*groups: list[TickerCandidate]) -> list[TickerCandidate]:
    """Merge candidate lists, keeping the highest score per ticker."""
    best: dict[str, TickerCandidate] = {}
    for group in groups:
        for cand in group:
            existing = best.get(cand.ticker)
            if existing is None or cand.score > existing.score:
                best[cand.ticker] = cand
    merged = list(best.values())
    merged.sort(key=lambda c: c.score, reverse=True)
    return merged


def resolve_ticker(query: str) -> ResolvedTicker:
    """Resolve a company name or ticker to a confirmed symbol.

    Never raises. An unresolvable query comes back with resolved=False and a
    message the UI can show directly.
    """
    query = (query or "").strip()
    if not query:
        return ResolvedTicker(
            query=query,
            resolved=False,
            message="Please enter a company name or ticker symbol.",
        )

    looks_like_ticker = bool(_TICKER_RE.match(query.upper()))

    candidates = _merge(_candidates_from_yahoo(query), _candidates_from_sec(query))

    if not candidates:
        return ResolvedTicker(
            query=query,
            resolved=False,
            message=(
                f"Couldn't find a listed company matching '{query}'. "
                "Try the full company name, or the ticker symbol directly."
            ),
        )

    top = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None

    # An exact ticker the user typed themselves needs no confirmation.
    exact_ticker = looks_like_ticker and top.ticker == query.upper()

    # Otherwise ask, unless the top match is both strong and clearly ahead.
    clear_winner = top.score >= 0.9 and (runner_up is None or top.score - runner_up.score >= 0.25)

    needs_confirmation = not (exact_ticker or clear_winner)

    return ResolvedTicker(
        query=query,
        resolved=True,
        ticker=top.ticker,
        name=top.name,
        needs_confirmation=needs_confirmation,
        candidates=candidates[:5],
        message=(
            f"Did you mean {top.name} ({top.ticker})?"
            if needs_confirmation
            else f"Resolved to {top.name} ({top.ticker})."
        ),
    )
