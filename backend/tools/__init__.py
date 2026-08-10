"""The grounded tool layer.

Every agent draws its facts from these functions and nowhere else. That single
constraint is what makes citations checkable: if a claim isn't traceable to a
SourceRef produced here, the Fact-Checker will strike it.

Applying the TLS fix at package import keeps every entry point (CLI, API, tests)
working without each one remembering to call it.
"""

import logging

from core import netsetup

netsetup.apply()

# yfinance logs its own ERROR lines for delisted/unknown symbols before returning
# empty data. We convert those into structured "no data" ToolResults ourselves, so
# its shouting only makes a handled case look like a crash.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

from tools.filings import get_recent_filings  # noqa: E402
from tools.market import (  # noqa: E402
    get_analyst_estimates,
    get_company_profile,
    get_fundamentals,
    get_price_history,
)
from tools.news import get_recent_news  # noqa: E402
from tools.resolver import resolve_ticker  # noqa: E402

__all__ = [
    "resolve_ticker",
    "get_price_history",
    "get_fundamentals",
    "get_company_profile",
    "get_analyst_estimates",
    "get_recent_filings",
    "get_recent_news",
]
