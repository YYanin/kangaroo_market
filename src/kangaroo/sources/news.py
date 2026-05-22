"""News client backed by Finnhub.io with in-memory LRU caching."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC
from typing import Any, cast

import aiohttp

logger = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1"
_MAX_RETRIES = 3
_RETRY_STATUSES = {500, 502, 503, 504}


@dataclass
class Headline:
    source: str
    title: str
    url: str
    published_utc: str


class _LRUCache:
    """Simple async-safe LRU cache."""

    def __init__(self, maxsize: int, ttl_seconds: float) -> None:
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[Any, float]] = {}
        self._order: list[str] = []
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            if key not in self._cache:
                return None
            value, ts = self._cache[key]
            if time.monotonic() - ts > self._ttl:
                del self._cache[key]
                self._order.remove(key)
                return None
            self._order.remove(key)
            self._order.append(key)
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            if key in self._cache:
                self._order.remove(key)
            elif len(self._order) >= self._maxsize:
                oldest = self._order.pop(0)
                del self._cache[oldest]
            self._cache[key] = (value, time.monotonic())
            self._order.append(key)


class NewsClient:
    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        headline_ttl_minutes: int = 30,
        article_ttl_minutes: int = 240,
    ) -> None:
        self._key = api_key
        self._session = session
        self._headline_cache: _LRUCache = _LRUCache(
            maxsize=500, ttl_seconds=headline_ttl_minutes * 60
        )
        self._article_cache: _LRUCache = _LRUCache(
            maxsize=200, ttl_seconds=article_ttl_minutes * 60
        )

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:

        url = f"{_BASE}{path}"
        p = dict(params or {})
        p["token"] = self._key
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with self._session.get(url, params=p) as resp:
                    if resp.status in _RETRY_STATUSES:
                        await asyncio.sleep(2**attempt)
                        continue
                    if resp.status >= 400:
                        body = await resp.text()
                        raise RuntimeError(f"Finnhub {path} returned {resp.status}: {body[:200]}")
                    return await resp.json()
            except RuntimeError:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning("News fetch error %s attempt %d: %s", path, attempt + 1, exc)
                await asyncio.sleep(2**attempt)
        raise RuntimeError(f"News fetch failed after {_MAX_RETRIES} attempts: {last_exc}")

    def _hour_bucket(self, ticker: str, hours: int) -> str:
        bucket = int(time.time() / 3600)
        return f"{ticker}:{hours}:{bucket}"

    async def get_recent_headlines(self, ticker: str, hours: int) -> list[Headline]:
        cache_key = self._hour_bucket(ticker, hours)
        cached = await self._headline_cache.get(cache_key)
        if cached is not None:
            return cast(list[Headline], cached)

        from datetime import datetime, timedelta

        now = datetime.now(UTC)
        from_dt = now - timedelta(hours=hours)
        data: list[dict[str, Any]] = await self._get(
            "/company-news",
            {
                "symbol": ticker,
                "from": from_dt.strftime("%Y-%m-%d"),
                "to": now.strftime("%Y-%m-%d"),
            },
        )
        headlines = [
            Headline(
                source=item.get("source", ""),
                title=item.get("headline", ""),
                url=item.get("url", ""),
                published_utc=_ts_to_utc(item.get("datetime", 0)),
            )
            for item in (data or [])
        ]
        await self._headline_cache.set(cache_key, headlines)
        return headlines

    async def get_article_text(self, url: str) -> str:
        cached = await self._article_cache.get(url)
        if cached is not None:
            return cast(str, cached)

        try:
            async with self._session.get(url) as resp:
                text = await resp.text()
        except Exception as exc:
            logger.warning("Failed to fetch article %s: %s", url, exc)
            text = ""

        await self._article_cache.set(url, text)
        return text


def _ts_to_utc(ts: int | float) -> str:
    from datetime import datetime

    dt = datetime.fromtimestamp(float(ts), tz=UTC)
    return dt.isoformat()
