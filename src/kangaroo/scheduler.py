"""Long-running scheduler process.

- Pipeline job: every 30 minutes during US market hours (9:30am-4:00pm ET,
  weekdays, non-holidays).
- Nightly job: 5:00pm ET, weekdays.

Usage:
    python -m kangaroo.scheduler
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

from kangaroo.config import get_settings
from kangaroo.db.init import init_db
from kangaroo.filters._calendar import is_trading_day
from kangaroo.jobs.nightly import expire_stale_ladders, fill_realized_returns
from kangaroo.notify import make_notifier
from kangaroo.pipeline import run_pipeline
from kangaroo.sources.market_data import MarketDataClient
from kangaroo.sources.news import NewsClient

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def is_market_hours(dt: datetime) -> bool:
    """True iff *dt* falls within US equity market hours (9:30–16:00 ET, trading days only)."""
    dt_et = dt.astimezone(_ET)
    if not is_trading_day(dt_et.date()):
        return False
    market_open = dt_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = dt_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= dt_et < market_close


# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------


async def _pipeline_job() -> None:
    now = datetime.now(UTC)
    if not is_market_hours(now):
        return

    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        market_data = MarketDataClient(api_key=settings.polygon_api_key, session=session)
        news = NewsClient(
            api_key=settings.finnhub_api_key,
            session=session,
            headline_ttl_minutes=settings.news.headline_cache_ttl_minutes,
            article_ttl_minutes=settings.news.article_cache_ttl_minutes,
        )
        notifier = make_notifier(settings, session)
        try:
            await run_pipeline(settings, settings.db_path, market_data, news, notifier=notifier)
        except Exception:
            logger.exception("Pipeline job raised an unhandled exception")


async def _nightly_job() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    async with aiohttp.ClientSession() as session:
        market_data = MarketDataClient(api_key=settings.polygon_api_key, session=session)
        try:
            await fill_realized_returns(settings.db_path, market_data, now)
            await expire_stale_ladders(settings.db_path, settings, now)
        except Exception:
            logger.exception("Nightly job raised an unhandled exception")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    settings = get_settings()
    init_db(settings.db_path)

    scheduler = AsyncIOScheduler(timezone="America/New_York")

    # Pipeline: every 30 minutes; the job body skips if outside market hours.
    scheduler.add_job(_pipeline_job, "interval", minutes=30, id="pipeline")

    # Nightly: 5pm ET, weekdays (APScheduler cron; holidays not excluded at
    # trigger level — the job's own calendar check handles that if needed).
    scheduler.add_job(
        _nightly_job,
        "cron",
        hour=17,
        minute=0,
        day_of_week="mon-fri",
        id="nightly",
    )

    scheduler.start()
    logger.info("Scheduler started — pipeline every 30 min, nightly at 17:00 ET")

    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
