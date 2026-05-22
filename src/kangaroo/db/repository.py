"""All SQL for the kangaroo database lives in this module."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite


def _db_path(path: str) -> Path:
    return Path(path)


def _connect_sync(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


@asynccontextmanager
async def _connect(path: str) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = aiosqlite.Row
        yield conn


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------


async def insert_alert(
    db_path: str,
    *,
    timestamp_utc: str,
    ticker: str,
    price_at_alert: float,
    rung_number: int = 1,
    parent_alert_id: int | None = None,
    company_name: str | None = None,
    sector: str | None = None,
    pct_change_day: float | None = None,
    drawdown_from_52w_high: float | None = None,
    rsi_14: float | None = None,
    pct_above_200dma: float | None = None,
    days_to_next_earnings: int | None = None,
    market_cap: float | None = None,
    flags: str | None = None,
) -> int:
    sql = """
        INSERT INTO alerts (
            timestamp_utc, ticker, company_name, sector,
            price_at_alert, pct_change_day, drawdown_from_52w_high,
            rsi_14, pct_above_200dma, days_to_next_earnings, market_cap,
            flags, rung_number, parent_alert_id
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """
    async with _connect(db_path) as conn:
        cursor = await conn.execute(
            sql,
            (
                timestamp_utc,
                ticker,
                company_name,
                sector,
                price_at_alert,
                pct_change_day,
                drawdown_from_52w_high,
                rsi_14,
                pct_above_200dma,
                days_to_next_earnings,
                market_cap,
                flags,
                rung_number,
                parent_alert_id,
            ),
        )
        await conn.commit()
        return int(cursor.lastrowid or 0)


async def insert_evidence(
    db_path: str,
    *,
    alert_id: int,
    headline: str | None = None,
    source: str | None = None,
    url: str | None = None,
    published_utc: str | None = None,
    full_text: str | None = None,
) -> None:
    sql = """
        INSERT INTO alert_evidence (alert_id, headline, source, url, published_utc, full_text)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    async with _connect(db_path) as conn:
        await conn.execute(sql, (alert_id, headline, source, url, published_utc, full_text))
        await conn.commit()


# ---------------------------------------------------------------------------
# filtered_out
# ---------------------------------------------------------------------------


async def insert_filtered_out(
    db_path: str,
    *,
    timestamp_utc: str,
    ticker: str,
    pct_change_day: float | None,
    filter_name: str,
    filter_reason: str | None,
) -> None:
    sql = """
        INSERT INTO filtered_out (timestamp_utc, ticker, pct_change_day, filter_name, filter_reason)
        VALUES (?, ?, ?, ?, ?)
    """
    async with _connect(db_path) as conn:
        await conn.execute(sql, (timestamp_utc, ticker, pct_change_day, filter_name, filter_reason))
        await conn.commit()


# ---------------------------------------------------------------------------
# tracked_tickers
# ---------------------------------------------------------------------------


async def get_active_tracked_tickers(db_path: str) -> list[dict[str, Any]]:
    sql = "SELECT * FROM tracked_tickers WHERE status = 'active'"
    async with _connect(db_path) as conn:
        cursor = await conn.execute(sql)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_tracked_ticker(db_path: str, ticker: str) -> dict[str, Any] | None:
    sql = "SELECT * FROM tracked_tickers WHERE ticker = ?"
    async with _connect(db_path) as conn:
        cursor = await conn.execute(sql, (ticker,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def upsert_tracked_ticker(
    db_path: str,
    *,
    ticker: str,
    first_alert_id: int,
    first_alert_timestamp_utc: str,
    first_alert_price: float,
    last_alert_id: int,
    last_alert_timestamp_utc: str,
    last_alert_price: float,
    rung_count: int = 1,
    status: str = "active",
) -> None:
    sql = """
        INSERT INTO tracked_tickers (
            ticker, first_alert_id, first_alert_timestamp_utc, first_alert_price,
            last_alert_id, last_alert_timestamp_utc, last_alert_price,
            rung_count, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            last_alert_id = excluded.last_alert_id,
            last_alert_timestamp_utc = excluded.last_alert_timestamp_utc,
            last_alert_price = excluded.last_alert_price,
            rung_count = excluded.rung_count,
            status = excluded.status
    """
    async with _connect(db_path) as conn:
        await conn.execute(
            sql,
            (
                ticker,
                first_alert_id,
                first_alert_timestamp_utc,
                first_alert_price,
                last_alert_id,
                last_alert_timestamp_utc,
                last_alert_price,
                rung_count,
                status,
            ),
        )
        await conn.commit()


async def close_tracked_ticker(
    db_path: str,
    *,
    ticker: str,
    status: str,
    closed_timestamp_utc: str,
    closed_reason: str | None = None,
) -> None:
    sql = """
        UPDATE tracked_tickers
        SET status = ?, closed_timestamp_utc = ?, closed_reason = ?
        WHERE ticker = ?
    """
    async with _connect(db_path) as conn:
        await conn.execute(sql, (status, closed_timestamp_utc, closed_reason, ticker))
        await conn.commit()


async def update_tracked_ticker_rung(
    db_path: str,
    *,
    ticker: str,
    last_alert_id: int,
    last_alert_timestamp_utc: str,
    last_alert_price: float,
    rung_count: int,
) -> None:
    sql = """
        UPDATE tracked_tickers
        SET last_alert_id = ?, last_alert_timestamp_utc = ?,
            last_alert_price = ?, rung_count = ?
        WHERE ticker = ?
    """
    async with _connect(db_path) as conn:
        await conn.execute(
            sql,
            (last_alert_id, last_alert_timestamp_utc, last_alert_price, rung_count, ticker),
        )
        await conn.commit()


async def expire_old_ladders(
    db_path: str,
    *,
    before_timestamp_utc: str,
    closed_timestamp_utc: str,
) -> list[str]:
    """Flip active ladders older than before_timestamp_utc to 'expired'. Returns tickers."""
    select_sql = """
        SELECT ticker FROM tracked_tickers
        WHERE status = 'active' AND first_alert_timestamp_utc < ?
    """
    update_sql = """
        UPDATE tracked_tickers
        SET status = 'expired', closed_timestamp_utc = ?, closed_reason = 'time_expiry'
        WHERE status = 'active' AND first_alert_timestamp_utc < ?
    """
    async with _connect(db_path) as conn:
        cursor = await conn.execute(select_sql, (before_timestamp_utc,))
        rows = await cursor.fetchall()
        tickers = [row[0] for row in rows]
        await conn.execute(update_sql, (closed_timestamp_utc, before_timestamp_utc))
        await conn.commit()
    return tickers


async def update_alert_realized_return(
    db_path: str,
    *,
    alert_id: int,
    period: str,
    price: float,
    return_pct: float,
) -> None:
    """period is '1d', '3d', '5d', or '20d'."""
    allowed = {"1d", "3d", "5d", "20d"}
    if period not in allowed:
        raise ValueError(f"period must be one of {allowed}")
    sql = f"""
        UPDATE alerts SET price_{period} = ?, return_{period} = ? WHERE id = ?
    """  # noqa: S608
    async with _connect(db_path) as conn:
        await conn.execute(sql, (price, return_pct, alert_id))
        await conn.commit()


async def get_alerts_from_n_trading_days_ago(
    db_path: str,
    *,
    after_utc: str,
    before_utc: str,
) -> list[dict[str, Any]]:
    sql = """
        SELECT * FROM alerts
        WHERE timestamp_utc >= ? AND timestamp_utc < ?
          AND rung_number = 1
    """
    async with _connect(db_path) as conn:
        cursor = await conn.execute(sql, (after_utc, before_utc))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_alert_by_id(db_path: str, alert_id: int) -> dict[str, Any] | None:
    sql = "SELECT * FROM alerts WHERE id = ?"
    async with _connect(db_path) as conn:
        cursor = await conn.execute(sql, (alert_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_today_alerts(db_path: str, *, date_prefix: str) -> list[dict[str, Any]]:
    sql = "SELECT * FROM alerts WHERE timestamp_utc LIKE ? ORDER BY timestamp_utc DESC"
    async with _connect(db_path) as conn:
        cursor = await conn.execute(sql, (f"{date_prefix}%",))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_evidence_for_alert(
    db_path: str, alert_id: int, *, limit: int = 3
) -> list[dict[str, Any]]:
    sql = """
        SELECT * FROM alert_evidence
        WHERE alert_id = ?
        ORDER BY published_utc DESC
        LIMIT ?
    """
    async with _connect(db_path) as conn:
        cursor = await conn.execute(sql, (alert_id, limit))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_alerts_by_ticker(db_path: str, ticker: str) -> list[dict[str, Any]]:
    sql = "SELECT * FROM alerts WHERE ticker = ? ORDER BY rung_number ASC"
    async with _connect(db_path) as conn:
        cursor = await conn.execute(sql, (ticker,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_performance_stats(db_path: str) -> dict[str, Any]:
    sql = """
        SELECT
            COUNT(*) AS total_alerts,
            SUM(CASE WHEN return_5d > 0 THEN 1 ELSE 0 END) AS positive_5d_count,
            COUNT(CASE WHEN return_5d IS NOT NULL THEN 1 END) AS total_with_5d,
            AVG(return_1d) AS avg_return_1d,
            AVG(return_3d) AS avg_return_3d,
            AVG(return_5d) AS avg_return_5d,
            AVG(return_20d) AS avg_return_20d
        FROM alerts WHERE rung_number = 1
    """
    async with _connect(db_path) as conn:
        cursor = await conn.execute(sql)
        row = await cursor.fetchone()
        if row is None:
            return {}
        d = dict(row)
        total_with_5d = d.get("total_with_5d") or 0
        positive = d.get("positive_5d_count") or 0
        d["hit_rate_5d"] = round(positive / total_with_5d * 100, 1) if total_with_5d else None
        return d
