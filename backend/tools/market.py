"""Market data tools built on yfinance.

Design notes that apply to all four tools here:

  * They return plain JSON-safe dicts, never pandas objects. The cache, the LLM
    tool layer, and the WebSocket all need serialisable data.
  * They compute derived metrics (volatility, moving averages, drawdown) rather
    than dumping raw rows. Agents reason better over "down 18% from its 52-week
    high" than over 250 OHLCV rows, and it keeps token cost sane.
  * Every result carries a SourceRef whose `detail` holds the exact numbers the
    agent may cite, so the Fact-Checker can verify a claim without refetching.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import date, timedelta
from typing import Any

from core import cache
from core.schemas import SourceRef, ToolResult

log = logging.getLogger(__name__)

# Trading days per year, used to annualise daily volatility.
_TRADING_DAYS = 252

VALID_PERIODS = {"1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}


def _num(value: Any) -> float | None:
    """Coerce to a finite float, or None. yfinance is full of NaN and None.

    Large values (market cap, cash) stay whole; ratios get rounded to 2 places.
    A P/E of 35.97359 implies a precision the underlying data does not have, and
    spurious digits make agents quote silly numbers.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f) if abs(f) >= 10_000 else round(f, 2)


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return round((new / old - 1.0) * 100, 2)


def _clean_ticker(ticker: str) -> str:
    return (ticker or "").strip().upper()


def _dividend_yield_pct(info: dict[str, Any]) -> float | None:
    """Dividend yield as a percent.

    Derived from the annual dividend and the share price where both exist,
    because that is arithmetic we control. `dividendYield` is the fallback:
    yfinance reports it already scaled as a percent (NVDA 0.45 means 0.45%),
    which is easy to mistake for a fraction and inflate a 0.45% yield into 45%.
    """
    rate = _num(info.get("dividendRate"))
    price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    if rate and price:
        return round(rate / price * 100, 2)

    reported = _num(info.get("dividendYield"))
    if reported is None:
        return None
    # No listed equity yields more than ~30%; a larger number means the source
    # changed convention on us, and a wrong number is worse than no number.
    return reported if reported <= 30 else None


# ---------------------------------------------------------------- price history


def get_price_history(ticker: str, period: str = "1y") -> ToolResult:
    """Price action plus the derived stats an analyst actually quotes.

    Returns recent returns, annualised volatility, moving averages, 52-week
    range, max drawdown, and a downsampled series for the UI chart.
    """
    tool = "get_price_history"
    ticker = _clean_ticker(ticker)
    if not ticker:
        return ToolResult.failure(tool, "No ticker supplied.")
    if period not in VALID_PERIODS:
        period = "1y"

    def fetch() -> dict[str, Any]:
        import yfinance as yf

        # Yahoo throttles bursts by returning an *empty frame* rather than an
        # error, so an empty result is retried rather than believed. Six tools
        # fetch in parallel at the start of a run, which is exactly the shape
        # that trips the throttle.
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                hist = yf.Ticker(ticker).history(period=period, auto_adjust=True)
                if hist is not None and not hist.empty:
                    hist = hist.dropna(subset=["Close"])
                    return {
                        "dates": [d.strftime("%Y-%m-%d") for d in hist.index],
                        "close": [float(v) for v in hist["Close"]],
                        "volume": [int(v) if v == v else 0 for v in hist["Volume"]],
                    }
            except Exception as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

        if last_error is not None:
            raise last_error
        return {}

    try:
        raw, from_cache = cache.cached(tool, f"{ticker}|{period}", fetch, ttl_hours=12)
    except Exception as exc:
        return ToolResult.failure(tool, f"Could not fetch price history: {exc}", ticker)

    closes: list[float] = raw.get("close") or []
    dates: list[str] = raw.get("dates") or []
    volumes: list[int] = raw.get("volume") or []

    if len(closes) < 2:
        return ToolResult.failure(
            tool, f"No price history returned for '{ticker}'. Is the ticker valid?", ticker
        )

    latest = closes[-1]

    # Look back by *calendar* days, not row counts. A "1 year" fetch contains only
    # ~250 trading rows, so indexing back 252 rows silently falls off the front of
    # the series and returns nothing.
    parsed_dates = [date.fromisoformat(d) for d in dates]
    last_date = parsed_dates[-1]

    def ago(calendar_days: int) -> float | None:
        target = last_date - timedelta(days=calendar_days)
        # Allow a week of slack so a 1y window can still price a 1y return.
        if parsed_dates[0] > target + timedelta(days=7):
            return None
        for i, d in enumerate(parsed_dates):
            if d >= target:
                return closes[i]
        return None

    # Daily simple returns -> annualised standard deviation.
    daily_returns = [
        (closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes)) if closes[i - 1]
    ]
    if len(daily_returns) > 1:
        mean = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        annual_vol = round(math.sqrt(variance) * math.sqrt(_TRADING_DAYS) * 100, 2)
    else:
        annual_vol = None

    def sma(window: int) -> float | None:
        if len(closes) < window:
            return None
        return round(sum(closes[-window:]) / window, 2)

    sma50, sma200 = sma(50), sma(200)
    high_52w = round(max(closes[-_TRADING_DAYS:]), 2)
    low_52w = round(min(closes[-_TRADING_DAYS:]), 2)

    # Largest peak-to-trough fall over the window.
    peak, max_drawdown = closes[0], 0.0
    for price in closes:
        peak = max(peak, price)
        if peak:
            max_drawdown = min(max_drawdown, (price / peak) - 1.0)

    data = {
        "period": period,
        "as_of": dates[-1],
        "latest_close": round(latest, 2),
        "currency_note": "Prices are split/dividend adjusted.",
        "returns_pct": {
            "1_month": _pct(latest, ago(30)),
            "3_month": _pct(latest, ago(91)),
            "6_month": _pct(latest, ago(182)),
            "1_year": _pct(latest, ago(365)),
            "period_total": _pct(latest, closes[0]),
        },
        "annualised_volatility_pct": annual_vol,
        "moving_averages": {
            "sma_50": sma50,
            "sma_200": sma200,
            "price_vs_sma_50_pct": _pct(latest, sma50),
            "price_vs_sma_200_pct": _pct(latest, sma200),
            "trend": (
                "above both 50d and 200d averages"
                if sma50 and sma200 and latest > sma50 and latest > sma200
                else "below both 50d and 200d averages"
                if sma50 and sma200 and latest < sma50 and latest < sma200
                else "mixed vs moving averages"
            ),
        },
        "range_52w": {
            "high": high_52w,
            "low": low_52w,
            "pct_below_high": _pct(latest, high_52w),
            "pct_above_low": _pct(latest, low_52w),
        },
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "avg_daily_volume": int(sum(volumes[-63:]) / max(len(volumes[-63:]), 1)) if volumes else None,
        # Downsampled to ~120 points: enough for a clean chart, cheap to ship.
        "chart_series": [
            {"date": d, "close": round(c, 2)}
            for d, c in list(zip(dates, closes))[:: max(1, len(closes) // 120)]
        ],
    }

    source = SourceRef(
        ref_id="",  # assigned by the bundler in tools.bundle
        kind="price_history",
        label=f"yfinance: {ticker} price history ({period})",
        url=f"https://finance.yahoo.com/quote/{ticker}",
        from_cache=from_cache,
        detail={
            "latest_close": data["latest_close"],
            "as_of": data["as_of"],
            "returns_pct": data["returns_pct"],
            "annualised_volatility_pct": annual_vol,
            "moving_averages": data["moving_averages"],
            "range_52w": data["range_52w"],
            "max_drawdown_pct": data["max_drawdown_pct"],
        },
    )
    return ToolResult(tool=tool, ticker=ticker, data=data, sources=[source])


# ----------------------------------------------------------------- fundamentals


def _yf_info(ticker: str) -> tuple[dict[str, Any], bool]:
    """yfinance `.info` is one network call feeding several tools -- cache it once."""

    def fetch() -> dict[str, Any]:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
        # Drop unserialisable or bulky junk before caching.
        return {k: v for k, v in info.items() if isinstance(v, (str, int, float, bool, type(None)))}

    return cache.cached("yf_info", ticker, fetch, ttl_hours=12)


def get_fundamentals(ticker: str) -> ToolResult:
    """Valuation multiples, growth, margins and balance-sheet health."""
    tool = "get_fundamentals"
    ticker = _clean_ticker(ticker)
    if not ticker:
        return ToolResult.failure(tool, "No ticker supplied.")

    try:
        info, from_cache = _yf_info(ticker)
    except Exception as exc:
        return ToolResult.failure(tool, f"Could not fetch fundamentals: {exc}", ticker)

    if not info or not info.get("symbol"):
        return ToolResult.failure(tool, f"No fundamentals available for '{ticker}'.", ticker)

    def as_pct(key: str) -> float | None:
        v = _num(info.get(key))
        return round(v * 100, 2) if v is not None else None

    data = {
        "market_cap": _num(info.get("marketCap")),
        "enterprise_value": _num(info.get("enterpriseValue")),
        "valuation": {
            "trailing_pe": _num(info.get("trailingPE")),
            "forward_pe": _num(info.get("forwardPE")),
            "peg_ratio": _num(info.get("pegRatio") or info.get("trailingPegRatio")),
            "price_to_sales": _num(info.get("priceToSalesTrailing12Months")),
            "price_to_book": _num(info.get("priceToBook")),
            "ev_to_ebitda": _num(info.get("enterpriseToEbitda")),
        },
        "growth_pct": {
            "revenue_yoy": as_pct("revenueGrowth"),
            "earnings_yoy": as_pct("earningsGrowth"),
            "earnings_quarterly_yoy": as_pct("earningsQuarterlyGrowth"),
        },
        "profitability_pct": {
            "gross_margin": as_pct("grossMargins"),
            "operating_margin": as_pct("operatingMargins"),
            "net_margin": as_pct("profitMargins"),
            "return_on_equity": as_pct("returnOnEquity"),
            "return_on_assets": as_pct("returnOnAssets"),
        },
        "cash_flow": {
            "free_cash_flow": _num(info.get("freeCashflow")),
            "operating_cash_flow": _num(info.get("operatingCashflow")),
            "ebitda": _num(info.get("ebitda")),
        },
        "financial_health": {
            "total_cash": _num(info.get("totalCash")),
            "total_debt": _num(info.get("totalDebt")),
            "debt_to_equity": _num(info.get("debtToEquity")),
            "current_ratio": _num(info.get("currentRatio")),
            "quick_ratio": _num(info.get("quickRatio")),
        },
        "dividend": {
            "yield_pct": _dividend_yield_pct(info),
            "payout_ratio_pct": as_pct("payoutRatio"),
        },
        "share_stats": {
            "shares_outstanding": _num(info.get("sharesOutstanding")),
            "beta": _num(info.get("beta")),
            "short_percent_of_float_pct": as_pct("shortPercentOfFloat"),
        },
    }

    missing = [k for k, v in data["valuation"].items() if v is None]
    warnings = [f"Valuation fields unavailable: {', '.join(missing)}"] if missing else []

    source = SourceRef(
        ref_id="",
        kind="fundamentals",
        label=f"yfinance: {ticker} fundamentals",
        url=f"https://finance.yahoo.com/quote/{ticker}/key-statistics",
        from_cache=from_cache,
        detail=data,
    )
    return ToolResult(tool=tool, ticker=ticker, data=data, sources=[source], warnings=warnings)


# --------------------------------------------------------------------- profile


def get_company_profile(ticker: str) -> ToolResult:
    """What the business actually does -- sector, industry, description, size."""
    tool = "get_company_profile"
    ticker = _clean_ticker(ticker)
    if not ticker:
        return ToolResult.failure(tool, "No ticker supplied.")

    try:
        info, from_cache = _yf_info(ticker)
    except Exception as exc:
        return ToolResult.failure(tool, f"Could not fetch company profile: {exc}", ticker)

    if not info or not info.get("symbol"):
        return ToolResult.failure(tool, f"No profile available for '{ticker}'.", ticker)

    summary = (info.get("longBusinessSummary") or "").strip()
    data = {
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "country": info.get("country"),
        "website": info.get("website"),
        "employees": _num(info.get("fullTimeEmployees")),
        "exchange": info.get("exchange"),
        # Truncated: agents need orientation, not a full annual-report preamble.
        "business_summary": summary[:2000],
    }

    source = SourceRef(
        ref_id="",
        kind="profile",
        label=f"yfinance: {ticker} company profile",
        url=info.get("website") or f"https://finance.yahoo.com/quote/{ticker}/profile",
        from_cache=from_cache,
        detail={k: v for k, v in data.items() if k != "business_summary"},
    )
    return ToolResult(tool=tool, ticker=ticker, data=data, sources=[source])


# ----------------------------------------------------------- analyst estimates


def get_analyst_estimates(ticker: str) -> ToolResult:
    """Street consensus. Context only -- agents must not just parrot it."""
    tool = "get_analyst_estimates"
    ticker = _clean_ticker(ticker)
    if not ticker:
        return ToolResult.failure(tool, "No ticker supplied.")

    try:
        info, from_cache = _yf_info(ticker)
    except Exception as exc:
        return ToolResult.failure(tool, f"Could not fetch analyst estimates: {exc}", ticker)

    if not info or not info.get("symbol"):
        return ToolResult.failure(tool, f"No estimates available for '{ticker}'.", ticker)

    target_mean = _num(info.get("targetMeanPrice"))
    current = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))

    data = {
        "recommendation_key": info.get("recommendationKey"),
        "recommendation_mean": _num(info.get("recommendationMean")),
        "analyst_count": _num(info.get("numberOfAnalystOpinions")),
        "price_targets": {
            "current_price": current,
            "mean": target_mean,
            "high": _num(info.get("targetHighPrice")),
            "low": _num(info.get("targetLowPrice")),
            "implied_upside_pct": _pct(target_mean, current),
        },
        "forward_estimates": {
            "forward_eps": _num(info.get("forwardEps")),
            "trailing_eps": _num(info.get("trailingEps")),
        },
        "caveat": (
            "Sell-side consensus is a crowd opinion, not evidence. Use it as context "
            "about expectations, never as a conclusion."
        ),
    }

    if data["analyst_count"] is None and target_mean is None:
        return ToolResult.failure(tool, f"No analyst coverage found for '{ticker}'.", ticker)

    source = SourceRef(
        ref_id="",
        kind="estimates",
        label=f"yfinance: {ticker} analyst consensus",
        url=f"https://finance.yahoo.com/quote/{ticker}/analysis",
        from_cache=from_cache,
        detail={k: v for k, v in data.items() if k != "caveat"},
    )
    return ToolResult(tool=tool, ticker=ticker, data=data, sources=[source])
