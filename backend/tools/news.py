"""Recent news headlines.

News is the weakest evidence class here -- it's opinionated, often recycled, and
sometimes plain wrong. We still need it, because for a short horizon the live
narrative *is* the thesis. So we hand agents headlines with publisher and date
attached and instruct them (in the prompts) to treat a headline as evidence of
*what is being said*, not of what is true.

yfinance has shipped two different news payload shapes; we normalise both.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core import cache
from core.schemas import SourceRef, ToolResult

log = logging.getLogger(__name__)

_MAX_SUMMARY_CHARS = 400


def _to_iso(value: Any) -> str | None:
    """Normalise epoch seconds or an ISO string to an ISO date string."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return text


def _normalise_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten either yfinance news shape into one record."""
    # Newer yfinance nests everything under "content".
    content = item.get("content") if isinstance(item.get("content"), dict) else None

    if content:
        provider = content.get("provider") or {}
        url = (
            (content.get("canonicalUrl") or {}).get("url")
            or (content.get("clickThroughUrl") or {}).get("url")
        )
        record = {
            "title": content.get("title"),
            "publisher": provider.get("displayName"),
            "url": url,
            "published_at": _to_iso(content.get("pubDate") or content.get("displayTime")),
            "summary": (content.get("summary") or content.get("description") or "")[
                :_MAX_SUMMARY_CHARS
            ],
            "type": content.get("contentType"),
        }
    else:
        record = {
            "title": item.get("title"),
            "publisher": item.get("publisher"),
            "url": item.get("link"),
            "published_at": _to_iso(item.get("providerPublishTime")),
            "summary": (item.get("summary") or "")[:_MAX_SUMMARY_CHARS],
            "type": item.get("type"),
        }

    return record if record["title"] else None


def get_recent_news(ticker: str, limit: int = 10) -> ToolResult:
    """Recent headlines with publisher, date, snippet and link."""
    tool = "get_recent_news"
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return ToolResult.failure(tool, "No ticker supplied.")

    def fetch() -> list[dict[str, Any]]:
        import yfinance as yf

        return yf.Ticker(ticker).news or []

    try:
        # Short TTL: for a "next few weeks" horizon, stale news is worse than none.
        raw, from_cache = cache.cached(tool, ticker, fetch, ttl_hours=4)
    except Exception as exc:
        return ToolResult.failure(tool, f"Could not fetch news: {exc}", ticker)

    articles = [n for n in (_normalise_item(i) for i in raw if isinstance(i, dict)) if n]
    articles = articles[:limit]

    if not articles:
        return ToolResult.failure(
            tool,
            f"No recent news found for '{ticker}'. Absence of coverage is itself "
            "worth noting, but don't treat it as good or bad news.",
            ticker,
        )

    sources = [
        SourceRef(
            ref_id="",
            kind="news",
            label=f"{a['publisher'] or 'News'}: {a['title'][:90]}",
            url=a["url"],
            from_cache=from_cache,
            detail={
                "title": a["title"],
                "publisher": a["publisher"],
                "published_at": a["published_at"],
                "summary": a["summary"],
            },
        )
        for a in articles
    ]

    data = {
        "article_count": len(articles),
        "articles": articles,
        "caveat": (
            "Headlines evidence what is being *said* about the company, not what is "
            "true. Weight them accordingly."
        ),
    }
    return ToolResult(tool=tool, ticker=ticker, data=data, sources=sources)
