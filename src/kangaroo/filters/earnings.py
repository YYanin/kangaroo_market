"""Earnings blackout filter.

Passes (returned=True) unless a confirmed earnings release is within
settings.blackout_days trading days of today.

Absence of earnings date data is treated as a pass (not a fail):
we only block on confirmed upcoming releases.
"""

from __future__ import annotations

from datetime import date

from kangaroo.config import EarningsSettings
from kangaroo.filters._calendar import trading_days_between
from kangaroo.filters.quality import FilterResult


def apply_earnings_blackout(
    ticker: str,
    next_earnings_date: date | None,
    today: date,
    settings: EarningsSettings,
) -> FilterResult:
    """Block ticker if earnings are within blackout_days trading days. Pure function."""
    if next_earnings_date is None:
        return FilterResult(passed=True)

    if next_earnings_date <= today:
        return FilterResult(passed=True)

    days_away = trading_days_between(today, next_earnings_date)

    if days_away <= settings.blackout_days:
        return FilterResult(
            passed=False,
            reason=f"earnings_in_{days_away}_trading_days",
        )

    return FilterResult(passed=True)
