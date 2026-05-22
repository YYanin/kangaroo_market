"""Phase 5 & Phase 6: Pipeline integration tests."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kangaroo.config import (
    DbSettings,
    EarningsSettings,
    LadderSettings,
    NewsSettings,
    QualitySettings,
    SectorSettings,
    Settings,
    SetupSettings,
    UniverseSettings,
)
from kangaroo.db import repository as repo
from kangaroo.db.init import init_db
from kangaroo.pipeline import re_evaluate_tracked_tickers, run_pipeline
from kangaroo.sources.market_data import (
    DeclinerRecord,
    EarningsEvent,
    Fundamentals,
    OHLCVBar,
)
from kangaroo.sources.news import Headline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(db_path: str) -> Settings:
    return Settings(
        universe=UniverseSettings(min_pct_drop=4.0, min_relative_volume=2.0, max_count=100),
        quality=QualitySettings(
            min_market_cap=1_000_000.0,
            min_avg_daily_dollar_volume=1_000.0,
            require_positive_ttm_income=True,
            allowed_security_types=["CS"],
        ),
        setup=SetupSettings(
            min_drawdown_pct=5.0,
            max_drawdown_pct=40.0,
            max_pct_below_200dma=20.0,
            max_rsi_14=60.0,
        ),
        earnings=EarningsSettings(blackout_days=5),
        sector=SectorSettings(flag_threshold_pct=1.5),
        news=NewsSettings(lookback_hours=24, max_articles=1),
        ladder=LadderSettings(
            step_pct=3.0,
            max_rungs=5,
            tracking_window_days=10,
            recovery_exit_pct=4.0,
            structural_damage_drawdown_pct=30.0,
            structural_damage_below_200dma_pct=15.0,
        ),
        db=DbSettings(path=db_path),
    )


def _good_fundamentals(ticker: str = "AAPL") -> Fundamentals:
    return Fundamentals(
        ticker=ticker,
        market_cap=500_000_000_000.0,
        ttm_net_income=50_000_000_000.0,
        avg_daily_dollar_volume_30d=2_000_000_000.0,
        security_type="CS",
        company_name=f"{ticker} Inc",
        sector="Technology",
    )


def _price_bars(current: float = 85.0, high: float = 100.0, n: int = 200) -> list[OHLCVBar]:
    """200 flat bars at a base level, 1 bar at peak, then 20 steadily declining bars.

    This gives: RSI < 40, drawdown 15% from 52w high, price near 200-dma.
    """
    base = current * 1.02  # 200-dma slightly below current
    bars: list[OHLCVBar] = [
        OHLCVBar(
            date=f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}",
            open=base,
            high=base,
            low=base,
            close=base,
            volume=1_000_000.0,
        )
        for i in range(n)
    ]
    bars.append(
        OHLCVBar(date="2025-01-01", open=high, high=high, low=high, close=high, volume=1_000_000.0)
    )
    step = (high - current) / 20.0
    for j in range(20):
        p = high - step * (j + 1)
        bars.append(
            OHLCVBar(
                date=f"2025-02-{j + 1:02d}",
                open=p,
                high=p * 1.005,
                low=p * 0.995,
                close=p,
                volume=2_000_000.0,
            )
        )
    return bars


def _make_market_data(
    decliners: list[DeclinerRecord] | None = None,
    fundamentals: Fundamentals | None = None,
    price_bars: list[OHLCVBar] | None = None,
    current_price: float = 85.0,
    sector_pct: float = 0.0,
) -> MagicMock:
    md = MagicMock()
    md.get_daily_decliners = AsyncMock(return_value=decliners or [])
    md.get_fundamentals = AsyncMock(return_value=fundamentals or _good_fundamentals())
    md.get_price_history = AsyncMock(return_value=price_bars or _price_bars())
    md.get_earnings_calendar = AsyncMock(
        return_value=EarningsEvent(ticker="AAPL", report_date=None)
    )
    md.get_sector_etf_change = AsyncMock(return_value=sector_pct)
    md.get_current_price = AsyncMock(return_value=current_price)
    return md


def _make_news(headlines: list[Headline] | None = None) -> MagicMock:
    nc = MagicMock()
    nc.get_recent_headlines = AsyncMock(return_value=headlines or [])
    nc.get_article_text = AsyncMock(return_value="")
    return nc


# ---------------------------------------------------------------------------
# Phase 5 tests
# ---------------------------------------------------------------------------


class TestPipelineNewAlerts:
    @pytest.mark.asyncio
    async def test_pipeline_writes_alert_for_clean_pass(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        decliners = [
            DeclinerRecord(
                ticker="AAPL", pct_change_day=-6.0, dollar_volume=1e9, relative_volume=3.0
            )
        ]
        md = _make_market_data(decliners=decliners, current_price=85.0)
        nc = _make_news()

        await run_pipeline(settings, db, md, nc)

        alerts = await repo.get_today_alerts(db, date_prefix=datetime.now(UTC).strftime("%Y-%m-%d"))
        assert len(alerts) == 1
        assert alerts[0]["ticker"] == "AAPL"
        assert alerts[0]["rung_number"] == 1

        tracked = await repo.get_active_tracked_tickers(db)
        assert len(tracked) == 1
        assert tracked[0]["ticker"] == "AAPL"
        assert tracked[0]["status"] == "active"

    @pytest.mark.asyncio
    async def test_pipeline_writes_filtered_out_at_correct_stage(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        decliners = [
            DeclinerRecord(
                ticker="AAPL", pct_change_day=-6.0, dollar_volume=1e9, relative_volume=3.0
            )
        ]
        md = _make_market_data(decliners=decliners)
        # Earnings in 2 days → blocked
        md.get_earnings_calendar = AsyncMock(
            return_value=EarningsEvent(
                ticker="AAPL",
                report_date=datetime.now(UTC).date() + timedelta(days=2),
            )
        )
        nc = _make_news()

        await run_pipeline(settings, db, md, nc)

        alerts = await repo.get_today_alerts(db, date_prefix=datetime.now(UTC).strftime("%Y-%m-%d"))
        assert len(alerts) == 0

        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT filter_name FROM filtered_out WHERE ticker='AAPL'")
        rows = cursor.fetchall()
        conn.close()
        assert any(r[0] == "earnings" for r in rows)

    @pytest.mark.asyncio
    async def test_pipeline_continues_after_per_ticker_error(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        decliners = [
            DeclinerRecord(
                ticker="FAIL", pct_change_day=-7.0, dollar_volume=1e9, relative_volume=3.0
            ),
            DeclinerRecord(
                ticker="PASS", pct_change_day=-6.0, dollar_volume=1e9, relative_volume=3.0
            ),
        ]

        call_count = 0

        async def side_effect(ticker: str) -> Fundamentals:
            nonlocal call_count
            call_count += 1
            if ticker == "FAIL":
                raise RuntimeError("simulated failure")
            return _good_fundamentals(ticker)

        md = _make_market_data(decliners=decliners)
        md.get_fundamentals = side_effect  # type: ignore[assignment]
        nc = _make_news()

        await run_pipeline(settings, db, md, nc)

        alerts = await repo.get_today_alerts(db, date_prefix=datetime.now(UTC).strftime("%Y-%m-%d"))
        tickers = [a["ticker"] for a in alerts]
        assert "PASS" in tickers
        assert "FAIL" not in tickers

    @pytest.mark.asyncio
    async def test_pipeline_does_not_double_alert_active_ladder(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)
        now_utc = datetime.now(UTC).isoformat()

        # Pre-seed AAPL as active
        alert_id = await repo.insert_alert(
            db, timestamp_utc=now_utc, ticker="AAPL", price_at_alert=100.0, rung_number=1
        )
        await repo.upsert_tracked_ticker(
            db,
            ticker="AAPL",
            first_alert_id=alert_id,
            first_alert_timestamp_utc=now_utc,
            first_alert_price=100.0,
            last_alert_id=alert_id,
            last_alert_timestamp_utc=now_utc,
            last_alert_price=100.0,
            rung_count=1,
            status="active",
        )

        decliners = [
            DeclinerRecord(
                ticker="AAPL", pct_change_day=-6.0, dollar_volume=1e9, relative_volume=3.0
            )
        ]
        md = _make_market_data(decliners=decliners, current_price=99.0)
        nc = _make_news()

        await run_pipeline(settings, db, md, nc)

        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT COUNT(*) FROM alerts WHERE ticker='AAPL' AND rung_number=1")
        count = cursor.fetchone()[0]
        conn.close()
        # Should still be just the one pre-seeded rung-1 alert
        assert count == 1


# ---------------------------------------------------------------------------
# Phase 6 tests: Ladder re-evaluation
# ---------------------------------------------------------------------------


class TestLadderReEvaluation:
    async def _seed_ladder(
        self,
        db: str,
        ticker: str = "AAPL",
        last_price: float = 100.0,
        rung_count: int = 1,
        first_price: float | None = None,
        first_ts: str | None = None,
    ) -> int:
        ts = datetime.now(UTC).isoformat()
        alert_id = await repo.insert_alert(
            db,
            timestamp_utc=first_ts or ts,
            ticker=ticker,
            price_at_alert=first_price or last_price,
            rung_number=1,
        )
        await repo.upsert_tracked_ticker(
            db,
            ticker=ticker,
            first_alert_id=alert_id,
            first_alert_timestamp_utc=first_ts or ts,
            first_alert_price=first_price or last_price,
            last_alert_id=alert_id,
            last_alert_timestamp_utc=ts,
            last_alert_price=last_price,
            rung_count=rung_count,
            status="active",
        )
        return alert_id

    @pytest.mark.asyncio
    async def test_rung_2_fires_when_price_drops_step_below_rung_1(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        await self._seed_ladder(db, ticker="AAPL", last_price=100.0)

        # Current price 96.5 < 100 * 0.97 = 97.0 → rung 2 should fire
        md = _make_market_data(current_price=96.5)
        nc = _make_news()

        await re_evaluate_tracked_tickers(settings, db, md, nc)

        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT rung_number, parent_alert_id FROM alerts ORDER BY id")
        rows = cursor.fetchall()
        conn.close()

        rungs = [r[0] for r in rows]
        assert 2 in rungs

        tracked = await repo.get_tracked_ticker(db, "AAPL")
        assert tracked is not None
        assert tracked["rung_count"] == 2
        assert tracked["status"] == "active"

    @pytest.mark.asyncio
    async def test_no_rung_when_price_above_threshold(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        await self._seed_ladder(db, ticker="AAPL", last_price=100.0)

        # Current price 98 > 97.0 → no new rung
        md = _make_market_data(current_price=98.0)
        nc = _make_news()

        await re_evaluate_tracked_tickers(settings, db, md, nc)

        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT COUNT(*) FROM alerts")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 1  # only the original rung-1

    @pytest.mark.asyncio
    async def test_rung_step_compounds(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        # Rung 2 already fired at 97.0
        await self._seed_ladder(db, ticker="AAPL", last_price=97.0, rung_count=2, first_price=100.0)

        # 97.0 * 0.97 = 94.09 → price 94.0 should trigger rung 3
        md = _make_market_data(current_price=94.0)
        nc = _make_news()
        await re_evaluate_tracked_tickers(settings, db, md, nc)

        tracked = await repo.get_tracked_ticker(db, "AAPL")
        assert tracked is not None
        assert tracked["rung_count"] == 3

    @pytest.mark.asyncio
    async def test_rung_step_no_fire_above_threshold(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        await self._seed_ladder(db, ticker="AAPL", last_price=97.0, rung_count=2, first_price=100.0)

        # 97 * 0.97 = 94.09 → price 94.5 should NOT trigger rung 3
        md = _make_market_data(current_price=94.5)
        nc = _make_news()
        await re_evaluate_tracked_tickers(settings, db, md, nc)

        tracked = await repo.get_tracked_ticker(db, "AAPL")
        assert tracked is not None
        assert tracked["rung_count"] == 2

    @pytest.mark.asyncio
    async def test_max_rungs_cap_enforced(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        # Already at max_rungs (5)
        await self._seed_ladder(
            db, ticker="AAPL", last_price=85.74, rung_count=5, first_price=100.0
        )

        # Price well below next theoretical step
        md = _make_market_data(current_price=70.0)
        nc = _make_news()
        await re_evaluate_tracked_tickers(settings, db, md, nc)

        tracked = await repo.get_tracked_ticker(db, "AAPL")
        assert tracked is not None
        assert tracked["rung_count"] == 5  # unchanged

    @pytest.mark.asyncio
    async def test_recovery_closes_ladder(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        await self._seed_ladder(db, ticker="AAPL", last_price=100.0)

        # Recovery threshold: 100 * 1.04 = 104 → price 105 triggers recovery
        md = _make_market_data(current_price=105.0)
        nc = _make_news()
        await re_evaluate_tracked_tickers(settings, db, md, nc)

        tracked = await repo.get_tracked_ticker(db, "AAPL")
        assert tracked is not None
        assert tracked["status"] == "recovered"

    @pytest.mark.asyncio
    async def test_blocklist_hit_closes_ladder_as_thesis_broken(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        await self._seed_ladder(db, ticker="AAPL", last_price=100.0)

        # Price below rung threshold, but blocklist triggers — should close, not rung
        md = _make_market_data(current_price=95.0)
        nc = _make_news(
            headlines=[
                Headline(
                    source="test",
                    title="Apple CEO resigned amid investigation",
                    url="",
                    published_utc="",
                )
            ]
        )

        await re_evaluate_tracked_tickers(settings, db, md, nc)

        tracked = await repo.get_tracked_ticker(db, "AAPL")
        assert tracked is not None
        assert tracked["status"] == "thesis_broken"

        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT COUNT(*) FROM alerts WHERE rung_number=2")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 0  # no rung alert written

    @pytest.mark.asyncio
    async def test_structural_damage_closes_ladder(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        await self._seed_ladder(db, ticker="AAPL", last_price=100.0)

        # Build price bars where current is 35% below 52w high → structural damage
        bars: list[OHLCVBar] = []
        for i in range(220):
            bars.append(
                OHLCVBar(
                    date=f"2024-{(i // 30) + 1:02d}-01",
                    open=100.0,
                    high=100.0,
                    low=100.0,
                    close=100.0,
                    volume=1e6,
                )
            )
        # Current price 65 = 35% below 52w high of 100
        bars.append(
            OHLCVBar(
                date="2025-06-01",
                open=65.0,
                high=65.0,
                low=65.0,
                close=65.0,
                volume=2e6,
            )
        )

        md = _make_market_data(current_price=65.0, price_bars=bars)
        nc = _make_news()

        await re_evaluate_tracked_tickers(settings, db, md, nc)

        tracked = await repo.get_tracked_ticker(db, "AAPL")
        assert tracked is not None
        assert tracked["status"] == "thesis_broken"

    @pytest.mark.asyncio
    async def test_expire_stale_ladders_flips_status(self, tmp_path: Path) -> None:
        from datetime import timedelta

        from kangaroo.jobs.nightly import expire_stale_ladders

        db = str(tmp_path / "test.db")
        init_db(db)
        settings = _settings(db)

        # Seed a ladder from 11 trading days ago (window is 10)
        old_ts = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        await self._seed_ladder(db, ticker="AAPL", last_price=100.0, first_ts=old_ts)

        now = datetime.now(UTC)
        expired = await expire_stale_ladders(db, settings, now)

        assert "AAPL" in expired
        tracked = await repo.get_tracked_ticker(db, "AAPL")
        assert tracked is not None
        assert tracked["status"] == "expired"
