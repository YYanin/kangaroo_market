"""Phase 3: Setup filter tests."""

from __future__ import annotations

from kangaroo.config import SetupSettings
from kangaroo.filters.setup import apply_setup_filter
from kangaroo.sources.market_data import OHLCVBar

_SETTINGS = SetupSettings()


def _bars(current_price: float, high_52w: float, ma200: float | None = None) -> list[OHLCVBar]:
    """Build a price history that matches the Kangaroo pattern.

    200 bars at the MA level (used for 200-dma), then a peak at high_52w,
    then 20 steadily declining bars ending at current_price (gives RSI < 40).
    """
    base = ma200 if ma200 is not None else current_price * 1.05
    bars: list[OHLCVBar] = []
    for i in range(200):
        bars.append(
            OHLCVBar(
                date=f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}",
                open=base,
                high=base,
                low=base,
                close=base,
                volume=1_000_000.0,
            )
        )
    # One bar at the 52w high
    bars.append(
        OHLCVBar(
            date="2025-01-01",
            open=high_52w,
            high=high_52w,
            low=high_52w,
            close=high_52w,
            volume=1_000_000.0,
        )
    )
    # 20 steadily declining bars from high_52w to current_price — gives low RSI
    step = (high_52w - current_price) / 20.0
    for j in range(20):
        p = high_52w - step * (j + 1)
        bars.append(
            OHLCVBar(
                date=f"2025-{(j // 30) + 2:02d}-{(j % 30) + 1:02d}",
                open=p,
                high=p * 1.005,
                low=p * 0.995,
                close=p,
                volume=2_000_000.0,
            )
        )
    return bars


class TestSetupFilter:
    def test_passes_in_buyable_drawdown_range(self) -> None:
        # 15% off high, well above 200dma
        bars = _bars(current_price=85.0, high_52w=100.0, ma200=80.0)
        result = apply_setup_filter("AAPL", bars, _SETTINGS)
        assert result.passed is True

    def test_fails_when_drawdown_below_minimum(self) -> None:
        # Only 4% off 52w high
        bars = _bars(current_price=96.0, high_52w=100.0, ma200=80.0)
        result = apply_setup_filter("AAPL", bars, _SETTINGS)
        assert result.passed is False
        assert result.reason is not None
        assert "drawdown_below_minimum" in result.reason

    def test_fails_when_drawdown_above_maximum(self) -> None:
        # 45% off 52w high
        bars = _bars(current_price=55.0, high_52w=100.0, ma200=80.0)
        result = apply_setup_filter("AAPL", bars, _SETTINGS)
        assert result.passed is False
        assert result.reason is not None
        assert "drawdown_above_maximum" in result.reason

    def test_fails_when_well_below_200dma(self) -> None:
        # 20% below 200dma — exceeds max_pct_below_200dma of 15%
        bars = _bars(current_price=80.0, high_52w=100.0, ma200=100.0)
        result = apply_setup_filter("AAPL", bars, _SETTINGS)
        assert result.passed is False
        assert result.reason is not None
        assert "200dma" in result.reason

    def test_fails_when_rsi_above_threshold(self) -> None:
        # All bars going up → RSI near 100
        bars_list: list[OHLCVBar] = []
        for i in range(30):
            p = float(50 + i)
            bars_list.append(
                OHLCVBar(
                    date=f"2025-01-{i + 1:02d}",
                    open=p,
                    high=p * 1.05,
                    low=p,
                    close=p,
                    volume=1_000_000.0,
                )
            )
        # Current price 10% below peak (which is bar[-1])
        peak = bars_list[-1].close
        bars_list.append(
            OHLCVBar(
                date="2025-02-01",
                open=peak * 0.90,
                high=peak,
                low=peak * 0.88,
                close=peak * 0.90,
                volume=2_000_000.0,
            )
        )
        settings = SetupSettings(max_rsi_14=40.0)
        result = apply_setup_filter("AAPL", bars_list, settings)
        # RSI close to 100 → fails
        assert result.passed is False
        assert result.reason is not None
        assert "rsi" in result.reason
