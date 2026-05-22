"""Market data client backed by Polygon.io.

Callers interact only with the public interface (DeclinerRecord, Fundamentals, etc.).
The Polygon-specific URL structure is an implementation detail of this module.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_BASE = "https://api.polygon.io"
_MAX_RETRIES = 3
_RETRY_STATUSES = {500, 502, 503, 504}


@dataclass
class DeclinerRecord:
    ticker: str
    pct_change_day: float
    dollar_volume: float
    relative_volume: float


@dataclass
class Fundamentals:
    ticker: str
    market_cap: float
    ttm_net_income: float
    avg_daily_dollar_volume_30d: float
    security_type: str
    company_name: str
    sector: str


@dataclass
class OHLCVBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class EarningsEvent:
    ticker: str
    report_date: date | None


class MarketDataError(Exception):
    pass


class MarketDataClient:
    def __init__(self, api_key: str, session: aiohttp.ClientSession) -> None:
        self._key = api_key
        self._session = session

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{_BASE}{path}"
        p = dict(params or {})
        p["apiKey"] = self._key
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with self._session.get(url, params=p) as resp:
                    if resp.status in _RETRY_STATUSES:
                        logger.warning(
                            "Polygon %s returned %s, attempt %d/%d",
                            path,
                            resp.status,
                            attempt + 1,
                            _MAX_RETRIES,
                        )
                        await asyncio.sleep(2**attempt)
                        continue
                    if resp.status >= 400:
                        body = await resp.text()
                        raise MarketDataError(
                            f"Polygon {path} returned {resp.status}: {body[:200]}"
                        )
                    return await resp.json()
            except MarketDataError:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning("Network error on %s attempt %d: %s", path, attempt + 1, exc)
                await asyncio.sleep(2**attempt)
        raise MarketDataError(f"Failed after {_MAX_RETRIES} attempts: {last_exc}")

    async def get_daily_decliners(
        self,
        min_pct_drop: float,
        min_relative_volume: float,
        limit: int,
    ) -> list[DeclinerRecord]:
        """Return the day's biggest US equity decliners meeting the thresholds."""
        data = await self._get(
            "/v2/snapshot/locale/us/markets/stocks/gainers",
            {"include_otc": "false"},
        )
        results = []
        tickers_data: list[dict[str, Any]] = data.get("tickers", [])
        for item in tickers_data:
            day = item.get("day", {})
            prev_day = item.get("prevDay", {})
            pct_change = item.get("todaysChangePerc", 0.0)
            if pct_change >= -min_pct_drop:
                continue
            dollar_vol = float(day.get("v", 0)) * float(day.get("vw", 0))
            prev_vol = float(prev_day.get("v", 1)) or 1.0
            rel_vol = float(day.get("v", 0)) / prev_vol
            if rel_vol < min_relative_volume:
                continue
            results.append(
                DeclinerRecord(
                    ticker=item["ticker"],
                    pct_change_day=float(pct_change),
                    dollar_volume=dollar_vol,
                    relative_volume=rel_vol,
                )
            )
        results.sort(key=lambda r: r.pct_change_day)
        return results[:limit]

    async def get_fundamentals(self, ticker: str) -> Fundamentals:
        data = await self._get(f"/v3/reference/tickers/{ticker}")
        result = data.get("results", {})
        financials_data = await self._get(
            "/vX/reference/financials",
            {"ticker": ticker, "limit": 4, "timeframe": "quarterly", "order": "desc"},
        )
        fin_results: list[dict[str, Any]] = financials_data.get("results", [])
        ttm_income = 0.0
        for quarter in fin_results[:4]:
            income = (
                quarter.get("financials", {})
                .get("income_statement", {})
                .get("net_income_loss", {})
                .get("value", 0.0)
            )
            ttm_income += float(income or 0)

        aggs = await self._get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/{_n_days_ago(30)}/{_today()}",
            {"adjusted": "true", "sort": "asc", "limit": 30},
        )
        bars: list[dict[str, Any]] = aggs.get("results", [])
        avg_dv = sum(b.get("v", 0) * b.get("vw", 0) for b in bars) / len(bars) if bars else 0.0

        market_cap = float(result.get("market_cap", 0) or 0)
        return Fundamentals(
            ticker=ticker,
            market_cap=market_cap,
            ttm_net_income=ttm_income,
            avg_daily_dollar_volume_30d=avg_dv,
            security_type=result.get("type", ""),
            company_name=result.get("name", ""),
            sector=result.get("sic_description", ""),
        )

    async def get_price_history(self, ticker: str, days: int) -> list[OHLCVBar]:
        data = await self._get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/{_n_days_ago(days)}/{_today()}",
            {"adjusted": "true", "sort": "asc", "limit": days + 10},
        )
        bars = []
        for b in data.get("results", []):
            ts = b.get("t", 0) / 1000
            dt = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")
            bars.append(
                OHLCVBar(
                    date=dt,
                    open=float(b.get("o", 0)),
                    high=float(b.get("h", 0)),
                    low=float(b.get("l", 0)),
                    close=float(b.get("c", 0)),
                    volume=float(b.get("v", 0)),
                )
            )
        return bars

    async def get_earnings_calendar(self, ticker: str) -> EarningsEvent:
        ticker_data = await self._get(f"/v3/reference/tickers/{ticker}")
        result = ticker_data.get("results", {})
        next_earnings_str: str | None = None
        if "earnings" in result:
            next_earnings_str = result["earnings"].get("next_earnings_date")
        next_date: date | None = None
        if next_earnings_str:
            import contextlib

            with contextlib.suppress(ValueError):
                next_date = date.fromisoformat(next_earnings_str)
        return EarningsEvent(ticker=ticker, report_date=next_date)

    async def get_current_price(self, ticker: str) -> float:
        data = await self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
        snap = data.get("ticker", {})
        day = snap.get("day", {})
        return float(day.get("c", 0) or day.get("vw", 0))

    async def get_sector_etf_change(self, sector: str) -> float:
        """Return the day's % change for the sector's representative ETF."""
        from kangaroo.sources._sector_map import SECTOR_ETF_MAP

        etf = SECTOR_ETF_MAP.get(sector, "SPY")
        data = await self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{etf}")
        snap = data.get("ticker", {})
        return float(snap.get("todaysChangePerc", 0.0))


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _n_days_ago(n: int) -> str:
    from datetime import timedelta

    d = datetime.now(UTC) - timedelta(days=n + 5)
    return d.strftime("%Y-%m-%d")
