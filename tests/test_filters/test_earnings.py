"""Phase 3: Earnings blackout filter tests."""

from __future__ import annotations

from datetime import date

from kangaroo.config import EarningsSettings
from kangaroo.filters.earnings import apply_earnings_blackout

_SETTINGS = EarningsSettings(blackout_days=5)
_TODAY = date(2025, 6, 1)


class TestEarningsBlackout:
    def test_passes_when_earnings_more_than_5_trading_days_out(self) -> None:
        next_earnings = date(2025, 6, 12)  # 8+ trading days away
        result = apply_earnings_blackout("AAPL", next_earnings, _TODAY, _SETTINGS)
        assert result.passed is True

    def test_fails_when_earnings_within_window(self) -> None:
        next_earnings = date(2025, 6, 4)  # 3 trading days away
        result = apply_earnings_blackout("AAPL", next_earnings, _TODAY, _SETTINGS)
        assert result.passed is False
        assert result.reason is not None
        assert "earnings_in_" in result.reason

    def test_passes_when_no_earnings_date_known(self) -> None:
        # No upcoming earnings on the calendar → pass (positive exclusion only)
        result = apply_earnings_blackout("AAPL", None, _TODAY, _SETTINGS)
        assert result.passed is True
