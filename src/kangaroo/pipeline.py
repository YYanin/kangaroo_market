"""Pipeline orchestration.

run_pipeline():       Runs the full screener pipeline for new-alert candidates.
re_evaluate_tracked_tickers(): Re-evaluates active ladders (Phase 6).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from kangaroo.config import Settings
from kangaroo.db import repository as repo
from kangaroo.filters.blocklist import apply_blocklist
from kangaroo.filters.earnings import apply_earnings_blackout
from kangaroo.filters.quality import apply_quality_filter
from kangaroo.filters.sector import apply_sector_check
from kangaroo.filters.setup import apply_setup_filter, compute_setup_metrics
from kangaroo.filters.universe import apply_universe_filter
from kangaroo.notify import Notifier, format_closed_alert, format_new_alert, format_rung_alert
from kangaroo.sources.market_data import MarketDataClient
from kangaroo.sources.news import NewsClient

logger = logging.getLogger(__name__)


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


async def run_pipeline(
    settings: Settings,
    db_path: str,
    market_data: MarketDataClient,
    news: NewsClient,
    notifier: Notifier | None = None,
) -> None:
    """Orchestrate the full filter pipeline and persist results."""
    timestamp = _now_utc()

    # ---------- Phase 6: re-evaluate active ladders first ----------
    await re_evaluate_tracked_tickers(settings, db_path, market_data, news, notifier=notifier)

    # ---------- Universe scan ----------
    try:
        raw_decliners = await market_data.get_daily_decliners(
            min_pct_drop=settings.universe.min_pct_drop,
            min_relative_volume=settings.universe.min_relative_volume,
            limit=settings.universe.max_count * 2,
        )
    except Exception:
        logger.exception("Universe scan failed — aborting pipeline run")
        return

    universe_candidates = apply_universe_filter(raw_decliners, settings.universe)

    # Fetch active tracked tickers to avoid re-alerting on them via new-alert path
    active_tracked = {row["ticker"] for row in await repo.get_active_tracked_tickers(db_path)}

    for record in universe_candidates:
        ticker = record.ticker

        # Tickers already on an active ladder → skip (handled by re_evaluate)
        if ticker in active_tracked:
            continue

        try:
            await _process_new_alert(
                ticker=ticker,
                record_pct_change=record.pct_change_day,
                settings=settings,
                db_path=db_path,
                market_data=market_data,
                news=news,
                timestamp=timestamp,
                notifier=notifier,
            )
        except Exception:
            logger.exception("Error processing ticker %s — skipping", ticker)

    if notifier is not None:
        await notifier.drain()


async def _process_new_alert(
    *,
    ticker: str,
    record_pct_change: float,
    settings: Settings,
    db_path: str,
    market_data: MarketDataClient,
    news: NewsClient,
    timestamp: str,
    notifier: Notifier | None = None,
) -> None:
    from datetime import date

    # Quality filter
    try:
        fundamentals = await market_data.get_fundamentals(ticker)
    except Exception:
        logger.exception("Failed to get fundamentals for %s", ticker)
        return

    quality_result = apply_quality_filter(ticker, fundamentals, settings.quality)
    if not quality_result.passed:
        await repo.insert_filtered_out(
            db_path,
            timestamp_utc=timestamp,
            ticker=ticker,
            pct_change_day=record_pct_change,
            filter_name="quality",
            filter_reason=quality_result.reason,
        )
        return

    # Setup filter
    try:
        price_history = await market_data.get_price_history(ticker, days=252)
    except Exception:
        logger.exception("Failed to get price history for %s", ticker)
        return

    setup_result = apply_setup_filter(ticker, price_history, settings.setup)
    if not setup_result.passed:
        await repo.insert_filtered_out(
            db_path,
            timestamp_utc=timestamp,
            ticker=ticker,
            pct_change_day=record_pct_change,
            filter_name="setup",
            filter_reason=setup_result.reason,
        )
        return

    # Earnings blackout
    try:
        earnings_event = await market_data.get_earnings_calendar(ticker)
    except Exception:
        logger.exception("Failed to get earnings calendar for %s", ticker)
        earnings_event = None

    today = date.today()
    earnings_result = apply_earnings_blackout(
        ticker,
        earnings_event.report_date if earnings_event else None,
        today,
        settings.earnings,
    )
    if not earnings_result.passed:
        await repo.insert_filtered_out(
            db_path,
            timestamp_utc=timestamp,
            ticker=ticker,
            pct_change_day=record_pct_change,
            filter_name="earnings",
            filter_reason=earnings_result.reason,
        )
        return

    # Sector check (annotates, does not drop)
    try:
        sector_pct = await market_data.get_sector_etf_change(fundamentals.sector)
    except Exception:
        logger.warning("Failed sector ETF check for %s, proceeding without flag", ticker)
        sector_pct = 0.0

    sector_result = apply_sector_check(fundamentals.sector, sector_pct, settings.sector)

    # News retrieval
    try:
        headlines = await news.get_recent_headlines(ticker, settings.news.lookback_hours)
        article_texts: list[str] = []
        for headline in headlines[: settings.news.max_articles]:
            if headline.url:
                text = await news.get_article_text(headline.url)
                if text:
                    article_texts.append(text)
    except Exception:
        logger.exception("Failed to get news for %s", ticker)
        headlines = []
        article_texts = []

    # Blocklist
    blocklist_result = apply_blocklist(ticker, headlines, article_texts)
    if not blocklist_result.passed:
        await repo.insert_filtered_out(
            db_path,
            timestamp_utc=timestamp,
            ticker=ticker,
            pct_change_day=record_pct_change,
            filter_name="blocklist",
            filter_reason=blocklist_result.reason,
        )
        return

    # Compute metrics for persistence
    metrics = compute_setup_metrics(price_history)
    flags: list[str] = []
    if sector_result.sector_wide:
        flags.append("sector_wide")
    current_price = price_history[-1].close if price_history else 0.0
    days_to_earnings: int | None = None
    if earnings_event and earnings_event.report_date and earnings_event.report_date > today:
        from kangaroo.filters._calendar import trading_days_between

        days_to_earnings = trading_days_between(today, earnings_event.report_date)

    # Persist alert
    alert_id = await repo.insert_alert(
        db_path,
        timestamp_utc=timestamp,
        ticker=ticker,
        price_at_alert=current_price,
        rung_number=1,
        company_name=fundamentals.company_name,
        sector=fundamentals.sector,
        pct_change_day=record_pct_change,
        drawdown_from_52w_high=metrics.drawdown_from_52w_high if metrics else None,
        rsi_14=metrics.rsi_14 if metrics else None,
        pct_above_200dma=metrics.pct_above_200dma if metrics else None,
        days_to_next_earnings=days_to_earnings,
        market_cap=fundamentals.market_cap,
        flags=json.dumps(flags) if flags else None,
    )

    # Persist evidence
    for headline in headlines[:5]:
        await repo.insert_evidence(
            db_path,
            alert_id=alert_id,
            headline=headline.title,
            source=headline.source,
            url=headline.url,
            published_utc=headline.published_utc,
        )

    # Register on the ladder
    await repo.upsert_tracked_ticker(
        db_path,
        ticker=ticker,
        first_alert_id=alert_id,
        first_alert_timestamp_utc=timestamp,
        first_alert_price=current_price,
        last_alert_id=alert_id,
        last_alert_timestamp_utc=timestamp,
        last_alert_price=current_price,
        rung_count=1,
        status="active",
    )

    if notifier is not None:
        notifier.enqueue(
            format_new_alert(
                ticker=ticker,
                pct_change=record_pct_change,
                summary=fundamentals.company_name or ticker,
            )
        )
    logger.info("New alert: %s at %.2f (rung 1)", ticker, current_price)


# ---------------------------------------------------------------------------
# Phase 6: Ladder re-evaluation
# ---------------------------------------------------------------------------


async def re_evaluate_tracked_tickers(
    settings: Settings,
    db_path: str,
    market_data: MarketDataClient,
    news: NewsClient,
    notifier: Notifier | None = None,
) -> None:
    """Re-evaluate every active ladder.  Fires rung alerts, closes broken theses."""
    active_rows = await repo.get_active_tracked_tickers(db_path)

    for row in active_rows:
        ticker: str = row["ticker"]
        try:
            await _re_evaluate_one(
                ticker=ticker,
                tracked_row=row,
                settings=settings,
                db_path=db_path,
                market_data=market_data,
                news=news,
                notifier=notifier,
            )
        except Exception:
            logger.exception("Error re-evaluating ladder for %s — skipping", ticker)


async def _re_evaluate_one(
    *,
    ticker: str,
    tracked_row: dict[str, object],
    settings: Settings,
    db_path: str,
    market_data: MarketDataClient,
    news: NewsClient,
    notifier: Notifier | None = None,
) -> None:

    timestamp = _now_utc()
    last_alert_price: float = float(tracked_row["last_alert_price"])  # type: ignore[arg-type]
    rung_count: int = int(tracked_row["rung_count"])  # type: ignore[call-overload]
    first_alert_id: int = int(tracked_row["first_alert_id"])  # type: ignore[call-overload]
    first_alert_price: float = float(tracked_row["first_alert_price"])  # type: ignore[arg-type]

    # 1. Fetch current price and recent news
    try:
        current_price = await market_data.get_current_price(ticker)
        headlines = await news.get_recent_headlines(ticker, settings.news.lookback_hours)
        article_texts: list[str] = []
        for h in headlines[: settings.news.max_articles]:
            if h.url:
                text = await news.get_article_text(h.url)
                if text:
                    article_texts.append(text)
    except Exception:
        logger.exception("Failed to fetch data for re-evaluation of %s", ticker)
        return

    # 2. Blocklist check — close as thesis_broken if hit
    blocklist_result = apply_blocklist(ticker, headlines, article_texts)
    if not blocklist_result.passed:
        await repo.close_tracked_ticker(
            db_path,
            ticker=ticker,
            status="thesis_broken",
            closed_timestamp_utc=timestamp,
            closed_reason=blocklist_result.reason,
        )
        if notifier is not None:
            notifier.enqueue(
                format_closed_alert(ticker=ticker, reason=blocklist_result.reason or "")
            )
        logger.info(
            "Ladder CLOSED thesis_broken (blocklist): %s — %s", ticker, blocklist_result.reason
        )
        return

    # 3. Structural damage check — close as thesis_broken if breached
    try:
        price_history = await market_data.get_price_history(ticker, days=252)
    except Exception:
        logger.exception("Failed price history for structural check on %s", ticker)
        price_history = []

    if price_history:
        metrics = compute_setup_metrics(price_history)
        if metrics is not None:
            if metrics.drawdown_from_52w_high > settings.ladder.structural_damage_drawdown_pct:
                closed_reason = (
                    f"structural_damage_drawdown: {metrics.drawdown_from_52w_high:.1f}% "
                    f"> {settings.ladder.structural_damage_drawdown_pct}%"
                )
                await repo.close_tracked_ticker(
                    db_path,
                    ticker=ticker,
                    status="thesis_broken",
                    closed_timestamp_utc=timestamp,
                    closed_reason=closed_reason,
                )
                if notifier is not None:
                    notifier.enqueue(format_closed_alert(ticker=ticker, reason=closed_reason))
                logger.info("Ladder CLOSED thesis_broken (structural drawdown): %s", ticker)
                return

            if metrics.pct_above_200dma < -settings.ladder.structural_damage_below_200dma_pct:
                closed_reason = (
                    f"structural_damage_below_200dma: {metrics.pct_above_200dma:.1f}% "
                    f"below {settings.ladder.structural_damage_below_200dma_pct}%"
                )
                await repo.close_tracked_ticker(
                    db_path,
                    ticker=ticker,
                    status="thesis_broken",
                    closed_timestamp_utc=timestamp,
                    closed_reason=closed_reason,
                )
                if notifier is not None:
                    notifier.enqueue(format_closed_alert(ticker=ticker, reason=closed_reason))
                logger.info("Ladder CLOSED thesis_broken (structural 200dma): %s", ticker)
                return

    # 4. Recovery check — close as recovered if price > last_alert_price * (1 + recovery_pct/100)
    recovery_threshold = last_alert_price * (1 + settings.ladder.recovery_exit_pct / 100.0)
    if current_price >= recovery_threshold:
        await repo.close_tracked_ticker(
            db_path,
            ticker=ticker,
            status="recovered",
            closed_timestamp_utc=timestamp,
            closed_reason=f"recovery_above_{recovery_threshold:.2f}",
        )
        logger.info(
            "Ladder CLOSED recovered: %s at %.2f (threshold %.2f)",
            ticker,
            current_price,
            recovery_threshold,
        )
        return

    # 5. Rung threshold check
    if rung_count >= settings.ladder.max_rungs:
        return  # Cap enforced — no more rungs

    rung_threshold = last_alert_price * (1 - settings.ladder.step_pct / 100.0)
    if current_price > rung_threshold:
        return  # Price hasn't dropped enough for next rung

    # Fire rung alert
    new_rung = rung_count + 1
    alert_id = await repo.insert_alert(
        db_path,
        timestamp_utc=timestamp,
        ticker=ticker,
        price_at_alert=current_price,
        rung_number=new_rung,
        parent_alert_id=first_alert_id,
        pct_change_day=None,
        flags=None,
    )

    await repo.update_tracked_ticker_rung(
        db_path,
        ticker=ticker,
        last_alert_id=alert_id,
        last_alert_timestamp_utc=timestamp,
        last_alert_price=current_price,
        rung_count=new_rung,
    )

    if notifier is not None:
        notifier.enqueue(
            format_rung_alert(
                ticker=ticker,
                rung_number=new_rung,
                prev_rung_number=rung_count,
                prev_alert_price=last_alert_price,
                current_price=current_price,
                first_alert_price=first_alert_price,
            )
        )
    logger.info(
        "Rung %d alert: %s at %.2f (prev %.2f, threshold %.2f)",
        new_rung,
        ticker,
        current_price,
        last_alert_price,
        rung_threshold,
    )
