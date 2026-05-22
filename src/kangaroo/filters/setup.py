"""Setup filter: verify the Kangaroo price pattern."""

from __future__ import annotations

from dataclasses import dataclass

from kangaroo.config import SetupSettings
from kangaroo.filters._indicators import rsi
from kangaroo.filters.quality import FilterResult
from kangaroo.sources.market_data import OHLCVBar


@dataclass
class SetupMetrics:
    drawdown_from_52w_high: float
    pct_above_200dma: float
    rsi_14: float | None


def compute_setup_metrics(price_history: list[OHLCVBar]) -> SetupMetrics | None:
    """Compute drawdown, 200-dma distance, and RSI from price history. Pure."""
    if len(price_history) < 2:
        return None

    closes = [b.close for b in price_history]
    highs = [b.high for b in price_history]

    high_52w = max(highs[-252:]) if len(highs) >= 252 else max(highs)
    current = closes[-1]

    drawdown = (high_52w - current) / high_52w * 100.0 if high_52w else 0.0

    ma200_bars = closes[-200:] if len(closes) >= 200 else closes
    ma200 = sum(ma200_bars) / len(ma200_bars)
    pct_above = (current - ma200) / ma200 * 100.0 if ma200 else 0.0

    rsi_val = rsi(closes)

    return SetupMetrics(
        drawdown_from_52w_high=drawdown,
        pct_above_200dma=pct_above,
        rsi_14=rsi_val,
    )


def apply_setup_filter(
    ticker: str,
    price_history: list[OHLCVBar],
    settings: SetupSettings,
) -> FilterResult:
    """Return passed=True when drawdown/MA/RSI match the Kangaroo pattern. Pure."""
    metrics = compute_setup_metrics(price_history)
    if metrics is None:
        return FilterResult(passed=False, reason="insufficient_price_history")

    if metrics.drawdown_from_52w_high < settings.min_drawdown_pct:
        return FilterResult(
            passed=False,
            reason=f"drawdown_below_minimum: {metrics.drawdown_from_52w_high:.1f}%",
        )

    if metrics.drawdown_from_52w_high > settings.max_drawdown_pct:
        return FilterResult(
            passed=False,
            reason=f"drawdown_above_maximum: {metrics.drawdown_from_52w_high:.1f}%",
        )

    if metrics.pct_above_200dma < -settings.max_pct_below_200dma:
        return FilterResult(
            passed=False,
            reason=f"well_below_200dma: {metrics.pct_above_200dma:.1f}%",
        )

    if metrics.rsi_14 is not None and metrics.rsi_14 > settings.max_rsi_14:
        return FilterResult(
            passed=False,
            reason=f"rsi_too_high: {metrics.rsi_14:.1f}",
        )

    return FilterResult(passed=True)
