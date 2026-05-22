"""Market data client tests — yfinance backend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kangaroo.sources.market_data import MarketDataClient, MarketDataError


def _screen_response(quotes: list[dict[str, object]]) -> dict[str, object]:
    return {"quotes": quotes}


_AAPL_QUOTE: dict[str, object] = {
    "symbol": "AAPL",
    "regularMarketChangePercent": -6.5,
    "regularMarketVolume": 50_000_000,
    "averageVolume": 20_000_000,
    "regularMarketPrice": 180.0,
}


class TestMarketDataClient:
    def test_constructor_requires_no_args(self) -> None:
        client = MarketDataClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_get_decliners_filters_by_pct_drop(self) -> None:
        quotes = [
            _AAPL_QUOTE,
            {
                "symbol": "MSFT",
                "regularMarketChangePercent": -2.0,  # below min_pct_drop=4
                "regularMarketVolume": 30_000_000,
                "averageVolume": 10_000_000,
                "regularMarketPrice": 300.0,
            },
        ]
        with patch(
            "kangaroo.sources.market_data.yf.screen",
            return_value=_screen_response(quotes),
        ):
            results = await MarketDataClient().get_daily_decliners(
                min_pct_drop=4.0, min_relative_volume=2.0, limit=100
            )

        assert len(results) == 1
        assert results[0].ticker == "AAPL"
        assert results[0].pct_change_day == pytest.approx(-6.5)

    @pytest.mark.asyncio
    async def test_get_decliners_filters_by_relative_volume(self) -> None:
        quotes = [
            _AAPL_QUOTE,
            {
                "symbol": "GOOG",
                "regularMarketChangePercent": -5.0,
                "regularMarketVolume": 1_000_000,
                "averageVolume": 10_000_000,  # rel_vol = 0.1, below threshold
                "regularMarketPrice": 150.0,
            },
        ]
        with patch(
            "kangaroo.sources.market_data.yf.screen",
            return_value=_screen_response(quotes),
        ):
            results = await MarketDataClient().get_daily_decliners(
                min_pct_drop=4.0, min_relative_volume=2.0, limit=100
            )

        assert len(results) == 1
        assert results[0].ticker == "AAPL"

    @pytest.mark.asyncio
    async def test_get_decliners_respects_limit(self) -> None:
        quotes = [
            {
                "symbol": f"X{i:03d}",
                "regularMarketChangePercent": -5.0 - i,
                "regularMarketVolume": 10_000_000,
                "averageVolume": 2_000_000,
                "regularMarketPrice": 100.0,
            }
            for i in range(20)
        ]
        with patch(
            "kangaroo.sources.market_data.yf.screen",
            return_value=_screen_response(quotes),
        ):
            results = await MarketDataClient().get_daily_decliners(
                min_pct_drop=4.0, min_relative_volume=2.0, limit=5
            )

        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_get_decliners_sorted_worst_first(self) -> None:
        quotes = [
            {**_AAPL_QUOTE, "symbol": "A", "regularMarketChangePercent": -5.0},
            {**_AAPL_QUOTE, "symbol": "B", "regularMarketChangePercent": -8.0},
            {**_AAPL_QUOTE, "symbol": "C", "regularMarketChangePercent": -6.0},
        ]
        with patch(
            "kangaroo.sources.market_data.yf.screen",
            return_value=_screen_response(quotes),
        ):
            results = await MarketDataClient().get_daily_decliners(
                min_pct_drop=4.0, min_relative_volume=2.0, limit=100
            )

        assert [r.ticker for r in results] == ["B", "C", "A"]

    @pytest.mark.asyncio
    async def test_get_decliners_raises_market_data_error_on_failure(self) -> None:
        with (
            patch(
                "kangaroo.sources.market_data.yf.screen",
                side_effect=RuntimeError("network down"),
            ),
            pytest.raises(MarketDataError, match="screener failed"),
        ):
            await MarketDataClient().get_daily_decliners(
                min_pct_drop=4.0, min_relative_volume=2.0, limit=100
            )

    @pytest.mark.asyncio
    async def test_get_decliners_handles_empty_response(self) -> None:
        with patch(
            "kangaroo.sources.market_data.yf.screen",
            return_value=_screen_response([]),
        ):
            results = await MarketDataClient().get_daily_decliners(
                min_pct_drop=4.0, min_relative_volume=2.0, limit=100
            )

        assert results == []

    @pytest.mark.asyncio
    async def test_get_fundamentals_normalises_equity_to_cs(self) -> None:
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "quoteType": "EQUITY",
            "marketCap": 500_000_000_000,
            "longName": "Apple Inc.",
            "sector": "Technology",
        }
        mock_ticker.quarterly_income_stmt = None
        mock_ticker.quarterly_financials = None
        mock_ticker.history.return_value = MagicMock(empty=True)

        with patch("kangaroo.sources.market_data.yf.Ticker", return_value=mock_ticker):
            result = await MarketDataClient().get_fundamentals("AAPL")

        assert result.security_type == "CS"
        assert result.company_name == "Apple Inc."
        assert result.market_cap == pytest.approx(500_000_000_000)

    @pytest.mark.asyncio
    async def test_get_fundamentals_raises_on_empty_info(self) -> None:
        mock_ticker = MagicMock()
        mock_ticker.info = {}

        with (
            patch("kangaroo.sources.market_data.yf.Ticker", return_value=mock_ticker),
            pytest.raises(MarketDataError),
        ):
            await MarketDataClient().get_fundamentals("FAKE")

    @pytest.mark.asyncio
    async def test_get_current_price_uses_fast_info(self) -> None:
        mock_ticker = MagicMock()
        mock_ticker.fast_info.last_price = 182.50

        with patch("kangaroo.sources.market_data.yf.Ticker", return_value=mock_ticker):
            price = await MarketDataClient().get_current_price("AAPL")

        assert price == pytest.approx(182.50)

    @pytest.mark.asyncio
    async def test_get_current_price_raises_on_failure(self) -> None:
        mock_ticker = MagicMock()
        mock_ticker.fast_info.last_price = None
        mock_ticker.history.return_value = MagicMock(empty=True)

        with (
            patch("kangaroo.sources.market_data.yf.Ticker", return_value=mock_ticker),
            pytest.raises(MarketDataError),
        ):
            await MarketDataClient().get_current_price("FAKE")
