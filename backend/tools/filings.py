"""SEC EDGAR filings.

Filings are the highest-quality evidence available: legally binding, dated, and
free. The company itself has to disclose what could go wrong, which makes the
Risk Factors section far better grounding than any news article.

Flow: ticker -> CIK (SEC's own registry) -> submissions JSON -> filing documents.

EDGAR requires a descriptive User-Agent with contact details. Without one it
returns 403. That comes from SEC_USER_AGENT in .env.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
from typing import Any

from core import cache
from core.config import settings
from core.schemas import SourceRef, ToolResult

log = logging.getLogger(__name__)

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{document}"

# Forms worth reading. Everything else (ownership forms, prospectuses) is noise.
#   US domestic filers: 10-K annual, 10-Q quarterly, 8-K material events.
#   Foreign private issuers (Toyota, Shell, SAP): 20-F annual, 6-K interim. Without
#   these, every non-US listing would come back with no filings at all.
_FORMS_OF_INTEREST = ("10-K", "10-Q", "8-K", "20-F", "6-K", "40-F")

# Annual reports, best-first, for risk-factor extraction. A 10-Q's Item 1A usually
# just says "no material changes since our 10-K", which is worthless as evidence.
_ANNUAL_FORMS = ("10-K", "20-F", "40-F")

# Cap on how much of a filing we download. 10-Ks run to tens of megabytes; the
# sections we want are near the front, and agents can't read 10MB anyway.
_MAX_FILING_BYTES = 4_000_000
_MAX_SECTION_CHARS = 6_000


def _headers() -> dict[str, str]:
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


# EDGAR's fair-access policy caps clients at 10 requests/second and starts
# returning 403s past that. We fetch six tools in parallel, so without a shared
# throttle the filings tool intermittently loses documents -- which looks like a
# parsing bug and is maddening to diagnose.
_SEC_MIN_INTERVAL = 0.15  # seconds between requests, ~6.7/s with headroom
_sec_rate_lock = threading.Lock()
_sec_last_request = 0.0


def _sec_throttle() -> None:
    global _sec_last_request
    with _sec_rate_lock:
        wait = _SEC_MIN_INTERVAL - (time.monotonic() - _sec_last_request)
        if wait > 0:
            time.sleep(wait)
        _sec_last_request = time.monotonic()


def _cik_for_ticker(ticker: str) -> str | None:
    from tools.resolver import _sec_company_map

    entry = _sec_company_map().get(ticker.upper())
    return entry["cik"] if entry else None


def _submissions(cik: str) -> dict[str, Any]:
    def fetch() -> dict[str, Any]:
        import httpx

        _sec_throttle()
        resp = httpx.get(
            _SUBMISSIONS_URL.format(cik=cik), headers=_headers(), timeout=30, follow_redirects=True
        )
        resp.raise_for_status()
        return resp.json()

    payload, _ = cache.cached("sec_submissions", cik, fetch, ttl_hours=12)
    return payload


def _strip_html(raw_html: str) -> str:
    """Crude but dependency-free HTML -> text.

    Entity decoding matters more than it looks: EDGAR filings pad headings with
    `&#160;` (non-breaking space), so "Item 1A.&#160;&#160;Risk Factors" only
    matches a section regex once the entities become real whitespace.
    """
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw_html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\xa0]+", " ", text).strip()


def _looks_like_table_of_contents(section: str) -> bool:
    """A ToC hit is dense with item headings and page numbers, and light on prose."""
    head = section[:800]
    item_headings = len(re.findall(r"(?i)item\s*\d+[a-b]?\s*[.\-:]", head))
    if item_headings >= 3:
        return True
    # ToC rows look like "Risk Factors   21" -- lots of bare numbers, few sentences.
    bare_numbers = len(re.findall(r"\s\d{1,3}\s", head))
    sentences = len(re.findall(r"[a-z]{3,}\s+[a-z]{3,}[^.]*\.", head))
    return bare_numbers > 8 and sentences < 3


# Hedging verbs and harm words that saturate real risk-factor prose and appear
# nowhere near as densely in a cross-reference or a contents listing.
_RISK_LANGUAGE = re.compile(
    r"(?i)\b(could|may|adversely|materially|harm|failure|decline|disrupt|"
    r"unfavorabl|unfavourabl|litigation|competition|uncertain)\w*"
)


# Hits per 1000 chars. A heading-anchored candidate only needs to clear a modest
# bar (its opening paragraph is often scene-setting preamble); the heading-free
# fallback needs a much higher one, since it has no structural evidence at all.
_MIN_RISK_DENSITY = 2.0
_FALLBACK_RISK_DENSITY = 4.5


def _risk_language_density(section: str) -> float:
    """Hits per 1000 characters of hedged harm language."""
    if not section:
        return 0.0
    return len(_RISK_LANGUAGE.findall(section)) / max(len(section) / 1000, 1)


# Where the risk-factor section begins. Filings label it inconsistently:
#   10-K / 10-Q  "Item 1A. Risk Factors", "Item 1A: Risk Factors"
#   20-F         "Item 3.D Risk Factors", or bare "3.D RISK FACTORS" (Toyota)
_RISK_HEADING = re.compile(
    r"(?i)(?:item\s*)?(?:1a|3\s*\.?\s*d)\s*[.\s:)\-]*\s*risk\s+factors"
)

# Where it ends -- the next top-level item, in either filing style.
_RISK_SECTION_END = re.compile(
    r"(?i)item\s*1b[.\s:)\-]*\s*unresolved"
    r"|item\s*2[.\s:)\-]*\s*propert"
    r"|item\s*5[.\s:)\-]*\s*other\s+information"
    r"|(?:item\s*)?4\s*[.\s:)\-]*\s*information\s+on\s+the\s+company"
)


def _extract_risk_factors(text: str) -> str | None:
    """Pull the Risk Factors section out of an annual/quarterly report.

    The heading appears many times in a filing: once in the table of contents,
    once at the real section, and repeatedly as cross-references ("see Item 1A,
    Risk Factors"). Position is not a reliable discriminator -- the real section
    is neither first nor last -- so we score *every* candidate and take the best.

    Two signals separate the real section from the impostors:
      * **Span.** The real section runs for tens of thousands of characters before
        the next item heading. A cross-reference has no such run.
      * **Density of hedged harm language.** Risk prose is saturated with
        "could/may/adversely/materially". Contents listings and boilerplate are not.
    """
    best_section: str | None = None
    best_score = 0.0

    for match in _RISK_HEADING.finditer(text):
        start = match.end()
        end_match = _RISK_SECTION_END.search(text, start)
        # Uncapped span: how far this candidate runs before the next item heading.
        span = (end_match.start() - start) if end_match else (len(text) - start)

        section = text[start : start + min(span, _MAX_SECTION_CHARS)].strip()
        if len(section) < 400 or _looks_like_table_of_contents(section):
            continue

        density = _risk_language_density(section)
        if density < _MIN_RISK_DENSITY:
            continue  # boilerplate or a cross-reference, not the section itself

        # Reward both signals; cap span's contribution so a runaway match that
        # swallows the rest of the document can't win on length alone.
        score = density * min(span, 60_000) / 60_000
        if score > best_score:
            best_score, best_section = score, section

    if best_section is not None:
        return best_section

    # No usable heading. Some filers (Microsoft) never write "Item 1A. Risk
    # Factors" as a heading at all -- they rely on running page headers, which
    # flatten into noise. Fall back to finding the passage that simply *reads*
    # most like risk factors, which needs no heading convention to work.
    return _densest_risk_passage(text)


def _densest_risk_passage(text: str) -> str | None:
    """Locate the risk-factor section by language alone, ignoring headings."""
    window, step = _MAX_SECTION_CHARS, 2_000
    if len(text) < window:
        return None

    best_start, best_density = -1, 0.0
    for start in range(0, len(text) - window, step):
        density = _risk_language_density(text[start : start + window])
        if density > best_density:
            best_density, best_start = density, start

    # Risk factors are far denser than MD&A or business prose; a high bar keeps us
    # from returning some mildly cautious paragraph when the section is absent.
    if best_start < 0 or best_density < _FALLBACK_RISK_DENSITY:
        return None

    # Snap backwards to a sentence boundary so the excerpt doesn't open mid-word.
    boundary = text.rfind(". ", max(0, best_start - 600), best_start + 1)
    start = boundary + 2 if boundary != -1 else best_start
    return text[start : start + _MAX_SECTION_CHARS].strip()


def _fetch_filing_text(url: str) -> str | None:
    """Download a filing and flatten it to text. Retries throttling responses."""

    def fetch() -> dict[str, str]:
        import httpx

        last_error: Exception | None = None
        for attempt in range(3):
            _sec_throttle()
            try:
                with httpx.stream(
                    "GET", url, headers=_headers(), timeout=60, follow_redirects=True
                ) as resp:
                    if resp.status_code in (403, 429, 503):
                        resp.read()
                        raise httpx.HTTPStatusError(
                            f"EDGAR throttled us ({resp.status_code})",
                            request=resp.request,
                            response=resp,
                        )
                    resp.raise_for_status()
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes(chunk_size=131_072):
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= _MAX_FILING_BYTES:
                            break
                raw = b"".join(chunks).decode("utf-8", errors="ignore")
                return {"text": _strip_html(raw)[: _MAX_FILING_BYTES // 4]}
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response is not None and exc.response.status_code == 404:
                    raise  # a wrong URL will never succeed; don't burn retries
                time.sleep(1.5 * (attempt + 1))  # linear backoff
        raise last_error or RuntimeError("filing fetch failed")

    try:
        payload, _ = cache.cached("sec_filing_text", url, fetch, ttl_hours=24 * 30)
        return payload.get("text")
    except Exception as exc:
        log.warning("Could not fetch filing %s: %s", url, exc)
        return None


def get_recent_filings(ticker: str, limit: int = 6, extract_risks: bool = True) -> ToolResult:
    """Latest 10-K / 10-Q / 8-K filings with links, plus extracted Risk Factors.

    `extract_risks` downloads the most recent annual/quarterly report and pulls
    Item 1A. Turn it off when you only need the filing list (it's the slow part).
    """
    tool = "get_recent_filings"
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return ToolResult.failure(tool, "No ticker supplied.")

    cik = _cik_for_ticker(ticker)
    if not cik:
        return ToolResult.failure(
            tool,
            f"'{ticker}' isn't in the SEC registry -- it may be a non-US listing, "
            "which doesn't file with EDGAR.",
            ticker,
        )

    try:
        submissions = _submissions(cik)
    except Exception as exc:
        return ToolResult.failure(tool, f"SEC EDGAR request failed: {exc}", ticker)

    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    if not forms:
        return ToolResult.failure(tool, f"No filings listed for '{ticker}'.", ticker)

    cik_int = str(int(cik))

    # Collect every filing of interest, not just the first `limit`. The newest
    # 10-K is often months behind several 10-Qs and 8-Ks, and it's the document we
    # most want for risk factors.
    all_of_interest: list[dict[str, Any]] = []
    for i, form in enumerate(forms):
        if form not in _FORMS_OF_INTEREST:
            continue
        accession = (recent.get("accessionNumber") or [""] * (i + 1))[i].replace("-", "")
        document = (recent.get("primaryDocument") or [""] * (i + 1))[i]
        all_of_interest.append(
            {
                "form": form,
                "filed_date": (recent.get("filingDate") or [None] * (i + 1))[i],
                "report_period": (recent.get("reportDate") or [None] * (i + 1))[i],
                "description": (recent.get("primaryDocDescription") or [""] * (i + 1))[i],
                "url": _ARCHIVE_URL.format(cik_int=cik_int, accession=accession, document=document),
            }
        )

    filings = all_of_interest[:limit]

    if not filings:
        return ToolResult.failure(
            tool, f"No periodic filings (10-K/10-Q/8-K/20-F) found for '{ticker}'.", ticker
        )

    data: dict[str, Any] = {
        "company_name": submissions.get("name"),
        "cik": cik,
        "sic_description": submissions.get("sicDescription"),
        "filings": filings,
        "risk_factors": None,
        "risk_factors_source": None,
    }
    warnings: list[str] = []

    sources = [
        SourceRef(
            ref_id="",
            kind="filing",
            label=f"SEC EDGAR: {ticker} {f['form']} filed {f['filed_date']}",
            url=f["url"],
            detail={
                "form": f["form"],
                "filed_date": f["filed_date"],
                "report_period": f["report_period"],
                "company": submissions.get("name"),
            },
        )
        for f in filings
    ]

    if extract_risks:
        periodic = next(
            (f for f in all_of_interest if f["form"] in _ANNUAL_FORMS), None
        ) or next((f for f in all_of_interest if f["form"] == "10-Q"), None)
        if periodic:
            text = _fetch_filing_text(periodic["url"])
            risks = _extract_risk_factors(text) if text else None
            if risks:
                data["risk_factors"] = risks
                data["risk_factors_source"] = periodic["url"]
                sources.append(
                    SourceRef(
                        ref_id="",
                        kind="filing",
                        label=(
                            f"SEC EDGAR: {ticker} {periodic['form']} Risk Factors "
                            f"(filed {periodic['filed_date']})"
                        ),
                        url=periodic["url"],
                        detail={
                            "form": periodic["form"],
                            "filed_date": periodic["filed_date"],
                            "section": "Item 1A Risk Factors",
                            "excerpt": risks[:1500],
                        },
                    )
                )
            else:
                warnings.append(
                    "Could not extract the Risk Factors section from the latest report "
                    "(filings vary in structure). Filing links are still available."
                )

    return ToolResult(tool=tool, ticker=ticker, data=data, sources=sources, warnings=warnings)
