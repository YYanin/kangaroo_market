"""Phase 3: Quality filter tests."""

from __future__ import annotations

from kangaroo.config import QualitySettings
from kangaroo.filters.quality import apply_quality_filter
from kangaroo.sources.market_data import Fundamentals


def _good_fundamentals(**overrides: object) -> Fundamentals:
    base = Fundamentals(
        ticker="AAPL",
        market_cap=500_000_000_000.0,
        ttm_net_income=50_000_000_000.0,
        avg_daily_dollar_volume_30d=2_000_000_000.0,
        security_type="CS",
        company_name="Apple Inc",
        sector="Technology",
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


_SETTINGS = QualitySettings()


class TestQualityFilter:
    def test_passes_when_all_criteria_met(self) -> None:
        result = apply_quality_filter("AAPL", _good_fundamentals(), _SETTINGS)
        assert result.passed is True
        assert result.reason is None

    def test_fails_on_market_cap_floor(self) -> None:
        result = apply_quality_filter(
            "AAPL",
            _good_fundamentals(market_cap=5_000_000_000.0),
            _SETTINGS,
        )
        assert result.passed is False
        assert result.reason is not None
        assert "market_cap" in result.reason

    def test_fails_on_negative_ttm_earnings(self) -> None:
        result = apply_quality_filter(
            "AAPL",
            _good_fundamentals(ttm_net_income=-1_000_000.0),
            _SETTINGS,
        )
        assert result.passed is False
        assert result.reason is not None
        assert "income" in result.reason

    def test_fails_on_low_average_dollar_volume(self) -> None:
        result = apply_quality_filter(
            "AAPL",
            _good_fundamentals(avg_daily_dollar_volume_30d=10_000_000.0),
            _SETTINGS,
        )
        assert result.passed is False
        assert result.reason is not None
        assert "volume" in result.reason

    def test_fails_on_non_common_stock_security_type(self) -> None:
        result = apply_quality_filter(
            "AAPL",
            _good_fundamentals(security_type="ETF"),
            _SETTINGS,
        )
        assert result.passed is False
        assert result.reason is not None
        assert "non_common_stock" in result.reason
