"""Phase 4: News client tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kangaroo.sources.news import NewsClient


def _make_client() -> NewsClient:
    session = MagicMock()
    return NewsClient(api_key="test", session=session, headline_ttl_minutes=30)


class TestNewsClient:
    @pytest.mark.asyncio
    async def test_headlines_parsed_correctly(self) -> None:
        payload = [
            {
                "source": "Reuters",
                "headline": "Apple drops on iPhone slowdown",
                "url": "https://reuters.com/1",
                "datetime": 1_700_000_000,
            }
        ]
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            results = await client.get_recent_headlines("AAPL", hours=24)

        assert len(results) == 1
        assert results[0].source == "Reuters"
        assert "iPhone" in results[0].title

    @pytest.mark.asyncio
    async def test_article_body_cached(self) -> None:
        url = "https://example.com/article"
        html = "<p>Article text here</p>"

        fetch_count = 0

        def fake_get(u: str, **kwargs: object) -> MagicMock:
            nonlocal fetch_count
            fetch_count += 1
            resp = MagicMock()
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=False)
            resp.text = AsyncMock(return_value=html)
            return resp

        client = _make_client()
        client._session.get = fake_get  # type: ignore[assignment]

        text1 = await client.get_article_text(url)
        text2 = await client.get_article_text(url)

        assert text1 == html
        assert text2 == html
        assert fetch_count == 1  # second call hits cache
