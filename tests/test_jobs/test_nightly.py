"""Tests for src/kangaroo/jobs/nightly.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from kangaroo.db import repository as repo
from kangaroo.db.init import init_db
from kangaroo.jobs.nightly import (
    _subtract_trading_days,
    expire_stale_ladders,
    fill_realized_returns,
)


@pytest.fixture
def db_path(tmp_path: object) -> str:
    import os

    path = os.path.join(str(tmp_path), "nightly_test.db")
    init_db(path)
    return path


# ---------------------------------------------------------------------------
# fill_realized_returns
# ---------------------------------------------------------------------------


async def test_nightly_fills_realized_returns(db_path: str) -> None:
    now = datetime.now(UTC)
    target_date = _subtract_trading_days(now.date(), 5)
    target_ts = datetime(
        target_date.year, target_date.month, target_date.day, 12, 0, 0, tzinfo=UTC
    ).isoformat()

    alert_id = await repo.insert_alert(
        db_path,
        timestamp_utc=target_ts,
        ticker="NIGHTLY",
        price_at_alert=100.0,
        rung_number=1,
    )

    mock_market_data = AsyncMock()
    mock_market_data.get_current_price.return_value = 110.0

    await fill_realized_returns(db_path, mock_market_data, now)

    alert = await repo.get_alert_by_id(db_path, alert_id)
    assert alert is not None
    assert alert["price_5d"] == pytest.approx(110.0)
    assert alert["return_5d"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# expire_stale_ladders
# ---------------------------------------------------------------------------


async def test_nightly_calls_expire_stale_ladders(db_path: str) -> None:
    now = datetime.now(UTC)
    old_date = _subtract_trading_days(now.date(), 11)
    old_ts = datetime(old_date.year, old_date.month, old_date.day, 12, 0, 0, tzinfo=UTC).isoformat()

    alert_id = await repo.insert_alert(
        db_path,
        timestamp_utc=old_ts,
        ticker="STALE",
        price_at_alert=200.0,
        rung_number=1,
    )
    await repo.upsert_tracked_ticker(
        db_path,
        ticker="STALE",
        first_alert_id=alert_id,
        first_alert_timestamp_utc=old_ts,
        first_alert_price=200.0,
        last_alert_id=alert_id,
        last_alert_timestamp_utc=old_ts,
        last_alert_price=200.0,
        status="active",
    )

    settings = MagicMock()
    settings.ladder.tracking_window_days = 10

    await expire_stale_ladders(db_path, settings, now)

    row = await repo.get_tracked_ticker(db_path, "STALE")
    assert row is not None
    assert row["status"] == "expired"
