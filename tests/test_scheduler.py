"""Tests for src/kangaroo/scheduler.py — market-hours guard logic."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from kangaroo.scheduler import is_market_hours

ET = ZoneInfo("America/New_York")


def test_scheduler_does_not_run_outside_market_hours() -> None:
    # Wednesday 2026-05-20 at 20:00 ET — well after market close
    dt = datetime(2026, 5, 20, 20, 0, 0, tzinfo=ET)
    assert is_market_hours(dt) is False


def test_scheduler_does_not_run_on_weekends() -> None:
    # Saturday 2026-05-23 at 10:30 ET — within clock-hours but not a trading day
    dt = datetime(2026, 5, 23, 10, 30, 0, tzinfo=ET)
    assert is_market_hours(dt) is False


def test_scheduler_does_not_run_on_us_holidays() -> None:
    # MLK Day 2026-01-19 at 10:30 ET — US holiday, markets closed
    dt = datetime(2026, 1, 19, 10, 30, 0, tzinfo=ET)
    assert is_market_hours(dt) is False


def test_scheduler_runs_during_market_hours() -> None:
    # Wednesday 2026-05-20 at 10:30 ET — normal trading day mid-morning
    dt = datetime(2026, 5, 20, 10, 30, 0, tzinfo=ET)
    assert is_market_hours(dt) is True


def test_scheduler_does_not_run_before_open() -> None:
    # Wednesday 2026-05-20 at 09:00 ET — before 9:30am open
    dt = datetime(2026, 5, 20, 9, 0, 0, tzinfo=ET)
    assert is_market_hours(dt) is False


def test_scheduler_does_not_run_at_or_after_close() -> None:
    # Wednesday 2026-05-20 at 16:00 ET — at exactly 4pm (market closed)
    dt = datetime(2026, 5, 20, 16, 0, 0, tzinfo=ET)
    assert is_market_hours(dt) is False
