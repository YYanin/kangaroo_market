"""Phase 2: Market data client tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kangaroo.sources.market_data import MarketDataClient, MarketDataError


def _make_client() -> MarketDataClient:
    session = MagicMock()
    return MarketDataClient(api_key="test", session=session)


class TestMarketDataClient:
    @pytest.mark.asyncio
    async def test_get_decliners_parses_response(self) -> None:
        payload = {
            "tickers": [
                {
                    "ticker": "AAPL",
                    "todaysChangePerc": -6.5,
                    "day": {"v": 50_000_000, "vw": 180.0},
                    "prevDay": {"v": 20_000_000},
                },
                {
                    "ticker": "MSFT",
                    "todaysChangePerc": -2.0,  # below threshold
                    "day": {"v": 30_000_000, "vw": 300.0},
                    "prevDay": {"v": 10_000_000},
                },
            ]
        }
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            results = await client.get_daily_decliners(
                min_pct_drop=4.0, min_relative_volume=2.0, limit=100
            )

        assert len(results) == 1
        assert results[0].ticker == "AAPL"
        assert results[0].pct_change_day == pytest.approx(-6.5)

    @pytest.mark.asyncio
    async def test_get_decliners_retries_on_5xx(self) -> None:
        call_count = 0

        def fake_get(url: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=False)
            if call_count <= 2:
                resp.status = 500
                resp.text = AsyncMock(return_value="error")
            else:
                resp.status = 200
                resp.json = AsyncMock(return_value={"tickers": []})
            return resp

        client = _make_client()
        client._session.get = fake_get  # type: ignore[assignment]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            results = await client.get_daily_decliners(
                min_pct_drop=4.0, min_relative_volume=2.0, limit=100
            )
        assert call_count == 3
        assert results == []

    @pytest.mark.asyncio
    async def test_get_decliners_raises_on_4xx(self) -> None:
        def fake_get(url: str, **kwargs: object) -> MagicMock:
            resp = MagicMock()
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=False)
            resp.status = 401
            resp.text = AsyncMock(return_value="Unauthorized")
            return resp

        client = _make_client()
        client._session.get = fake_get  # type: ignore[assignment]

        with pytest.raises(MarketDataError):
            await client.get_daily_decliners(min_pct_drop=4.0, min_relative_volume=2.0, limit=100)
