"""Quality filter: ensure ticker meets 'well-established company' criteria."""

from __future__ import annotations

from dataclasses import dataclass

from kangaroo.config import QualitySettings
from kangaroo.sources.market_data import Fundamentals


@dataclass
class FilterResult:
    passed: bool
    reason: str | None = None


def apply_quality_filter(
    ticker: str,
    fundamentals: Fundamentals,
    settings: QualitySettings,
) -> FilterResult:
    """Return passed=True only if all quality criteria are met. Pure function."""
    if fundamentals.market_cap < settings.min_market_cap:
        return FilterResult(
            passed=False,
            reason=(
                f"market_cap_below_floor: {fundamentals.market_cap:.0f} < "
                f"{settings.min_market_cap:.0f}"
            ),
        )

    if settings.require_positive_ttm_income and fundamentals.ttm_net_income <= 0:
        return FilterResult(
            passed=False,
            reason=f"negative_ttm_income: {fundamentals.ttm_net_income:.0f}",
        )

    if fundamentals.avg_daily_dollar_volume_30d < settings.min_avg_daily_dollar_volume:
        return FilterResult(
            passed=False,
            reason=(
                f"low_avg_dollar_volume: {fundamentals.avg_daily_dollar_volume_30d:.0f} < "
                f"{settings.min_avg_daily_dollar_volume:.0f}"
            ),
        )

    if fundamentals.security_type not in settings.allowed_security_types:
        return FilterResult(
            passed=False,
            reason=f"non_common_stock: type={fundamentals.security_type!r}",
        )

    return FilterResult(passed=True)
