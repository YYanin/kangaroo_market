"""Phase 3: RSI indicator tests."""

from __future__ import annotations

import pytest

from kangaroo.filters._indicators import rsi


class TestRSI:
    def test_rsi_returns_none_with_insufficient_data(self) -> None:
        closes = [100.0 + i for i in range(10)]
        result = rsi(closes, period=14)
        assert result is None

    def test_rsi_known_value_set(self) -> None:
        # 14 gains of 1.0 followed by 0 losses → RSI should be 100.
        closes = [float(i) for i in range(16)]
        result = rsi(closes, period=14)
        assert result is not None
        assert result == pytest.approx(100.0, abs=0.01)

    def test_rsi_all_losses_returns_zero(self) -> None:
        closes = [100.0 - i for i in range(16)]
        result = rsi(closes, period=14)
        assert result is not None
        assert result == pytest.approx(0.0, abs=0.01)

    def test_rsi_mixed_returns_midrange(self) -> None:
        closes = [100.0 + (1 if i % 2 == 0 else -1) for i in range(30)]
        result = rsi(closes, period=14)
        assert result is not None
        assert 0 < result < 100
