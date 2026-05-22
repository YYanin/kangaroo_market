"""Phase 2: Universe filter tests."""

from __future__ import annotations

from kangaroo.config import UniverseSettings
from kangaroo.filters.universe import apply_universe_filter
from kangaroo.sources.market_data import DeclinerRecord


def _rec(ticker: str, pct: float, rel_vol: float = 3.0) -> DeclinerRecord:
    return DeclinerRecord(
        ticker=ticker, pct_change_day=pct, dollar_volume=1_000_000_000.0, relative_volume=rel_vol
    )


class TestUniverseFilter:
    def test_universe_filter_drops_below_pct_threshold(self) -> None:
        settings = UniverseSettings(min_pct_drop=4.0, min_relative_volume=2.0, max_count=100)
        decliners = [_rec("AAPL", -6.0), _rec("MSFT", -3.9), _rec("GOOG", -5.0)]
        result = apply_universe_filter(decliners, settings)
        tickers = [r.ticker for r in result]
        assert "AAPL" in tickers
        assert "GOOG" in tickers
        assert "MSFT" not in tickers

    def test_universe_filter_drops_below_volume_threshold(self) -> None:
        settings = UniverseSettings(min_pct_drop=4.0, min_relative_volume=2.0, max_count=100)
        decliners = [_rec("AAPL", -6.0, rel_vol=1.5), _rec("MSFT", -5.0, rel_vol=2.5)]
        result = apply_universe_filter(decliners, settings)
        assert len(result) == 1
        assert result[0].ticker == "MSFT"

    def test_universe_filter_caps_at_max_count(self) -> None:
        settings = UniverseSettings(min_pct_drop=4.0, min_relative_volume=2.0, max_count=3)
        decliners = [_rec(f"T{i}", -(4.0 + i * 0.5)) for i in range(10)]
        result = apply_universe_filter(decliners, settings)
        assert len(result) == 3
        # Should contain the largest decliners (most negative pct)
        pcts = [r.pct_change_day for r in result]
        assert all(p <= -4.0 for p in pcts)
        assert pcts == sorted(pcts)
