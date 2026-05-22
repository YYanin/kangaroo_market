"""Notification layer: queues alert messages and drains them to Pushbullet or Telegram.

Design (from AGENTS.md + design doc Section 12):
- Notifier is a Protocol; concrete implementations are PushbulletNotifier and TelegramNotifier.
- Format strings are module-level constants — independently testable without HTTP.
- The notifier accumulates messages in an in-memory queue during a pipeline run.
  run_pipeline() calls drain() once at the end; a send failure never aborts the run.
- The provider is chosen by config.notification.provider ("pushbullet" | "telegram").
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import aiohttp

from kangaroo.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Format string constants (Section 12 of design doc)
# ---------------------------------------------------------------------------

# rung-1 alert: [NEW] AAPL -6.2% — Tap for details.
FMT_NEW = "[NEW] {ticker} {pct:+.1f}% — {summary}. Tap for details."

# rung-2+ alert: [RUNG 2] AAPL -3.1% from rung 1 ($92.40). Total drawdown 10.3%. Tap for details.
FMT_RUNG = (
    "[RUNG {n}] {ticker} {pct_from_prev:+.1f}% from rung {prev_n} (${prev_price:.2f}). "
    "Total drawdown {total_drawdown:.1f}%. Tap for details."
)

# thesis-broken close: [CLOSED] AAPL — tracking ended with reason. Review before any further action.
FMT_CLOSED = "[CLOSED] {ticker} tracking ended. Reason: {reason}. Review before any further action."


# ---------------------------------------------------------------------------
# Typed payload helpers
# ---------------------------------------------------------------------------


def format_new_alert(
    *,
    ticker: str,
    pct_change: float,
    summary: str,
) -> str:
    """Format a rung-1 [NEW] notification."""
    return FMT_NEW.format(ticker=ticker, pct=pct_change, summary=summary)


def format_rung_alert(
    *,
    ticker: str,
    rung_number: int,
    prev_rung_number: int,
    prev_alert_price: float,
    current_price: float,
    first_alert_price: float,
) -> str:
    """Format a rung-2+ [RUNG N] notification with cumulative drawdown."""
    pct_from_prev = (current_price - prev_alert_price) / prev_alert_price * 100.0
    total_drawdown = (first_alert_price - current_price) / first_alert_price * 100.0
    return FMT_RUNG.format(
        n=rung_number,
        ticker=ticker,
        pct_from_prev=pct_from_prev,
        prev_n=prev_rung_number,
        prev_price=prev_alert_price,
        total_drawdown=total_drawdown,
    )


def format_closed_alert(*, ticker: str, reason: str) -> str:
    """Format a [CLOSED] thesis-broken notification."""
    return FMT_CLOSED.format(ticker=ticker, reason=reason)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Notifier(Protocol):
    """Queue-and-drain notification interface.  Concrete classes do not need to inherit."""

    def enqueue(self, message: str) -> None:
        """Add a message to the outbound queue."""
        ...

    async def drain(self) -> None:
        """Send all queued messages; swallow and log HTTP failures."""
        ...


# ---------------------------------------------------------------------------
# Concrete implementations
# ---------------------------------------------------------------------------


class PushbulletNotifier:
    """Sends push notes via the Pushbullet HTTP API.

    POST https://api.pushbullet.com/v2/pushes
    Header: Access-Token: {token}
    Body: {"type": "note", "body": "..."}
    """

    _API_URL = "https://api.pushbullet.com/v2/pushes"

    def __init__(self, token: str, session: aiohttp.ClientSession) -> None:
        self._token = token
        self._session = session
        self._queue: list[str] = []

    def enqueue(self, message: str) -> None:
        self._queue.append(message)

    async def drain(self) -> None:
        messages, self._queue = self._queue, []
        for msg in messages:
            try:
                async with self._session.post(
                    self._API_URL,
                    headers={"Access-Token": self._token},
                    json={"type": "note", "body": msg},
                ) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.warning("Pushbullet push returned %s: %s", resp.status, body[:200])
            except Exception:
                logger.exception("Failed to send Pushbullet notification")


class TelegramNotifier:
    """Sends messages via the Telegram Bot HTTP API.

    POST https://api.telegram.org/bot{token}/sendMessage
    Body: {"chat_id": "...", "text": "..."}
    """

    def __init__(self, bot_token: str, chat_id: str, session: aiohttp.ClientSession) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._session = session
        self._queue: list[str] = []

    def _api_url(self) -> str:
        return f"https://api.telegram.org/bot{self._bot_token}/sendMessage"

    def enqueue(self, message: str) -> None:
        self._queue.append(message)

    async def drain(self) -> None:
        messages, self._queue = self._queue, []
        for msg in messages:
            try:
                async with self._session.post(
                    self._api_url(),
                    json={"chat_id": self._chat_id, "text": msg},
                ) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.warning("Telegram push returned %s: %s", resp.status, body[:200])
            except Exception:
                logger.exception("Failed to send Telegram notification")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_notifier(settings: Settings, session: aiohttp.ClientSession) -> Notifier:
    """Instantiate the configured notifier based on config.notification.provider."""
    provider = settings.notification.provider.lower()
    if provider == "telegram":
        return TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            session=session,
        )
    return PushbulletNotifier(token=settings.pushbullet_token, session=session)
