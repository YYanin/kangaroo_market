"""Run the pipeline once. Used by the scheduler and for manual testing.

Usage:
    python -m kangaroo.jobs.pipeline_run
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from kangaroo.config import get_settings
from kangaroo.db.init import init_db
from kangaroo.notify import make_notifier
from kangaroo.pipeline import run_pipeline
from kangaroo.sources.market_data import MarketDataClient
from kangaroo.sources.news import NewsClient

logger = logging.getLogger(__name__)


async def _main() -> None:
    settings = get_settings()
    init_db(settings.db_path)

    async with aiohttp.ClientSession() as session:
        market_data = MarketDataClient()
        news = NewsClient(
            api_key=settings.finnhub_api_key,
            session=session,
            headline_ttl_minutes=settings.news.headline_cache_ttl_minutes,
            article_ttl_minutes=settings.news.article_cache_ttl_minutes,
        )
        notifier = make_notifier(settings, session)
        await run_pipeline(settings, settings.db_path, market_data, news, notifier=notifier)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
