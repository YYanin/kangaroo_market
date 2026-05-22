"""Market data client backed by Yahoo Finance (yfinance).

Callers interact only with the public interface (DeclinerRecord, Fundamentals, etc.).
The yfinance-specific implementation is an implementation detail of this module.
All yfinance calls are synchronous; each public method dispatches to a private sync
helper via asyncio.to_thread to satisfy the project's async-by-default I/O rule.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import yfinance as yf  # type: ignore[import-untyped]

from kangaroo.sources._sector_map import SECTOR_ETF_MAP

logger = logging.getLogger(__name__)


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
    """Async wrapper around yfinance.  No API key required."""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Public async API — same interface as before; callers unchanged
    # ------------------------------------------------------------------

    async def get_daily_decliners(
        self,
        min_pct_drop: float,
        min_relative_volume: float,
        limit: int,
    ) -> list[DeclinerRecord]:
        """Return today's biggest US equity decliners meeting the thresholds."""
        return await asyncio.to_thread(
            self._sync_get_daily_decliners, min_pct_drop, min_relative_volume, limit
        )

    async def get_fundamentals(self, ticker: str) -> Fundamentals:
        return await asyncio.to_thread(self._sync_get_fundamentals, ticker)

    async def get_price_history(self, ticker: str, days: int) -> list[OHLCVBar]:
        return await asyncio.to_thread(self._sync_get_price_history, ticker, days)

    async def get_current_price(self, ticker: str) -> float:
        return await asyncio.to_thread(self._sync_get_current_price, ticker)

    async def get_earnings_calendar(self, ticker: str) -> EarningsEvent:
        return await asyncio.to_thread(self._sync_get_earnings_calendar, ticker)

    async def get_sector_etf_change(self, sector: str) -> float:
        return await asyncio.to_thread(self._sync_get_sector_etf_change, sector)

    # ------------------------------------------------------------------
    # Sync helpers — run in a thread via asyncio.to_thread
    # ------------------------------------------------------------------

    def _sync_get_daily_decliners(
        self,
        min_pct_drop: float,
        min_relative_volume: float,
        limit: int,
    ) -> list[DeclinerRecord]:
        try:
            # yfinance >=1.0 API: yf.screen() replaces the old Screener class
            data = yf.screen("day_losers", count=min(limit * 2, 250))
        except Exception as exc:
            raise MarketDataError(f"yfinance screener failed: {exc}") from exc

        results: list[DeclinerRecord] = []
        for q in data.get("quotes", []):
            if not isinstance(q, dict):
                continue
            pct_change = float(q.get("regularMarketChangePercent", 0.0) or 0.0)
            if pct_change >= -min_pct_drop:
                continue
            vol = float(q.get("regularMarketVolume", 0) or 0)
            avg_vol = float(q.get("averageVolume", 1) or 1) or 1.0
            rel_vol = vol / avg_vol
            if rel_vol < min_relative_volume:
                continue
            price = float(q.get("regularMarketPrice", 0) or 0)
            results.append(
                DeclinerRecord(
                    ticker=str(q.get("symbol", "")),
                    pct_change_day=pct_change,
                    dollar_volume=price * vol,
                    relative_volume=rel_vol,
                )
            )
        results.sort(key=lambda r: r.pct_change_day)
        return results[:limit]

    def _sync_get_fundamentals(self, ticker: str) -> Fundamentals:
        try:
            t = yf.Ticker(ticker)
            info: dict[str, Any] = t.info or {}
        except Exception as exc:
            raise MarketDataError(f"yfinance info failed for {ticker}: {exc}") from exc

        if not info:
            raise MarketDataError(f"No data returned for {ticker}")

        # TTM net income: sum last 4 quarters
        ttm_income = 0.0
        try:
            q_fin = getattr(t, "quarterly_income_stmt", None) or getattr(
                t, "quarterly_financials", None
            )
            if q_fin is not None:
                net_income_row = None
                for key in ("Net Income", "NetIncome"):
                    if key in q_fin.index:
                        net_income_row = q_fin.loc[key]
                        break
                if net_income_row is not None:
                    vals = list(net_income_row.head(4))
                    # v == v filters out NaN (NaN != NaN in IEEE 754)
                    ttm_income = sum(float(v) for v in vals if v == v)
        except Exception:
            logger.debug("Could not compute TTM net income for %s", ticker)

        # Avg daily dollar volume over the last 30 days
        avg_dv = 0.0
        try:
            hist = t.history(period="30d")
            if not hist.empty:
                avg_dv = float((hist["Close"] * hist["Volume"]).mean())
        except Exception:
            logger.debug("Could not compute avg dollar volume for %s", ticker)

        # Yahoo Finance uses "EQUITY" for common stocks; normalise to "CS"
        # so the quality filter's allowed_security_types default ["CS", "Common Stock"] applies.
        raw_type = str(info.get("quoteType", "") or "")
        security_type = "CS" if raw_type == "EQUITY" else raw_type

        return Fundamentals(
            ticker=ticker,
            market_cap=float(info.get("marketCap", 0) or 0),
            ttm_net_income=ttm_income,
            avg_daily_dollar_volume_30d=avg_dv,
            security_type=security_type,
            company_name=str(info.get("longName", "") or info.get("shortName", "") or ""),
            sector=str(info.get("sector", "") or ""),
        )

    def _sync_get_price_history(self, ticker: str, days: int) -> list[OHLCVBar]:
        try:
            hist = yf.Ticker(ticker).history(period=f"{days}d")
        except Exception as exc:
            raise MarketDataError(f"yfinance price history failed for {ticker}: {exc}") from exc

        bars: list[OHLCVBar] = []
        for dt, row in hist.iterrows():
            bars.append(
                OHLCVBar(
                    date=str(dt)[:10],
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                )
            )
        return bars

    def _sync_get_current_price(self, ticker: str) -> float:
        try:
            fast = yf.Ticker(ticker).fast_info
            price = fast.last_price
            if price is not None:
                return float(price)
            # Fallback: last close from 1-day history
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
            raise MarketDataError(f"No price data available for {ticker}")
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(f"yfinance current price failed for {ticker}: {exc}") from exc

    def _sync_get_earnings_calendar(self, ticker: str) -> EarningsEvent:
        next_date: date | None = None
        try:
            cal = yf.Ticker(ticker).calendar
            if isinstance(cal, dict) and "Earnings Date" in cal:
                raw = cal["Earnings Date"]
                dates = raw if isinstance(raw, list) else [raw]
                if dates:
                    # str(Timestamp) → "YYYY-MM-DD HH:MM:SS+TZ"; take first 10 chars
                    next_date = date.fromisoformat(str(dates[0])[:10])
        except Exception:
            logger.debug("Could not parse earnings calendar for %s", ticker)
        return EarningsEvent(ticker=ticker, report_date=next_date)

    def _sync_get_sector_etf_change(self, sector: str) -> float:
        etf = SECTOR_ETF_MAP.get(sector, "SPY")
        try:
            hist = yf.Ticker(etf).history(period="2d")
            if len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])
                last_close = float(hist["Close"].iloc[-1])
                if prev_close:
                    return (last_close - prev_close) / prev_close * 100.0
            return 0.0
        except Exception as exc:
            raise MarketDataError(f"yfinance sector ETF failed for {etf}: {exc}") from exc


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")
