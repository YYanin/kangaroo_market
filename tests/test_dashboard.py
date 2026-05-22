"""Tests for the Phase 8 dashboard (FastAPI + Jinja2)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from kangaroo.dashboard.app import DASHBOARD_HOST, app, get_db_path
from kangaroo.db import repository as repo
from kangaroo.db.init import init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: object) -> str:
    import os

    path = os.path.join(str(tmp_path), "test.db")
    init_db(path)
    return path


@pytest.fixture(autouse=False)
def override_db(db_path: str) -> object:
    app.dependency_overrides[get_db_path] = lambda: db_path
    yield
    app.dependency_overrides.clear()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Today page
# ---------------------------------------------------------------------------


async def test_today_page_renders_with_no_alerts(db_path: str, override_db: object) -> None:
    async with _client() as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "no alerts today" in resp.text.lower()


async def test_today_page_renders_with_seeded_alerts(db_path: str, override_db: object) -> None:
    from datetime import UTC, datetime

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    await repo.insert_alert(
        db_path, timestamp_utc=ts, ticker="AAPL", price_at_alert=150.0, company_name="Apple Inc"
    )
    await repo.insert_alert(
        db_path, timestamp_utc=ts, ticker="MSFT", price_at_alert=300.0, company_name="Microsoft"
    )

    async with _client() as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "AAPL" in resp.text
    assert "MSFT" in resp.text


# ---------------------------------------------------------------------------
# Ladders page
# ---------------------------------------------------------------------------


async def test_ladders_page_shows_active_only(db_path: str, override_db: object) -> None:
    from datetime import UTC, datetime

    ts = datetime.now(UTC).isoformat()

    # Active ladder
    aid_active = await repo.insert_alert(
        db_path, timestamp_utc=ts, ticker="ACTIVE", price_at_alert=100.0
    )
    await repo.upsert_tracked_ticker(
        db_path,
        ticker="ACTIVE",
        first_alert_id=aid_active,
        first_alert_timestamp_utc=ts,
        first_alert_price=100.0,
        last_alert_id=aid_active,
        last_alert_timestamp_utc=ts,
        last_alert_price=100.0,
        status="active",
    )

    # Recovered ladder
    aid_rec = await repo.insert_alert(
        db_path, timestamp_utc=ts, ticker="RECOV", price_at_alert=80.0
    )
    await repo.upsert_tracked_ticker(
        db_path,
        ticker="RECOV",
        first_alert_id=aid_rec,
        first_alert_timestamp_utc=ts,
        first_alert_price=80.0,
        last_alert_id=aid_rec,
        last_alert_timestamp_utc=ts,
        last_alert_price=80.0,
        status="recovered",
    )

    # Expired ladder
    aid_exp = await repo.insert_alert(db_path, timestamp_utc=ts, ticker="EXPD", price_at_alert=60.0)
    await repo.upsert_tracked_ticker(
        db_path,
        ticker="EXPD",
        first_alert_id=aid_exp,
        first_alert_timestamp_utc=ts,
        first_alert_price=60.0,
        last_alert_id=aid_exp,
        last_alert_timestamp_utc=ts,
        last_alert_price=60.0,
        status="expired",
    )

    async with _client() as client:
        resp = await client.get("/ladders")
    assert resp.status_code == 200
    assert "ACTIVE" in resp.text
    assert "RECOV" not in resp.text
    assert "EXPD" not in resp.text


# ---------------------------------------------------------------------------
# Close ladder endpoint
# ---------------------------------------------------------------------------


async def test_close_ladder_endpoint_updates_status(db_path: str, override_db: object) -> None:
    from datetime import UTC, datetime

    ts = datetime.now(UTC).isoformat()
    aid = await repo.insert_alert(db_path, timestamp_utc=ts, ticker="CLOSE_ME", price_at_alert=50.0)
    await repo.upsert_tracked_ticker(
        db_path,
        ticker="CLOSE_ME",
        first_alert_id=aid,
        first_alert_timestamp_utc=ts,
        first_alert_price=50.0,
        last_alert_id=aid,
        last_alert_timestamp_utc=ts,
        last_alert_price=50.0,
        status="active",
    )

    async with _client() as client:
        resp = await client.post("/ladders/CLOSE_ME/close", follow_redirects=False)
    assert resp.status_code in (302, 303)

    row = await repo.get_tracked_ticker(db_path, "CLOSE_ME")
    assert row is not None
    assert row["status"] == "user_closed"


# ---------------------------------------------------------------------------
# Localhost binding regression-prevention
# ---------------------------------------------------------------------------


def test_dashboard_binds_to_localhost_only() -> None:
    assert DASHBOARD_HOST == "127.0.0.1"
