"""Nightly job: fills realized returns and expires stale ladders.

Usage:
    python -m kangaroo.jobs.nightly
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

import aiohttp

from kangaroo.config import Settings, get_settings
from kangaroo.db import repository as repo
from kangaroo.filters._calendar import is_trading_day
from kangaroo.sources.market_data import MarketDataClient

logger = logging.getLogger(__name__)


async def expire_stale_ladders(
    db_path: str,
    settings: Settings,
    now: datetime,
) -> list[str]:
    """Flip active ladders older than tracking_window_days to 'expired'.

    Returns list of expired tickers.
    """
    window = settings.ladder.tracking_window_days
    # Compute the UTC timestamp that is window trading days before now
    cutoff = _subtract_trading_days(now.date(), window)
    cutoff_utc = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=UTC).isoformat()

    expired = await repo.expire_old_ladders(
        db_path,
        before_timestamp_utc=cutoff_utc,
        closed_timestamp_utc=now.isoformat(),
    )
    for ticker in expired:
        logger.info("Ladder EXPIRED: %s", ticker)
    return expired


def _subtract_trading_days(d: date, n: int) -> date:

    current = d
    counted = 0
    while counted < n:
        current -= timedelta(days=1)
        if is_trading_day(current):
            counted += 1
    return current


async def fill_realized_returns(
    db_path: str,
    market_data: MarketDataClient,
    now: datetime,
) -> None:
    """For each alert from N=1,3,5,20 trading days ago, fetch close and fill returns."""
    for n in [1, 3, 5, 20]:
        period_label = f"{n}d"
        target_date = _subtract_trading_days(now.date(), n)
        target_start = datetime(
            target_date.year, target_date.month, target_date.day, tzinfo=UTC
        ).isoformat()
        target_end = datetime(
            target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=UTC
        ).isoformat()

        alerts = await repo.get_alerts_from_n_trading_days_ago(
            db_path, after_utc=target_start, before_utc=target_end
        )

        for alert in alerts:
            ticker: str = alert["ticker"]
            price_at_alert: float = float(alert["price_at_alert"])
            alert_id: int = int(alert["id"])

            # Skip if already filled
            if alert.get(f"price_{period_label}") is not None:
                continue

            try:
                current_price = await market_data.get_current_price(ticker)
                if current_price and price_at_alert:
                    return_pct = (current_price - price_at_alert) / price_at_alert * 100.0
                    await repo.update_alert_realized_return(
                        db_path,
                        alert_id=alert_id,
                        period=period_label,
                        price=current_price,
                        return_pct=return_pct,
                    )
            except Exception:
                logger.exception(
                    "Failed to fill realized return for alert %d (%s)", alert_id, ticker
                )


async def _main() -> None:
    settings = get_settings()
    now = datetime.now(UTC)

    async with aiohttp.ClientSession() as session:
        market_data = MarketDataClient(api_key=settings.polygon_api_key, session=session)
        await fill_realized_returns(settings.db_path, market_data, now)
        await expire_stale_ladders(settings.db_path, settings, now)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
